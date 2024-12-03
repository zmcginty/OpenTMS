#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "QuadEncoder.h"
#include <Math.h>

//putting charge control stuff here
const int analogInPin = A1;  // Analog input pin from voltage-divider/HV-Probe
int chargeControlSSR = 10;    // Digital output pin which controls solid-state-relay which turns on/off charging circuit
int sensorValue = 0;        // raw value read from voltage-divider/HV-Probe
float aRef = 3.3;
float bitDepth = 1023.0;
float voltageProbeScaling = 2000;
const float adc_scaling = 3.3 / 1024.0;  // TODO: divide by 1023 or 1024 ?  // TODO: use measured ~3.28V here instead?
float voltage;
float targetVoltage = 1500;
float maxVoltage = 0;


//bool pulseOn = false;
bool pulseOn = true;
int State = LOW;
volatile unsigned long Count = 0; // use volatile for shared variables

const int encoderButton = 3;
const int scrTrigger1 = 25;
const int scrTrigger2 = 26;


unsigned int DWELL = 5;
unsigned int Hz = 10;
unsigned int delayMs = 1000;
bool runMode = 0;   //Currently Unused

//timer stuff
unsigned long previousMillis  = 0;
unsigned long currentMillis   = 0;

//int interval = 1000;

uint32_t mCurPosValue;
uint32_t old_position = 0;
QuadEncoder myEnc1(1, 0, 1, 0);  // Encoder on channel 1 of 4 available


#define SCREEN_WIDTH 128 // OLED display width, in pixels
//#define SCREEN_HEIGHT 64 // OLED display height, in pixels
#define SCREEN_HEIGHT 32 // OLED display height, in pixels

// Declaration for an SSD1306 display connected to I2C (SDA, SCL pins)
// The pins for I2C are defined by the Wire-library.
#define OLED_RESET     -1 // Reset pin # (or -1 if sharing Arduino reset pin)
//#define SCREEN_ADDRESS 0x3C ///< See datasheet for Address; 0x3D for 128x64, 0x3C for 128x32
#define SCREEN_ADDRESS 0x3c ///< See datasheet for Address; 0x3D for 128x64, 0x3C for 128x32
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

void setup() {
  Serial.begin(9600);

  pinMode(encoderButton, INPUT);
  pinMode(PIN_A1, INPUT);

  /* Initialize the Encoder module. */
  myEnc1.setInitConfig();  //
  myEnc1.EncConfig.revolutionCountCondition = ENABLE;
  myEnc1.EncConfig.enableModuloCountMode = DISABLE;
  myEnc1.EncConfig.positionModulusValue = 20;
  // with above settings count rev every 20 ticks
  // if myEnc1.EncConfig.revolutionCountCondition = ENABLE;
  // is not defined or set to DISABLE, the position is zeroed every
  // 20 counts, if enabled revolution counter is incremented when
  // phaseA ahead of phaseB, and decrements from 65535 when reversed.
  myEnc1.init();

  // SSD1306_SWITCHCAPVCC = generate display voltage from 3.3V internally
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for (;;); // Don't proceed, loop forever
  }

  // Show initial display buffer contents on the screen --
  // the library initializes this with an Adafruit splash screen.
  display.display();
  delay(2000); // Pause for 2 seconds

  // Clear the buffer
  display.clearDisplay();

  // Show the display buffer on the screen. You MUST call display() after
  // drawing commands to make them visible on screen!
  display.display();
  delay(2000);

  // Invert and restore display, pausing in-between
  display.invertDisplay(true);
  delay(1000);
  display.invertDisplay(false);
  delay(1000);
  drawDisplay(Hz, Count);
}

void loop() {
  //Charge controller shit
  // read the analog in value:
  //trying online version to read teensy adc.....
  //int adc_value;
  //float adc_voltage;
  //float measured_voltage;
  //adc_value = analogRead(analogInPin);
  //Serial.print("RAW ADC value: ");
  //Serial.println(adc_value);
  //adc_voltage = adc_value * adc_scaling;
  //voltage = (adc_voltage * (470000.0+100000.0)) / 100000.0;
  
  //Serial.print("adc_value = ");
  //Serial.print(adc_value);

  //Serial.print("   adc_voltage = ");
  //Serial.print(adc_voltage);

  
  //sensorValue = analogRead(analogInPin);
  //Here is where we scale the analog value we read to get a voltage value
  //voltage = ((sensorValue * aRef / bitDepth) * voltageProbeScaling);
  //voltage = ReadADC(analogInPin);

  if(voltage > maxVoltage){
    maxVoltage = voltage;
  }

  if(voltage < targetVoltage){
     digitalWrite(chargeControlSSR, HIGH);
  }
  else if(voltage >= targetVoltage){
     digitalWrite(chargeControlSSR, LOW);
  }
  //This is the raw analog-read value, not scaled to account for voltage divider/HV-probe
  //Serial.print("sensor = ");
  //Serial.print(sensorValue);

  //Voltage is the reading of the analog pin scaled to account for the voltage divider which is usually 1/1000, so 1V at the high voltage probe is read as 1mV
  //Serial.print("    voltage = ");
  //Serial.print(voltage);
  
  //This just prints out the target voltage you set. This is mainly here to verify when I make it adjustable via encoder/interface
  //Serial.print("    targetVoltage = ");
  //Serial.print(targetVoltage);

  //Max Voltage is just a running Maximum of ACTUAL voltage recorded at the discharge capacitor
  //Serial.print("    maxVoltage = ");
  //Serial.println(maxVoltage);

  currentMillis = millis();
  mCurPosValue = (myEnc1.read() / 4);

  if (mCurPosValue != old_position) {
    Hz = mCurPosValue;
    drawDisplay(Hz, Count);
  }
  old_position = mCurPosValue;

  /*if (digitalRead(encoderButton) == LOW) {
    runMode = !runMode;
  }*/
  

  //convert from Hz(Frequency) to delayMs(Period in Ms)
  //double x = (double)(1 / Hz);
  //Serial.print("X = ");
  //Serial.println(x);
  delayMs = (int)(1000 / Hz);
  //Serial.print("Freq = ");
  //Serial.println(Hz);
  //Serial.print("DelayMs = ");
  //Serial.println(delayMs);

  if(pulseOn) {
    if (currentMillis - previousMillis >= delayMs) {
      digitalWrite(scrTrigger1, HIGH);
      digitalWrite(scrTrigger2, HIGH);
      delayMicroseconds(DWELL);
      digitalWrite(scrTrigger1, LOW);
      digitalWrite(scrTrigger2, LOW);
      previousMillis = currentMillis;
      Count++;
      drawDisplay(Hz, Count);
    }
  }
}

//TODO: Change display and adjustment from Ms(Milliseconds) to Hz
void drawDisplay(int Hz, volatile unsigned long Count) {
  display.clearDisplay();
  display.setTextSize(1);      // Normal 1:1 pixel scale
  display.setTextColor(SSD1306_WHITE); // Draw white text
  display.setCursor(0, 0);     // Start at top-left corner
  /*if (runMode == 0) {
    display.println("RunMode = Config");
  }
  else if (runMode == 1) {
    display.println("RunMode = Running");
  }*/
  display.println("Freq");
  display.print(Hz);
  display.println("hz");
  //display.setTextSize(2);      // Normal 1:1 pixel scale
  display.print("Count: ");
  display.print(Count);
  display.println("pulses");
  display.display();
}
