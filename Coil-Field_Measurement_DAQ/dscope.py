"""
dscope.py - control a DreamSourceLab DScope (e.g. DScope B20) from Python.

Talks to the small native `dscope_helper` program (built from helper/),
which uses DSView's own capture library. DSView must be CLOSED while this
runs, because only one program can own the USB device.

    from dscope import DScope

    with DScope() as scope:
        scope.open()                      # first DScope found
        scope.configure(
            channels={0: dict(vdiv_mV=500), 1: dict(vdiv_mV=1000)},
            window_s=5e-3,                # at least 5 ms per frame
            trigger=dict(source="ch1", level_V=0.5, slope="rising",
                         pre_pct=20))
        scope.arm()                       # now fire the stimulator
        frame = scope.wait(timeout_s=10)
        t, v0 = frame.t, frame.volts[0]

Conversion from raw ADC codes to volts follows DSView:
    V = (hw_offset - raw) * vdiv * factor * 10 divisions / (ref_max - ref_min)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
VDIVS = 10  # vertical divisions on screen (DS_CONF_DSO_VDIVS)


class DScopeError(RuntimeError):
    pass


class TriggerTimeout(DScopeError):
    pass


def find_helper(path: str | None = None) -> str:
    cands = [path, os.environ.get("DSCOPE_HELPER"),
             os.path.join(HERE, "helper", "build", "dscope_helper"),
             os.path.join(HERE, "helper", "build", "dscope_helper.exe"),
             os.path.join(HERE, "dscope_helper"),
             shutil.which("dscope_helper")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise DScopeError(
        "dscope_helper not found. Build it first (see README): "
        "cmake -S helper -B helper/build && cmake --build helper/build")


@dataclass
class Frame:
    meta: dict
    raw: dict                     # channel index -> uint8 array
    volts: dict                   # channel index -> float array [V]
    t: np.ndarray                 # time [s], 0 = trigger
    clipped: dict                 # channel index -> bool array (at ADC limit)
    settings: dict = field(default_factory=dict)

    @property
    def samplerate(self) -> float:
        return float(self.meta["samplerate"])

    @property
    def triggered(self) -> bool:
        return bool(self.meta["trig_flag"])

    def full_scale(self, ch: int) -> float:
        """Half-screen range of a channel in volts (+-5 divisions)."""
        c = self._ch(ch)
        return c["vdiv"] * c["factor"] * VDIVS / 2 / 1000.0

    def _ch(self, ch):
        for c in self.meta["channels"]:
            if c["index"] == ch:
                return c
        raise KeyError(ch)


class DScope:
    """Thin wrapper over the dscope_helper process."""

    def __init__(self, helper: str | None = None, res_dir: str | None = None,
                 log_level: int = 1, verbose: bool = False):
        exe = find_helper(helper)
        args = [exe]
        if res_dir:
            args += ["--res", res_dir]
        env = dict(os.environ, DSCOPE_LOG=str(log_level))
        self.verbose = verbose
        self._p = subprocess.Popen(args, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env,
                                   bufsize=0)
        self._log = []
        self._t = threading.Thread(target=self._pump_stderr, daemon=True)
        self._t.start()
        self._read_reply()                     # "OK {"ready":1}"
        self.info_cache: dict | None = None
        self.probe_factor = {0: 1, 1: 1}

    # ---------------------------------------------------------------- I/O
    def _pump_stderr(self):
        for line in iter(self._p.stderr.readline, b""):
            s = line.decode(errors="replace").rstrip()
            self._log.append(s)
            del self._log[:-200]
            if self.verbose:
                print("  [helper]", s, file=sys.stderr)

    def recent_log(self, n=15) -> str:
        return "\n".join(self._log[-n:])

    def _readline(self) -> str:
        line = self._p.stdout.readline()
        if not line:
            raise DScopeError("helper exited unexpectedly:\n" +
                              self.recent_log())
        return line.decode(errors="replace").rstrip("\r\n")

    def _read_reply(self):
        line = self._readline()
        if line.startswith("OK"):
            rest = line[2:].strip()
            return json.loads(rest) if rest else {}
        if line.startswith("ERR"):
            msg = line[3:].strip()
            if msg.startswith("timeout"):
                raise TriggerTimeout(msg)
            raise DScopeError(msg)
        if line.startswith("FRAME"):
            meta = json.loads(line[5:].strip())
            data = self._read_exact(meta["nbytes"])
            return self._make_frame(meta, data)
        raise DScopeError(f"unexpected reply: {line!r}")

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._p.stdout.read(n - len(buf))
            if not chunk:
                raise DScopeError("helper closed during frame transfer")
            buf += chunk
        return bytes(buf)

    def cmd(self, line: str):
        if self._p.poll() is not None:
            raise DScopeError("helper is not running:\n" + self.recent_log())
        self._p.stdin.write((line.strip() + "\n").encode())
        self._p.stdin.flush()
        return self._read_reply()

    def close(self):
        if self._p.poll() is None:
            try:
                self._p.stdin.write(b"quit\n")
                self._p.stdin.flush()
                self._p.wait(timeout=5)
            except Exception:
                self._p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ device
    def list(self) -> list:
        return self.cmd("list")["devices"]

    def open(self, which: str | int | None = None) -> dict:
        """Open by index or name substring (default: first '*Scope*')."""
        self.info_cache = self.cmd(f"open {which}" if which is not None
                                   else "open")
        return self.info_cache

    def info(self) -> dict:
        self.info_cache = self.cmd("info")
        return self.info_cache

    def set(self, *parts):
        return self.cmd("set " + " ".join(str(p) for p in parts))

    # --------------------------------------------------------- configure
    def configure(self, channels: dict, window_s: float | None = None,
                  samplerate: float | None = None, trigger: dict | None = None):
        """
        channels: {index: dict(enabled=True, vdiv_mV=500, coupling="dc",
                               factor=1, offset_raw=None)}
                  channels not listed are disabled.
        window_s: minimum frame duration; picks the fastest sample rate that
                  still covers it (ignored if samplerate is given).
        trigger:  dict(source="ch0"|"ch1"|"auto"|"and"|"or",
                       level_V=0.2, slope="rising"|"falling", pre_pct=20,
                       margin=None)
        """
        info = self.info()
        vdivs = info.get("vdivs") or []
        for c in info["channels"]:
            idx = c["index"]
            want = channels.get(idx)
            self.set("ch", idx, "enable", 1 if want and
                     want.get("enabled", True) else 0)
        for idx, opt in channels.items():
            if not opt.get("enabled", True):
                continue
            if "vdiv_mV" in opt:
                v = int(opt["vdiv_mV"])
                if vdivs and v not in vdivs:
                    v = min(vdivs, key=lambda x: abs(x - v))
                self.set("ch", idx, "vdiv", v)
            # the scale factor is applied here in Python (the scope itself
            # doesn't use it), so any value works, e.g. 10000 for a
            # Rogowski coil -> that channel then reads in amps
            factor = float(opt.get("factor", 1))
            self.probe_factor[idx] = factor
            self.set("ch", idx, "coupling", opt.get("coupling", "dc"))
            if opt.get("offset_raw") is not None:
                self.set("ch", idx, "offset", int(opt["offset_raw"]))

        info = self.info()
        if samplerate is None and window_s is not None:
            samplerate = self.pick_samplerate(window_s, info)
        if samplerate is not None:
            self.set("samplerate", int(samplerate))

        trigger = trigger or {}
        self.set("trigsrc", trigger.get("source", "auto"))
        self.set("slope", trigger.get("slope", "rising"))
        self.set("trigpos", int(trigger.get("pre_pct", 20)))
        if trigger.get("margin") is not None:
            self.set("margin", int(trigger["margin"]))
        src = trigger.get("source", "auto")
        if src != "auto" and trigger.get("level_V") is not None:
            for idx in ([0, 1] if src in ("and", "or") else
                        [int(src[-1])]):
                raw = self.volts_to_raw(idx, float(trigger["level_V"]))
                self.set("ch", idx, "trigraw", raw)
        return self.info()

    def pick_samplerate(self, window_s, info=None):
        info = info or self.info()
        n = info["samples"]
        nch = max(1, sum(c["enabled"] for c in info["channels"]))
        cap = info["max_samplerate"] / nch if info.get("max_samplerate") \
            else float("inf")
        rates = sorted(r for r in info.get("samplerates", []) if r <= cap)
        good = [r for r in rates if n / r >= window_s]
        if good:
            return good[-1]
        return rates[0] if rates else None

    def volts_to_raw(self, ch: int, volts: float) -> int:
        info = self.info_cache or self.info()
        c = next(c for c in info["channels"] if c["index"] == ch)
        span = info["ref_max"] - info["ref_min"]
        factor = self.probe_factor.get(ch, c["factor"] or 1)
        zero = self._zero(c)
        raw = zero - volts * 1000.0 * span / (c["vdiv"] * factor * VDIVS)
        # DSView keeps the trigger level inside 6 % .. 94.5 % of the screen
        lo = info["ref_min"] + 0.06 * span
        hi = info["ref_min"] + 0.945 * span
        return int(round(min(max(raw, lo), hi)))

    # ------------------------------------------------------------ capture
    def arm(self, settle_ms: int = 30):
        """Start acquiring; returns when the scope is ready for a trigger."""
        self.cmd(f"arm {int(settle_ms)}")

    def wait(self, timeout_s: float = 10.0, accept_untriggered=False) -> Frame:
        """Wait for the next triggered frame (scope stops afterwards)."""
        return self.cmd(f"wait {int(timeout_s * 1000)}"
                        + (" any" if accept_untriggered else ""))

    def capture(self, timeout_s: float = 10.0, accept_untriggered=False):
        self.arm()
        return self.wait(timeout_s, accept_untriggered)

    def disarm(self):
        self.cmd("disarm")

    # ---------------------------------------------------------- decoding
    def _zero(self, c: dict) -> float:
        """Raw ADC code of 0 V: the hardware-measured zero on a real scope
        (as DSView uses), the configured offset otherwise."""
        real = (self.info_cache or {}).get("dev_type") == 3   # USB device
        if real and c.get("hw_offset"):
            return c["hw_offset"]
        return c["offset"]

    def _make_frame(self, meta: dict, data: bytes) -> Frame:
        nch, n = meta["nch"], meta["num_samples"]
        arr = np.frombuffer(data, dtype=np.uint8)[: nch * n].reshape(n, nch)
        span = meta["ref_max"] - meta["ref_min"]
        raw, volts, clipped = {}, {}, {}
        for k, c in enumerate(meta["channels"]):
            idx = c["index"]
            factor = self.probe_factor.get(idx, c["factor"] or 1)
            c["factor"] = factor
            r = arr[:, k].copy()
            zero = self._zero(c)
            scale = c["vdiv"] * factor * VDIVS / span / 1000.0
            raw[idx] = r
            volts[idx] = (zero - r.astype(np.float64)) * scale
            clipped[idx] = (r <= meta["ref_min"]) | (r >= meta["ref_max"])
        sr = float(meta["samplerate"])
        i0 = n * meta.get("trigpos", 0) / 100.0
        t = (np.arange(n) - i0) / sr
        return Frame(meta=meta, raw=raw, volts=volts, t=t, clipped=clipped,
                     settings=dict(self.info_cache or {}))
