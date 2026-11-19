/*
  ===========================================================================
  OpenTMS Charge_Control -- Teensy 4.1  (also builds unchanged for a Teensy 4.0)
  ===========================================================================

  WHAT THIS DOES
    Watches the voltage on a capacitor bank (through a Micsig 500x HV differential
    probe) and turns the charging circuit off when it reaches a setpoint. Two
    independent ways of doing that run at the same time:

      METHOD 1 (hardware): the AD5683 DAC outputs a threshold voltage, and an external
               comparator compares it with the probe output. The comparator decides,
               so this works even if the firmware hangs. Most fail-safe.
      METHOD 2 (firmware): the ADS8699 ADC measures the probe output and loop() drives
               PIN_CHRG_CTRL directly.

    Both use the same setpoint, set over USB with CMD_SET_SETPOINT. It is always 0 V
    (charging disabled) after boot.

  HARDWARE
    Chip         Bus / pins                        Measures / does
    -----------  --------------------------------  -----------------------------------------
    ADS8699      SPI  (11 MOSI, 12 MISO, 13 SCK)   probe output, 18-bit, fast. Drives METHOD 2.
                 CS/CONVST 10, RVS 6, RST 9
    AD5683(R)    SPI1 (26 MOSI1, 27 SCK1), CS 0    comparator threshold (METHOD 1).
                 VLOGIC -> 3.3 V                   Write-only: this package has VLOGIC where
                                                   the "-3" option has SDO, so MISO1 (pin 1)
                                                   is unused. (On a Teensy 4.0, 26/27 are
                                                   bottom pads.)
    Teensy ADC_0 A0 (pin 14)                       probe output via 27.14k/9.99k divider:
                                                   a second, independent cap-voltage reading
    Teensy ADC_1 A4 (pin 18)                       DAC output via DACSENSE divider: checks the
                                                   comparator threshold is really there
    Output       pin 5                             PIN_CHRG_CTRL: HIGH = charge circuit connected

    The two SPI buses are separate peripherals (LPSPI4 and LPSPI3), so DAC traffic never
    shares wires, clock or chip-select with the ADC. Nothing uses I2C, so A4/A5 (the
    default SDA/SCL pins) are free for analog use.

  HOW loop() RUNS  (no RTOS, no threads, nothing blocks)
    Each pass:
      1. ads8699_readBatch()  NUM_AVG conversions back-to-back.   <- almost all of the time
                              (~7 us each, so 500 -> ~3.5 ms -> ~280 passes/s)
      2. intadc_service()     collect the Teensy A0/A4 pair that converted during step 1,
                              start the next pair.
      3. control decision     set PIN_CHRG_CTRL from the batch mean.
      4. telemetry_service()  send any telemetry packets that are due.
      5. slow services        config readback, commands, DAC, Teensy-ADC averages.
                              Each one returns immediately unless its own timer is due, and
                              does at most one short bus transaction when it is.
    So the control loop's reaction time is about one batch. Lower NUM_AVG (CMD_SET_AVG)
    = faster reaction, more noise.

  CODE MAP (sections appear in this order below)
     1. Configuration     pins, ranges, calibration constants   <- most edits happen here
     2. Shared state      globals shared between sections
     3. Link output       sendPacket / sendAck / logMsg
     4. ADS8699           L0 SPI + registers, L1 frame decode, L2 batch, L3 volts
     5. Teensy ADCs       A0 / A4 setup + register verification, non-blocking read
     6. AD5683 DAC        SPI1 driver, verification, dac_service
     7. Config checks     runtime readback of every chip's configuration
     8. Setpoint          applySetpoint (the only place the setpoint changes)
     9. Telemetry         packet builders + rate scheduler
    10. Commands          frame receiver + dispatchCommand
    11. setup() / loop()
    Protocol.h holds the wire format (IDs, structs, CRC, COBS), shared with the Python side.

  SAFETY DESIGN
    - PIN_CHRG_CTRL is driven LOW before it becomes an output, and stays LOW until a
      setpoint is set AND the ADS8699 config is confirmed by readback.
    - Every chip's configuration is read back at boot and re-checked continuously while
      running (section 7). A mismatch is logged, counted and rewritten; an ADS8699
      mismatch also forces the charge pin off.
    - The DAC output is measured by Teensy ADC_1 (A4); a mismatch rewrites the DAC. Its
      settings and code are also rewritten every DAC_REFRESH_MS, so a glitch or brownout
      reset is undone even when it isn't detected. (Parts with SDO also get an exact
      digital readback of the code the chip holds.)
    - Nothing in loop() waits on a bus or on USB: a full USB buffer drops telemetry
      (counted in tx_dropped) instead of stalling the control loop.
    - No sample is ever discarded. Parity errors / RVS timeouts are counted, not filtered.

  COMMON CHANGES -- where to look
    Calibration constants ............ section 1 (*_CAL_SCALE / *_CAL_OFFSET)
    DAC divider on A4 ................ section 1, DACSENSE_R1 / DACSENSE_R2  (!! set these !!)
    AD5683 vs AD5683R, gain, SDO ..... section 1, "AD5683 DAC"
    Default telemetry rates .......... section 1, tlmSchedule[]
    Add a command .................... Protocol.h + section 10 + opentms_protocol.py
    Add a telemetry packet ........... Protocol.h + section 9 + opentms_protocol.py
    (Step-by-step versions of the last two are at the end of this file.)

  BUILDING: Arduino IDE + Teensyduino, Tools > Board > Teensy 4.1, USB Type: Serial.
    Protocol.h must sit in the same folder as this file. Libraries: ADC (ships with
    Teensyduino) and SPI. No Adafruit libraries are needed any more.
*/

#include <SPI.h>
#include <ADC.h>
#include <stdarg.h>
#include "Protocol.h"

using namespace proto;   // lets us write TLM_FAST instead of proto::TLM_FAST

ADC *adc = new ADC();    // Teensy internal ADCs (adc->adc0 = HW ADC1, adc->adc1 = HW ADC2)

// Set to 1 to stop METHOD 2 from touching PIN_CHRG_CTRL (e.g. while calibrating the ADC
// against a meter). Telemetry and the DAC/comparator path keep running.
const uint8_t CALIBRATION_MODE = 0;

///////////////////////////////////////////////////////////////////////////////
// 1. CONFIGURATION
///////////////////////////////////////////////////////////////////////////////

// ---------- Pins ----------
const uint8_t PIN_CS        = 10;  // ADS8699 CONVST/CS
const uint8_t PIN_RST       = 9;   // ADS8699 RST
const uint8_t PIN_RVS       = 6;   // ADS8699 RVS (conversion ready)
const uint8_t PIN_CHRG_CTRL = 5;   // charge control output

// Each signal gets its own hardware ADC, so neither high-impedance source has to charge
// a sample capacitor left at the other signal's voltage. Both pins must be readable by
// their ADC; intadc_init() test-reads them and reports a wrong pin.
constexpr uint8_t PIN_CAP_SENSE = A0;  // pin 14, read by Teensy ADC_0
constexpr uint8_t PIN_DAC_SENSE = A4;  // pin 18, read by Teensy ADC_1

// ---------- ADS8699 ----------
const uint32_t SPI_CLOCK_HZ = 30000000; // datasheet allows up to 66.67 MHz
SPISettings adsSPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0);

const uint16_t T_CONV_US      = 7;   // >= 5 us tconv + margin (register access only)
const uint16_t RVS_TIMEOUT_US = 50;  // safety net for the RVS poll; real tconv ~5 us

// Input ranges (RANGE_SEL_REG bits 3:0). The firmware assumes ADS8699_RANGE below for
// every code -> volts conversion, which is why the register is continuously verified:
// if the chip silently fell back to its +-12.288 V default, readings would be ~42% low.
enum ADS8699_Range : uint8_t {
  RANGE_PM_3VREF     = 0b0000, // +-12.288 V  (power-on default)
  RANGE_PM_2p5VREF   = 0b0001, // +-10.24 V
  RANGE_PM_1p5VREF   = 0b0010, // +-6.144 V
  RANGE_PM_1p25VREF  = 0b0011, // +-5.12 V
  RANGE_PM_0p625VREF = 0b0100, // +-2.56 V
  RANGE_0_3VREF      = 0b1000, // 0 to 12.288 V
  RANGE_0_2p5VREF    = 0b1001, // 0 to 10.24 V
  RANGE_0_1p5VREF    = 0b1010, // 0 to 6.144 V
  RANGE_0_1p25VREF   = 0b1011  // 0 to 5.12 V
};
struct RangeInfo { float negFS; float posFS; };

const ADS8699_Range ADS8699_RANGE = RANGE_PM_1p25VREF;  // +-5.12 V
const bool ADS8699_USE_EXT_REF = false;

const uint8_t  ADS8699_REG_DATAOUT_CTL = 0x10;
const uint8_t  ADS8699_REG_RANGE_SEL   = 0x14;
const uint16_t DATAOUT_CTL_PAR_EN      = (1 << 3); // append ADC + frame parity bits

// Batch size: conversions averaged per loop pass. Changeable at runtime (CMD_SET_AVG).
uint16_t NUM_AVG = 500;
const uint16_t NUM_AVG_MIN = 1;
const uint16_t NUM_AVG_MAX = 8192;   // 8192 * (2^18 - 1) < 2^31, so the sum fits int32

// ---------- Calibration ----------
// Every chain is: ADC input volts -> that channel's linear cal (0-5 V level, fit against
// a meter) -> x PROBE_MULTIPLIER -> probe cal -> capacitor volts. The channel cals are
// fit in the plotter's Calibration tab, which prints these lines ready to paste.
//   meter_volts = *_CAL_SCALE * raw_volts + *_CAL_OFFSET

// ADS8699: ADC input pin (no divider) vs. meter.
// Y = 0.9997*X + 9.895e-005 (cal performed 10/2/2026)
const float ADS8699_CAL_OFFSET = 0.00009895f;
const float ADS8699_CAL_SCALE  = 0.9997f;

// ---------- Probe ----------
const uint16_t PROBE_MULTIPLIER = 500;   // Micsig 1300 V diff probe, 500x
// Probe calibration (cap volts vs. meter), applied after the multiplier to every channel
// that measures the probe (ADS8699 and Teensy A0): Y = 0.9854*X + 0.1270
const float VDIV_CAL_OFFSET = 0.127f;
const float VDIV_CAL_SCALE  = 0.9854f;

// ---------- Teensy internal ADCs ----------
const float    TEENSY_ADC_VREF   = 3.3f;     // T4 ADC reference is the 3.3 V rail
const uint8_t  INTADC_RES_BITS   = 12;
const float    INTADC_FULL_SCALE = 4096.0f;  // LSB = VREF / 2^12
const uint8_t  INTADC_HW_AVG     = 16;       // hardware averaging per reported count: 4/8/16/32
const uint32_t INTADC_STALL_US   = 5000;     // a conversion pair taking longer = error
static_assert(INTADC_RES_BITS == 12, "intadc_registersOk() expects 12-bit mode");
static_assert(INTADC_HW_AVG == 4 || INTADC_HW_AVG == 8 || INTADC_HW_AVG == 16 ||
              INTADC_HW_AVG == 32, "i.MX RT ADC hardware averaging is 4, 8, 16 or 32");

// A0: probe output -> divider -> pin.  Pin volts * (R1+R2)/R2 = signal volts (0-5 V).
// R1 is the top resistor (signal to pin), R2 the bottom (pin to ground).
const float CAPSENSE_R1 = 20028.0f;
const float CAPSENSE_R2 = 10033.0f;
// A0 signal volts (before the divider) vs. meter. Start at identity.
const float INTADC_CAP_CAL_SCALE  = 1.0f;
const float INTADC_CAP_CAL_OFFSET = 0.0f;

// A4: DAC output (0-5 V) -> divider -> pin. Teensy 4.x pins are NOT 5 V tolerant.
// !!! SET THESE TO YOUR ACTUAL DIVIDER !!!  (placeholder: 5 V -> 3.0 V)
const float DACSENSE_R1 = 10008.0f;
const float DACSENSE_R2 = 20129.0f;
// A4 signal volts (DAC output, before the divider) vs. meter. Start at identity.
const float INTADC_DAC_CAL_SCALE  = 1.0f;
const float INTADC_DAC_CAL_OFFSET = 0.0f;

// ---------- AD5683 DAC (SPI1) ----------
// Output = code / 65536 * DAC_VREF_V * (DAC_GAIN_2X ? 2 : 1)
// Teensy 4.1 SPI1 pins: MOSI1 26, SCK1 27, MISO1 1 (or 39 with SPI1.setMISO(39)).
const uint8_t  PIN_DAC_CS       = 0;       // SPI1 CS (any GPIO works; driven by hand)
const uint8_t  PIN_DAC_RESET    = 255;     // 255 = not connected (tie RESET to VLOGIC on
                                           // RESET-option parts). Set a pin to hardware-reset.
const uint32_t DAC_SPI_CLOCK_HZ = 10000000;// chip allows 50 MHz; DAC writes are rare, so a
                                           // slower clock costs nothing and tolerates wiring
SPISettings dacSPISettings(DAC_SPI_CLOCK_HZ, MSBFIRST, SPI_MODE1);  // data latched on SCLK fall

const bool  DAC_INTERNAL_REF = true;  // AD5683R: true (2.5 V internal ref, VREF pin is an output)
                                      // AD5683 (no R): false, and wire an external ref to VREF
const float DAC_VREF_V       = 2.5f;  // reference voltage (internal ref = 2.5 V)
const bool  DAC_GAIN_2X      = true;  // true: 0..2*VREF (0-5 V; needs VDD >= 5 V)
const bool  DAC_HAS_SDO      = false; // false: this part has VLOGIC instead of SDO (write-only).
                                      // Only the AD5683R 10-lead MSOP "-3" option has SDO; set
                                      // true for that part, with SDO wired to MISO1 (pin 1).
const uint8_t DAC_GAIN = DAC_GAIN_2X ? 2 : 1;
const uint32_t DAC_RETRY_MS = 100;    // retry interval after a failed (readback-mismatch) write
// Without readback the firmware can't see what the chip holds, so it rewrites the control
// register and the current code this often (0 = never). Rewriting the same code doesn't
// move the output; scope VOUT once to confirm the control-register rewrite is glitch-free
// on your board.
const uint32_t DAC_REFRESH_MS = 1000;

// Measured DAC output (Teensy A4) must be within this of the commanded voltage.
// 50 mV of DAC = ~25 V of capacitor voltage at 500x, so this is a coarse "the threshold is
// roughly right" check: the T4 ADC references the 3.3 V rail (~1% tolerance). After fitting
// INTADC_DAC_CAL_* against a meter you can tighten it to ~10-15 mV.
const float DAC_VERIFY_TOL_INT_V = 0.050f;

// ---------- Setpoint ----------
const float SETPOINT_MAX_V        = 1000.0f; // hard ceiling, enforced in firmware
const float SETPOINT_MIN_ACTIVE_V = 1.0f;    // below this = charging disabled
float chargeSetpointV = 0.0f;                // always 0 on boot

// ---------- Slow-task timing ----------
const uint32_t CFG_CHECK_MS = 20;   // one config item checked per interval (round-robin)
const uint32_t INT_SLOW_MS  = 100;  // Teensy-ADC slow averages / DAC check rate

// ---------- Telemetry schedule ----------
// One entry per rate-controlled telemetry packet. rateHz: RATE_OFF, 1..65534 Hz, or
// RATE_EVERY_BATCH. The host changes these with CMD_SET_TELEM_RATE; these are the
// power-on defaults.
struct TlmSchedule {
  uint8_t  id;
  uint16_t rateHz;
  uint32_t lastUs;   // scheduler bookkeeping
};
TlmSchedule tlmSchedule[] = {
  { TLM_FAST,   RATE_EVERY_BATCH, 0 },
  { TLM_RAW,    RATE_OFF,         0 },
  { TLM_STATUS, 10,               0 },
  { TLM_CAL,    RATE_OFF,         0 },   // the plotter turns this on in its Calibration tab
};
const uint8_t TLM_SCHED_N = sizeof(tlmSchedule) / sizeof(tlmSchedule[0]);

///////////////////////////////////////////////////////////////////////////////
// 2. SHARED STATE
// Types first (the Arduino IDE auto-generates function prototypes just above the
// first function, so any type used in a function signature must be declared before it).
///////////////////////////////////////////////////////////////////////////////

// One batch of raw ADS8699 conversions. All integer.
struct Ads8699Batch {
  int32_t  sum;           // sum of n 18-bit codes
  uint16_t n;
  int32_t  minCode;
  int32_t  maxCode;
  uint16_t parityErrors;  // frames whose parity didn't match (still included in sum)
  uint16_t rvsTimeouts;   // frames read after an RVS timeout (still included in sum)
  uint32_t tStartUs;
};

// Latest Teensy ADC pair, raw counts.
struct IntAdcSample {
  int32_t capCounts;   // A0, 0..4095
  int32_t dacCounts;   // A4, 0..4095
  bool    fresh;       // converted during the most recent loop pass
};

// Result of comparing the measured DAC output against the commanded voltage.
struct DacCheck {
  bool  valid;   // a comparison has been made since the last DAC change
  bool  ok;
  bool  skip;    // skip the averaging window that straddles a DAC change
  float errV;
};

// ADS8699
bool ads8699RangeOk  = false;  // RANGE_SEL_REG confirmed by readback
bool ads8699ParityOk = false;  // DATAOUT_CTL_REG (parity) confirmed by readback
Ads8699Batch ads8699Last = {0, 1, 0, 0, 0, 0, 0};
uint32_t batchSeq = 0;
float vCapFast = 0.0f;            // latest batch mean, cap volts (drives METHOD 2)
uint32_t totalParityErrors = 0;
uint32_t totalRvsTimeouts  = 0;
uint32_t totalCfgFaults    = 0;   // config mismatches found at runtime (all chips)

// Teensy ADCs
IntAdcSample intSample = {0, 0, false};
bool intadcCfgOk = false;         // both modules' hardware registers verified
uint32_t intadcCfgSnapshot[2] = {0, 0};  // ADCx_CFG as verified at init (see section 5)
uint32_t totalIntAdcErrors = 0;
uint16_t lastIntAdcError = 0;
float vCapIntFast = 0.0f;         // latest A0 reading in cap volts
float vCapIntSlow = 0.0f;         // A0 average over INT_SLOW_MS
float vDacIntSlow = 0.0f;         // A4 average over INT_SLOW_MS, in DAC volts
int32_t intSlowCapSum = 0, intSlowDacSum = 0;
uint16_t intSlowN = 0;

// DAC
float dacTargetVolts = 0.0f;      // set by applySetpoint(); dac_service() writes it
int32_t lastDacCodeWritten = -1;  // -1 = nothing confirmed written yet
bool dacReadbackOk = false;       // last SPI readback matched (only meaningful with SDO)
DacCheck dacChkInt = {false, false, true, 0.0f};

// Link
bool chargePinOn = false;
uint8_t txSeq = 0;
uint32_t txDropped = 0;
uint32_t rxErrors = 0;
float loopHz = 0.0f;

///////////////////////////////////////////////////////////////////////////////
// 3. LINK OUTPUT
// Everything the device sends goes through sendPacket(). See Protocol.h for the format.
///////////////////////////////////////////////////////////////////////////////

// Frames and sends one packet. Never blocks: if the USB buffer can't take the
// whole frame it's dropped and counted. seq advances either way, so the host
// sees drops as gaps.
bool sendPacket(uint8_t id, const void *payload, size_t len) {
  uint8_t frame[MAX_ENCODED];
  size_t n = buildFrame(id, txSeq++, payload, len, frame);
  if (n == 0) return false;
  if (Serial.availableForWrite() < (int)n) { txDropped++; return false; }
  Serial.write(frame, n);
  return true;
}

// Reply to a command. Every command must get exactly one ACK (the host times out otherwise).
void sendAck(uint8_t cmdId, uint8_t cmdSeq, uint8_t status, float value, uint32_t aux) {
  TlmAck a;
  a.cmd_id = cmdId;
  a.cmd_seq = cmdSeq;
  a.status = status;
  a.value = value;
  a.aux = aux;
  sendPacket(TLM_ACK, &a, sizeof(a));
}

// printf-style text message as a TLM_LOG packet; the plotter prints these to its console.
// Use for rare events (boot results, faults), not per-loop data -- that's what telemetry is
// for. (Not called logf: that name is taken by math.h.)
void logMsg(uint8_t level, const char *fmt, ...) {
  uint8_t buf[MAX_PAYLOAD];
  buf[0] = level;
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf((char *)buf + 1, sizeof(buf) - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  size_t textLen = ((size_t)n < sizeof(buf) - 2) ? (size_t)n : sizeof(buf) - 2;  // drop the NUL
  sendPacket(TLM_LOG, buf, 1 + textLen);
}

void setChargePin(bool on) {
  digitalWrite(PIN_CHRG_CTRL, on ? HIGH : LOW);
  chargePinOn = on;
}

///////////////////////////////////////////////////////////////////////////////
// 4. ADS8699 (SPI)
// Layered so each level can be used on its own:
//   L0 ads8699_readFrame()          raw 32-bit SPI frame (uint32)
//   L1 ads8699_frameCode()          18-bit code as int32 (0..262143, offset binary)
//      ads8699_frameParityOk()      parity check of one frame
//   L2 ads8699_readBatch()          N frames -> integer sum/min/max + error counts
//   L3 ads8699_codeToAdcVolts()     code -> ADC input volts (first float step)
//      ads8699_adcVoltsToCapVolts() ADC volts -> capacitor volts
//
// CONVST/CS PIN: one pin, two jobs. LOW->HIGH starts a conversion; HIGH->LOW opens the
// SPI data frame. Read cycle: raise pin, wait for RVS high, lower pin, clock 32 bits,
// leave LOW (the chip acquires the next sample) until the next conversion.
///////////////////////////////////////////////////////////////////////////////

bool ads8699CfgOk() { return ads8699RangeOk && ads8699ParityOk; }

// ---------- L0: SPI / register access ----------

// Sends 32 bits MSB-first, returns the 32 bits clocked back. CS must be LOW.
uint32_t spiTransfer32(uint32_t txWord) {
  uint32_t rx = 0;
  rx |= (uint32_t)SPI.transfer((txWord >> 24) & 0xFF) << 24;
  rx |= (uint32_t)SPI.transfer((txWord >> 16) & 0xFF) << 16;
  rx |= (uint32_t)SPI.transfer((txWord >> 8)  & 0xFF) << 8;
  rx |= (uint32_t)SPI.transfer( txWord        & 0xFF);
  return rx;
}

// WRITE command (full 16-bit word). CONVST and CS share a pin, so closing the
// frame also starts a conversion; it's read and discarded to keep the pin and
// state machine in sync. Leaves CS LOW (acquiring).
void ads8699_writeRegister16(uint8_t addr, uint16_t data16) {
  uint32_t cmd = 0;
  cmd |= (0b11010UL << 27);               // WRITE opcode
  cmd |= (0b00UL << 25);                  // full word
  cmd |= ((uint32_t)(addr & 0xFE) << 16); // 9-bit address, LSB 0
  cmd |= data16;

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(cmd);
  SPI.endTransaction();
  digitalWrite(PIN_CS, HIGH);             // command executes, conversion starts
  delayMicroseconds(T_CONV_US);

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(0x00000000);              // NOP, discard
  SPI.endTransaction();
}

// READ command (full 16-bit word). The register value comes out in the NEXT frame, so
// this sends the command, then a NOP, and returns the raw 32-bit NOP frame. Callers
// check both 16-bit halves. Leaves CS LOW.
uint32_t ads8699_readRegister16(uint8_t addr) {
  uint32_t cmd = 0;
  cmd |= (0b11001UL << 27);               // READ opcode
  cmd |= (0b00UL << 25);
  cmd |= ((uint32_t)(addr & 0xFE) << 16);

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(cmd);
  SPI.endTransaction();
  digitalWrite(PIN_CS, HIGH);
  delayMicroseconds(T_CONV_US);

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  uint32_t raw = spiTransfer32(0x00000000);
  SPI.endTransaction();
  return raw;
}

bool ads8699_registerMatches(uint32_t rb, uint16_t expected) {
  return ((uint16_t)(rb >> 16) == expected) || ((uint16_t)(rb & 0xFFFF) == expected);
}

// Write, read back, retry up to maxAttempts. Returns true only on a confirmed match.
bool ads8699_writeRegisterVerified(uint8_t addr, uint16_t data16, const char *regName,
                                   uint8_t maxAttempts = 5) {
  for (uint8_t attempt = 1; attempt <= maxAttempts; attempt++) {
    ads8699_writeRegister16(addr, data16);
    uint32_t rb = ads8699_readRegister16(addr);
    bool ok = ads8699_registerMatches(rb, data16);
    logMsg(ok ? LVL_INFO : LVL_WARN, "%s write %u: expected=0x%04X readback=0x%04X/0x%04X %s",
           regName, attempt, data16, (uint16_t)(rb >> 16), (uint16_t)(rb & 0xFFFF),
           ok ? "[OK]" : "[MISMATCH]");
    if (ok) return true;
    delay(5);
  }
  logMsg(LVL_ERROR, "%s could not be verified", regName);
  return false;
}

uint16_t ads8699_rangeRegValue() {
  uint16_t v = (uint16_t)ADS8699_RANGE;
  if (ADS8699_USE_EXT_REF) v |= (1 << 6);  // INTREF_DIS
  return v;
}

// Reset, then configure range and parity with readback verification.
void ads8699_init() {
  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_RST, LOW);
  delayMicroseconds(1);         // >= 100 ns
  digitalWrite(PIN_RST, HIGH);
  delay(25);                    // tD_RST_POR = 20 ms max

  SPI.begin();
  ads8699RangeOk  = ads8699_writeRegisterVerified(ADS8699_REG_RANGE_SEL, ads8699_rangeRegValue(),
                                                  "RANGE_SEL_REG");
  ads8699ParityOk = ads8699_writeRegisterVerified(ADS8699_REG_DATAOUT_CTL, DATAOUT_CTL_PAR_EN,
                                                  "DATAOUT_CTL_REG");
  if (!ads8699CfgOk()) {
    logMsg(LVL_ERROR, "ADS8699 config not confirmed -- direct charge control held OFF");
  }
}

// Reads one conversion: CS rising edge starts it, RVS signals ready, CS falling
// edge opens the frame. Leaves CS LOW (acquiring the next sample).
uint32_t ads8699_readFrame(bool *rvsTimedOut) {
  digitalWrite(PIN_CS, HIGH);
  uint32_t t0 = micros();
  bool timedOut = false;
  while (digitalRead(PIN_RVS) == LOW) {
    if ((uint32_t)(micros() - t0) > RVS_TIMEOUT_US) { timedOut = true; break; }
  }

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  uint32_t frame = spiTransfer32(0x00000000);
  SPI.endTransaction();

  if (rvsTimedOut) *rvsTimedOut = timedOut;
  return frame;
}

// ---------- L1: frame decoding (integer) ----------
// 32-bit frame layout with parity on: [31:14] 18-bit code, [13] ADC parity,
// [12] frame parity, [11:0] zeros.

int32_t ads8699_frameCode(uint32_t frame) {
  return (int32_t)((frame >> 14) & 0x3FFFFUL);   // 0 = negative full scale
}

// 1 if v has an odd number of set bits (XOR-fold), i.e. the even-parity bit.
uint8_t evenParityBit(uint32_t v) {
  v ^= v >> 16; v ^= v >> 8; v ^= v >> 4; v ^= v >> 2; v ^= v >> 1;
  return (uint8_t)(v & 0x1);
}

// Bit 13 = parity of the ADC bits, bit 12 = parity of the whole output frame
// (same thing here, since the frame carries only conversion data).
bool ads8699_frameParityOk(uint32_t frame) {
  uint8_t expected = evenParityBit((uint32_t)ads8699_frameCode(frame));
  return (((frame >> 13) & 0x1) == expected) && (((frame >> 12) & 0x1) == expected);
}

// ---------- L2: batch (integer) ----------

void ads8699_readBatch(uint16_t n, Ads8699Batch &b) {
  if (n < NUM_AVG_MIN) n = NUM_AVG_MIN;
  if (n > NUM_AVG_MAX) n = NUM_AVG_MAX;

  b.sum = 0;
  b.n = n;
  b.minCode = INT32_MAX;
  b.maxCode = INT32_MIN;
  b.parityErrors = 0;
  b.rvsTimeouts = 0;
  b.tStartUs = micros();

  const bool checkParity = ads8699ParityOk;   // parity bits only mean something once enabled
  for (uint16_t i = 0; i < n; i++) {
    bool timedOut;
    uint32_t frame = ads8699_readFrame(&timedOut);
    int32_t code = ads8699_frameCode(frame);

    if (timedOut) b.rvsTimeouts++;
    if (checkParity && !ads8699_frameParityOk(frame)) b.parityErrors++;

    b.sum += code;                       // every sample counts, error or not
    if (code < b.minCode) b.minCode = code;
    if (code > b.maxCode) b.maxCode = code;
  }
}

// ---------- L3: conversion to volts (float) ----------

RangeInfo getRangeInfo(ADS8699_Range r) {
  switch (r) {
    case RANGE_PM_3VREF:     return { -12.288f, 12.288f };
    case RANGE_PM_2p5VREF:   return { -10.24f,  10.24f  };
    case RANGE_PM_1p5VREF:   return { -6.144f,  6.144f  };
    case RANGE_PM_1p25VREF:  return { -5.12f,   5.12f   };
    case RANGE_PM_0p625VREF: return { -2.56f,   2.56f   };
    case RANGE_0_3VREF:      return { 0.0f,     12.288f };
    case RANGE_0_2p5VREF:    return { 0.0f,     10.24f  };
    case RANGE_0_1p5VREF:    return { 0.0f,     6.144f  };
    case RANGE_0_1p25VREF:   return { 0.0f,     5.12f   };
    default:                 return { -12.288f, 12.288f };
  }
}

// Code (integer or fractional mean) -> ADC input volts, uncalibrated.
float ads8699_codeToAdcVolts(double code) {
  RangeInfo ri = getRangeInfo(ADS8699_RANGE);
  double lsb = (double)(ri.posFS - ri.negFS) / 262144.0;   // 2^18
  return (float)(ri.negFS + code * lsb);
}

double ads8699_batchMeanCode(const Ads8699Batch &b) {
  return (double)b.sum / (double)(b.n ? b.n : 1);
}

// Probe-output volts -> capacitor volts (probe multiplier, then probe cal). Shared by every
// channel that measures the probe, and by the DAC (the comparator compares it with the probe).
float probeVoltsToCapVolts(float vProbe) {
  return (vProbe * PROBE_MULTIPLIER * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;
}

// ADS8699 input volts -> calibrated input volts (channel cal only, still 0-5 V level).
float ads8699_calibrate(float v_adc) {
  return (v_adc * ADS8699_CAL_SCALE) + ADS8699_CAL_OFFSET;
}

// ADC input volts -> capacitor volts (ADC cal, probe multiplier, probe cal).
float ads8699_adcVoltsToCapVolts(float v_adc) {
  return probeVoltsToCapVolts(ads8699_calibrate(v_adc));
}

float ads8699_codeToCapVolts(double code) {
  return ads8699_adcVoltsToCapVolts(ads8699_codeToAdcVolts(code));
}

///////////////////////////////////////////////////////////////////////////////
// 5. TEENSY INTERNAL ADCs
// The two hardware ADCs convert at the same time, one per signal. Conversions run in the
// background during the ADS8699 batch (started at the end of one loop pass, collected at
// the start of the next), so they cost the loop almost nothing.
//
// Configuration is verified against the real hardware registers, not the library's
// cached settings: ADCx_CFG holds resolution (MODE), averaging count (AVGS), reference
// (REFSEL) and timing; ADCx_GC.AVGE enables averaging. The ADC library writes these only
// from its set*() functions, never during reads, so any change while running is a fault.
///////////////////////////////////////////////////////////////////////////////

float intadc_countsToPinVolts(float counts) {
  return counts * (TEENSY_ADC_VREF / INTADC_FULL_SCALE);
}

// Each Teensy chain, in layers:  counts -> pin volts -> raw signal volts (undo divider)
//                                -> calibrated signal volts (channel cal, 0-5 V level)
// A0 then continues through probeVoltsToCapVolts() to capacitor volts.

float intadc_capRawVolts(float counts) {   // A0 signal before divider, uncalibrated
  return intadc_countsToPinVolts(counts) * ((CAPSENSE_R1 + CAPSENSE_R2) / CAPSENSE_R2);
}
float intadc_capCalVolts(float counts) {   // A0 signal, calibrated
  return (intadc_capRawVolts(counts) * INTADC_CAP_CAL_SCALE) + INTADC_CAP_CAL_OFFSET;
}
float intadc_capCountsToCapVolts(float counts) {
  return probeVoltsToCapVolts(intadc_capCalVolts(counts));
}

float intadc_dacRawVolts(float counts) {   // A4 signal (DAC output) before divider, uncalibrated
  return intadc_countsToPinVolts(counts) * ((DACSENSE_R1 + DACSENSE_R2) / DACSENSE_R2);
}
float intadc_dacCountsToDacVolts(float counts) {   // A4 signal, calibrated
  return (intadc_dacRawVolts(counts) * INTADC_DAC_CAL_SCALE) + INTADC_DAC_CAL_OFFSET;
}

volatile uint32_t &intadc_cfgReg(uint8_t i) { return i ? ADC2_CFG : ADC1_CFG; }
volatile uint32_t &intadc_gcReg(uint8_t i)  { return i ? ADC2_GC  : ADC1_GC;  }

uint8_t intadc_avgsField() {   // AVGS encoding: 0 = 4, 1 = 8, 2 = 16, 3 = 32 samples
  switch (INTADC_HW_AVG) { case 4: return 0; case 8: return 1; case 16: return 2; default: return 3; }
}

// Decodes the hardware registers of module i and checks the fields we rely on.
bool intadc_registersOk(uint8_t i) {
  uint32_t cfg = intadc_cfgReg(i);
  uint32_t want = ADC_CFG_MODE(2)                     // 12-bit
                | ADC_CFG_AVGS(intadc_avgsField())    // hardware averaging count
                | ADC_CFG_REFSEL(0);                  // VREFH/VREFL = 3.3 V rail
  uint32_t mask = ADC_CFG_MODE(3) | ADC_CFG_AVGS(3) | ADC_CFG_REFSEL(3);
  return ((cfg & mask) == want) && (intadc_gcReg(i) & ADC_GC_AVGE);
}

void intadc_configureModule(ADC_Module *m) {
  m->setReference(ADC_REFERENCE::REF_3V3);
  m->setResolution(INTADC_RES_BITS);
  m->setAveraging(INTADC_HW_AVG);
  m->setConversionSpeed(ADC_CONVERSION_SPEED::MED_SPEED);
  m->setSamplingSpeed(ADC_SAMPLING_SPEED::LOW_SPEED);   // longer sample time for the divider source
}

// Records and clears any error flag raised by either module.
bool intadc_checkErrors() {
  uint16_t e0 = (uint16_t)adc->adc0->fail_flag;
  uint16_t e1 = (uint16_t)adc->adc1->fail_flag;
  if (e0 == (uint16_t)ADC_ERROR::CLEAR && e1 == (uint16_t)ADC_ERROR::CLEAR) return true;
  totalIntAdcErrors++;
  lastIntAdcError = e0 ? e0 : e1;
  adc->resetError();
  return false;
}

// Configures both modules, verifies the hardware registers, snapshots ADCx_CFG for the
// runtime check, then test-reads each pin on its module (a pin the module can't reach
// raises an error flag here).
bool intadc_init() {
  pinMode(PIN_CAP_SENSE, INPUT_DISABLE);   // disable the T4 pad keeper/pulls on analog inputs
  pinMode(PIN_DAC_SENSE, INPUT_DISABLE);

  ADC_Module *mods[2] = { adc->adc0, adc->adc1 };
  bool ok = true;
  for (uint8_t i = 0; i < 2; i++) {
    intadc_configureModule(mods[i]);
    bool regOk = intadc_registersOk(i);
    intadcCfgSnapshot[i] = intadc_cfgReg(i);
    logMsg(regOk ? LVL_INFO : LVL_ERROR, "Teensy ADC_%u: CFG=0x%08lX GC=0x%08lX %s", i,
           (unsigned long)intadcCfgSnapshot[i], (unsigned long)intadc_gcReg(i),
           regOk ? "[OK]" : "[MISMATCH]");
    ok &= regOk;
  }

  int a0 = adc->adc0->analogRead(PIN_CAP_SENSE);
  int a1 = adc->adc1->analogRead(PIN_DAC_SENSE);
  uint16_t e0 = (uint16_t)adc->adc0->fail_flag;
  uint16_t e1 = (uint16_t)adc->adc1->fail_flag;
  logMsg((e0 || e1) ? LVL_ERROR : LVL_INFO,
         "Teensy ADC test read: A0=%d (err 0x%X)  DAC sense=%d (err 0x%X)", a0, e0, a1, e1);
  if (e0 || e1) {
    ok = false;
    adc->resetError();
  }
  if (!ok) logMsg(LVL_ERROR, "Teensy internal ADC config not confirmed");
  return ok;
}

// Called once per loop pass, right after the ADS8699 batch. Collects the
// A0/A4 pair started at the end of the previous pass (so it was converted
// during this batch), then starts the next pair. Never waits.
void intadc_service() {
  static bool busy = false;
  static elapsedMicros sinceStart;

  intSample.fresh = false;

  if (busy) {
    if (adc->adc0->isComplete() && adc->adc1->isComplete()) {
      intSample.capCounts = adc->adc0->readSingle();
      intSample.dacCounts = adc->adc1->readSingle();
      intSample.fresh = true;
      busy = false;
    } else if (sinceStart > INTADC_STALL_US) {
      totalIntAdcErrors++;
      busy = false;
    } else {
      return;   // still converting (only happens with very small NUM_AVG)
    }
  }

  intadc_checkErrors();
  bool ok0 = adc->adc0->startSingleRead(PIN_CAP_SENSE);
  bool ok1 = adc->adc1->startSingleRead(PIN_DAC_SENSE);
  busy = ok0 && ok1;
  sinceStart = 0;
  if (!busy) totalIntAdcErrors++;
}
// (The slow running average of A0/A4, intadc_slow_service(), is in section 7 because it
//  also feeds the DAC output check from section 6.)

///////////////////////////////////////////////////////////////////////////////
// 6. AD5683(R) DAC (SPI1)
//
// Sets the comparator threshold (METHOD 1).
//
// Every SPI frame is 24 bits, MSB first, latched on SCLK falling edges (SPI mode 1).
// The command executes when CS (the datasheet calls it SYNC) goes high after 24 bits.
//   bits [23:20] command   [19:4] 16-bit data   [3:0] don't care
// Commands used here:
//   0x3 write input + DAC register  (output changes immediately, whatever LDAC is doing)
//   0x4 write control register      data bits: DB19 reset, DB18:17 power-down (00 = normal),
//                                    DB16 REF (1 = internal ref OFF), DB15 gain (1 = 2x),
//                                    DB14 daisy-chain (0 = off)
//   0x5 readback input register     the value comes out on SDO during the NEXT frame
//
// How the DAC is verified (there's no I2C-style ACK on SPI, so a write "succeeds" even
// with nothing connected):
//   - Teensy ADC_1 (A4) measures the real output voltage every INT_SLOW_MS. That catches a
//     wrong code, gain, reference or power-down state (e.g. the chip browned out and reset
//     to 1x gain, code 0). A mismatch rewrites the control register and the code.
//   - Every DAC_REFRESH_MS the control register and code are rewritten anyway, in case a
//     fault is too small for the A4 check's tolerance to see.
//   - SDO readback (only if DAC_HAS_SDO): confirms the exact code the chip holds, after
//     every write and continuously in section 7. The control register can't be read back
//     even then.
// A brownout reset fails safe on its own (code 0 = threshold 0 V, so the comparator
// disconnects charging); the rewrites are what bring the threshold back.
///////////////////////////////////////////////////////////////////////////////

const uint8_t AD5683_CMD_NOP          = 0x0;
const uint8_t AD5683_CMD_WRITE_UPDATE = 0x3;
const uint8_t AD5683_CMD_WRITE_CTRL   = 0x4;
const uint8_t AD5683_CMD_READBACK     = 0x5;

// Volts -> 16-bit DAC code (clamped).
uint16_t dacVoltsToCode(float volts) {
  float code = (volts / (DAC_VREF_V * DAC_GAIN)) * 65536.0f;
  if (code > 65535.0f) code = 65535.0f;
  if (code < 0.0f) code = 0.0f;
  return (uint16_t)code;
}

// 16-bit DAC code -> expected output volts (used to check the measured output).
float dacCodeToVolts(uint16_t code) {
  return DAC_VREF_V * DAC_GAIN * ((float)code / 65536.0f);
}

// Capacitor volts -> probe-output volts the comparator should trip at
// (the inverse of the probe multiplier + probe calibration).
float capVoltToDacVolts(float capVolts) {
  float v_div = (capVolts - VDIV_CAL_OFFSET) / VDIV_CAL_SCALE;
  return v_div / (float)PROBE_MULTIPLIER;
}

// Probe-output (DAC) volts -> capacitor volts: where the comparator actually trips.
float dacVoltsToCapVolts(float dacVolts) {
  return probeVoltsToCapVolts(dacVolts);
}

// ---------- Driver layer ----------

uint32_t ad5683_frame(uint8_t command, uint16_t data16) {
  return ((uint32_t)(command & 0x0F) << 20) | ((uint32_t)data16 << 4);
}

// One 24-bit SPI1 transaction. Returns the 24 bits clocked in on MISO1 (SDO).
uint32_t ad5683_transfer24(uint32_t word) {
  SPI1.beginTransaction(dacSPISettings);
  digitalWrite(PIN_DAC_CS, LOW);
  uint32_t rx = 0;
  rx |= (uint32_t)SPI1.transfer((word >> 16) & 0xFF) << 16;
  rx |= (uint32_t)SPI1.transfer((word >> 8)  & 0xFF) << 8;
  rx |= (uint32_t)SPI1.transfer( word        & 0xFF);
  digitalWrite(PIN_DAC_CS, HIGH);   // rising CS after 24 bits executes the command
  SPI1.endTransaction();
  delayNanoseconds(50);             // CS high >= 20 ns between frames
  return rx;
}

// Control word in the 16-bit data field: data bit n lands on frame bit n+4,
// so DB19 = bit 15, DB16 = bit 12, DB15 = bit 11.
uint16_t ad5683_controlBits(bool reset) {
  uint16_t c = 0;
  if (reset)             c |= (1 << 15);  // DB19 software reset (self-clearing)
  /* DB18:17 = 00  normal operation (not powered down) */
  if (!DAC_INTERNAL_REF) c |= (1 << 12);  // DB16 REF = 1: internal reference off
  if (DAC_GAIN_2X)       c |= (1 << 11);  // DB15 gain = 2x
  /* DB14 = 0  daisy-chain off */
  return c;
}

void ad5683_writeControl() {
  ad5683_transfer24(ad5683_frame(AD5683_CMD_WRITE_CTRL, ad5683_controlBits(false)));
}

// Reads the input register over SDO. Returns false if SDO isn't wired.
bool ad5683_readback(uint16_t &code) {
  if (!DAC_HAS_SDO) return false;
  ad5683_transfer24(ad5683_frame(AD5683_CMD_READBACK, 0));
  uint32_t rx = ad5683_transfer24(ad5683_frame(AD5683_CMD_NOP, 0));
  code = (uint16_t)((rx >> 4) & 0xFFFF);   // same alignment as writes: data in [19:4]
  return true;
}

// Writes a code to the output. Returns true if it was confirmed by readback, or if
// readback isn't available (then only the Teensy ADC check can confirm it).
bool ad5683_writeCode(uint16_t code) {
  ad5683_transfer24(ad5683_frame(AD5683_CMD_WRITE_UPDATE, code));
  uint16_t rb;
  if (!ad5683_readback(rb)) return true;
  dacReadbackOk = (rb == code);
  if (!dacReadbackOk) {
    logMsg(LVL_ERROR, "DAC readback 0x%04X, wrote 0x%04X", rb, code);
  }
  return dacReadbackOk;
}

// Hardware reset (if wired), software reset, control register, output to 0 V.
// With SDO, also proves the readback path is alive: code 1 (76 uV, harmless as a
// comparator threshold) must read back as 1. Reading back only 0 would pass even with
// MISO stuck low or disconnected.
bool ad5683_init() {
  pinMode(PIN_DAC_CS, OUTPUT);
  digitalWrite(PIN_DAC_CS, HIGH);
  if (PIN_DAC_RESET != 255) {
    pinMode(PIN_DAC_RESET, OUTPUT);
    digitalWrite(PIN_DAC_RESET, LOW);
    delayMicroseconds(1);            // >= 75 ns
    digitalWrite(PIN_DAC_RESET, HIGH);
    delay(1);
  }
  SPI1.begin();

  ad5683_transfer24(ad5683_frame(AD5683_CMD_WRITE_CTRL, ad5683_controlBits(true)));
  delay(1);
  ad5683_writeControl();

  if (!DAC_HAS_SDO) {
    ad5683_writeCode(0);
    lastDacCodeWritten = 0;
    logMsg(LVL_WARN, "AD5683: no SDO readback -- output verified by Teensy ADC (A4) only");
    return true;
  }

  bool liveOk = ad5683_writeCode(1);
  bool zeroOk = ad5683_writeCode(0);
  if (liveOk && zeroOk) lastDacCodeWritten = 0;
  logMsg((liveOk && zeroOk) ? LVL_INFO : LVL_ERROR, "AD5683 init: readback test %s",
         (liveOk && zeroOk) ? "[OK]" : "[FAILED] -- check SPI1 wiring (MOSI1 26, SCK1 27, MISO1 1, CS 0)");
  return liveOk && zeroOk;
}

// Rewrites the control register and the current code, e.g. after the chip was found
// holding the wrong value (a brownout resets it to 1x gain, code 0).
void ad5683_restore() {
  ad5683_writeControl();
  lastDacCodeWritten = -1;   // makes dac_service() rewrite the target code next pass
}

// ---------- Service ----------

// Writes the DAC only when the target code changes.
void dac_service() {
  static elapsedMillis sinceFail = DAC_RETRY_MS;

  uint16_t code = dacVoltsToCode(dacTargetVolts);
  if ((int32_t)code == lastDacCodeWritten) return;
  if (sinceFail < DAC_RETRY_MS) return;

  if (ad5683_writeCode(code)) {
    lastDacCodeWritten = code;
    dacChkInt.skip = true;       // the next average may include pre-change samples
    dacChkInt.valid = false;
  } else {
    sinceFail = 0;               // retried after DAC_RETRY_MS
  }
}

// Compares a measured DAC output voltage with what the last written code should give.
void dacCheck_update(DacCheck &c, float measuredV, float tolV) {
  if (lastDacCodeWritten < 0) { c.valid = false; return; }
  if (c.skip) { c.skip = false; c.valid = false; return; }
  float expected = dacCodeToVolts((uint16_t)lastDacCodeWritten);
  c.errV = measuredV - expected;
  c.ok = fabsf(c.errV) <= tolV;
  c.valid = true;
}

///////////////////////////////////////////////////////////////////////////////
// 7. RUNTIME CONFIG CHECKS
// config_service() checks ONE item every CFG_CHECK_MS, round-robin, so each check costs
// one short bus transaction and the fast loop is never held up. Full cycle = 4 x 20 ms.
// To check something new, add a case and bump CFG_CHECK_STEPS.
///////////////////////////////////////////////////////////////////////////////

// ADS8699 register mismatch: counted, charge pin forced off, register rewritten.
bool ads8699_checkRegister(uint8_t addr, uint16_t expected, const char *name) {
  uint32_t rb = ads8699_readRegister16(addr);
  if (ads8699_registerMatches(rb, expected)) return true;

  totalCfgFaults++;
  setChargePin(false);
  logMsg(LVL_ERROR, "CFG FAULT %s readback 0x%04X/0x%04X expected 0x%04X -- rewriting",
         name, (uint16_t)(rb >> 16), (uint16_t)(rb & 0xFFFF), expected);
  return ads8699_writeRegisterVerified(addr, expected, name);
}

// Teensy ADC registers changed since init: counted, both modules reconfigured.
void intadc_checkConfig() {
  bool ok = true;
  for (uint8_t i = 0; i < 2; i++) {
    ok &= intadc_registersOk(i) && (intadc_cfgReg(i) == intadcCfgSnapshot[i]);
  }
  if (ok) { intadcCfgOk = true; return; }

  totalCfgFaults++;
  logMsg(LVL_ERROR, "CFG FAULT Teensy ADC CFG 0x%08lX/0x%08lX expected 0x%08lX/0x%08lX -- reconfiguring",
         (unsigned long)ADC1_CFG, (unsigned long)ADC2_CFG,
         (unsigned long)intadcCfgSnapshot[0], (unsigned long)intadcCfgSnapshot[1]);
  intadc_configureModule(adc->adc0);
  intadc_configureModule(adc->adc1);
  intadcCfgOk = intadc_registersOk(0) && intadc_registersOk(1);
}

// With SDO: DAC holding a different code than we wrote is counted, then the control
// register + code are rewritten. Without SDO: blind rewrite every DAC_REFRESH_MS.
void ad5683_checkConfig() {
  if (lastDacCodeWritten < 0) return;   // a write is already pending
  if (!DAC_HAS_SDO) {
    static elapsedMillis sinceRefresh;
    if (DAC_REFRESH_MS == 0 || sinceRefresh < DAC_REFRESH_MS) return;
    sinceRefresh = 0;
    ad5683_writeControl();
    ad5683_writeCode((uint16_t)lastDacCodeWritten);   // same code: output doesn't move
    return;
  }
  uint16_t rb;
  ad5683_readback(rb);
  dacReadbackOk = (rb == (uint16_t)lastDacCodeWritten);
  if (dacReadbackOk) return;
  totalCfgFaults++;
  logMsg(LVL_ERROR, "CFG FAULT AD5683 holds 0x%04X, expected 0x%04X -- rewriting",
         rb, (uint16_t)lastDacCodeWritten);
  ad5683_restore();
}

const uint8_t CFG_CHECK_STEPS = 4;

void config_service() {
  static elapsedMillis since;
  static uint8_t step = 0;
  if (since < CFG_CHECK_MS) return;
  since = 0;

  switch (step) {
    case 0:
      ads8699RangeOk = ads8699_checkRegister(ADS8699_REG_RANGE_SEL, ads8699_rangeRegValue(),
                                             "RANGE_SEL_REG");
      break;
    case 1:
      ads8699ParityOk = ads8699_checkRegister(ADS8699_REG_DATAOUT_CTL, DATAOUT_CTL_PAR_EN,
                                              "DATAOUT_CTL_REG");
      break;
    case 2:
      intadc_checkConfig();
      break;
    case 3:
      ad5683_checkConfig();
      break;
  }
  step = (step + 1) % CFG_CHECK_STEPS;
}

// Teensy ADC slow averages, published every INT_SLOW_MS, + the analog DAC output check.
// The DAC check only counts while the Teensy ADC config is verified.
void intadc_slow_service() {
  static elapsedMillis since;
  if (intSample.fresh) {
    intSlowCapSum += intSample.capCounts;
    intSlowDacSum += intSample.dacCounts;
    intSlowN++;
  }
  if (since < INT_SLOW_MS) return;
  since = 0;
  if (intSlowN == 0) return;

  vCapIntSlow = intadc_capCountsToCapVolts((float)intSlowCapSum / intSlowN);
  vDacIntSlow = intadc_dacCountsToDacVolts((float)intSlowDacSum / intSlowN);
  intSlowCapSum = intSlowDacSum = 0;
  intSlowN = 0;
  if (!intadcCfgOk) {
    dacChkInt.valid = false;
    return;
  }
  dacCheck_update(dacChkInt, vDacIntSlow, DAC_VERIFY_TOL_INT_V);

  // Measured output disagrees with the commanded one: rewrite the DAC. Logged and counted
  // only when it starts failing, so a persistent mismatch (e.g. DACSENSE_R1/R2 not set to
  // the real divider yet) doesn't flood the log while it keeps retrying.
  static bool wasFailing = false;
  bool failing = dacChkInt.valid && !dacChkInt.ok;
  if (failing) {
    if (!wasFailing) {
      totalCfgFaults++;
      logMsg(LVL_ERROR, "DAC output %.4f V, expected %.4f V -- rewriting DAC",
             vDacIntSlow, vDacIntSlow - dacChkInt.errV);
    }
    ad5683_restore();
  }
  if (dacChkInt.valid) wasFailing = failing;
}

///////////////////////////////////////////////////////////////////////////////
// 8. CHARGE SETPOINT
///////////////////////////////////////////////////////////////////////////////

bool chargeEnabled() {
  return chargeSetpointV >= SETPOINT_MIN_ACTIVE_V;
}

// The only place the setpoint changes; keeps the DAC target (METHOD 1) and the direct
// control threshold (METHOD 2) in step. dac_service() picks up the new target.
void applySetpoint(float capVolts) {
  if (!(capVolts >= 0.0f)) capVolts = 0.0f;   // also catches NaN
  if (capVolts > SETPOINT_MAX_V) capVolts = SETPOINT_MAX_V;
  chargeSetpointV = capVolts;
  dacTargetVolts = chargeEnabled() ? capVoltToDacVolts(chargeSetpointV) : 0.0f;
}

///////////////////////////////////////////////////////////////////////////////
// 9. TELEMETRY
// sendXxx() fills one Protocol.h struct from the shared state and sends it.
// telemetry_service() runs once per loop pass and sends whatever tlmSchedule[] says is due.
// FAST/RAW describe one batch, so a rate below the batch rate decimates (sends the most
// recent batch), it does not average.
///////////////////////////////////////////////////////////////////////////////

TlmSchedule *findSchedule(uint8_t id) {
  for (uint8_t i = 0; i < TLM_SCHED_N; i++) {
    if (tlmSchedule[i].id == id) return &tlmSchedule[i];
  }
  return nullptr;
}

uint16_t scheduleRate(uint8_t id) {
  TlmSchedule *s = findSchedule(id);
  return s ? s->rateHz : RATE_OFF;
}

void sendFast() {
  TlmFast t;
  t.batch_seq = batchSeq;
  t.t_us = ads8699Last.tStartUs;
  t.v_cap = vCapFast;
  t.v_cap_int = vCapIntFast;
  t.flags = (chargePinOn ? FF_CHG_PIN : 0) | (chargeEnabled() ? FF_CHARGE_ENABLED : 0) |
            (ads8699CfgOk() ? FF_ADS8699_CFG_OK : 0) | (intSample.fresh ? FF_INT_FRESH : 0);
  sendPacket(TLM_FAST, &t, sizeof(t));
}

void sendRaw() {
  const Ads8699Batch &b = ads8699Last;
  TlmRaw t;
  t.batch_seq = batchSeq;
  t.t_us = b.tStartUs;
  t.sum = b.sum;
  t.n = b.n;
  t.min_code = b.minCode;
  t.max_code = b.maxCode;
  t.parity_errors = b.parityErrors;
  t.rvs_timeouts = b.rvsTimeouts;
  t.cap_counts = (uint16_t)intSample.capCounts;
  t.dac_counts = (uint16_t)intSample.dacCounts;
  t.int_fresh = intSample.fresh ? 1 : 0;
  sendPacket(TLM_RAW, &t, sizeof(t));
}

void sendStatus() {
  const Ads8699Batch &b = ads8699Last;
  TlmStatus t;
  t.t_ms = millis();
  t.v_cap_int_avg = vCapIntSlow;
  t.set_v = chargeSetpointV;
  t.dac_set_v = (lastDacCodeWritten >= 0) ? dacCodeToVolts((uint16_t)lastDacCodeWritten) : 0.0f;
  t.dac_int = vDacIntSlow;
  t.dac_err_int = dacChkInt.errV;
  t.dac_threshold_cap_v = dacVoltsToCapVolts(vDacIntSlow);
  t.loop_hz = loopHz;
  t.pp_cap_v = ads8699_codeToCapVolts(b.maxCode) - ads8699_codeToCapVolts(b.minCode);
  t.n_avg = b.n;
  t.pp_codes = b.maxCode - b.minCode;
  t.parity_errors = totalParityErrors;
  t.rvs_timeouts = totalRvsTimeouts;
  t.cfg_faults = totalCfgFaults;
  t.intadc_errors = totalIntAdcErrors;
  t.tx_dropped = txDropped;
  t.rx_errors = rxErrors;
  uint16_t f = 0;
  if (chargeEnabled())                       f |= SF_CHARGE_ENABLED;
  if (chargePinOn)                           f |= SF_CHG_PIN;
  if (ads8699CfgOk())                        f |= SF_ADS8699_CFG_OK;
  if (intadcCfgOk)                           f |= SF_INTADC_CFG_OK;
  if (dacChkInt.valid)                       f |= SF_DAC_INT_VALID;
  if (dacChkInt.ok)                          f |= SF_DAC_INT_OK;
  if (scheduleRate(TLM_RAW) != RATE_OFF)     f |= SF_RAW_ON;
  if (DAC_HAS_SDO)                           f |= SF_DAC_RB_AVAIL;
  if (dacReadbackOk)                         f |= SF_DAC_RB_OK;
  t.flags = f;
  sendPacket(TLM_STATUS, &t, sizeof(t));
}

// Input-level volts for calibration: no probe multiplier, Teensy dividers undone.
void sendCal() {
  const Ads8699Batch &b = ads8699Last;
  float adsRaw = ads8699_codeToAdcVolts(ads8699_batchMeanCode(b));
  float a0 = (float)intSample.capCounts;
  float a4 = (float)intSample.dacCounts;
  TlmCal t;
  t.batch_seq = batchSeq;
  t.ads_raw_v = adsRaw;
  t.ads_cal_v = ads8699_calibrate(adsRaw);
  t.a0_raw_v  = intadc_capRawVolts(a0);
  t.a0_cal_v  = intadc_capCalVolts(a0);
  t.a4_raw_v  = intadc_dacRawVolts(a4);
  t.a4_cal_v  = intadc_dacCountsToDacVolts(a4);
  t.ads_pp_v  = ads8699_codeToAdcVolts(b.maxCode) - ads8699_codeToAdcVolts(b.minCode);
  t.int_fresh = intSample.fresh ? 1 : 0;
  sendPacket(TLM_CAL, &t, sizeof(t));
}

void sendHello() {
  TlmHello t;
  t.proto_version = PROTO_VERSION;
  t.reserved = 0;
  t.setpoint_max = SETPOINT_MAX_V;
  t.n_avg = NUM_AVG;
  t.n_avg_max = NUM_AVG_MAX;
  t.rate_fast = scheduleRate(TLM_FAST);
  t.rate_raw = scheduleRate(TLM_RAW);
  t.rate_status = scheduleRate(TLM_STATUS);
  sendPacket(TLM_HELLO, &t, sizeof(t));
}

// Sends one telemetry packet by ID. Returns false for IDs that can't be requested.
// A new telemetry packet needs a case here.
bool sendTelem(uint8_t id) {
  switch (id) {
    case TLM_FAST:   sendFast();   return true;
    case TLM_RAW:    sendRaw();    return true;
    case TLM_STATUS: sendStatus(); return true;
    case TLM_HELLO:  sendHello();  return true;
    case TLM_CAL:    sendCal();    return true;
    default:         return false;
  }
}

// Fixed-rate scheduling without drift: lastUs advances by exactly one period each time.
// If the loop fell behind by more than a period, it resyncs instead of sending a burst.
bool tlmDue(TlmSchedule &s, uint32_t nowUs) {
  if (s.rateHz == RATE_OFF) return false;
  if (s.rateHz == RATE_EVERY_BATCH) return true;
  uint32_t period = 1000000UL / s.rateHz;
  if ((uint32_t)(nowUs - s.lastUs) < period) return false;
  s.lastUs += period;
  if ((uint32_t)(nowUs - s.lastUs) >= period) s.lastUs = nowUs;
  return true;
}

void telemetry_service() {
  uint32_t now = micros();
  for (uint8_t i = 0; i < TLM_SCHED_N; i++) {
    if (tlmDue(tlmSchedule[i], now)) sendTelem(tlmSchedule[i].id);
  }
}

///////////////////////////////////////////////////////////////////////////////
// 10. COMMANDS
// command_service() collects bytes until a 0x00 delimiter, handleFrame() checks COBS + CRC,
// dispatchCommand() runs the command. Every path ends in exactly one sendAck().
///////////////////////////////////////////////////////////////////////////////

// Helpers for dispatchCommand(): copy the payload into a struct, or reply BAD_LEN and
// return if the length is wrong. Commands must match their struct size exactly.
#define NEED_PAYLOAD(T, var)                         \
  T var;                                             \
  if (!takePayload(pkt, var)) {                      \
    sendAck(pkt.id, pkt.seq, ACK_BAD_LEN, 0.0f, 0);  \
    return;                                          \
  }
#define NEED_EMPTY()                                 \
  if (pkt.len != 0) {                                \
    sendAck(pkt.id, pkt.seq, ACK_BAD_LEN, 0.0f, 0);  \
    return;                                          \
  }

void dispatchCommand(const Packet &pkt) {
  switch (pkt.id) {
    case CMD_PING: {
      NEED_PAYLOAD(CmdPing, c);
      sendAck(pkt.id, pkt.seq, ACK_OK, 0.0f, c.token);
      return;
    }
    case CMD_SET_SETPOINT: {
      NEED_PAYLOAD(CmdSetSetpoint, c);
      if (!isfinite(c.volts) || c.volts < 0.0f || c.volts > SETPOINT_MAX_V) {
        sendAck(pkt.id, pkt.seq, ACK_OUT_OF_RANGE, chargeSetpointV, 0);
        return;
      }
      applySetpoint(c.volts);
      sendAck(pkt.id, pkt.seq, ACK_OK, chargeSetpointV, 0);
      return;
    }
    case CMD_CHARGE_OFF: {
      NEED_EMPTY();
      applySetpoint(0.0f);
      setChargePin(false);
      sendAck(pkt.id, pkt.seq, ACK_OK, chargeSetpointV, 0);
      return;
    }
    case CMD_GET_SETPOINT: {
      NEED_EMPTY();
      sendAck(pkt.id, pkt.seq, ACK_OK, chargeSetpointV, 0);
      return;
    }
    case CMD_SET_AVG: {
      NEED_PAYLOAD(CmdSetAvg, c);
      if (c.n < NUM_AVG_MIN || c.n > NUM_AVG_MAX) {
        sendAck(pkt.id, pkt.seq, ACK_OUT_OF_RANGE, NUM_AVG, 0);
        return;
      }
      NUM_AVG = c.n;
      sendAck(pkt.id, pkt.seq, ACK_OK, NUM_AVG, 0);
      return;
    }
    case CMD_SET_TELEM_RATE: {
      NEED_PAYLOAD(CmdSetTelemRate, c);
      TlmSchedule *s = findSchedule(c.tlm_id);
      if (s == nullptr) {
        sendAck(pkt.id, pkt.seq, ACK_BAD_ARG, 0.0f, c.tlm_id);
        return;
      }
      s->rateHz = c.rate_hz;
      s->lastUs = micros();
      sendAck(pkt.id, pkt.seq, ACK_OK, s->rateHz, c.tlm_id);
      return;
    }
    case CMD_REQUEST_TELEM: {
      NEED_PAYLOAD(CmdRequestTelem, c);
      bool known = (c.tlm_id == TLM_FAST || c.tlm_id == TLM_RAW ||
                    c.tlm_id == TLM_STATUS || c.tlm_id == TLM_HELLO ||
                    c.tlm_id == TLM_CAL);
      sendAck(pkt.id, pkt.seq, known ? ACK_OK : ACK_BAD_ARG, 0.0f, c.tlm_id);
      if (known) sendTelem(c.tlm_id);
      return;
    }
    // New commands go here: case CMD_XXX: { NEED_PAYLOAD(CmdXxx, c); ...; sendAck(...); return; }
    default:
      sendAck(pkt.id, pkt.seq, ACK_UNKNOWN_CMD, 0.0f, 0);
      return;
  }
}

void handleFrame(const uint8_t *frame, size_t len) {
  uint8_t scratch[MAX_PACKET];
  Packet pkt;
  ParseResult r = parseFrame(frame, len, scratch, pkt);
  if (r == PARSE_BAD_FRAME) {
    rxErrors++;
    sendAck(0, 0, ACK_BAD_FRAME, 0.0f, 0);
    return;
  }
  if (r == PARSE_BAD_CRC) {
    rxErrors++;
    sendAck(pkt.id, pkt.seq, ACK_BAD_CRC, 0.0f, 0);   // id/seq unverified
    return;
  }
  dispatchCommand(pkt);
}

void command_service() {
  static uint8_t buf[MAX_ENCODED];
  static size_t len = 0;
  static bool overflow = false;

  while (Serial.available() > 0) {
    uint8_t c = (uint8_t)Serial.read();
    if (c == 0x00) {                 // end of frame
      if (overflow) {
        rxErrors++;
        sendAck(0, 0, ACK_BAD_FRAME, 0.0f, 0);
      } else if (len > 0) {
        handleFrame(buf, len);
      }
      len = 0;
      overflow = false;
    } else if (len < sizeof(buf)) {
      buf[len++] = c;
    } else {
      overflow = true;               // keep discarding until the next delimiter
    }
  }
}

///////////////////////////////////////////////////////////////////////////////
// 11. SETUP / LOOP
///////////////////////////////////////////////////////////////////////////////

// Init failures that make the hardware threshold untrustworthy halt here with the charge
// pin LOW, repeating the reason once a second so a host that connects later sees it.
void haltWithError(const char *why) {
  setChargePin(false);
  while (1) { logMsg(LVL_ERROR, "%s -- halted", why); delay(1000); }
}

void setup() {
  // Drive the charge output LOW before it becomes an output, so it never glitches high.
  digitalWrite(PIN_CHRG_CTRL, LOW);
  pinMode(PIN_CHRG_CTRL, OUTPUT);
  setChargePin(false);

  Serial.begin(115200);   // USB: baud is ignored, runs at USB speed
  while (!Serial && millis() < 3000) { }

  pinMode(PIN_CS, OUTPUT);
  pinMode(PIN_RVS, INPUT_PULLDOWN);
  digitalWrite(PIN_CS, LOW);   // idle low so the first HIGH is a clean rising edge

  ads8699_init();               // reset + verified range and parity
  intadcCfgOk = intadc_init();  // verified hardware registers + test reads
  if (!ad5683_init()) haltWithError("AD5683 readback test failed");

  applySetpoint(0.0f);   // always boot with charging disabled (DAC at 0 V)
  logMsg(LVL_INFO, "Charge setpoint 0 V (charging disabled), max %.1f V", SETPOINT_MAX_V);
  sendHello();
}

void loop() {
  static elapsedMillis rateWindow;
  static uint32_t loopCount = 0;

  // ===== FAST PATH =====
  ads8699_readBatch(NUM_AVG, ads8699Last);   // integer batch, nothing discarded
  intadc_service();                          // A0/A4 pair converted during this batch
  batchSeq++;

  totalParityErrors += ads8699Last.parityErrors;
  totalRvsTimeouts  += ads8699Last.rvsTimeouts;

  vCapFast = ads8699_codeToCapVolts(ads8699_batchMeanCode(ads8699Last));
  if (intSample.fresh) vCapIntFast = intadc_capCountsToCapVolts((float)intSample.capCounts);

  // METHOD 2: direct control. Held off unless the ADS8699 config is confirmed.
  if (!CALIBRATION_MODE) {
    if (!chargeEnabled() || !ads8699CfgOk()) {
      setChargePin(false);
    } else {
      // TODO: maybe add some hysteresis
      setChargePin(vCapFast <= chargeSetpointV);
    }
  }

  telemetry_service();

  // ===== SLOW PATHS: non-blocking, each self-timed =====
  config_service();     // one register / config readback per CFG_CHECK_MS
  command_service();
  dac_service();
  intadc_slow_service();

  loopCount++;
  if (rateWindow >= 1000) {
    loopHz = loopCount * 1000.0f / (float)rateWindow;
    loopCount = 0;
    rateWindow = 0;
  }
}

/*
  ===========================================================================
  HOW-TO: ADD A COMMAND (example: CMD_SET_HYSTERESIS taking a float)
  ===========================================================================
    1. Protocol.h:   add   CMD_SET_HYSTERESIS = 0x08,   to enum CmdId
                     add   struct __attribute__((packed)) CmdSetHysteresis { float volts; };
    2. This file:    in dispatchCommand(), add
                       case CMD_SET_HYSTERESIS: {
                         NEED_PAYLOAD(CmdSetHysteresis, c);
                         if (!isfinite(c.volts) || c.volts < 0) { sendAck(pkt.id, pkt.seq, ACK_OUT_OF_RANGE, 0, 0); return; }
                         hysteresisV = c.volts;
                         sendAck(pkt.id, pkt.seq, ACK_OK, hysteresisV, 0);
                         return;
                       }
    3. opentms_protocol.py:  add  SET_HYSTERESIS = 0x08  to class Cmd
                             add  Cmd.SET_HYSTERESIS: "<f"  to CMD_FORMATS
       Then from Python:     link.send(Cmd.SET_HYSTERESIS, 2.0)

  ===========================================================================
  HOW-TO: ADD A TELEMETRY PACKET (example: TLM_TEMPS at 1 Hz)
  ===========================================================================
    1. Protocol.h:   add   TLM_TEMPS = 0x86,  to enum TlmId
                     add   the packed struct and a static_assert on its size
    2. This file:    write sendTemps() in section 9 (fill struct, sendPacket(TLM_TEMPS, ...))
                     add   case TLM_TEMPS: sendTemps(); return true;   to sendTelem()
                     add   { TLM_TEMPS, 1, 0 },   to tlmSchedule[] in section 1
                     add   TLM_TEMPS to the "known" list in CMD_REQUEST_TELEM
    3. opentms_protocol.py:  add  TEMPS = 0x86  to class Tlm, and a TLM_LAYOUTS entry
                             (struct format string + field names) and _EXPECTED_SIZES entry
       The plotter receives it in handle_packets(); add an elif branch to use it.

  Adding a FIELD to an existing telemetry struct: append it at the END of the struct,
  update the size static_assert, then append to the Python format string and name list.
  Older Python that doesn't know the field just ignores the extra bytes.
  Removing or reordering fields breaks older hosts: bump PROTO_VERSION in both places.
*/
