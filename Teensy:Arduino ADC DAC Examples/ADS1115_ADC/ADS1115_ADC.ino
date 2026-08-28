#include <Wire.h>
#include <Adafruit_ADS1X15.h>

Adafruit_ADS1115 ads;

void setup(void) {
  Serial.begin(9600);
  if (!ads.begin()) {
    Serial.println("Failed to initialize ADS.");
    while (1);
  }
}

void loop(void) {
  int16_t adc0 = ads.readADC_SingleEnded(0);
  float volts = ads.computeVolts(adc0);
  
  Serial.print("AIN0: "); Serial.print(adc0);
  Serial.print("  | Voltage: "); Serial.print(volts); Serial.println("V");
  delay(1000);
}
