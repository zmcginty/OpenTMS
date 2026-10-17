// teensy_fire.ino - fire a TMS stimulator from dscope_tms.py
//
// Send 'F' over USB serial -> one TTL pulse on FIRE_PIN.
// Use with:  python dscope_tms.py measure --fire-serial <port> --fire-cmd "F"
//
// Check your stimulator's trigger-input spec first (voltage level, polarity,
// minimum pulse width; some need an opto-isolator). Teensy 4.x pins are 3.3 V:
// add a 5 V buffer (e.g. 74HCT125) if the input needs 5 V TTL.

const int FIRE_PIN = 2;
const unsigned PULSE_US = 1000;   // trigger pulse width
const unsigned long MIN_GAP_MS = 1000;   // never fire faster than this

unsigned long last_fire = 0;

void setup() {
  pinMode(FIRE_PIN, OUTPUT);
  digitalWrite(FIRE_PIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c != 'F') return;
  if (millis() - last_fire < MIN_GAP_MS) {
    Serial.println("SKIP too soon");
    return;
  }
  digitalWrite(LED_BUILTIN, HIGH);
  digitalWrite(FIRE_PIN, HIGH);
  delayMicroseconds(PULSE_US);
  digitalWrite(FIRE_PIN, LOW);
  digitalWrite(LED_BUILTIN, LOW);
  last_fire = millis();
  Serial.println("FIRED");
}
