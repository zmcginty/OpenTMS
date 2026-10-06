/*
  OpenTMS Charge_Control, built to run on Teensy 4.0
    ADS8699 18-bit SAR fast ADC for reading capacitor voltage through Micsig Active H.V. Differential Probe
    AD5693 16-bit DAC outputs analog signal to comparator, which compares DAC output to voltage from Micsig H.V. Diff Probe (capacitor voltage)
    ADS1115 16-bit Sigma-Delta slow ADC for verifying DAC output, and as another redundant reading of Micsig H.V. Diff Probe (capacitor voltage)

  This control system can either
    1. Directly control connecting/disconnecting the capacitor's charge circuit by reading the capacitor's voltage, and turning off charge-circuit when it gets above a set threshold
    2. Or by outputting an analog signal via the AD5693 DAC which a comparator compares to the capacitor's voltage. I beleive the comparator is more robust and fail-safe.

  CHARGE SETPOINT (settable over Serial):
    One setpoint, in capacitor volts, drives both methods: the DAC/comparator
    threshold and the direct ADC control. It starts at 0 V (charging disabled)
    on every boot and can never exceed SETPOINT_MAX_V.
    Commands (one per line, case-insensitive):
      SET <volts>   set the charge setpoint, e.g. "SET 800"
      OFF           setpoint to 0 V (charging disabled)
      GET           report the current setpoint
      AVG <n>       ADS8699 averaging depth (NUM_AVG)
    Replies: "ACK SET 800.000", "ACK AVG 500", or "ERR <reason>".
    The status line also reports the active setpoint as "set_v:<volts>".

  LOOP STRUCTURE (cooperative multi-rate scheduling, no threads):
    - FAST path, every loop():  ADS8699 averaged read -> direct charge control.
    - SLOW paths, non-blocking, each on its own timer:
        dac_service()     -- writes the AD5693 only when the target code changes.
        ads1115_service() -- state machine: starts a conversion, comes back
                             later to collect it. Never waits on the ADC.
        command_service() -- reads serial commands, never blocks.
        print_service()   -- rate-limited Serial output.
    Each slow task does at most ONE short I2C transaction per loop() pass,
    so the fast path is never stalled for more than ~100-200 us by I2C.
*/

#include <SPI.h>
#include "Adafruit_AD569x.h"
#include <Wire.h>
#include <Adafruit_ADS1X15.h>

Adafruit_ADS1115 ads;
Adafruit_AD569x ad5693; // Create an object of the AD5693 library

const uint8_t DEBUG = 0;
const uint8_t CALIBRATION_MODE = 0;

// ---------- User configuration ----------
const uint8_t PIN_CS   = 10;   // CONVST/CS pin (any digital GPIO)
const uint8_t PIN_RST  = 9;    // RST pin (optional but recommended)
const uint8_t PIN_RVS  = 6;    // RVS (conversion-ready status) pin
const uint8_t PIN_CHRG_CTRL  = 5;    // charge control output pin

// SPI clock: datasheet allows up to 66.67 MHz for SPI
const uint32_t SPI_CLOCK_HZ =   40000000; // 40 MHz

SPISettings adsSPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0);

// Timing from the datasheet (Timing Requirements: Conversion Cycle,
// ADS8699 row): tconv = 5000 ns max. Used only as a safety-timeout
// ceiling for the RVS poll below now, not as the wait time itself.
const uint16_t T_CONV_US = 7;   // >= 5 us conversion time + margin
const uint16_t RVS_TIMEOUT_US = 50; // generous safety net; real tconv is ~5us

// ---------- Charge setpoint ----------
// Hard ceiling for SET commands. Set this to what your capacitor bank,
// IGBTs and probe (1300 V max) can safely take; anything above it is refused.
const float SETPOINT_MAX_V = 1000.0f;
// Setpoints below this count as "charging disabled": DAC at 0 V and the
// direct-control output held LOW.
const float SETPOINT_MIN_ACTIVE_V = 1.0f;
// The active setpoint in capacitor volts. Always starts at 0 V on boot.
// Change it with the SET command, not by editing this line.
float chargeSetpointV = 0.0f;

// If a value is below -10v, ignore it and wait for the next one...
const float MIN_VOLTAGE_THRESHOLD = -10;

// ---------- ADS8699 averaging configuration ----------
// NUM_AVG sets the fast control loop's latency: each batch takes roughly
// NUM_AVG x (one conversion + SPI read). Lower = faster cutoff response.
uint16_t NUM_AVG = 500;
const uint16_t NUM_AVG_MIN = 1;
const uint16_t NUM_AVG_MAX = 20000; // sanity ceiling for the 64-bit accumulator / loop time

// ---------- Slow-task rates ----------
// ADS1115: one "sweep" = one conversion on every channel in ADS1115_CHANNELS.
// Sweeps start at ADS1115_SWEEP_HZ; ADS1115_AVG sweeps are averaged before
// a new value is published (published rate = ADS1115_SWEEP_HZ / ADS1115_AVG).
uint16_t ADS1115_SWEEP_HZ = 100;
uint16_t ADS1115_AVG = 10;               // -> 10 Hz published, each a 10-sample average
// At 860 SPS a conversion takes ~1.16 ms; we don't touch the bus until this has passed.
const uint32_t ADS1115_CONV_WAIT_US = 1300;

uint16_t PRINT_HZ = 10;                  // Serial output rate
const uint32_t DAC_RETRY_MS = 100;       // retry interval if a DAC write fails

// ---------- ADS1115 channel map ----------
const uint8_t ADS1115_CHANNELS[] = {1, 3};
const uint8_t ADS1115_NUM_CH = sizeof(ADS1115_CHANNELS);
const uint8_t ADS1115_CH_DAC   = 1;
const uint8_t ADS1115_CH_PROBE = 3;

// ---------- DAC configuration ----------
float vref = 2.5000;
uint8_t gain = 2;   // gain of 2 means our span is 0-5V
// Target DAC output voltage (what the comparator sees). Set from the charge
// setpoint by applySetpoint(); dac_service() only writes to the chip when
// the resulting code changes.
float dacTargetVolts = 0.00f;
const float DAC_VERIFY_TOL_V = 0.010f;   // ADS1115 readback must be within this of the commanded voltage

// ADS8699 Calibration of ADC input voltage to calibrated multimeter
// Y = 0.9997*X + 9.895e-005 (cal performed 10/2/2026)
const float ADS8699_CAL_OFFSET = 0.00009895;
const float ADS8699_CAL_SCALE = 0.9997;

// ADS1115 Calibration of ADC input voltage to calibrated multimeter
//  Y = 1.000*X + 0.001057 (cal performed 10/2/2026)
const float ADS1115_CAL_OFFSET = 0.001057;
const float ADS1115_CAL_SCALE = 1.000;

// Probe Multiplier; used for measuring active HV differential probes.
// For measuring capacitor voltage, I'm using a 1300V diff probe with a multiplier of 500X
const uint16_t PROBE_MULTIPLIER = 500;

// Calibration for actual measured capacitor voltage
// Y = 0.9854*X + 0.1270
const float VDIV_CAL_OFFSET = 0.127;
const float VDIV_CAL_SCALE = 0.9854;

// ---------- Input range table (Table 7-3 / 7-4, VREF = 4.096 V) ----------
// (Kept above the first function: the Arduino IDE inserts auto-generated
// prototypes just before the first function, so custom types used in
// function signatures must be defined before it.)
enum ADS8699_Range : uint8_t {
  RANGE_PM_3VREF    = 0b0000, // ±12.288 V
  RANGE_PM_2p5VREF  = 0b0001, // ±10.24 V
  RANGE_PM_1p5VREF  = 0b0010, // ±6.144 V
  RANGE_PM_1p25VREF = 0b0011, // ±5.12 V
  RANGE_PM_0p625VREF= 0b0100, // ±2.56 V
  RANGE_0_3VREF     = 0b1000, // 0 to 12.288 V
  RANGE_0_2p5VREF   = 0b1001, // 0 to 10.24 V
  RANGE_0_1p5VREF   = 0b1010, // 0 to 6.144 V
  RANGE_0_1p25VREF  = 0b1011  // 0 to 5.12 V
};

// Positive/negative full-scale voltages for each range, VREF = 4.096 V
struct RangeInfo { float negFS; float posFS; };

///////////////////////////////////////////////////////////////////////////////
// SHARED STATE FOR SLOW TASKS
// (globals declared before any function that uses them)

// Latest published ADS1115 readings, calibrated, indexed by AIN number.
float ads1115_volts[4] = {0.0f, 0.0f, 0.0f, 0.0f};
bool ads1115_haveData = false;   // true once the first averaged set has been published
bool ads1115_newData = false;    // set on each publish, cleared by the consumer in loop()

int32_t lastDacCodeWritten = -1; // -1 = nothing written yet
bool dacSkipNextVerify = true;   // skip the averaging window that straddles a DAC change
bool dacVerifyOk = false;
float dacVerifyErrorV = 0.0f;

bool chargePinOn = false;        // last level written to PIN_CHRG_CTRL (for the status line)

///////////////////////////////////////////////////////////////////////////////
// AD5693 DAC FUNCTIONS
uint16_t voltageToDacCode(float voltage, float vref, uint8_t gain) {
  float maxCode = 65536.0;
  float code = (voltage / (vref * gain)) * maxCode;
  if (code > 65535.0) code = 65535.0;
  if (code < 0.0) code = 0.0;
  return (uint16_t)code;
}

// Inverse of voltageToDacCode(): the voltage the DAC should output for a given code.
float codeToVoltage(uint16_t code, float vref, uint8_t gain) {
  return vref * gain * ((float)code / 65536.0);
}

// Converts a desired capacitor voltage into the probe-output voltage the
// comparator should trip at (i.e. the DAC target), by inverting the probe
// multiplier and the probe calibration used for v_cap in loop().
// The DAC's own accuracy isn't calibrated here -- that's what the ADS1115
// verification is for.
float capVoltToDacVolts(float capVolts) {
  float v_div = (capVolts - VDIV_CAL_OFFSET) / VDIV_CAL_SCALE;
  return v_div / (float)PROBE_MULTIPLIER;
}

// Writes the DAC only when the target code differs from what was last
// successfully written. On failure, retries no more often than DAC_RETRY_MS
// so a disconnected DAC can't flood the bus or Serial.
void dac_service() {
  static elapsedMillis sinceFail = DAC_RETRY_MS;

  uint16_t code = voltageToDacCode(dacTargetVolts, vref, gain);
  if ((int32_t)code == lastDacCodeWritten) return;
  if (sinceFail < DAC_RETRY_MS) return;

  if (ad5693.writeUpdateDAC(code)) {
    lastDacCodeWritten = code;
    dacSkipNextVerify = true;   // the next published ADS1115 average may include pre-change samples
  } else {
    sinceFail = 0;
    Serial.println("Failed to update DAC.");
  }
}

// Compares the ADS1115's DAC-channel reading with the commanded DAC
// voltage. Called whenever a new averaged ADS1115 set is published.
void dac_verify() {
  if (lastDacCodeWritten < 0) { dacVerifyOk = false; return; }
  if (dacSkipNextVerify) { dacSkipNextVerify = false; return; }

  float expected = codeToVoltage((uint16_t)lastDacCodeWritten, vref, gain);
  dacVerifyErrorV = ads1115_volts[ADS1115_CH_DAC] - expected;
  dacVerifyOk = fabsf(dacVerifyErrorV) <= DAC_VERIFY_TOL_V;
}

///////////////////////////////////////////////////////////////////////////////
// CHARGE SETPOINT

bool chargeEnabled() {
  return chargeSetpointV >= SETPOINT_MIN_ACTIVE_V;
}

// The only place the setpoint changes. Updates the DAC target too, so the
// comparator and the direct control always use the same value.
void applySetpoint(float capVolts) {
  if (!(capVolts >= 0.0f)) capVolts = 0.0f;            // also catches NaN
  if (capVolts > SETPOINT_MAX_V) capVolts = SETPOINT_MAX_V;
  chargeSetpointV = capVolts;
  dacTargetVolts = chargeEnabled() ? capVoltToDacVolts(chargeSetpointV) : 0.0f;
}

///////////////////////////////////////////////////////////////////////////////
// SERIAL COMMANDS (non-blocking)

void handleCommand(char *line) {
  char *cmd = strtok(line, " \t");
  if (cmd == nullptr) return;
  for (char *p = cmd; *p; ++p) *p = (char)toupper((unsigned char)*p);
  char *arg = strtok(nullptr, " \t");

  if (strcmp(cmd, "SET") == 0) {
    if (arg == nullptr) { Serial.println("ERR SET needs a value, e.g. SET 800"); return; }
    char *end = nullptr;
    float v = strtof(arg, &end);
    if (end == arg || *end != '\0' || !isfinite(v)) {
      Serial.printf("ERR SET '%s' is not a number\r\n", arg);
      return;
    }
    if (v < 0.0f || v > SETPOINT_MAX_V) {
      Serial.printf("ERR SET %.1f out of range 0-%.1f\r\n", v, SETPOINT_MAX_V);
      return;
    }
    applySetpoint(v);
    Serial.printf("ACK SET %.3f\r\n", chargeSetpointV);
  } else if (strcmp(cmd, "OFF") == 0) {
    applySetpoint(0.0f);
    Serial.printf("ACK SET %.3f\r\n", chargeSetpointV);
  } else if (strcmp(cmd, "GET") == 0) {
    Serial.printf("ACK SET %.3f\r\n", chargeSetpointV);
  } else if (strcmp(cmd, "AVG") == 0) {
    long n = (arg != nullptr) ? strtol(arg, nullptr, 10) : 0;
    if (n < NUM_AVG_MIN || n > NUM_AVG_MAX) {
      Serial.printf("ERR AVG must be %u-%u\r\n", NUM_AVG_MIN, NUM_AVG_MAX);
      return;
    }
    NUM_AVG = (uint16_t)n;
    Serial.printf("ACK AVG %u\r\n", NUM_AVG);
  } else {
    Serial.printf("ERR unknown command '%s' (use SET <V>, OFF, GET, AVG <n>)\r\n", cmd);
  }
}

// Collects characters into a line; runs the command on newline.
void command_service() {
  static char buf[48];
  static uint8_t len = 0;
  static bool overflow = false;

  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (overflow) {
        Serial.println("ERR command too long");
      } else if (len > 0) {
        buf[len] = '\0';
        handleCommand(buf);
      }
      len = 0;
      overflow = false;
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    } else {
      overflow = true;
    }
  }
}

///////////////////////////////////////////////////////////////////////////////
// ADS1115 SLOW ADC FUNCTIONS (non-blocking)

uint16_t ads1115_muxForChannel(uint8_t ch) {
  switch (ch) {
    case 0:  return ADS1X15_REG_CONFIG_MUX_SINGLE_0;
    case 1:  return ADS1X15_REG_CONFIG_MUX_SINGLE_1;
    case 2:  return ADS1X15_REG_CONFIG_MUX_SINGLE_2;
    default: return ADS1X15_REG_CONFIG_MUX_SINGLE_3;
  }
}

// Two-state machine, called every loop():
//   IDLE:       when the sweep timer expires, start a single-shot conversion
//               on the first channel and return immediately.
//   CONVERTING: once ADS1115_CONV_WAIT_US has passed, collect the result,
//               start the next channel, return. After the last channel the
//               sweep is done; every ADS1115_AVG sweeps an average is published.
// Each call does at most a couple of short I2C transactions, never a wait.
void ads1115_service() {
  static bool converting = false;
  static uint8_t chIdx = 0;
  static elapsedMicros sweepTimer;
  static elapsedMicros convTimer;
  static float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  static uint16_t sweepsDone = 0;

  if (!converting) {
    uint32_t sweepInterval = 1000000UL / (ADS1115_SWEEP_HZ ? ADS1115_SWEEP_HZ : 1);
    if (sweepTimer < sweepInterval) return;
    sweepTimer = 0;
    chIdx = 0;
    ads.startADCReading(ads1115_muxForChannel(ADS1115_CHANNELS[0]), false);
    convTimer = 0;
    converting = true;
    return;
  }

  if (convTimer < ADS1115_CONV_WAIT_US) return;
  if (!ads.conversionComplete()) return;   // rare; just check again next pass

  int16_t raw = ads.getLastConversionResults();
  acc[ADS1115_CHANNELS[chIdx]] += ads.computeVolts(raw);
  chIdx++;

  if (chIdx < ADS1115_NUM_CH) {
    ads.startADCReading(ads1115_muxForChannel(ADS1115_CHANNELS[chIdx]), false);
    convTimer = 0;
    return;
  }

  // Sweep finished.
  converting = false;
  sweepsDone++;
  if (sweepsDone >= ADS1115_AVG) {
    for (uint8_t i = 0; i < ADS1115_NUM_CH; i++) {
      uint8_t ch = ADS1115_CHANNELS[i];
      float avg = acc[ch] / (float)sweepsDone;
      ads1115_volts[ch] = (avg * ADS1115_CAL_SCALE) + ADS1115_CAL_OFFSET;
      acc[ch] = 0.0f;
    }
    sweepsDone = 0;
    ads1115_haveData = true;
    ads1115_newData = true;
  }
}

////////////////////////////////////////////////////////////////////////////////
// ADS8699 ADC FUNCTIONS

// Positive/negative full-scale voltages for each range, VREF = 4.096 V
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

// Track the currently configured range so voltage conversion is correct.
// Defaults to the chip's power-on default range.
ADS8699_Range currentRange = RANGE_PM_3VREF;
// True only once RANGE_SEL_REG has actually been confirmed (via readback)
// to match currentRange.
bool rangeConfirmed = false;

// ---------- Low level 32-bit SPI transfer ----------
// Sends 32 bits MSB-first, returns the 32 bits clocked back in.
// Must be called with CS already LOW (data frame open).
uint32_t spiTransfer32(uint32_t txWord) {
  uint32_t rx = 0;
  rx |= (uint32_t)SPI.transfer((txWord >> 24) & 0xFF) << 24;
  rx |= (uint32_t)SPI.transfer((txWord >> 16) & 0xFF) << 16;
  rx |= (uint32_t)SPI.transfer((txWord >> 8)  & 0xFF) << 8;
  rx |= (uint32_t)SPI.transfer( txWord        & 0xFF);
  return rx;
}

// ---------- Register write (WRITE command, whole 16-bit word) ----------
// NOTE: because CONVST and CS share one pin, opening/closing the frame
// to send this command ALSO triggers a conversion. We read and discard
// that conversion afterward so the pin/state machine stays in sync.
void ads8699_writeRegister16(uint8_t addr, uint16_t data16) {
  uint32_t cmd = 0;
  cmd |= (0b11010UL << 27);              // WRITE opcode (whole 16-bit word)
  cmd |= (0b00UL << 25);                 // sub-type: write full word
  cmd |= ((uint32_t)(addr & 0xFE) << 16); // 9-bit address, LSB forced 0
  cmd |= data16;

  digitalWrite(PIN_CS, LOW);             // open data frame
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(cmd);
  SPI.endTransaction();
  digitalWrite(PIN_CS, HIGH);            // closes frame -> command executes
                                          // (this edge also starts a conversion)
  delayMicroseconds(T_CONV_US);

  // Read & discard the conversion that the above edge triggered, and
  // leave CS low so the device resumes normal acquisition.
  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(0x00000000); // NOP
  SPI.endTransaction();
}

// ---------- Register read (READ command, whole 16-bit word) ----------
// Returns the raw 32-bit NOP-frame word; callers check both halves
// against the value they expect.
uint32_t ads8699_readRegister16(uint8_t addr) {
  uint32_t cmd = 0;
  cmd |= (0b11001UL << 27);              // READ opcode (whole 16-bit word)
  cmd |= (0b00UL << 25);                 // sub-type: read full word
  cmd |= ((uint32_t)(addr & 0xFE) << 16); // 9-bit address, LSB forced 0

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  spiTransfer32(cmd);                    // frame F: send the read command
  SPI.endTransaction();
  digitalWrite(PIN_CS, HIGH);            // closes frame -> command executes
  delayMicroseconds(T_CONV_US);

  digitalWrite(PIN_CS, LOW);
  SPI.beginTransaction(adsSPISettings);
  uint32_t raw = spiTransfer32(0x00000000); // frame F+1: NOP, shifts register data out
  SPI.endTransaction();
  // CS stays low; chip resumes acquiring as usual.

  return raw;
}

// Convenience: set input range and reference source
// (RANGE_SEL_REG, address 0x14. Bit 6 = INTREF_DIS, bits 3:0 = RANGE_SEL)
void ads8699_setRange(ADS8699_Range range, bool useExternalReference = false) {
  uint16_t data = (uint16_t)range;
  if (useExternalReference) data |= (1 << 6); // INTREF_DIS = 1
  ads8699_writeRegister16(0x14, data);
  currentRange = range;
}

// Generic retry-and-verify write: writes data16 to addr, reads it back,
// and retries (up to maxAttempts) if neither half of the readback
// matches what was written.
bool ads8699_writeRegisterVerified(uint8_t addr, uint16_t data16, const char *regName,
                                    uint8_t maxAttempts = 5) {
  for (uint8_t attempt = 1; attempt <= maxAttempts; attempt++) {
    ads8699_writeRegister16(addr, data16);
    uint32_t rb = ads8699_readRegister16(addr);
    uint16_t rbUpper = (uint16_t)(rb >> 16);
    uint16_t rbLower = (uint16_t)(rb & 0xFFFF);

    bool matched = (rbUpper == data16) || (rbLower == data16);
    Serial.print(regName);
    Serial.print(" write attempt ");
    Serial.print(attempt);
    Serial.print(": expected=0x");
    Serial.print(data16, HEX);
    Serial.print(" readback upper=0x");
    Serial.print(rbUpper, HEX);
    Serial.print(" lower=0x");
    Serial.print(rbLower, HEX);
    Serial.println(matched ? "  [OK]" : "  [MISMATCH -- retrying]");

    if (matched) return true;
    delay(5);
  }

  Serial.print("WARNING: ");
  Serial.print(regName);
  Serial.println(" could not be verified after retries.");
  return false;
}

// Same as ads8699_setRange(), but reads RANGE_SEL_REG back afterward and
// retries if it doesn't match. currentRange (and rangeConfirmed) are only
// updated on a CONFIRMED success.
bool ads8699_setRangeVerified(ADS8699_Range range, bool useExternalReference = false,
                               uint8_t maxAttempts = 5) {
  uint16_t expected = (uint16_t)range;
  if (useExternalReference) expected |= (1 << 6);
  bool ok = ads8699_writeRegisterVerified(0x14, expected, "RANGE_SEL_REG", maxAttempts);
  if (ok) {
    currentRange = range; // only trust this once the readback actually confirmed it
  }
  rangeConfirmed = ok;
  return ok;
}

// ---------- Output parity (DATAOUT_CTL_REG, address 0x10) ----------
// Bit 3 (PAR_EN) appends two even-parity bits right after the 18-bit
// conversion result: bit 13 = parity of the ADC output bits, bit 12 =
// parity of the whole output frame.
const uint8_t DATAOUT_CTL_REG_ADDR = 0x10;
const uint16_t DATAOUT_CTL_PAR_EN = (1 << 3);
bool parityEnabled = false; // set by ads8699_enableParity() in setup()

bool ads8699_enableParity() {
  parityEnabled = ads8699_writeRegisterVerified(DATAOUT_CTL_REG_ADDR, DATAOUT_CTL_PAR_EN,
                                                 "DATAOUT_CTL_REG");
  return parityEnabled;
}

// Even parity (1 if the number of set bits is odd, else 0) via XOR-fold.
uint8_t ads8699_evenParityBit(uint32_t v) {
  v ^= v >> 16;
  v ^= v >> 8;
  v ^= v >> 4;
  v ^= v >> 2;
  v ^= v >> 1;
  return (uint8_t)(v & 0x1);
}

// Checks both parity bits against the expected even parity of the 18-bit code.
bool ads8699_checkParity(uint32_t rawWord, uint32_t code) {
  uint8_t adcParityBit = (uint8_t)((rawWord >> 13) & 0x1);
  uint8_t frameParityBit = (uint8_t)((rawWord >> 12) & 0x1);
  uint8_t expected = ads8699_evenParityBit(code & 0x3FFFFUL);
  return (adcParityBit == expected) && (frameParityBit == expected);
}

// ---------- Reading-status state ----------
float lastGoodVoltage = 0.0f;
bool dataStale = false; // true when the last returned reading isn't trusted (see ads8699_readVoltageAveraged)

// ---------- Stuck-SPI-bus detection & recovery ----------
uint32_t lastRawWord = 0xFFFFFFFFUL;
uint16_t identicalRawCount = 0;
const uint16_t STUCK_BUS_THRESHOLD = 15; // consecutive identical raw words -> assume wedged

const uint8_t MAX_REINIT_ROUNDS = 10;

void ads8699_fullReinit() {
  for (uint8_t round = 1; round <= MAX_REINIT_ROUNDS; round++) {
    pinMode(PIN_RST, OUTPUT);
    digitalWrite(PIN_RST, LOW);
    delayMicroseconds(1);      // datasheet requires RST low >= 100 ns
    digitalWrite(PIN_RST, HIGH);
    delay(25);                 // tD_RST_POR = 20 ms max, power-on reset settle time

    SPI.begin();

    bool rangeOk = ads8699_setRangeVerified(RANGE_PM_1p25VREF); // switch to ±5.12 V range
    bool parityOk = ads8699_enableParity();

    if (rangeOk && parityOk) {
      if (DEBUG && round > 1) {
        Serial.print("ads8699_fullReinit confirmed on round ");
        Serial.println(round);
      }
      return;
    }

    Serial.print("ads8699_fullReinit round ");
    Serial.print(round);
    Serial.println(" incomplete (range or parity not confirmed) -- retrying");
    delay(50); // brief pause, in case an active noise burst needs a moment to pass
  }

  Serial.println("WARNING: ads8699_fullReinit could not confirm range/parity after "
                  "repeated attempts. rangeConfirmed/parityEnabled remain false -- "
                  "readings will stay marked STALE until this succeeds.");
}

// Called from ads8699_readRaw() when the stuck-bus detector trips.
void ads8699_recoverStuckBus() {
  if (DEBUG) {
    Serial.println("STUCK SPI BUS DETECTED -- reinitializing SPI peripheral and ADC");
  }

  SPI.end();
  delay(2);
  ads8699_fullReinit();

  identicalRawCount = 0;
  lastRawWord = 0xFFFFFFFFUL;
  dataStale = true;
}

// ---------- Raw conversion read ----------
// Assumes PIN_CS is currently LOW (device acquiring). Triggers a
// conversion, waits for RVS to signal the result is ready, reads it out,
// and leaves PIN_CS LOW. Also runs the stuck-bus detector.
uint32_t ads8699_readRaw() {
  digitalWrite(PIN_CS, HIGH);        // rising edge -> start conversion

  uint32_t t0 = micros();
  while (digitalRead(PIN_RVS) == LOW) {
    if ((uint32_t)(micros() - t0) > RVS_TIMEOUT_US) break;
  }

  digitalWrite(PIN_CS, LOW);         // falling edge -> open data frame
  SPI.beginTransaction(adsSPISettings);
  uint32_t data = spiTransfer32(0x00000000); // send NOP, read result
  SPI.endTransaction();
  // CS stays low; chip is acquiring the next sample now.

  if (data == lastRawWord) {
    identicalRawCount++;
    if (identicalRawCount >= STUCK_BUS_THRESHOLD) {
      ads8699_recoverStuckBus();
      // One immediate retry post-recovery so this call returns a fresh sample.
      digitalWrite(PIN_CS, HIGH);
      t0 = micros();
      while (digitalRead(PIN_RVS) == LOW) {
        if ((uint32_t)(micros() - t0) > RVS_TIMEOUT_US) break;
      }
      digitalWrite(PIN_CS, LOW);
      SPI.beginTransaction(adsSPISettings);
      data = spiTransfer32(0x00000000);
      SPI.endTransaction();
      identicalRawCount = 0;
    }
  } else {
    identicalRawCount = 0;
  }
  lastRawWord = data;

  return data;
}

// Extracts the 18-bit code from the 32-bit output word.
uint32_t ads8699_extractCode(uint32_t rawWord) {
  return (rawWord >> 14) & 0x3FFFFUL;
}

// Converts an 18-bit code (integer or averaged/fractional) to a voltage,
// given the currently configured input range.
float ads8699_codeToVoltage(double code) {
  RangeInfo ri = getRangeInfo(currentRange);
  float fsr = ri.posFS - ri.negFS;
  float lsb = fsr / 262144.0f; // 2^18
  return ri.negFS + (float)code * lsb;
}

// Single-shot: trigger, read, convert to volts. No averaging.
float ads8699_readVoltage() {
  uint32_t raw  = ads8699_readRaw();
  uint32_t code = ads8699_extractCode(raw);
  return ads8699_codeToVoltage(code);
}

// Reads one raw sample, extracts its code, and checks parity (if enabled).
uint32_t ads8699_readCodeChecked(bool *parityOk) {
  uint32_t raw = ads8699_readRaw();
  uint32_t code = ads8699_extractCode(raw);
  if (parityOk != nullptr) {
    *parityOk = parityEnabled ? ads8699_checkParity(raw, code) : true;
  }
  return code;
}

// ---------- Averaged conversion read ----------
// Takes numSamples raw conversions, drops any that fail parity, averages
// the rest, and converts the mean code to volts once. dataStale is set if
// the range/parity config was never confirmed, or if every sample failed
// parity (in which case the last good voltage is returned).
float ads8699_readVoltageAveraged(uint16_t numSamples) {
  if (numSamples < NUM_AVG_MIN) numSamples = NUM_AVG_MIN;
  if (numSamples > NUM_AVG_MAX) numSamples = NUM_AVG_MAX;

  uint64_t sum = 0;
  uint16_t acceptedCount = 0;
  uint16_t parityRejected = 0;

  for (uint16_t i = 0; i < numSamples; i++) {
    bool parityOk;
    uint32_t code = ads8699_readCodeChecked(&parityOk);
    if (!parityOk) {
      parityRejected++;
      continue;
    }
    sum += code;
    acceptedCount++;
  }

  if (acceptedCount == 0) {
    dataStale = true;
    if (DEBUG) {
      Serial.print("ALL SAMPLES FAILED PARITY (");
      Serial.print(parityRejected);
      Serial.print(") -- returning last good voltage ");
      Serial.println(lastGoodVoltage, 4);
    }
    return lastGoodVoltage;
  }

  dataStale = !rangeConfirmed || !parityEnabled;

  double avgCode = (double)sum / (double)acceptedCount;
  if (DEBUG) {
    Serial.print("RAW_ADC = ");
    Serial.print(avgCode, 4);
    Serial.print("  accepted=");
    Serial.print(acceptedCount);
    Serial.print("  parity_rejected=");
    Serial.println(parityRejected);
  }
  float voltage = ads8699_codeToVoltage(avgCode);
  lastGoodVoltage = voltage;
  return voltage;
}

///////////////////////////////////////////////////////////////////////////////
// RATE-LIMITED SERIAL OUTPUT
// Fast line every loop pass:  "v_cap:123.456"
// Status line at PRINT_HZ:    "v_cap_slow:... dac_meas:... set_v:... loop_hz:..."
const bool PRINT_FAST_VCAP = true;

void print_service(float v_cap_fast) {
  static elapsedMillis sincePrint;
  static elapsedMillis rateWindow;
  static uint32_t loopCount = 0;
  static float loopHz = 0.0f;

  loopCount++;
  if (rateWindow >= 1000) {
    loopHz = loopCount * 1000.0f / (float)rateWindow;
    loopCount = 0;
    rateWindow = 0;
  }

  // Fast line. Skipped if the USB TX buffer can't take it right now, so
  // printing can never stall the control loop (e.g. nothing reading the port).
  if (PRINT_FAST_VCAP && Serial.availableForWrite() >= 24) {
    Serial.print("v_cap:");
    Serial.println(v_cap_fast, 3);
  }

  uint32_t printInterval = 1000UL / (PRINT_HZ ? PRINT_HZ : 1);
  if (sincePrint < printInterval) return;
  sincePrint = 0;

  float v_cap_slow = (ads1115_volts[ADS1115_CH_PROBE] * PROBE_MULTIPLIER * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;
  float dac_meas_hv = (ads1115_volts[ADS1115_CH_DAC] * PROBE_MULTIPLIER * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;

  Serial.print("v_cap_slow:");    Serial.print(v_cap_slow, 3);
  Serial.print(" set_v:");        Serial.print(chargeSetpointV, 1);
  Serial.print(" dac_meas:");     Serial.print(ads1115_volts[ADS1115_CH_DAC], 4);
  Serial.print(" read_dac_set_volt:");  Serial.print(dac_meas_hv, 3);
  Serial.print(" chg:");          Serial.print(chargePinOn ? 1 : 0);
  Serial.print(" loop_hz:");      Serial.print(loopHz, 1);
  if (!chargeEnabled()) Serial.print(" CHARGE_OFF");
  if (dataStale) Serial.print(" STALE");
  if (ads1115_haveData && !dacVerifyOk && lastDacCodeWritten >= 0 && !dacSkipNextVerify) {
    Serial.print(" DAC_MISMATCH(");
    Serial.print(dacVerifyErrorV, 4);
    Serial.print("V)");
  }
  Serial.println();
}

void setChargePin(bool on) {
  digitalWrite(PIN_CHRG_CTRL, on ? HIGH : LOW);
  chargePinOn = on;
}

// ---------- Setup / loop ----------
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) { /* wait for USB serial, Teensy-friendly */ }

  pinMode(PIN_CS, OUTPUT);
  pinMode(PIN_RVS, INPUT_PULLDOWN);
  pinMode(PIN_CHRG_CTRL, OUTPUT);

  digitalWrite(PIN_CS, LOW); // idle low so the first HIGH is a clean rising edge
  setChargePin(false);       // charge circuit disconnected until a setpoint is set

  // Pulses RST, brings up SPI, and applies + verifies range and parity.
  ads8699_fullReinit();

  // AD5693 DAC and ADS1115 ADC, both on Wire
  if (ad5693.begin(0x4C, &Wire)) { // If A0 jumper is set high, use 0x4E
    Serial.println("AD5693 initialization successful!");
  } else {
    Serial.println("Failed to initialize AD5693. Please check your connections.");
    while (1) delay(10); // Halt
  }
  if (!ads.begin()) {
    Serial.println("Failed to initialize ADS1115.");
    while (1);
  }
  // Fastest data rate: ~1.16 ms per conversion. Noise is a bit higher than
  // at the 128 SPS default; the ADS1115_AVG averaging makes up for it.
  ads.setDataRate(RATE_ADS1115_860SPS);

  ad5693.reset();

  // Normal mode, internal reference, 2x gain (0-5 V span)
  if (ad5693.setMode(NORMAL_MODE, true, true)) {
    Serial.println("AD5693 configured");
  } else {
    Serial.println("Failed to configure AD5693.");
    while (1) delay(10); // Halt
  }

  Wire.setClock(400000);    // 400 kHz

  applySetpoint(0.0f);      // Always boot with charging disabled (DAC at 0 V).
  Serial.printf("Charge setpoint 0 V (charging disabled), max %.1f V. "
                "Commands: SET <V>, OFF, GET, AVG <n>\r\n", SETPOINT_MAX_V);
}


// Two ways of controlling the capacitor charge circuit, both driven by the
// same setpoint (chargeSetpointV):
// METHOD 1: DAC -> external comparator vs. the probe output (hardware decides).
//           The ADS1115 verifies the DAC output.
// METHOD 2: Fast ADC reads the cap voltage and the Teensy drives PIN_CHRG_CTRL.

void loop() {
  // ===== FAST PATH: runs every pass =====
  float v_adc = ads8699_readVoltageAveraged(NUM_AVG);                // Read ADC, average of NUM_AVG samples
  float v_cal = (v_adc * ADS8699_CAL_SCALE) + ADS8699_CAL_OFFSET;    // Calibration of the ADC input itself
  float v_div = v_cal * PROBE_MULTIPLIER;                            // Apply probe multiplier
  float v_cap = (v_div * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;          // Probe calibration

  if (!CALIBRATION_MODE) {
    if (!chargeEnabled()) {
      setChargePin(false);                // setpoint 0: never charge
    } else if (v_cap > MIN_VOLTAGE_THRESHOLD) {   // If we suddenly jump to negative voltage, ignore it and wait for the next sample...
      if (dataStale) {
        // Reading isn't trusted (config unconfirmed, or a whole batch
        // failed parity) -- fail safe rather than act on it.
        setChargePin(false);
      }
      // TODO: maybe add some hysteresis???
      else if (v_cap <= chargeSetpointV) {  // below setpoint
        setChargePin(true);    // connect charge circuit, charging capacitor
      }
      else {  // above setpoint
        setChargePin(false);   // disconnect charge circuit.
      }
    }
  }

  // ===== SLOW PATHS: non-blocking, each self-timed =====
  command_service();   // SET/OFF/GET/AVG over Serial -> applySetpoint()
  dac_service();       // writes the DAC only if dacTargetVolts' code changed
  ads1115_service();   // advances the ADS1115 state machine by at most one step
  if (ads1115_newData) {
    ads1115_newData = false;
    dac_verify();
  }

  print_service(v_cap);
}


///////////////////////////////////////////////////////
// NOTES
/*
  IMPORTANT ABOUT THE CONVST/CS PIN:
  This chip has ONE physical pin that does two jobs:
    - While HIGH it behaves as CONVST: a LOW->HIGH transition on it
      starts a conversion.
    - While LOW it behaves as CS: the chip is "selected" and will
      shift data in/out over SPI.
  So a normal read cycle looks like:
      CS/CONVST pin LOW  -> HIGH   (starts conversion)
      wait for conversion to finish (see RVS note below)
      CS/CONVST pin HIGH -> LOW    (opens the data frame, chip drives SDO)
      clock out 32 bits over SPI to read the last conversion result
      (leave the pin LOW; the chip keeps acquiring the next sample
       until you raise the pin again for the next conversion)

  RVS PIN (conversion-ready status):
  Whenever CONVST/CS is held HIGH, RVS goes LOW when a conversion starts
  and back HIGH when the result is ready. We poll it instead of using a
  fixed delay.

  WIRING (Teensy 4.0 hardware SPI0, same pins as 4.1):
    Teensy MOSI (pin 11) -> ADS8699 SDI
    Teensy MISO (pin 12) -> ADS8699 SDO-0
    Teensy SCK  (pin 13) -> ADS8699 SCLK
    Teensy pin  (PIN_CS)  -> ADS8699 CONVST/CS
    Teensy pin  (PIN_RVS) -> ADS8699 RVS
    Teensy pin  (PIN_RST) -> ADS8699 RST
    ADS8699 ALARM/SDO-1/GPO -> leave unconnected for basic single-SDO use
    AD5693 + ADS1115 -> Wire (SDA pin 18, SCL pin 19)

  BOOT-TIME RANGE VERIFICATION:
  A cold power-up occasionally sees the first RANGE_SEL_REG write fail,
  leaving the chip at ±12.288 V while firmware assumes ±5.12 V (~42% of
  true value). ads8699_setRangeVerified() reads it back and retries.

  CALIBRATION:
  v_cal = (v_adc * CAL_SCALE) + CAL_OFFSET, fit against an external meter.
*/
