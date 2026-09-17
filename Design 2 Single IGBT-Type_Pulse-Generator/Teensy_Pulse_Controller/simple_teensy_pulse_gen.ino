/*
  Blink
  Turns on an LED on for one second, then off for one second, repeatedly.

  This example code is in the public domain.
 */
//Tested igbt ampacity at DWELL = 50;
unsigned int DWELL = 40;
unsigned int INTERPULSE_DELAY = 60;
unsigned int INTER_TRAIN_DELAY = 1000;
unsigned int PULSES_PER_TRAIN = 4;
// Pin 13 has an LED connected on most Arduino boards.
// Pin 11 has the LED on Teensy 2.0
// Pin 6  has the LED on Teensy++ 2.0
// Pin 13 has the LED on Teensy 3.0
// give it a name:
int led = 25;

// the setup routine runs once when you press reset:
void setup() {
  // initialize the digital pin as an output.
  pinMode(led, OUTPUT);
}

// the loop routine runs over and over again forever:
void loop() {
  for(unsigned int i = 0; i < PULSES_PER_TRAIN; i++) {
    digitalWrite(led, HIGH);   // turn the LED on (HIGH is the voltage level)
    delayMicroseconds(DWELL);
    digitalWrite(led, LOW);    // turn the LED off by making the voltage LOW
    delay(INTERPULSE_DELAY);               // wait for a second
  }
  delay(INTER_TRAIN_DELAY);
}
