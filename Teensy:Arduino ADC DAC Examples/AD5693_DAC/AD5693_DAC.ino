#include "Adafruit_AD569x.h"
#include <math.h> // For sine function
#include <Wire.h>
#include <Adafruit_ADS1X15.h>

Adafruit_ADS1115 ads;

Adafruit_AD569x ad5693; // Create an object of the AD5693 library

float R1_val = 3992000.00;
float R2_val = 9890.00;
uint16_t value = 0;
float incomingVoltage = 0.0;
float vref = 2.5000;
uint8_t gain = 2;   // gain of 2 means our span is 0-5V
                    // gain of 1 means our span is 0-2.5V

uint16_t voltageToDacCode(float voltage, float vref, uint8_t gain) {
  float maxCode = 65536.0;
  float code = (voltage / (vref * gain)) * maxCode;
  if (code > 65535.0) code = 65535.0;
  if (code < 0.0) code = 0.0;
  return (uint16_t)code;  
}

float codeToVoltage(uint16_t code, float vref, uint8_t gain) {
  return vref * gain * ((float)code / 65536.0);
}

float highVoltConvert(float lowVolt) {
    return lowVolt * ((R1_val + R2_val) / R2_val);
}


void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10); // Wait for serial port to start
  Serial.println("Adafruit AD5693 Test Sketch");

  // Initialize the AD5693 chip
  if (ad5693.begin(0x4C, &Wire)) { // If A0 jumper is set high, use 0x4E
    Serial.println("AD5693 initialization successful!");
  } else {
    Serial.println("Failed to initialize AD5693. Please check your connections.");
    while (1) delay(10); // Halt
  }
  if (!ads.begin()) {
    Serial.println("Failed to initialize ADS.");
    while (1);
  }


  // Reset the DAC
  ad5693.reset();

  // Configure the DAC for normal mode, internal reference, and no 2x gain
  //if (ad5693.setMode(NORMAL_MODE, true, false)) {
  if (ad5693.setMode(NORMAL_MODE, true, true)) {  //set 2x gain
    Serial.println("AD5693 configured");
  } else {
    Serial.println("Failed to configure AD5693.");
    while (1) delay(10); // Halt
  }

  // You probably will want to set the I2C clock rate to faster
  // than the default 100KHz, try 400K or 800K or even 1M!
  Wire.setClock(800000);

  Serial.println("Writing 2.5Vpp sine wave to output");
}

void loop() {
  // Generate a sine wave and write it to the DAC
  //for (float angle = 0; angle <= 2 * PI; angle += 0.1) {
  //  uint16_t value = (uint16_t)((sin(angle) + 1) * 32767.5); // Convert sine value to uint16_t
  //  if (!ad5693.writeUpdateDAC(value)) {
  //    Serial.println("Failed to update DAC.");
  //  }
  //}
  //for(uint8_t i = 0; i < 5; i++) {
  //  uint16_t value = voltageToDacCode(i, vref, gain);
  //  if (!ad5693.writeUpdateDAC(value)) {
  //    Serial.println("Failed to update DAC.");
  //  }
  //  delay(1);
  //}
//  
//  if (Serial.available() > 0) {
//    
//    // Parse the incoming stream into a float variable
//    incomingVoltage = Serial.parseFloat();
//    
//    // Print the received float value back to verify
//    Serial.print("Received Float Voltage: ");
//    Serial.println(incomingVoltage);
//    value = voltageToDacCode(incomingVoltage, vref, gain);  
//  }
  int16_t adc0 = ads.readADC_SingleEnded(0);
  int16_t adc1 = ads.readADC_SingleEnded(1);
  float volts = ads.computeVolts(adc0);
  float volts1 = ads.computeVolts(adc1);
  float highVolts = highVoltConvert(volts1);
  float setVolts = highVoltConvert(volts);

  Serial.print("AIN0: "); Serial.print(adc0);
  Serial.print("  | Voltage: "); Serial.print(volts); Serial.println("V");
  Serial.print("AIN1: "); Serial.print(adc1);
  Serial.print("  | Voltage: "); Serial.print(volts1); Serial.println("V");
  
  Serial.print("High Voltage: "); Serial.print(highVolts); Serial.println("V");
  Serial.print("Set Voltage: "); Serial.print(setVolts); Serial.println("V");

  delay(1000);
  
  value = voltageToDacCode(0.25, vref, gain);
  if (!ad5693.writeUpdateDAC(value)) {
      Serial.println("Failed to update DAC.");
  }
}
