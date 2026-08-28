/*
  ADS8699 18-bit SAR ADC driver for Teensy 4.1 (Arduino IDE)
  ------------------------------------------------------------
  Datasheet: TI SBAS777 (ADS8691 / ADS8695 / ADS8699 family)

  IMPORTANT ABOUT THE CONVST/CS PIN:
  This chip has ONE physical pin that does two jobs:
    - While HIGH it behaves as CONVST: a LOW->HIGH transition on it
      starts a conversion.
    - While LOW it behaves as CS: the chip is "selected" and will
      shift data in/out over SPI.
  So a normal read cycle looks like:
      CS/CONVST pin LOW  -> HIGH   (starts conversion)
      wait >= tconv (5 us for the 100kSPS ADS8699, plus margin)
      CS/CONVST pin HIGH -> LOW    (opens the data frame, chip drives SDO)
      clock out 32 bits over SPI to read the last conversion result
      (leave the pin LOW; the chip keeps acquiring the next sample
       until you raise the pin again for the next conversion)

  WIRING (Teensy 4.1 hardware SPI0):
    Teensy MOSI (pin 11) -> ADS8699 SDI
    Teensy MISO (pin 12) -> ADS8699 SDO-0
    Teensy SCK  (pin 13) -> ADS8699 SCLK
    Teensy pin  (define below) -> ADS8699 CONVST/CS
    ADS8699 RST -> tie HIGH (DVDD) or drive from a Teensy pin (see setup())
    ADS8699 ALARM/SDO-1/GPO -> leave unconnected for basic single-SDO use

  This sketch uses the DEFAULT power-on protocol (SPI Mode 0, no
  register writes needed) and the DEFAULT input range (±3 x VREF =
  ±12.288 V with the internal 4.096 V reference). A helper function
  to change the input range is included and commented out in setup().
*/

#include <SPI.h>

// ---------- User configuration ----------
const uint8_t PIN_CS   = 10;   // CONVST/CS pin (any digital GPIO)
const uint8_t PIN_RST  = 9;    // RST pin (optional but recommended)

// SPI clock: datasheet allows up to 66.67 MHz for the SPI-compatible
// protocol. Start conservative; you can raise this once your wiring
// is verified to be clean (short traces, good ground).
const uint32_t SPI_CLOCK_HZ = 20000000; // 20 MHz

SPISettings adsSPISettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0);

// Timing from the datasheet (Timing Requirements: Conversion Cycle,
// ADS8699 row): tconv = 5000 ns max. We add margin.
const uint16_t T_CONV_US = 7;   // >= 5 us conversion time + margin

// ---------- Input range table (Table 7-3 / 7-4, VREF = 4.096 V) ----------
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
// addr = byte address of the register per Table 7-10 (e.g. 0x14 for
// RANGE_SEL_REG). The LSB of the address is ignored by the device for
// half-word commands, so pass the even byte address of the pair.
// data16 = the 16-bit value to write (covers that byte and the next one).
//
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

// Convenience: set input range and reference source
// (RANGE_SEL_REG, address 0x14. Bit 6 = INTREF_DIS, bits 3:0 = RANGE_SEL)
void ads8699_setRange(ADS8699_Range range, bool useExternalReference = false) {
  uint16_t data = (uint16_t)range;
  if (useExternalReference) data |= (1 << 6); // INTREF_DIS = 1
  ads8699_writeRegister16(0x14, data);
  currentRange = range;
}

// ---------- Raw conversion read ----------
// Assumes PIN_CS is currently LOW (device acquiring). Triggers a
// conversion of whatever has been acquired, waits, then reads it out.
// Leaves PIN_CS LOW afterward (device resumes acquiring for next call).
uint32_t ads8699_readRaw() {
  digitalWrite(PIN_CS, HIGH);        // rising edge -> start conversion
  delayMicroseconds(T_CONV_US);      // wait for conversion to finish

  digitalWrite(PIN_CS, LOW);         // falling edge -> open data frame
  SPI.beginTransaction(adsSPISettings);
  uint32_t data = spiTransfer32(0x00000000); // send NOP, read result
  SPI.endTransaction();
  // CS stays low; chip is acquiring the next sample now.

  return data;
}

// Extracts the 18-bit code from the 32-bit output word.
// (Valid only when no extra flags/parity bits have been enabled in
// DATAOUT_CTL_REG - which is the default, untouched state.)
uint32_t ads8699_extractCode(uint32_t rawWord) {
  return (rawWord >> 14) & 0x3FFFFUL;
}

// Converts an 18-bit straight-binary code to a voltage, given the
// currently configured input range.
float ads8699_codeToVoltage(uint32_t code) {
  RangeInfo ri = getRangeInfo(currentRange);
  float fsr = ri.posFS - ri.negFS;
  float lsb = fsr / 262144.0f; // 2^18
  return ri.negFS + (float)code * lsb;
}

// One-call convenience function: trigger, read, convert to volts.
float ads8699_readVoltage() {
  uint32_t raw  = ads8699_readRaw();
  uint32_t code = ads8699_extractCode(raw);
  return ads8699_codeToVoltage(code);
}

// ---------- Setup / loop ----------
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) { /* wait for USB serial, Teensy-friendly */ }

  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, LOW); // idle low so the first HIGH is a clean rising edge

  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_RST, LOW);
  delayMicroseconds(1);      // datasheet requires RST low >= 100 ns
  digitalWrite(PIN_RST, HIGH);
  delay(25);                 // tD_RST_POR = 20 ms max, power-on reset settle time

  SPI.begin();

  // Optional: change the input range / reference here. The default
  // power-on range is ±12.288 V with the internal reference, so this
  // is only needed if you want a different range, e.g.:
  //
  ads8699_setRange(RANGE_PM_1p25VREF); // switch to ±5.12 V range

  Serial.println("ADS8699 ready.");
}

uint16_t NUM_AVG = 1000;

void loop() {
  float v_acum = 0;
  for(uint16_t i=0; i<NUM_AVG; i++) {
    v_acum += ads8699_readVoltage();
  }
  float v = (v_acum / NUM_AVG);
  
  Serial.print("AIN_P - AIN_GND = ");
  Serial.print(v, 5);
  Serial.println(" V");

  //delay(200); // slow it down for readable serial output; remove for max rate
}
