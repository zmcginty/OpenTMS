/*
  OpenTMS IGBT pulse generator + gate-driver feedback monitor -- Teensy 4.1
  --------------------------------------------------------------------------
  Fires pulse trains to two 1SD418F2 gate drivers (one shared output pin, so
  both IGBTs of the two-switch flyback switch together) and checks each
  driver's fiber status/acknowledge line on every pulse.

  Expected driver behaviour (1SD418F2 datasheet):
    - ~450 ns after EACH edge of the command pulse (turn-on and turn-off),
      the status line gives an acknowledge pulse 0.7-1.8 us wide.
    - Status line active for > ~2 us = short-circuit / fault. After a fault
      the driver holds it active for its blocking time (~1 s).

  Detected anomalies (only anomalies are printed, never good pulses):
    FAULT  NO_ACK          no acknowledge after a rising or falling edge
    FAULT  SC_FAULT        status line active > FAULT_WIDTH_NS (short circuit / fault)
    FAULT  NOT_IDLE        status line already active when a pulse fired
    FAULT  IDLE_SC_FAULT   status line active > FAULT_WIDTH_NS between pulses
    FAULT  FAULT_HELD      status line held active > FAULT_HELD_MS (driver blocking)
    WARN   LATE_ACK        ack arrived later than ACK_DELAY_MAX_NS
    WARN   SHORT_ACK       ack narrower than ACK_WIDTH_MIN_NS
    WARN   LONG_ACK        ack wider than ACK_WIDTH_MAX_NS but below the fault threshold
    WARN   EXTRA_PULSE     unexpected status pulse during/just after a command pulse
    WARN   IDLE_GLITCH     short status pulse between command pulses
    WARN   TOO_MANY_EDGES  more status transitions than can be stored in one capture
    INFO   FAULT_CLEARED   a held fault line went idle again (with total duration)

  Fault response:
    - Any FAULT latches: LED on solid, safety relay (if wired) de-energized.
    - WARNs blink the LED briefly (or latch too, if TRIP_ON_WARNINGS = true).
    - Pulses KEEP FIRING regardless (STOP_FIRING_ON_FAULT = false) -- per request.
    - Send 'r' over Serial to clear the latch (only if both lines are idle),
      's' to print a status summary immediately.

  Timing: each pulse runs with interrupts disabled (~DWELL + 8 us) so the
  pulse width is exact and the status lines are sampled every ~50 ns using
  the CPU cycle counter.
*/

#include <Arduino.h>

#if !defined(__IMXRT1062__)
#error "This sketch is written for Teensy 4.x (uses ARM_DWT_CYCCNT and digitalReadFast timing)."
#endif

// Declared up front so the Arduino IDE doesn't auto-generate a C++-linkage
// prototype that would clash with the core's C-linkage weak symbol.
extern "C" void startup_middle_hook(void);

// ============================================================================
// PULSE PATTERN
// ============================================================================
// Tested IGBT ampacity at DWELL = 50
uint32_t DWELL = 60;                  // us, IGBT on-time per pulse
const uint32_t DWELL_MAX_US = 100;    // hard ceiling: DWELL is clamped to this no matter what
uint32_t INTERPULSE_DELAY = 40;       // ms, start-to-start spacing of pulses in a train (40 ms = 25 Hz)
uint32_t INTER_TRAIN_DELAY = 200;     // ms, extra gap added after the last pulse of a train
uint32_t PULSES_PER_TRAIN = 7;

// ============================================================================
// PINS
// ============================================================================
// Single pulse-output pin drives BOTH gate drivers' fiber transmitters, so
// IGBT1 and IGBT2 switch at the same instant (two-switch flyback).
constexpr uint8_t PULSE_OUTPUT   = 25;
// Gate-driver status (fiber RX -> level shifter -> Teensy), matched to your
// wiring: IGBT1 RX on pin 35, IGBT2 RX on pin 34.
constexpr uint8_t IGBT1_FEEDBACK = 35;
constexpr uint8_t IGBT2_FEEDBACK = 34;
constexpr uint8_t FAULT_LED_PIN  = 24;   // on-board LED: solid = latched fault, blink = warning
// Safety relay for the charging circuit. -1 = not wired yet (disabled).
// Wire it so the relay must be ENERGIZED to allow charging; then an unpowered,
// resetting, or tripped Teensy always leaves charging disabled.
constexpr int SAFETY_RELAY_PIN = -1;
constexpr uint8_t RELAY_ENERGIZED_LEVEL = HIGH;

// ============================================================================
// FEEDBACK DECODING
// ============================================================================
// Logic level (after your level shifter) that means "status pulse / fault"
// (i.e. fiber dark, per your description). The startup check prints what
// the idle level actually reads, so you can confirm this.
// constexpr uint8_t FB_ACTIVE_LEVEL = LOW;
constexpr uint8_t FB_ACTIVE_LEVEL = HIGH;

uint32_t ACK_SEARCH_NS    = 3000;  // look for an ack this long after each command edge
uint32_t ACK_DELAY_MAX_NS = 1000;  // datasheet 450 ns; margin for fiber RX + level shifter delay
uint32_t ACK_WIDTH_MIN_NS = 500;   // datasheet min 0.7 us
uint32_t ACK_WIDTH_MAX_NS = 1800;  // datasheet max 1.8 us
uint32_t FAULT_WIDTH_NS   = 2000;  // status active longer than this = short circuit / fault
const uint32_t POST_FALL_CAPTURE_US = 8;  // keep sampling this long after the falling edge
const uint32_t FAULT_HELD_MS = 1;         // status held active longer than this = driver blocking

// ============================================================================
// FAULT RESPONSE / TELEMETRY
// ============================================================================
const bool STOP_FIRING_ON_FAULT = false;   // This means the pulse driver will keep firing pulses even when a fault is detected. This is so you don't let your capacitor overcharge. Need to add safety relay.
const bool TRIP_ON_WARNINGS     = false;   // true = warnings also latch LED + relay
const uint32_t STATUS_INTERVAL_MS  = 10000; // periodic summary w/ ack timing ranges; 0 = off
const uint32_t REPEAT_SUPPRESS_MS  = 500;   // same anomaly on same IGBT printed at most this often

// ============================================================================
// TYPES (kept above the first function -- Arduino IDE prototype quirk)
// ============================================================================
enum Severity : uint8_t { SEV_INFO, SEV_WARN, SEV_FAULT };

enum EventCode : uint8_t {
  EV_NO_ACK, EV_LATE_ACK, EV_SHORT_ACK, EV_LONG_ACK, EV_SC_FAULT, EV_EXTRA_PULSE,
  EV_NOT_IDLE, EV_TOO_MANY_EDGES, EV_IDLE_GLITCH, EV_IDLE_SC_FAULT, EV_FAULT_HELD,
  EV_FAULT_CLEARED, EV_COUNT
};

enum Edge : uint8_t { EDGE_RISE = 0, EDGE_FALL = 1, EDGE_NONE = 2 };

struct Event {
  uint32_t ms;
  uint32_t train;
  uint32_t pulse;
  uint32_t a;          // meaning depends on code (delay ns, start ns, duration...)
  uint32_t b;          // width ns
  uint16_t suppressed; // identical events hidden since the last one printed
  uint8_t ch;
  uint8_t code;
  uint8_t edge;
  bool open;           // interval was still active when capture ended
};

constexpr uint8_t MAX_IV = 8;   // status pulses stored per IGBT per command pulse
struct Interval { uint32_t start; uint32_t end; bool open; };   // cycles from rising edge
struct Capture {
  Interval iv[MAX_IV];
  uint16_t n;          // intervals seen (may exceed MAX_IV)
  bool activeBefore;   // line was active right before the pulse fired
  bool activeAtEnd;    // line still active when capture ended
};

struct IdleMon {       // tracks status activity between pulses
  bool active;
  bool fromPulse;      // activity started inside a pulse capture (already reported)
  bool heldReported;
  uint32_t startCyc;
  uint32_t startMs;
};

struct EdgeStats { uint32_t n, dMin, dMax, wMin, wMax; };

// ============================================================================
// GLOBALS
// ============================================================================
const char *const CODE_NAME[EV_COUNT] = {
  "NO_ACK", "LATE_ACK", "SHORT_ACK", "LONG_ACK", "SC_FAULT", "EXTRA_PULSE",
  "NOT_IDLE", "TOO_MANY_EDGES", "IDLE_GLITCH", "IDLE_SC_FAULT", "FAULT_HELD",
  "FAULT_CLEARED"
};
const uint8_t CODE_SEV[EV_COUNT] = {
  SEV_FAULT, SEV_WARN, SEV_WARN, SEV_WARN, SEV_FAULT, SEV_WARN,
  SEV_FAULT, SEV_WARN, SEV_WARN, SEV_FAULT, SEV_FAULT,
  SEV_INFO
};
const char *const SEV_NAME[] = { "INFO", "WARN", "FAULT" };
const char *const EDGE_NAME[] = { "rise", "fall", "-" };

constexpr uint16_t EVQ_SIZE = 64;
Event evq[EVQ_SIZE];
uint16_t evHead = 0, evTail = 0;
uint32_t evDropped = 0;

uint32_t lastPrintMs[2][EV_COUNT];
bool     everPrinted[2][EV_COUNT];
uint16_t suppressedCount[2][EV_COUNT];

IdleMon mon[2];
EdgeStats stats[2][2];   // [IGBT][edge]

uint32_t nextFireUs = 0;
uint32_t pulseInTrain = 0;
uint32_t trainsDone = 0;
uint32_t pulsesFired = 0;
uint32_t curTrain = 0, curPulse = 0;   // context for events (last pulse fired)

uint32_t warnCount = 0, faultCount = 0;
bool faultLatched = false;
uint32_t warnFlashUntilMs = 0;
uint32_t lastStatusMs = 0;
bool statusRequested = false;

// ============================================================================
// EARLY BOOT: drive the pulse line LOW as soon as possible
// ============================================================================
// Runs during Teensy startup, ~300 ms before setup() (before the USB init
// delay). Without this the pulse pin is an unconfigured input until setup().
// A hardware pull-down on the line is still required for the time before
// even this runs -- see notes at the bottom.
extern "C" void startup_middle_hook(void) {
  pinMode(PULSE_OUTPUT, OUTPUT);
  digitalWriteFast(PULSE_OUTPUT, LOW);
}

// ============================================================================
// HELPERS
// ============================================================================
static inline uint32_t usToCyc(uint32_t us) {
  return (uint32_t)(((uint64_t)us * F_CPU_ACTUAL) / 1000000ULL);
}
static inline uint32_t nsToCyc(uint32_t ns) {
  return (uint32_t)(((uint64_t)ns * F_CPU_ACTUAL) / 1000000000ULL);
}
static inline uint32_t cycToNs(uint32_t cyc) {
  return (uint32_t)(((uint64_t)cyc * 1000000000ULL) / F_CPU_ACTUAL);
}

static inline bool fb1Active() { return digitalReadFast(IGBT1_FEEDBACK) == FB_ACTIVE_LEVEL; }
static inline bool fb2Active() { return digitalReadFast(IGBT2_FEEDBACK) == FB_ACTIVE_LEVEL; }
static inline bool fbActive(uint8_t ch) { return ch == 0 ? fb1Active() : fb2Active(); }

void setRelay(bool energize) {
  if (SAFETY_RELAY_PIN >= 0) {
    digitalWrite((uint8_t)SAFETY_RELAY_PIN,
                 energize ? RELAY_ENERGIZED_LEVEL : !RELAY_ENERGIZED_LEVEL);
  }
}

void latchFault() {
  faultLatched = true;
  digitalWriteFast(FAULT_LED_PIN, HIGH);
  setRelay(false);   // charging disabled
}

void resetStats() {
  for (uint8_t ch = 0; ch < 2; ch++) {
    for (uint8_t e = 0; e < 2; e++) {
      stats[ch][e] = { 0, UINT32_MAX, 0, UINT32_MAX, 0 };
    }
  }
}

void addStat(uint8_t ch, uint8_t edge, uint32_t delayNs, uint32_t widthNs) {
  EdgeStats &s = stats[ch][edge];
  s.n++;
  if (delayNs < s.dMin) s.dMin = delayNs;
  if (delayNs > s.dMax) s.dMax = delayNs;
  if (widthNs < s.wMin) s.wMin = widthNs;
  if (widthNs > s.wMax) s.wMax = widthNs;
}

// Records an anomaly: updates counters, LED and relay immediately, and
// queues it for printing (rate-limited per IGBT + code). Never blocks.
void pushEvent(uint8_t ch, uint8_t code, uint8_t edge, uint32_t a, uint32_t b, bool open) {
  uint8_t sev = CODE_SEV[code];
  if (sev == SEV_FAULT) {
    faultCount++;
    latchFault();
  } else if (sev == SEV_WARN) {
    warnCount++;
    warnFlashUntilMs = millis() + 100;
    if (TRIP_ON_WARNINGS) latchFault();
  }

  uint32_t now = millis();
  if (everPrinted[ch][code] && (now - lastPrintMs[ch][code]) < REPEAT_SUPPRESS_MS) {
    if (suppressedCount[ch][code] < 0xFFFF) suppressedCount[ch][code]++;
    return;
  }
  everPrinted[ch][code] = true;
  lastPrintMs[ch][code] = now;

  uint16_t next = (evHead + 1) % EVQ_SIZE;
  if (next == evTail) { evDropped++; return; }
  Event &e = evq[evHead];
  e.ms = now;
  e.train = curTrain;
  e.pulse = curPulse;
  e.a = a;
  e.b = b;
  e.suppressed = suppressedCount[ch][code];
  e.ch = ch;
  e.code = code;
  e.edge = edge;
  e.open = open;
  suppressedCount[ch][code] = 0;
  evHead = next;
}

// ============================================================================
// FIRE ONE PULSE AND CAPTURE BOTH STATUS LINES
// ============================================================================
static inline void trackEdge(Capture &c, bool nowActive, uint32_t t) {
  if (nowActive) {
    if (c.n < MAX_IV) {
      c.iv[c.n].start = t;
      c.iv[c.n].end = t;
      c.iv[c.n].open = true;
    }
    c.n++;
  } else if (c.n > 0 && c.n <= MAX_IV) {
    c.iv[c.n - 1].end = t;
    c.iv[c.n - 1].open = false;
  }
}

// Fires one command pulse with interrupts off, sampling both status lines
// from the rising edge until POST_FALL_CAPTURE_US after the falling edge.
// Returns the falling-edge time (cycles after the rising edge).
uint32_t fireAndCapture(Capture cap[2], uint32_t &t0Abs, uint32_t &endCyc) {
  uint32_t dwellUs = (DWELL > DWELL_MAX_US) ? DWELL_MAX_US : DWELL;
  const uint32_t dwellCyc = usToCyc(dwellUs);
  endCyc = dwellCyc + usToCyc(POST_FALL_CAPTURE_US);

  for (uint8_t ch = 0; ch < 2; ch++) {
    cap[ch].n = 0;
    cap[ch].activeBefore = false;
    cap[ch].activeAtEnd = false;
  }

  uint32_t fallCyc = dwellCyc;
  uint32_t el = 0;

  noInterrupts();
  bool p1 = fb1Active();
  bool p2 = fb2Active();
  if (p1) { cap[0].activeBefore = true; trackEdge(cap[0], true, 0); }
  if (p2) { cap[1].activeBefore = true; trackEdge(cap[1], true, 0); }

  const uint32_t t0 = ARM_DWT_CYCCNT;
  digitalWriteFast(PULSE_OUTPUT, HIGH);
  bool high = true;
  do {
    el = ARM_DWT_CYCCNT - t0;
    if (high && el >= dwellCyc) {
      digitalWriteFast(PULSE_OUTPUT, LOW);
      fallCyc = el;
      high = false;
    }
    bool s1 = fb1Active();
    bool s2 = fb2Active();
    if (s1 != p1) { trackEdge(cap[0], s1, el); p1 = s1; }
    if (s2 != p2) { trackEdge(cap[1], s2, el); p2 = s2; }
  } while (el < endCyc);
  digitalWriteFast(PULSE_OUTPUT, LOW);   // belt and braces
  interrupts();

  cap[0].activeAtEnd = p1;
  cap[1].activeAtEnd = p2;
  for (uint8_t ch = 0; ch < 2; ch++) {
    uint8_t stored = cap[ch].n < MAX_IV ? cap[ch].n : MAX_IV;
    for (uint8_t i = 0; i < stored; i++) {
      if (cap[ch].iv[i].open) cap[ch].iv[i].end = el;
    }
  }
  t0Abs = t0;
  return fallCyc;
}

// Checks one IGBT's capture against the expected two acks.
void analyzeCapture(uint8_t ch, const Capture &c, uint32_t fallCyc) {
  uint8_t stored = c.n < MAX_IV ? c.n : MAX_IV;
  bool used[MAX_IV] = { false };

  if (c.activeBefore) {
    // Driver was already signalling (most likely still blocking after a
    // fault). Acks aren't expected then, so NO_ACK isn't reported below.
    pushEvent(ch, EV_NOT_IDLE, EDGE_NONE, 0, 0, false);
    used[0] = true;
  }

  const uint32_t searchCyc = nsToCyc(ACK_SEARCH_NS);
  const uint32_t edgeAt[2] = { 0, fallCyc };

  for (uint8_t e = 0; e < 2; e++) {
    int found = -1;
    for (uint8_t i = 0; i < stored; i++) {
      if (used[i]) continue;
      uint32_t s = c.iv[i].start;
      if (s >= edgeAt[e] && (s - edgeAt[e]) <= searchCyc) { found = i; break; }
    }
    if (found < 0) {
      if (!c.activeBefore) pushEvent(ch, EV_NO_ACK, e, 0, 0, false);
      continue;
    }
    used[found] = true;
    const Interval &iv = c.iv[found];
    uint32_t delayNs = cycToNs(iv.start - edgeAt[e]);
    uint32_t widthNs = cycToNs(iv.end - iv.start);

    if (iv.open || widthNs > FAULT_WIDTH_NS) {
      pushEvent(ch, EV_SC_FAULT, e, delayNs, widthNs, iv.open);
      continue;
    }
    addStat(ch, e, delayNs, widthNs);
    if (widthNs < ACK_WIDTH_MIN_NS)      pushEvent(ch, EV_SHORT_ACK, e, delayNs, widthNs, false);
    else if (widthNs > ACK_WIDTH_MAX_NS) pushEvent(ch, EV_LONG_ACK, e, delayNs, widthNs, false);
    if (delayNs > ACK_DELAY_MAX_NS)      pushEvent(ch, EV_LATE_ACK, e, delayNs, widthNs, false);
  }

  // Anything left over wasn't an ack for either edge.
  for (uint8_t i = 0; i < stored; i++) {
    if (used[i]) continue;
    const Interval &iv = c.iv[i];
    uint32_t startNs = cycToNs(iv.start);
    uint32_t widthNs = cycToNs(iv.end - iv.start);
    if (iv.open || widthNs > FAULT_WIDTH_NS) {
      pushEvent(ch, EV_SC_FAULT, EDGE_NONE, startNs, widthNs, iv.open);
    } else {
      pushEvent(ch, EV_EXTRA_PULSE, EDGE_NONE, startNs, widthNs, false);
    }
  }

  if (c.n > MAX_IV) pushEvent(ch, EV_TOO_MANY_EDGES, EDGE_NONE, c.n, 0, false);
}

// ============================================================================
// BETWEEN-PULSE MONITOR
// ============================================================================
void monStart(uint8_t ch, uint32_t startCyc, bool fromPulse) {
  IdleMon &m = mon[ch];
  m.active = true;
  m.fromPulse = fromPulse;
  m.heldReported = false;
  m.startCyc = startCyc;
  m.startMs = millis();
}

void monRelease(uint8_t ch, uint32_t endCyc) {
  IdleMon &m = mon[ch];
  uint32_t elMs = millis() - m.startMs;
  // The cycle counter wraps every ~7 s at 600 MHz; use millis for long ones.
  uint32_t durNs = (elMs < 4000) ? cycToNs(endCyc - m.startCyc) : UINT32_MAX;
  if (m.fromPulse || m.heldReported) {
    uint32_t durUs = (elMs < 4000) ? durNs / 1000 : elMs * 1000;
    pushEvent(ch, EV_FAULT_CLEARED, EDGE_NONE, durUs, 0, false);
  } else if (durNs > FAULT_WIDTH_NS) {
    pushEvent(ch, EV_IDLE_SC_FAULT, EDGE_NONE, durNs, 0, false);
  } else {
    pushEvent(ch, EV_IDLE_GLITCH, EDGE_NONE, durNs, 0, false);
  }
  m.active = false;
}

// Polled from loop() between pulses. Catches status activity that doesn't
// line up with a command pulse, and tracks how long a fault is held.
void monitorIdle() {
  const uint32_t cyc = ARM_DWT_CYCCNT;
  const bool act[2] = { fb1Active(), fb2Active() };
  for (uint8_t ch = 0; ch < 2; ch++) {
    IdleMon &m = mon[ch];
    if (act[ch]) {
      if (!m.active) {
        monStart(ch, cyc, false);
      } else if (!m.heldReported && (millis() - m.startMs) > FAULT_HELD_MS) {
        pushEvent(ch, EV_FAULT_HELD, EDGE_NONE, 0, 0, false);
        m.heldReported = true;
      }
    } else if (m.active) {
      monRelease(ch, cyc);
    }
  }
}

// ============================================================================
// PULSE SCHEDULER
// ============================================================================
void doPulse() {
  curTrain = trainsDone + 1;
  curPulse = pulseInTrain + 1;

  if (!(STOP_FIRING_ON_FAULT && faultLatched)) {
    monitorIdle();   // settle the idle monitor right before firing

    Capture cap[2];
    uint32_t t0, endCyc;
    uint32_t fallCyc = fireAndCapture(cap, t0, endCyc);
    pulsesFired++;

    for (uint8_t ch = 0; ch < 2; ch++) {
      analyzeCapture(ch, cap[ch], fallCyc);

      if (cap[ch].activeBefore) {
        // Activity that began before this pulse: the idle monitor already
        // owns it and will report when it ends.
        continue;
      }
      mon[ch].active = false;
      if (cap[ch].activeAtEnd) {
        // Fault began during this pulse (already reported as SC_FAULT);
        // hand it to the idle monitor to time how long it's held.
        uint8_t last = (cap[ch].n <= MAX_IV ? cap[ch].n : MAX_IV) - 1;
        monStart(ch, t0 + cap[ch].iv[last].start, true);
      }
    }
  }

  uint32_t gapMs = INTERPULSE_DELAY;
  pulseInTrain++;
  if (pulseInTrain >= PULSES_PER_TRAIN) {
    pulseInTrain = 0;
    trainsDone++;
    gapMs += INTER_TRAIN_DELAY;
  }
  nextFireUs += gapMs * 1000UL;
  // If the loop ever fell badly behind, re-anchor instead of firing a burst.
  if ((int32_t)(micros() - nextFireUs) > 0) nextFireUs = micros() + gapMs * 1000UL;
}

// ============================================================================
// SERIAL OUTPUT / INPUT (non-blocking)
// ============================================================================
bool printEvent(const Event &e) {
  char buf[220];
  int n = snprintf(buf, sizeof(buf), "[%s] t=%lums train=%lu pulse=%lu IGBT%u %s",
                   SEV_NAME[CODE_SEV[e.code]], (unsigned long)e.ms,
                   (unsigned long)e.train, (unsigned long)e.pulse,
                   (unsigned)(e.ch + 1), CODE_NAME[e.code]);
  const size_t cap = sizeof(buf);
  switch (e.code) {
    case EV_NO_ACK:
      n += snprintf(buf + n, cap - n, " edge=%s (no acknowledge from driver)", EDGE_NAME[e.edge]);
      break;
    case EV_LATE_ACK:
    case EV_SHORT_ACK:
    case EV_LONG_ACK:
      n += snprintf(buf + n, cap - n, " edge=%s delay=%luns width=%luns",
                    EDGE_NAME[e.edge], (unsigned long)e.a, (unsigned long)e.b);
      break;
    case EV_SC_FAULT:
      if (e.edge == EDGE_NONE) {
        n += snprintf(buf + n, cap - n, " at=+%luns", (unsigned long)e.a);
      } else {
        n += snprintf(buf + n, cap - n, " edge=%s delay=%luns", EDGE_NAME[e.edge], (unsigned long)e.a);
      }
      n += snprintf(buf + n, cap - n, " width=%s%luns (status active > %luns: short circuit / driver fault)",
                    e.open ? ">=" : "", (unsigned long)e.b, (unsigned long)FAULT_WIDTH_NS);
      break;
    case EV_EXTRA_PULSE:
      n += snprintf(buf + n, cap - n, " at=+%luns width=%luns (status pulse not matching an edge)",
                    (unsigned long)e.a, (unsigned long)e.b);
      break;
    case EV_NOT_IDLE:
      n += snprintf(buf + n, cap - n, " (status already active when pulse fired; driver blocking?)");
      break;
    case EV_TOO_MANY_EDGES:
      n += snprintf(buf + n, cap - n, " count=%lu (noisy status line?)", (unsigned long)e.a);
      break;
    case EV_IDLE_GLITCH:
    case EV_IDLE_SC_FAULT:
      n += snprintf(buf + n, cap - n, " width=%luns (between pulses)", (unsigned long)e.a);
      break;
    case EV_FAULT_HELD:
      n += snprintf(buf + n, cap - n, " (status held active > %lums)", (unsigned long)FAULT_HELD_MS);
      break;
    case EV_FAULT_CLEARED:
      n += snprintf(buf + n, cap - n, " after %lu.%03lums",
                    (unsigned long)(e.a / 1000), (unsigned long)(e.a % 1000));
      break;
    default:
      break;
  }
  if (e.suppressed) {
    n += snprintf(buf + n, cap - n, " (+%u repeats suppressed)", (unsigned)e.suppressed);
  }
  if (n < 0) return true;
  if ((size_t)n >= cap) n = cap - 1;

  if (Serial.availableForWrite() < n + 2) return false;   // try again later; never block
  Serial.write((const uint8_t *)buf, n);
  Serial.write("\r\n");
  return true;
}

void drainOneEvent() {
  if (evTail == evHead) return;
  if (printEvent(evq[evTail])) evTail = (evTail + 1) % EVQ_SIZE;
}

void printRange(uint8_t ch, uint8_t e) {
  const EdgeStats &s = stats[ch][e];
  if (s.n == 0) {
    Serial.printf("%s: no clean acks", EDGE_NAME[e]);
  } else {
    Serial.printf("%s: n=%lu delay=%lu-%luns width=%lu-%luns", EDGE_NAME[e],
                  (unsigned long)s.n, (unsigned long)s.dMin, (unsigned long)s.dMax,
                  (unsigned long)s.wMin, (unsigned long)s.wMax);
  }
}

void maybePrintStatus() {
  bool due = STATUS_INTERVAL_MS > 0 && (millis() - lastStatusMs) >= STATUS_INTERVAL_MS;
  if (!due && !statusRequested) return;
  if (Serial.availableForWrite() < 400) return;   // wait for room; never block
  statusRequested = false;
  lastStatusMs = millis();

  Serial.printf("[STATUS] up=%lus trains=%lu pulses=%lu warnings=%lu faults=%lu latched=%s dropped_msgs=%lu\r\n",
                (unsigned long)(millis() / 1000), (unsigned long)trainsDone,
                (unsigned long)pulsesFired, (unsigned long)warnCount,
                (unsigned long)faultCount, faultLatched ? "YES" : "no",
                (unsigned long)evDropped);
  for (uint8_t ch = 0; ch < 2; ch++) {
    Serial.printf("[STATUS]   IGBT%u ", (unsigned)(ch + 1));
    printRange(ch, EDGE_RISE);
    Serial.print(" | ");
    printRange(ch, EDGE_FALL);
    Serial.print("\r\n");
  }
  resetStats();
}

void clearFaultLatch() {
  if (fb1Active() || fb2Active()) {
    Serial.println("[INFO] fault latch NOT cleared: a status line is still active");
    return;
  }
  faultLatched = false;
  setRelay(true);
  Serial.println("[INFO] fault latch cleared, safety relay re-enabled");
}

void handleSerialInput() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == 'r' || c == 'R') clearFaultLatch();
    else if (c == 's' || c == 'S') statusRequested = true;
  }
}

void updateLed() {
  bool on = faultLatched || (int32_t)(warnFlashUntilMs - millis()) > 0;
  digitalWriteFast(FAULT_LED_PIN, on ? HIGH : LOW);
}

// ============================================================================
// STARTUP
// ============================================================================
// Samples both status lines for 100 ms before any pulse is fired and reports
// what the idle level looks like. Returns true if both lines look idle.
bool startupLineCheck() {
  uint32_t samples = 0, active[2] = { 0, 0 };
  uint32_t t = millis();
  while (millis() - t < 100) {
    samples++;
    if (fb1Active()) active[0]++;
    if (fb2Active()) active[1]++;
  }
  bool ok = true;
  for (uint8_t ch = 0; ch < 2; ch++) {
    uint8_t pin = (ch == 0) ? IGBT1_FEEDBACK : IGBT2_FEEDBACK;
    uint32_t pct = (uint32_t)((uint64_t)active[ch] * 100 / (samples ? samples : 1));
    Serial.printf("[INFO] IGBT%u status (pin %u) idle reads %s, active %lu%% of the time",
                  (unsigned)(ch + 1), (unsigned)pin,
                  digitalReadFast(pin) ? "HIGH" : "LOW", (unsigned long)pct);
    if (pct > 90) {
      Serial.print("  <-- STUCK ACTIVE: driver in fault, fiber/level shifter wiring, "
                   "or FB_ACTIVE_LEVEL is the wrong polarity");
      ok = false;
    } else if (pct > 0) {
      Serial.print("  <-- activity seen with no pulses fired (noise?)");
    }
    Serial.print("\r\n");
  }
  return ok;
}

void setup() {
  pinMode(PULSE_OUTPUT, OUTPUT);
  digitalWriteFast(PULSE_OUTPUT, LOW);
  pinMode(IGBT1_FEEDBACK, INPUT);
  pinMode(IGBT2_FEEDBACK, INPUT);
  pinMode(FAULT_LED_PIN, OUTPUT);
  digitalWriteFast(FAULT_LED_PIN, LOW);
  if (SAFETY_RELAY_PIN >= 0) pinMode((uint8_t)SAFETY_RELAY_PIN, OUTPUT);
  setRelay(false);   // charging stays disabled until the startup check passes

  Serial.begin(115200);
  uint32_t t = millis();
  while (!Serial && millis() - t < 1500) { }   // don't hang if no USB host

  resetStats();

  Serial.printf("[INFO] IGBT pulse driver: DWELL=%luus (max %lu) INTERPULSE=%lums "
                "INTER_TRAIN=%lums PULSES_PER_TRAIN=%lu\r\n",
                (unsigned long)DWELL, (unsigned long)DWELL_MAX_US,
                (unsigned long)INTERPULSE_DELAY, (unsigned long)INTER_TRAIN_DELAY,
                (unsigned long)PULSES_PER_TRAIN);
  Serial.printf("[INFO] ack checks: delay<=%luns width %lu-%luns, fault if active >%luns; "
                "stop_on_fault=%s relay=%s\r\n",
                (unsigned long)ACK_DELAY_MAX_NS, (unsigned long)ACK_WIDTH_MIN_NS,
                (unsigned long)ACK_WIDTH_MAX_NS, (unsigned long)FAULT_WIDTH_NS,
                STOP_FIRING_ON_FAULT ? "yes" : "no",
                SAFETY_RELAY_PIN >= 0 ? "wired" : "not configured");

  if (startupLineCheck()) {
    setRelay(true);   // charging allowed
  } else {
    latchFault();
  }
  Serial.println("[INFO] commands: 'r' = clear fault latch, 's' = status now");

  lastStatusMs = millis();
  nextFireUs = micros() + 100000UL;   // first pulse 100 ms after setup
}

// ============================================================================
// LOOP
// ============================================================================
void loop() {
  if ((int32_t)(micros() - nextFireUs) >= 0) {
    doPulse();
    return;
  }

  monitorIdle();
  updateLed();

  // Only do serial work when the next pulse isn't imminent.
  if ((int32_t)(nextFireUs - micros()) > 500) {
    handleSerialInput();
    drainOneEvent();
    maybePrintStatus();
  }
}

/*
  NOTES
  -----
  - The original sketch never called pinMode(PULSE_OUTPUT, OUTPUT). On Teensy 4,
    digitalWrite() on a pin that isn't an output only switches its internal
    pull-up (~22k) / pull-down (~100k), so the line was driven very weakly.
  - Before any code runs (power-up, reset, upload), the pulse pin is an
    unconfigured input. Put a hardware pull-down (e.g. 10k to GND) at the
    fiber transmitter driver input so the IGBTs can't be commanded on while
    the Teensy boots.
  - Ack delay/width measured here include the fiber receiver and level shifter.
    MOSFET-type level shifters (BSS138) are slow on rising edges, which can
    stretch measured widths. Use the [STATUS] ranges to tune the thresholds.
*/
