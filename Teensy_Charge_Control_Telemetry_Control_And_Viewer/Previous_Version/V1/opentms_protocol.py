"""
OpenTMS Charge_Control binary protocol, version 1 -- host side.

Mirrors Protocol.h in the firmware. Change both together.

  wire frame = COBS(packet) + 0x00
  packet     = id(u8) seq(u8) payload crc16(u16 LE)
  crc16      = CRC-16/CCITT-FALSE over id+seq+payload (binascii.crc_hqx(data, 0xFFFF))

  IDs 0x01-0x7F are commands (host -> device, each answered by one ACK),
  0x80-0xFF are telemetry (device -> host).

Telemetry structs may grow by appending fields: this module parses the prefix it
knows and ignores extra bytes, so older host code keeps working with newer firmware.

Library use (no GUI dependencies):

    import serial
    from opentms_protocol import Link, Cmd, Tlm, RATE_EVERY_BATCH
    link = Link(serial.Serial(port, timeout=0))
    link.send(Cmd.SET_TELEM_RATE, Tlm.RAW, RATE_EVERY_BATCH)
    for pkt in link.poll():
        if pkt.id == Tlm.RAW:
            print(pkt.fields["sum"] / pkt.fields["n"])

Headless raw capture:

    python opentms_protocol.py /dev/cu.usbmodemXXXX --seconds 10 --raw-csv raw.csv
"""

import argparse
import binascii
import struct
import sys
import time
from enum import IntEnum
from typing import NamedTuple, Optional

PROTO_VERSION = 1
MAX_PAYLOAD = 96
RATE_EVERY_BATCH = 0xFFFF
RATE_OFF = 0


class Cmd(IntEnum):
    PING = 0x01
    SET_SETPOINT = 0x02
    CHARGE_OFF = 0x03
    GET_SETPOINT = 0x04
    SET_AVG = 0x05
    SET_TELEM_RATE = 0x06
    REQUEST_TELEM = 0x07


class Tlm(IntEnum):
    ACK = 0x80
    FAST = 0x81
    RAW = 0x82
    STATUS = 0x83
    LOG = 0x84
    HELLO = 0x85


class AckStatus(IntEnum):
    OK = 0
    BAD_CRC = 1
    BAD_LEN = 2
    UNKNOWN_CMD = 3
    OUT_OF_RANGE = 4
    BAD_ARG = 5
    BAD_FRAME = 6


class LogLevel(IntEnum):
    INFO = 0
    WARN = 1
    ERROR = 2


# Command payload formats (struct, little-endian). "" = no payload.
CMD_FORMATS = {
    Cmd.PING: "<I",            # token
    Cmd.SET_SETPOINT: "<f",    # cap volts
    Cmd.CHARGE_OFF: "",
    Cmd.GET_SETPOINT: "",
    Cmd.SET_AVG: "<H",         # batch size
    Cmd.SET_TELEM_RATE: "<BH", # tlm id, rate Hz (0 = off, 65535 = every batch)
    Cmd.REQUEST_TELEM: "<B",   # tlm id
}

RAW_FIELDS = ("batch_seq", "t_us", "sum", "n", "min_code", "max_code",
              "parity_errors", "rvs_timeouts", "a0_counts", "a1_counts", "int_fresh")

STATUS_FIELDS = ("t_ms", "v_cap_slow", "v_cap_int_avg", "set_v", "dac_meas", "dac_int",
                 "read_dac_set_volt", "loop_hz", "pp_cap_v", "dac_err_ads", "dac_err_int",
                 "n_avg", "pp_codes", "parity_errors", "rvs_timeouts", "cfg_faults",
                 "intadc_errors", "tx_dropped", "rx_errors", "flags")

# Telemetry layouts: id -> (struct format, field names). TLM_LOG is variable length.
TLM_LAYOUTS = {
    Tlm.ACK: ("<BBBfI", ("cmd_id", "cmd_seq", "status", "value", "aux")),
    Tlm.FAST: ("<IIffB", ("batch_seq", "t_us", "v_cap", "v_cap_int", "flags")),
    Tlm.RAW: ("<IIiHiiHHHHB", RAW_FIELDS),
    Tlm.STATUS: ("<I10fHi6IH", STATUS_FIELDS),
    Tlm.HELLO: ("<BBfHHHHH", ("proto_version", "reserved", "setpoint_max", "n_avg",
                              "n_avg_max", "rate_fast", "rate_raw", "rate_status")),
}
# Must match the static_asserts in Protocol.h.
_EXPECTED_SIZES = {Tlm.ACK: 11, Tlm.FAST: 17, Tlm.RAW: 31, Tlm.STATUS: 76, Tlm.HELLO: 16}
_TLM_STRUCTS = {}
for _tid, (_fmt, _names) in TLM_LAYOUTS.items():
    _s = struct.Struct(_fmt)
    assert _s.size == _EXPECTED_SIZES[_tid], f"{_tid.name} size {_s.size} != firmware"
    assert len(_names) == len(_s.unpack(bytes(_s.size))), f"{_tid.name} field count"
    _TLM_STRUCTS[_tid] = (_s, _names)

# TlmFast.flags
FF_CHG_PIN, FF_CHARGE_ENABLED, FF_ADS8699_CFG_OK, FF_INT_FRESH = 1, 2, 4, 8

# TlmStatus.flags
SF_CHARGE_ENABLED = 1 << 0
SF_CHG_PIN = 1 << 1
SF_ADS8699_CFG_OK = 1 << 2
SF_ADS1115_CFG_OK = 1 << 3
SF_INTADC_CFG_OK = 1 << 4
SF_DAC_ADS_VALID = 1 << 5
SF_DAC_ADS_OK = 1 << 6
SF_DAC_INT_VALID = 1 << 7
SF_DAC_INT_OK = 1 << 8
SF_RAW_ON = 1 << 9


def status_alerts(fields):
    """Human-readable flag list for a STATUS packet (empty = all healthy)."""
    f = fields["flags"]
    out = []
    if not f & SF_CHARGE_ENABLED:
        out.append("CHARGE_OFF")
    if not f & SF_ADS8699_CFG_OK:
        out.append("CFG_FAULT")
    if not f & SF_ADS1115_CFG_OK:
        out.append("ADS1115_CFG_FAULT")
    if not f & SF_INTADC_CFG_OK:
        out.append("INTADC_FAULT")
    if f & SF_RAW_ON:
        out.append("RAW_ON")
    if f & SF_DAC_ADS_VALID and not f & SF_DAC_ADS_OK:
        out.append(f"DAC_MISMATCH({fields['dac_err_ads']:+.4f}V)")
    if f & SF_DAC_INT_VALID and not f & SF_DAC_INT_OK:
        out.append(f"DAC_INT_MISMATCH({fields['dac_err_int']:+.4f}V)")
    return out


# ---------- CRC / COBS ----------

def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


class ProtocolError(Exception):
    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind   # "framing", "crc", "length"


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_idx, code = 0, 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx, code = len(out), 1
            out.append(0)
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx, code = len(out), 1
                out.append(0)
    out[code_idx] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        i += 1
        if code == 0:
            raise ProtocolError("framing", "zero byte inside COBS frame")
        end = i + code - 1
        if end > n:
            raise ProtocolError("framing", "truncated COBS block")
        out += data[i:end]
        i = end
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


# ---------- Packets ----------

class Packet(NamedTuple):
    id: int
    seq: int
    fields: Optional[dict]   # None for unknown IDs
    payload: bytes

    @property
    def name(self):
        try:
            return Tlm(self.id).name
        except ValueError:
            return f"0x{self.id:02X}"


def encode_frame(pid: int, seq: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = bytes([pid & 0xFF, seq & 0xFF]) + payload
    return cobs_encode(body + crc16(body).to_bytes(2, "little")) + b"\x00"


def encode_command(cmd: Cmd, seq: int, *args) -> bytes:
    fmt = CMD_FORMATS[Cmd(cmd)]
    if fmt:
        payload = struct.pack(fmt, *args)
    elif args:
        raise ValueError(f"{Cmd(cmd).name} takes no arguments")
    else:
        payload = b""
    return encode_frame(cmd, seq, payload)


def parse_payload(pid: int, payload: bytes) -> Optional[dict]:
    if pid == Tlm.LOG:
        if not payload:
            raise ProtocolError("length", "empty LOG")
        return {"level": payload[0], "text": payload[1:].decode("utf-8", errors="replace")}
    entry = _TLM_STRUCTS.get(pid)
    if entry is None:
        return None
    s, names = entry
    if len(payload) < s.size:
        raise ProtocolError("length", f"0x{pid:02X} payload {len(payload)} < {s.size}")
    return dict(zip(names, s.unpack_from(payload)))


def decode_frame(frame: bytes) -> Packet:
    """Decode one COBS frame (delimiter already removed)."""
    pkt = cobs_decode(frame)
    if len(pkt) < 4:
        raise ProtocolError("framing", "packet too short")
    body, rx_crc = pkt[:-2], int.from_bytes(pkt[-2:], "little")
    if crc16(body) != rx_crc:
        raise ProtocolError("crc", "CRC mismatch")
    pid, seq, payload = body[0], body[1], body[2:]
    return Packet(pid, seq, parse_payload(pid, payload), payload)


class Decoder:
    """Stream decoder: feed bytes, get packets. Keeps link-quality counters."""
    MAX_BUFFER = 8192

    def __init__(self):
        self.buf = bytearray()
        self.packets = 0
        self.crc_errors = 0
        self.framing_errors = 0
        self.length_errors = 0
        self.unknown = 0
        self.lost = 0           # seq gaps: dropped on the device, or corrupted in transit
        self._last_seq = None

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(0)
            if i < 0:
                break
            frame = bytes(self.buf[:i])
            del self.buf[:i + 1]
            if not frame:
                continue
            try:
                pkt = decode_frame(frame)
            except ProtocolError as e:
                if e.kind == "crc":
                    self.crc_errors += 1
                elif e.kind == "length":
                    self.length_errors += 1
                else:
                    self.framing_errors += 1
                continue
            if self._last_seq is not None:
                self.lost += (pkt.seq - self._last_seq - 1) & 0xFF
            self._last_seq = pkt.seq
            self.packets += 1
            if pkt.fields is None:
                self.unknown += 1
            out.append(pkt)
        if len(self.buf) > self.MAX_BUFFER:   # no delimiter for a long time: garbage
            self.buf.clear()
            self.framing_errors += 1
        return out

    def stats(self):
        return {"packets": self.packets, "lost": self.lost, "crc_errors": self.crc_errors,
                "framing_errors": self.framing_errors, "length_errors": self.length_errors,
                "unknown": self.unknown}


class PendingCmd(NamedTuple):
    cmd: Cmd
    seq: int
    args: tuple
    t_sent: float


class Link:
    """Serial link: send commands (tracks ACKs), poll for packets. Non-blocking."""

    def __init__(self, ser):
        self.ser = ser
        self.decoder = Decoder()
        self._seq = 0
        self.pending = {}   # seq -> PendingCmd

    def send(self, cmd: Cmd, *args) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        self.ser.write(encode_command(cmd, seq, *args))
        self.pending[seq] = PendingCmd(Cmd(cmd), seq, args, time.monotonic())
        return seq

    def poll(self):
        """Read everything waiting and return decoded packets. ACK packets get
        fields['request'] = the PendingCmd they answer (or None)."""
        n = self.ser.in_waiting
        data = self.ser.read(n) if n else b""
        pkts = self.decoder.feed(data) if data else []
        for p in pkts:
            if p.id == Tlm.ACK and p.fields is not None:
                req = None
                if p.fields["status"] not in (AckStatus.BAD_CRC, AckStatus.BAD_FRAME):
                    req = self.pending.pop(p.fields["cmd_seq"], None)
                p.fields["request"] = req
        return pkts

    def overdue(self, timeout_s: float):
        """Remove and return commands with no ACK after timeout_s."""
        now = time.monotonic()
        late = [p for p in self.pending.values() if now - p.t_sent > timeout_s]
        for p in late:
            del self.pending[p.seq]
        return late

    def reset(self):
        self.decoder = Decoder()
        self.pending.clear()


def describe_ack(f) -> str:
    req = f.get("request")
    try:
        cmd = Cmd(f["cmd_id"]).name
    except ValueError:
        cmd = f"0x{f['cmd_id']:02X}"
    try:
        status = AckStatus(f["status"]).name
    except ValueError:
        status = str(f["status"])
    text = f"ACK {cmd} {status}"
    if req is not None and req.cmd in (Cmd.SET_SETPOINT, Cmd.CHARGE_OFF, Cmd.GET_SETPOINT):
        text += f" setpoint={f['value']:.1f} V"
    elif req is not None and req.cmd == Cmd.SET_AVG:
        text += f" n_avg={int(f['value'])}"
    elif req is not None and req.cmd == Cmd.SET_TELEM_RATE:
        text += f" {Tlm(req.args[0]).name}={rate_str(int(f['value']))}"
    return text


def rate_str(rate: int) -> str:
    if rate == RATE_OFF:
        return "off"
    if rate == RATE_EVERY_BATCH:
        return "every batch"
    return f"{rate} Hz"


def parse_rate(text: str) -> int:
    """'batch' / 'off' / integer Hz -> wire rate value (for argparse)."""
    t = text.strip().lower()
    if t == "batch":
        return RATE_EVERY_BATCH
    if t == "off":
        return RATE_OFF
    v = int(t)
    if not 0 <= v < RATE_EVERY_BATCH:
        raise argparse.ArgumentTypeError("rate must be 0-65534 Hz, 'off' or 'batch'")
    return v


# ---------- Headless raw capture ----------

def _capture_main():
    import serial

    p = argparse.ArgumentParser(description="Capture RAW integer telemetry to CSV (no GUI)")
    p.add_argument("port")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--raw-csv", required=True)
    p.add_argument("--raw-rate", type=parse_rate, default=RATE_EVERY_BATCH,
                   help="'batch' (default), 'off', or Hz")
    p.add_argument("--avg", type=int, default=None, help="ADS8699 batch size to set first")
    args = p.parse_args()

    ser = serial.Serial(args.port, timeout=0, write_timeout=0.5)
    time.sleep(0.5)
    ser.reset_input_buffer()
    link = Link(ser)
    link.send(Cmd.REQUEST_TELEM, Tlm.HELLO)
    if args.avg is not None:
        link.send(Cmd.SET_AVG, args.avg)
    link.send(Cmd.SET_TELEM_RATE, Tlm.RAW, args.raw_rate)

    rows = 0
    t_end = time.monotonic() + args.seconds
    last_report = 0.0
    try:
        with open(args.raw_csv, "w", buffering=1 << 16) as f:
            f.write(",".join(RAW_FIELDS) + "\n")
            while time.monotonic() < t_end:
                for pkt in link.poll():
                    if pkt.id == Tlm.RAW:
                        f.write(",".join(str(pkt.fields[k]) for k in RAW_FIELDS) + "\n")
                        rows += 1
                    elif pkt.id == Tlm.LOG:
                        print(f"[{LogLevel(pkt.fields['level']).name}] {pkt.fields['text']}")
                    elif pkt.id == Tlm.ACK:
                        print(describe_ack(pkt.fields))
                    elif pkt.id == Tlm.HELLO:
                        print(f"HELLO {pkt.fields}")
                    elif pkt.id == Tlm.STATUS and time.monotonic() - last_report > 1.0:
                        last_report = time.monotonic()
                        s = pkt.fields
                        print(f"loop {s['loop_hz']:.0f} Hz  n_avg {s['n_avg']}  "
                              f"pp {s['pp_codes']} codes  perr {s['parity_errors']}  "
                              f"rows {rows}  {' '.join(status_alerts(s))}")
                for late in link.overdue(1.0):
                    print(f"NO ACK for {late.cmd.name}")
                time.sleep(0.005)
    finally:
        try:
            link.send(Cmd.SET_TELEM_RATE, Tlm.RAW, RATE_OFF)
            time.sleep(0.05)
        except Exception:
            pass
        ser.close()
        print(f"{rows} RAW rows -> {args.raw_csv}   link: {link.decoder.stats()}")


if __name__ == "__main__":
    sys.exit(_capture_main())
