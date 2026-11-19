// Protocol.h -- OpenTMS Charge_Control binary serial protocol, version 1.
// Mirrored on the host by opentms_protocol.py. Change both together.
//
// Pure C++ (no Arduino headers) so it can also be compiled and tested on a PC.
//
// FRAMING
//   wire frame = COBS(packet) followed by one 0x00 delimiter
//   packet     = id(u8) seq(u8) payload(0..MAX_PAYLOAD bytes) crc16(u16, little-endian)
//   crc16      = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout)
//                over id + seq + payload.  Python: binascii.crc_hqx(data, 0xFFFF)
//   COBS guarantees 0x00 only appears as the delimiter, so a receiver that joins
//   mid-stream (or hits a corrupted byte) resynchronizes at the next 0x00.
//   All multi-byte fields are little-endian; structs are packed.
//
// WORKED EXAMPLE: host sends CMD_SET_SETPOINT 800 V with seq 5
//   payload (float32 LE 800.0)        00 00 48 44
//   packet  id seq payload crc        02 05 00 00 48 44 22 2a      (crc16 = 0x2A22)
//   COBS + delimiter (on the wire)    03 02 05 01 05 48 44 22 2a 00
//   COBS replaced each 0x00 with a "distance to the next zero" byte (03, 01, 05), so the
//   only 0x00 left is the final delimiter. The device replies with TLM_ACK carrying
//   cmd_id=0x02, cmd_seq=5, status=ACK_OK, value=800.0.
//
// IDS
//   0x01-0x7F  host -> device commands. Every command gets exactly one TLM_ACK.
//   0x80-0xFF  device -> host telemetry.
//
// SEQ
//   Commands:  host counter; the device echoes it in the ACK so the host can match replies.
//   Telemetry: device counter, +1 for every packet the device tries to send (including ones
//              dropped because the USB buffer was full). A gap on the host = a lost packet.
//
// EXTENDING
//   New command/telemetry: add an ID + struct here and in opentms_protocol.py.
//   Existing telemetry structs may grow by APPENDING fields only: the host parses the prefix
//   it knows and ignores the rest. Commands are length-checked exactly (the device refuses
//   anything it doesn't fully understand). Bump PROTO_VERSION for incompatible changes.

#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace proto {

// v2: ADS1115 removed (STATUS fields/flags changed), RAW a0/a1_counts -> cap/dac_counts.
constexpr uint8_t  PROTO_VERSION    = 2;
constexpr size_t   MAX_PAYLOAD      = 96;
constexpr size_t   MAX_PACKET       = 2 + MAX_PAYLOAD + 2;                    // id, seq, payload, crc
constexpr size_t   MAX_ENCODED      = MAX_PACKET + 1 + (MAX_PACKET / 254) + 1; // COBS overhead + delimiter
constexpr uint16_t RATE_EVERY_BATCH = 0xFFFF;  // telemetry rate value: send after every ADS8699 batch
constexpr uint16_t RATE_OFF         = 0;

// ---------- Commands (host -> device) ----------
enum CmdId : uint8_t {
  CMD_PING           = 0x01,  // CmdPing           -> ACK aux = token
  CMD_SET_SETPOINT   = 0x02,  // CmdSetSetpoint    -> ACK value = applied setpoint (cap V)
  CMD_CHARGE_OFF     = 0x03,  // (no payload)      -> ACK value = 0
  CMD_GET_SETPOINT   = 0x04,  // (no payload)      -> ACK value = setpoint
  CMD_SET_AVG        = 0x05,  // CmdSetAvg         -> ACK value = applied batch size
  CMD_SET_TELEM_RATE = 0x06,  // CmdSetTelemRate   -> ACK value = applied rate (Hz, or 65535)
  CMD_REQUEST_TELEM  = 0x07,  // CmdRequestTelem   -> ACK, then that telemetry packet once
  CMD_SET_DAC_OVERRIDE = 0x08,// CmdSetDacOverride -> ACK value = DAC output volts now commanded
                              //   enable=1: drive the DAC to `volts` directly (calibration).
                              //   Only while the setpoint is 0 (else ACK_REFUSED). Any SET_SETPOINT
                              //   or CHARGE_OFF cancels it. enable=0: back to setpoint control.
};

// ---------- Telemetry (device -> host) ----------
enum TlmId : uint8_t {
  TLM_ACK    = 0x80,  // TlmAck: reply to every command
  TLM_FAST   = 0x81,  // TlmFast: per-batch voltages               (default: every batch)
  TLM_RAW    = 0x82,  // TlmRaw: per-batch integer ADC data         (default: off)
  TLM_STATUS = 0x83,  // TlmStatus: slow telemetry + health         (default: 10 Hz)
  TLM_LOG    = 0x84,  // level(u8) + UTF-8 text, variable length   (boot messages, faults)
  TLM_HELLO  = 0x85,  // TlmHello: sent at boot and on request
  TLM_CAL    = 0x86,  // TlmCal: per-batch ADC input volts for calibration (default: off)
};

enum AckStatus : uint8_t {
  ACK_OK           = 0,
  ACK_BAD_CRC      = 1,  // cmd_id / cmd_seq in this ACK are unverified
  ACK_BAD_LEN      = 2,
  ACK_UNKNOWN_CMD  = 3,
  ACK_OUT_OF_RANGE = 4,
  ACK_BAD_ARG      = 5,
  ACK_BAD_FRAME    = 6,  // COBS error or frame too long; cmd_id / cmd_seq are 0
  ACK_REFUSED      = 7,  // valid command, not allowed in the current state
};

enum LogLevel : uint8_t { LVL_INFO = 0, LVL_WARN = 1, LVL_ERROR = 2 };

// TlmFast.flags
constexpr uint8_t FF_CHG_PIN        = 1 << 0;
constexpr uint8_t FF_CHARGE_ENABLED = 1 << 1;
constexpr uint8_t FF_ADS8699_CFG_OK = 1 << 2;
constexpr uint8_t FF_INT_FRESH      = 1 << 3;  // v_cap_int converted during this batch

// TlmStatus.flags
constexpr uint16_t SF_CHARGE_ENABLED = 1 << 0;
constexpr uint16_t SF_CHG_PIN        = 1 << 1;
constexpr uint16_t SF_ADS8699_CFG_OK = 1 << 2;  // range + parity registers verified
constexpr uint16_t SF_INTADC_CFG_OK  = 1 << 3;  // Teensy ADC hardware registers verified
constexpr uint16_t SF_DAC_INT_VALID  = 1 << 4;  // a measured-vs-commanded DAC check exists
constexpr uint16_t SF_DAC_INT_OK     = 1 << 5;  // ...and it was within tolerance
constexpr uint16_t SF_RAW_ON         = 1 << 6;
constexpr uint16_t SF_DAC_RB_AVAIL   = 1 << 7;  // DAC SDO readback is wired (DAC_HAS_SDO)
constexpr uint16_t SF_DAC_RB_OK      = 1 << 8;  // last DAC SDO readback matched
constexpr uint16_t SF_DAC_OVERRIDE   = 1 << 9;  // DAC forced by CMD_SET_DAC_OVERRIDE

// ---------- Command payloads ----------
struct __attribute__((packed)) CmdPing         { uint32_t token; };
struct __attribute__((packed)) CmdSetSetpoint  { float volts; };
struct __attribute__((packed)) CmdSetAvg       { uint16_t n; };
struct __attribute__((packed)) CmdSetTelemRate { uint8_t tlm_id; uint16_t rate_hz; };
struct __attribute__((packed)) CmdRequestTelem { uint8_t tlm_id; };
struct __attribute__((packed)) CmdSetDacOverride { uint8_t enable; float volts; };

// ---------- Telemetry payloads ----------
struct __attribute__((packed)) TlmAck {
  uint8_t  cmd_id;
  uint8_t  cmd_seq;
  uint8_t  status;    // AckStatus
  float    value;     // command-specific (see CmdId comments)
  uint32_t aux;       // command-specific (PING token)
};

struct __attribute__((packed)) TlmFast {
  uint32_t batch_seq;
  uint32_t t_us;       // micros() at batch start
  float    v_cap;      // ADS8699 batch mean, cap volts
  float    v_cap_int;  // Teensy A0, cap volts
  uint8_t  flags;      // FF_*
};

struct __attribute__((packed)) TlmRaw {
  uint32_t batch_seq;
  uint32_t t_us;
  int32_t  sum;        // sum of n 18-bit ADS8699 codes (mean = sum / n)
  uint16_t n;
  int32_t  min_code;
  int32_t  max_code;
  uint16_t parity_errors;
  uint16_t rvs_timeouts;
  uint16_t cap_counts; // Teensy ADC_0 (A0, cap sense), 12-bit
  uint16_t dac_counts; // Teensy ADC_1 (A4, DAC sense), 12-bit
  uint8_t  int_fresh;
};

struct __attribute__((packed)) TlmStatus {
  uint32_t t_ms;
  float    v_cap_int_avg;        // Teensy A0 average, cap V
  float    set_v;                // charge setpoint, cap V
  float    dac_set_v;            // DAC output the last written code should give, V
  float    dac_int;              // Teensy A4 average: measured DAC output, V
  float    dac_err_int;          // dac_int - dac_set_v, V
  float    dac_threshold_cap_v;  // dac_int converted to cap V: where the comparator trips
  float    loop_hz;
  float    pp_cap_v;             // last batch max-min, cap V
  uint16_t n_avg;
  int32_t  pp_codes;
  uint32_t parity_errors;      // running totals
  uint32_t rvs_timeouts;
  uint32_t cfg_faults;
  uint32_t intadc_errors;
  uint32_t tx_dropped;
  uint32_t rx_errors;          // bad CRC / framing / length on received commands
  uint16_t flags;              // SF_*
};

struct __attribute__((packed)) TlmHello {
  uint8_t  proto_version;
  uint8_t  reserved;
  float    setpoint_max;
  uint16_t n_avg;
  uint16_t n_avg_max;
  uint16_t rate_fast;
  uint16_t rate_raw;
  uint16_t rate_status;
};

// Input-level voltages for calibrating each ADC against a meter. "raw" = before that
// channel's *_CAL_SCALE/OFFSET, "cal" = after. Neither includes the 500x probe scale.
//   ads   ADS8699 input pin (no divider)
//   a0    Teensy A0 signal BEFORE its divider (probe output, 0-5 V)
//   a4    Teensy A4 signal BEFORE its divider (DAC output, 0-5 V)
struct __attribute__((packed)) TlmCal {
  uint32_t batch_seq;
  float    ads_raw_v;   // batch mean
  float    ads_cal_v;
  float    a0_raw_v;    // one hardware-averaged Teensy conversion
  float    a0_cal_v;
  float    a4_raw_v;
  float    a4_cal_v;
  float    ads_pp_v;    // batch max - min, ADS8699 input volts
  uint8_t  int_fresh;   // a0/a4 converted during this batch (else repeated)
};

// Sizes are part of the wire format; opentms_protocol.py asserts the same numbers.
static_assert(sizeof(TlmAck)    == 11, "TlmAck size");
static_assert(sizeof(TlmFast)   == 17, "TlmFast size");
static_assert(sizeof(TlmRaw)    == 31, "TlmRaw size");
static_assert(sizeof(TlmStatus) == 68, "TlmStatus size");
static_assert(sizeof(TlmHello)  == 16, "TlmHello size");
static_assert(sizeof(TlmCal)    == 33, "TlmCal size");
static_assert(sizeof(TlmStatus) <= MAX_PAYLOAD, "payload too large");

// ---------- CRC-16/CCITT-FALSE ----------
inline uint16_t crc16(const uint8_t *data, size_t len, uint16_t crc = 0xFFFF) {
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

// ---------- COBS ----------
// out must hold len + len/254 + 1 bytes. Returns encoded length (no delimiter).
inline size_t cobsEncode(const uint8_t *in, size_t len, uint8_t *out) {
  size_t write = 1, codeIdx = 0;
  uint8_t code = 1;
  for (size_t read = 0; read < len; read++) {
    if (in[read] == 0) {
      out[codeIdx] = code;
      code = 1;
      codeIdx = write++;
    } else {
      out[write++] = in[read];
      if (++code == 0xFF) {
        out[codeIdx] = code;
        code = 1;
        codeIdx = write++;
      }
    }
  }
  out[codeIdx] = code;
  return write;
}

// Returns decoded length, or 0 on a malformed frame / overflow.
inline size_t cobsDecode(const uint8_t *in, size_t len, uint8_t *out, size_t outMax) {
  size_t r = 0, w = 0;
  while (r < len) {
    uint8_t code = in[r++];
    if (code == 0) return 0;
    for (uint8_t i = 1; i < code; i++) {
      if (r >= len || w >= outMax) return 0;
      out[w++] = in[r++];
    }
    if (code < 0xFF && r < len) {
      if (w >= outMax) return 0;
      out[w++] = 0;
    }
  }
  return w;
}

// Builds a complete wire frame (COBS + delimiter) into out[MAX_ENCODED].
// Returns frame length, or 0 if the payload is too long.
inline size_t buildFrame(uint8_t id, uint8_t seq, const void *payload, size_t len, uint8_t *out) {
  if (len > MAX_PAYLOAD) return 0;
  uint8_t pkt[MAX_PACKET];
  pkt[0] = id;
  pkt[1] = seq;
  if (len) memcpy(pkt + 2, payload, len);
  uint16_t crc = crc16(pkt, len + 2);
  pkt[len + 2] = (uint8_t)(crc & 0xFF);
  pkt[len + 3] = (uint8_t)(crc >> 8);
  size_t n = cobsEncode(pkt, len + 4, out);
  out[n++] = 0x00;
  return n;
}

// Decoded, CRC-checked packet view.
struct Packet {
  uint8_t id;
  uint8_t seq;
  const uint8_t *payload;
  size_t len;
};

enum ParseResult : uint8_t { PARSE_OK, PARSE_BAD_FRAME, PARSE_BAD_CRC };

// Decodes one COBS frame (delimiter already stripped) into scratch[MAX_PACKET].
// On PARSE_BAD_CRC, pkt.id / pkt.seq are filled but unverified.
inline ParseResult parseFrame(const uint8_t *frame, size_t len, uint8_t *scratch, Packet &pkt) {
  size_t m = cobsDecode(frame, len, scratch, MAX_PACKET);
  if (m < 4) return PARSE_BAD_FRAME;
  pkt.id = scratch[0];
  pkt.seq = scratch[1];
  pkt.payload = scratch + 2;
  pkt.len = m - 4;
  uint16_t rx = (uint16_t)scratch[m - 2] | ((uint16_t)scratch[m - 1] << 8);
  return (crc16(scratch, m - 2) == rx) ? PARSE_OK : PARSE_BAD_CRC;
}

// Fixed-size FIFO of encoded frames (no heap). The firmware parks must-deliver frames
// (ACKs, logs) here when the USB buffer is momentarily full, and retries each loop pass.
template <size_t N>
struct FrameQueue {
  static_assert(MAX_ENCODED <= 255, "frame length stored in a uint8_t");
  uint8_t data[N][MAX_ENCODED];
  uint8_t len[N];
  size_t head = 0;
  size_t count = 0;

  bool empty() const { return count == 0; }
  bool full() const { return count == N; }
  bool push(const uint8_t *frame, size_t n) {
    if (full() || n == 0 || n > MAX_ENCODED) return false;
    size_t i = (head + count) % N;
    memcpy(data[i], frame, n);
    len[i] = (uint8_t)n;
    count++;
    return true;
  }
  const uint8_t *frontData() const { return data[head]; }
  size_t frontLen() const { return len[head]; }
  void pop() {
    if (count) { head = (head + 1) % N; count--; }
  }
};

// Copies a command payload into T if the length matches exactly.
template <typename T>
inline bool takePayload(const Packet &p, T &out) {
  if (p.len != sizeof(T)) return false;
  memcpy(&out, p.payload, sizeof(T));
  return true;
}

}  // namespace proto
