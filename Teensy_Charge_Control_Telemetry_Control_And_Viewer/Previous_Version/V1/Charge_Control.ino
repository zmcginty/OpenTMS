/*
  OpenTMS Charge_Control, built to run on Teensy 4.0

  MEASUREMENT CHAINS
    ADS8699  18-bit SAR, SPI      -> Micsig 500x probe output (capacitor voltage). Fast path,
                                     drives the direct charge control.
    ADS1115  16-bit delta-sigma   -> AIN1: AD5693 DAC output (verification)
                                     AIN3: probe output (slow redundant reading)
    Teensy ADC_0 (HW ADC1), A0    -> probe output through R1=27.14k / R2=9.99k divider
                                     (12 V -> 3.23 V), x500 probe, then linear cal.
    Teensy ADC_1 (HW ADC2), A1    -> AD5693 DAC output through its own divider (DACSENSE_R1/R2).
                                     Second, independent ADC so neither high-impedance source
                                     shares a sample capacitor with the other.
    AD5693   16-bit DAC, I2C      -> comparator threshold (METHOD 1)

  CONTROL
    1. DAC -> external comparator vs. probe output (hardware decides, most fail-safe).
    2. Direct: ADS8699 batch mean vs. setpoint drives PIN_CHRG_CTRL.
       Held OFF unless the ADS8699 range AND parity registers are confirmed by readback.

  NOTHING IS DISCARDED
    Every ADS8699 conversion goes into the batch. Parity errors and RVS timeouts are
    counted and reported, never dropped. Batch min/max are reported for noise work.

  ADS8699 DATA LAYERS
    L0  ads8699_readFrame()            raw 32-bit SPI frame (uint32)
    L1  ads8699_frameCode()            18-bit code as int32 (0..262143, offset binary)
        ads8699_frameParityOk()        parity check of one frame
    L2  ads8699_readBatch()            N frames -> integer sum/min/max + error counts
    L3  ads8699_codeToAdcVolts()       code -> ADC input volts (first float step)
        ads8699_adcVoltsToCapVolts()   ADC volts -> capacitor volts

  SERIAL LINK: binary, see Protocol.h (COBS framing, CRC-16, command + telemetry IDs).
    Telemetry is scheduled per ID (CMD_SET_TELEM_RATE). Defaults:
      TLM_FAST   every batch     TLM_RAW  off     TLM_STATUS  10 Hz
    FAST/RAW describe one batch, so a rate below the batch rate decimates (sends the most
    recent batch), it does not average. A USB-buffer-full packet is dropped and counted
    (tx_dropped) instead of stalling the control loop.
*/

#include <SPI.h>
#include <Wire.h>
#include <ADC.h>
#include <stdarg.h>
#include "Adafruit_AD569x.h"
#include <Adafruit_ADS1X15.h>
#include "Protocol.h"

using namespace proto;

Adafruit_ADS1115 ads;
Adafruit_AD569x ad5693;
ADC *adc = new ADC();   // Teensy internal ADCs (adc->adc0 = HW ADC1, adc->adc1 = HW ADC2)

const uint8_t CALIBRATION_MODE = 0;

///////////////////////////////////////////////////////////////////////////////
// PINS
const uint8_t PIN_CS        = 10;  // ADS8699 CONVST/CS
const uint8_t PIN_RST       = 9;   // ADS8699 RST
const uint8_t PIN_RVS       = 6;   // ADS8699 RVS (conversion ready)
const uint8_t PIN_CHRG_CTRL = 5;   // charge control output

constexpr uint8_t PIN_CAP_SENSE = A0;  // pin 14, read by Teensy ADC_0
constexpr uint8_t PIN_DAC_SENSE = A1;  // pin 15, read by Teensy ADC_1
// A4/A5 are pins 18/19 = SDA/SCL of Wire, which the AD5693 and ADS1115 use.
static_assert(PIN_DAC_SENSE != 18 && PIN_DAC_SENSE != 19 &&
              PIN_CAP_SENSE != 18 && PIN_CAP_SENSE != 19,
              "Pins 18/19 (A4/A5) are the I2C bus (Wire). Pick another analog pin or move I2C to Wire1.");

///////////////////////////////////////////////////////////////////////////////
// ADS8699 CONFIGURATION
const uint32_t SPI_CLOCK_HZ = 30000000; // datasheet allows up to 66.67 MHz
SPISettings adsSPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0);

const uint16_t T_CONV_US      = 7;   // >= 5 us tconv + margin (register access only)
const uint16_t RVS_TIMEOUT_US = 50;  // safety net for the RVS poll; real tconv ~5 us

enum ADS8699_Range : uint8_t {
  RANGE_PM_3VREF     = 0b0000, // +-12.288 V
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

// Batch size. Each batch ~ NUM_AVG x (conversion + SPI read).
uint16_t NUM_AVG = 500;
const uint16_t NUM_AVG_MIN = 1;
const uint16_t NUM_AVG_MAX = 8192;   // 8192 * (2^18 - 1) < 2^31, so the sum fits int32

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

// ADS8699 calibration of ADC input voltage to calibrated multimeter
// Y = 0.9997*X + 9.895e-005 (cal performed 10/2/2026)
const float ADS8699_CAL_OFFSET = 0.00009895f;
const float ADS8699_CAL_SCALE  = 0.9997f;

///////////////////////////////////////////////////////////////////////////////
// PROBE
const uint16_t PROBE_MULTIPLIER = 500;   // Micsig 1300 V diff probe, 500x
// Probe calibration: Y = 0.9854*X + 0.1270
const float VDIV_CAL_OFFSET = 0.127f;
const float VDIV_CAL_SCALE  = 0.9854f;

///////////////////////////////////////////////////////////////////////////////
// TEENSY INTERNAL ADCs
const float    TEENSY_ADC_VREF   = 3.3f;     // T4 ADC reference is the 3.3 V rail
const uint8_t  INTADC_RES_BITS   = 12;
const float    INTADC_FULL_SCALE = 4096.0f;  // LSB = VREF / 2^12
const uint8_t  INTADC_HW_AVG     = 16;       // hardware averaging per reported count
const uint32_t INTADC_STALL_US   = 5000;     // a conversion pair taking longer = error

// A0: probe output -> divider -> pin
const float CAPSENSE_R1 = 27140.0f;
const float CAPSENSE_R2 = 9990.0f;
// Linear cal of the A0 chain (fit capacitor volts vs. meter). Start at identity.
const float INTADC_CAP_CAL_SCALE  = 1.0f;
const float INTADC_CAP_CAL_OFFSET = 0.0f;

// A1: DAC output (0-5 V) -> divider -> pin. Teensy 4.0 pins are NOT 5 V tolerant.
// !!! SET THESE TO YOUR ACTUAL DIVIDER !!!  (placeholder: 5 V -> 3.0 V)
const float DACSENSE_R1 = 10000.0f;
const float DACSENSE_R2 = 15000.0f;
const float INTADC_DAC_CAL_SCALE  = 1.0f;
const float INTADC_DAC_CAL_OFFSET = 0.0f;

struct IntAdcSample {
  int32_t capCounts;   // A0, 0..4095
  int32_t dacCounts;   // A1, 0..4095
  bool    fresh;       // converted during the most recent loop pass
};

///////////////////////////////////////////////////////////////////////////////
// ADS1115 / DAC / SETPOINT CONFIGURATION
const uint8_t  ADS1115_ADDR = 0x48;
uint16_t ADS1115_SWEEP_HZ = 100;
uint16_t ADS1115_AVG = 10;                  // -> 10 Hz published, each a 10-sweep average
const uint32_t ADS1115_CONV_WAIT_US = 1300; // 860 SPS ~ 1.16 ms per conversion

// Channels 1 and 3 used (2 and 4 grounded) to avoid mux crosstalk.
const uint8_t ADS1115_CHANNELS[] = {1, 3};
const uint8_t ADS1115_NUM_CH   = sizeof(ADS1115_CHANNELS);
const uint8_t ADS1115_CH_DAC   = 1;
const uint8_t ADS1115_CH_PROBE = 3;

// ADS1115 cal: Y = 1.000*X + 0.001057 (cal performed 10/2/2026)
const float ADS1115_CAL_OFFSET = 0.001057f;
const float ADS1115_CAL_SCALE  = 1.000f;

float vref = 2.5000f;
uint8_t gain = 2;                          // 0-5 V span
float dacTargetVolts = 0.0f;
const float DAC_VERIFY_TOL_V     = 0.010f; // ADS1115 readback tolerance
const float DAC_VERIFY_TOL_INT_V = 0.050f; // Teensy ADC readback tolerance (3.3 V rail ref)
const uint32_t DAC_RETRY_MS = 100;

const float SETPOINT_MAX_V        = 1000.0f;
const float SETPOINT_MIN_ACTIVE_V = 1.0f;
float chargeSetpointV = 0.0f;              // always 0 on boot

const uint32_t CFG_CHECK_MS = 20;          // one config register checked per interval
const uint32_t INT_SLOW_MS  = 100;         // Teensy-ADC slow averages / DAC check rate

// Result of comparing a DAC readback against the commanded voltage.
struct DacCheck {
  bool  valid;   // a comparison has been made since the last DAC change
  bool  ok;
  bool  skip;    // skip the averaging window that straddles a DAC change
  float errV;
};

///////////////////////////////////////////////////////////////////////////////
// TELEMETRY SCHEDULE (rates changeable with CMD_SET_TELEM_RATE)
struct TlmSchedule {
  uint8_t  id;
  uint16_t rateHz;   // RATE_OFF, 1..65534 Hz, or RATE_EVERY_BATCH
  uint32_t lastUs;
};
TlmSchedule tlmSchedule[] = {
  { TLM_FAST,   RATE_EVERY_BATCH, 0 },
  { TLM_RAW,    RATE_OFF,         0 },
  { TLM_STATUS, 10,               0 },
};
const uint8_t TLM_SCHED_N = sizeof(tlmSchedule) / sizeof(tlmSchedule[0]);

///////////////////////////////////////////////////////////////////////////////
// SHARED STATE

// ADS8699
bool ads8699RangeOk  = false;  // RANGE_SEL_REG confirmed by readback
bool ads8699ParityOk = false;  // DATAOUT_CTL_REG (parity) confirmed by readback
Ads8699Batch ads8699Last = {0, 1, 0, 0, 0, 0, 0};
uint32_t batchSeq = 0;
float vCapFast = 0.0f;
uint32_t totalParityErrors = 0;
uint32_t totalRvsTimeouts  = 0;
uint32_t totalCfgFaults    = 0;   // config mismatches found at runtime (all chips)

// Teensy ADCs
IntAdcSample intSample = {0, 0, false};
bool intadcCfgOk = false;
uint32_t totalIntAdcErrors = 0;
uint16_t lastIntAdcError = 0;
float vCapIntFast = 0.0f;         // latest A0 reading in cap volts
float vCapIntSlow = 0.0f;         // A0 average over INT_SLOW_MS
float vDacIntSlow = 0.0f;         // A1 average over INT_SLOW_MS, in DAC volts
int32_t intSlowCapSum = 0, intSlowDacSum = 0;
uint16_t intSlowN = 0;

// ADS1115
float ads1115_volts[4] = {0.0f, 0.0f, 0.0f, 0.0f};
bool ads1115_newData = false;
bool ads1115CfgOk    = false;

// DAC
int32_t lastDacCodeWritten = -1;
DacCheck dacChkAds1115 = {false, false, true, 0.0f};
DacCheck dacChkInt     = {false, false, true, 0.0f};

// Link
bool chargePinOn = false;
uint8_t txSeq = 0;
uint32_t txDropped = 0;
uint32_t rxErrors = 0;
float loopHz = 0.0f;

///////////////////////////////////////////////////////////////////////////////
// LINK: packet output

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

void sendAck(uint8_t cmdId, uint8_t cmdSeq, uint8_t status, float value, uint32_t aux) {
  TlmAck a;
  a.cmd_id = cmdId;
  a.cmd_seq = cmdSeq;
  a.status = status;
  a.value = value;
  a.aux = aux;
  sendPacket(TLM_ACK, &a, sizeof(a));
}

// printf-style text message as a TLM_LOG packet. (Not called logf: that's math.h.)
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

///////////////////////////////////////////////////////////////////////////////
// CHARGE PIN

void setChargePin(bool on) {
  digitalWrite(PIN_CHRG_CTRL, on ? HIGH : LOW);
  chargePinOn = on;
}

bool ads8699CfgOk() { return ads8699RangeOk && ads8699ParityOk; }

///////////////////////////////////////////////////////////////////////////////
// ADS8699 -- L0: SPI / register access

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

// READ command (full 16-bit word). Returns the raw 32-bit NOP frame; callers
// check both halves. Leaves CS LOW.
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

// Write, read back, retry up to maxAttempts.
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

///////////////////////////////////////////////////////////////////////////////
// ADS8699 -- L1: frame decoding (integer)

// 18-bit conversion code, 0..262143 (offset binary: 0 = negative full scale).
int32_t ads8699_frameCode(uint32_t frame) {
  return (int32_t)((frame >> 14) & 0x3FFFFUL);
}

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

///////////////////////////////////////////////////////////////////////////////
// ADS8699 -- L2: batch (integer)

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

    b.sum += code;
    if (code < b.minCode) b.minCode = code;
    if (code > b.maxCode) b.maxCode = code;
  }
}

///////////////////////////////////////////////////////////////////////////////
// ADS8699 -- L3: conversion to volts (float)

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

// ADC input volts -> capacitor volts (ADC cal, probe multiplier, probe cal).
float ads8699_adcVoltsToCapVolts(float v_adc) {
  float v_cal = (v_adc * ADS8699_CAL_SCALE) + ADS8699_CAL_OFFSET;
  float v_div = v_cal * PROBE_MULTIPLIER;
  return (v_div * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;
}

float ads8699_codeToCapVolts(double code) {
  return ads8699_adcVoltsToCapVolts(ads8699_codeToAdcVolts(code));
}

///////////////////////////////////////////////////////////////////////////////
// TEENSY INTERNAL ADCs

float intadc_countsToPinVolts(float counts) {
  return counts * (TEENSY_ADC_VREF / INTADC_FULL_SCALE);
}

// A0 counts -> capacitor volts: divider, probe multiplier, then linear cal.
float intadc_capCountsToCapVolts(float counts) {
  float v_probe = intadc_countsToPinVolts(counts) * ((CAPSENSE_R1 + CAPSENSE_R2) / CAPSENSE_R2);
  float v_cap = v_probe * PROBE_MULTIPLIER;
  return (v_cap * INTADC_CAP_CAL_SCALE) + INTADC_CAP_CAL_OFFSET;
}

// A1 counts -> DAC output volts.
float intadc_dacCountsToDacVolts(float counts) {
  float v_dac = intadc_countsToPinVolts(counts) * ((DACSENSE_R1 + DACSENSE_R2) / DACSENSE_R2);
  return (v_dac * INTADC_DAC_CAL_SCALE) + INTADC_DAC_CAL_OFFSET;
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

// Configures both modules identically and verifies the settings took, then does
// one test read per pin (this also catches a pin the module can't reach).
bool intadc_init() {
  pinMode(PIN_CAP_SENSE, INPUT_DISABLE);   // disable the T4 pad keeper/pulls on analog inputs
  pinMode(PIN_DAC_SENSE, INPUT_DISABLE);

  ADC_Module *mods[2] = { adc->adc0, adc->adc1 };
  bool ok = true;
  for (uint8_t i = 0; i < 2; i++) {
    ADC_Module *m = mods[i];
    m->setReference(ADC_REFERENCE::REF_3V3);
    m->setResolution(INTADC_RES_BITS);
    m->setAveraging(INTADC_HW_AVG);
    m->setConversionSpeed(ADC_CONVERSION_SPEED::MED_SPEED);
    m->setSamplingSpeed(ADC_SAMPLING_SPEED::LOW_SPEED);   // longer sample time for the divider source

    bool resOk = (m->getResolution() == INTADC_RES_BITS) &&
                 (m->getMaxValue() == (1UL << INTADC_RES_BITS) - 1);
    logMsg(resOk ? LVL_INFO : LVL_ERROR, "Teensy ADC_%u: resolution=%u max=%lu %s", i,
           m->getResolution(), (unsigned long)m->getMaxValue(), resOk ? "[OK]" : "[MISMATCH]");
    ok &= resOk;
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
// A0/A1 pair started at the end of the previous pass (so it was converted
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

///////////////////////////////////////////////////////////////////////////////
// AD5693 DAC

uint16_t voltageToDacCode(float voltage, float vref, uint8_t gain) {
  float code = (voltage / (vref * gain)) * 65536.0f;
  if (code > 65535.0f) code = 65535.0f;
  if (code < 0.0f) code = 0.0f;
  return (uint16_t)code;
}

float codeToVoltage(uint16_t code, float vref, uint8_t gain) {
  return vref * gain * ((float)code / 65536.0f);
}

// Capacitor volts -> probe-output volts the comparator should trip at.
float capVoltToDacVolts(float capVolts) {
  float v_div = (capVolts - VDIV_CAL_OFFSET) / VDIV_CAL_SCALE;
  return v_div / (float)PROBE_MULTIPLIER;
}

// Writes the DAC only when the target code changes. Every write is I2C-ACK
// checked; the output itself is verified by the ADS1115 and Teensy ADC_1.
void dac_service() {
  static elapsedMillis sinceFail = DAC_RETRY_MS;

  uint16_t code = voltageToDacCode(dacTargetVolts, vref, gain);
  if ((int32_t)code == lastDacCodeWritten) return;
  if (sinceFail < DAC_RETRY_MS) return;

  if (ad5693.writeUpdateDAC(code)) {
    lastDacCodeWritten = code;
    dacChkAds1115.skip = true;   // next averages may include pre-change samples
    dacChkInt.skip = true;
    dacChkAds1115.valid = false;
    dacChkInt.valid = false;
  } else {
    sinceFail = 0;
    logMsg(LVL_ERROR, "Failed to update DAC");
  }
}

void dacCheck_update(DacCheck &c, float measuredV, float tolV) {
  if (lastDacCodeWritten < 0) { c.valid = false; return; }
  if (c.skip) { c.skip = false; c.valid = false; return; }
  float expected = codeToVoltage((uint16_t)lastDacCodeWritten, vref, gain);
  c.errV = measuredV - expected;
  c.ok = fabsf(c.errV) <= tolV;
  c.valid = true;
}

///////////////////////////////////////////////////////////////////////////////
// ADS1115 (non-blocking)

uint16_t ads1115_muxForChannel(uint8_t ch) {
  switch (ch) {
    case 0:  return ADS1X15_REG_CONFIG_MUX_SINGLE_0;
    case 1:  return ADS1X15_REG_CONFIG_MUX_SINGLE_1;
    case 2:  return ADS1X15_REG_CONFIG_MUX_SINGLE_2;
    default: return ADS1X15_REG_CONFIG_MUX_SINGLE_3;
  }
}

// Reads the config register and checks gain (PGA) and data rate (DR) bits.
// The library rewrites the config on every conversion start, so this
// confirms what the chip is actually using.
bool ads1115_verifyConfig(uint16_t *cfgOut = nullptr) {
  Wire.beginTransmission(ADS1115_ADDR);
  Wire.write((uint8_t)0x01);                    // config register pointer
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom(ADS1115_ADDR, (uint8_t)2) != 2) return false;
  uint16_t hi = Wire.read();
  uint16_t lo = Wire.read();
  uint16_t cfg = (hi << 8) | lo;
  if (cfgOut) *cfgOut = cfg;
  return ((cfg & 0x0E00) == ((uint16_t)ads.getGain() & 0x0E00)) &&
         ((cfg & 0x00E0) == (RATE_ADS1115_860SPS & 0x00E0));
}

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
  if (!ads.conversionComplete()) return;

  int16_t raw = ads.getLastConversionResults();
  acc[ADS1115_CHANNELS[chIdx]] += ads.computeVolts(raw);
  chIdx++;

  if (chIdx < ADS1115_NUM_CH) {
    ads.startADCReading(ads1115_muxForChannel(ADS1115_CHANNELS[chIdx]), false);
    convTimer = 0;
    return;
  }

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
    ads1115_newData = true;
  }
}

///////////////////////////////////////////////////////////////////////////////
// RUNTIME CONFIG VERIFICATION
// Checks one item per CFG_CHECK_MS, round-robin. ADS8699 register mismatch:
// counted, charge pin forced off, register rewritten with verification.

bool ads8699_checkRegister(uint8_t addr, uint16_t expected, const char *name) {
  uint32_t rb = ads8699_readRegister16(addr);
  if (ads8699_registerMatches(rb, expected)) return true;

  totalCfgFaults++;
  setChargePin(false);
  logMsg(LVL_ERROR, "CFG FAULT %s readback 0x%04X/0x%04X expected 0x%04X -- rewriting",
         name, (uint16_t)(rb >> 16), (uint16_t)(rb & 0xFFFF), expected);
  return ads8699_writeRegisterVerified(addr, expected, name);
}

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
    case 2: {
      uint16_t cfg = 0;
      bool ok = ads1115_verifyConfig(&cfg);
      if (!ok && ads1115CfgOk) {
        totalCfgFaults++;
        logMsg(LVL_ERROR, "CFG FAULT ADS1115 config 0x%04X", cfg);
      }
      ads1115CfgOk = ok;
      break;
    }
  }
  step = (step + 1) % 3;
}

///////////////////////////////////////////////////////////////////////////////
// TEENSY ADC SLOW AVERAGES + DAC CHECK

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
  dacCheck_update(dacChkInt, vDacIntSlow, DAC_VERIFY_TOL_INT_V);
}

///////////////////////////////////////////////////////////////////////////////
// CHARGE SETPOINT

bool chargeEnabled() {
  return chargeSetpointV >= SETPOINT_MIN_ACTIVE_V;
}

// The only place the setpoint changes; keeps DAC target and direct control in step.
void applySetpoint(float capVolts) {
  if (!(capVolts >= 0.0f)) capVolts = 0.0f;   // also catches NaN
  if (capVolts > SETPOINT_MAX_V) capVolts = SETPOINT_MAX_V;
  chargeSetpointV = capVolts;
  dacTargetVolts = chargeEnabled() ? capVoltToDacVolts(chargeSetpointV) : 0.0f;
}

///////////////////////////////////////////////////////////////////////////////
// TELEMETRY BUILDERS + SCHEDULER

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
  t.a0_counts = (uint16_t)intSample.capCounts;
  t.a1_counts = (uint16_t)intSample.dacCounts;
  t.int_fresh = intSample.fresh ? 1 : 0;
  sendPacket(TLM_RAW, &t, sizeof(t));
}

void sendStatus() {
  const Ads8699Batch &b = ads8699Last;
  TlmStatus t;
  t.t_ms = millis();
  t.v_cap_slow = (ads1115_volts[ADS1115_CH_PROBE] * PROBE_MULTIPLIER * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;
  t.v_cap_int_avg = vCapIntSlow;
  t.set_v = chargeSetpointV;
  t.dac_meas = ads1115_volts[ADS1115_CH_DAC];
  t.dac_int = vDacIntSlow;
  t.read_dac_set_volt = (ads1115_volts[ADS1115_CH_DAC] * PROBE_MULTIPLIER * VDIV_CAL_SCALE) + VDIV_CAL_OFFSET;
  t.loop_hz = loopHz;
  t.pp_cap_v = ads8699_codeToCapVolts(b.maxCode) - ads8699_codeToCapVolts(b.minCode);
  t.dac_err_ads = dacChkAds1115.errV;
  t.dac_err_int = dacChkInt.errV;
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
  if (ads1115CfgOk)                          f |= SF_ADS1115_CFG_OK;
  if (intadcCfgOk)                           f |= SF_INTADC_CFG_OK;
  if (dacChkAds1115.valid)                   f |= SF_DAC_ADS_VALID;
  if (dacChkAds1115.ok)                      f |= SF_DAC_ADS_OK;
  if (dacChkInt.valid)                       f |= SF_DAC_INT_VALID;
  if (dacChkInt.ok)                          f |= SF_DAC_INT_OK;
  if (scheduleRate(TLM_RAW) != RATE_OFF)     f |= SF_RAW_ON;
  t.flags = f;
  sendPacket(TLM_STATUS, &t, sizeof(t));
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
bool sendTelem(uint8_t id) {
  switch (id) {
    case TLM_FAST:   sendFast();   return true;
    case TLM_RAW:    sendRaw();    return true;
    case TLM_STATUS: sendStatus(); return true;
    case TLM_HELLO:  sendHello();  return true;
    default:         return false;
  }
}

bool tlmDue(TlmSchedule &s, uint32_t nowUs) {
  if (s.rateHz == RATE_OFF) return false;
  if (s.rateHz == RATE_EVERY_BATCH) return true;
  uint32_t period = 1000000UL / s.rateHz;
  if ((uint32_t)(nowUs - s.lastUs) < period) return false;
  s.lastUs += period;
  if ((uint32_t)(nowUs - s.lastUs) >= period) s.lastUs = nowUs;  // fell behind: no burst catch-up
  return true;
}

// Called once per loop pass, after the batch is processed.
void telemetry_service() {
  uint32_t now = micros();
  for (uint8_t i = 0; i < TLM_SCHED_N; i++) {
    if (tlmDue(tlmSchedule[i], now)) sendTelem(tlmSchedule[i].id);
  }
}

///////////////////////////////////////////////////////////////////////////////
// COMMANDS (non-blocking)

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
                    c.tlm_id == TLM_STATUS || c.tlm_id == TLM_HELLO);
      sendAck(pkt.id, pkt.seq, known ? ACK_OK : ACK_BAD_ARG, 0.0f, c.tlm_id);
      if (known) sendTelem(c.tlm_id);
      return;
    }
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
    if (c == 0x00) {
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
      overflow = true;
    }
  }
}

///////////////////////////////////////////////////////////////////////////////
// SETUP / LOOP

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
  intadcCfgOk = intadc_init();  // verified resolution + test reads

  // AD5693 DAC and ADS1115, both on Wire
  if (ad5693.begin(0x4C, &Wire)) {   // 0x4E if A0 jumper set high
    logMsg(LVL_INFO, "AD5693 initialization successful");
  } else {
    while (1) { logMsg(LVL_ERROR, "Failed to initialize AD5693 -- halted"); delay(1000); }
  }
  if (!ads.begin(ADS1115_ADDR)) {
    while (1) { logMsg(LVL_ERROR, "Failed to initialize ADS1115 -- halted"); delay(1000); }
  }
  Wire.setClock(400000);

  ads.setDataRate(RATE_ADS1115_860SPS);
  // Start one conversion so the chip holds our config, then read it back.
  ads.startADCReading(ads1115_muxForChannel(ADS1115_CHANNELS[0]), false);
  delay(3);
  uint16_t cfg = 0;
  ads1115CfgOk = ads1115_verifyConfig(&cfg);
  logMsg(ads1115CfgOk ? LVL_INFO : LVL_ERROR, "ADS1115 config 0x%04X %s", cfg,
         ads1115CfgOk ? "[OK]" : "[MISMATCH]");

  ad5693.reset();
  if (ad5693.setMode(NORMAL_MODE, true, true)) {   // normal, internal ref, 2x gain
    logMsg(LVL_INFO, "AD5693 configured");
  } else {
    while (1) { logMsg(LVL_ERROR, "Failed to configure AD5693 -- halted"); delay(1000); }
  }

  applySetpoint(0.0f);   // always boot with charging disabled (DAC at 0 V)
  logMsg(LVL_INFO, "Charge setpoint 0 V (charging disabled), max %.1f V", SETPOINT_MAX_V);
  sendHello();
}

void loop() {
  static elapsedMillis rateWindow;
  static uint32_t loopCount = 0;

  // ===== FAST PATH =====
  ads8699_readBatch(NUM_AVG, ads8699Last);   // integer batch, nothing discarded
  intadc_service();                          // A0/A1 pair converted during this batch
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
  ads1115_service();
  if (ads1115_newData) {
    ads1115_newData = false;
    dacCheck_update(dacChkAds1115, ads1115_volts[ADS1115_CH_DAC], DAC_VERIFY_TOL_V);
  }
  intadc_slow_service();

  loopCount++;
  if (rateWindow >= 1000) {
    loopHz = loopCount * 1000.0f / (float)rateWindow;
    loopCount = 0;
    rateWindow = 0;
  }
}

///////////////////////////////////////////////////////
// NOTES
/*
  CONVST/CS PIN: one pin, two jobs. LOW->HIGH starts a conversion; HIGH->LOW
  opens the data frame. Read cycle: raise pin, wait for RVS high, lower pin,
  clock 32 bits, leave LOW (acquiring) until the next conversion.

  WIRING (Teensy 4.0)
    MOSI 11 -> SDI, MISO 12 -> SDO-0, SCK 13 -> SCLK
    PIN_CS -> CONVST/CS, PIN_RVS -> RVS, PIN_RST -> RST
    AD5693 + ADS1115 -> Wire (SDA 18 / A4, SCL 19 / A5)
    A0 (14) <- probe output via 27.14k / 9.99k divider
    A1 (15) <- DAC output via DACSENSE_R1 / DACSENSE_R2 divider (keep pin < 3.3 V)

  CALIBRATION
    ADS8699: v_cal = v_adc * ADS8699_CAL_SCALE + ADS8699_CAL_OFFSET
    Teensy A0: fit INTADC_CAP_CAL_SCALE/OFFSET against a meter (cap volts)
    Teensy A1: fit INTADC_DAC_CAL_SCALE/OFFSET against a meter (DAC volts)

  ADDING A COMMAND
    1. Protocol.h: new CmdId + payload struct.   2. dispatchCommand(): new case, always sendAck().
    3. opentms_protocol.py: Cmd enum + CMD_FORMATS entry.
  ADDING A TELEMETRY PACKET
    1. Protocol.h: new TlmId + struct + size static_assert.   2. sendXxx() + sendTelem() case,
       and a tlmSchedule[] entry if it should be rate-controlled.
    3. opentms_protocol.py: Tlm enum + TLM_LAYOUTS entry.
*/
