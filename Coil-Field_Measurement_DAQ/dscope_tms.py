#!/usr/bin/env python3
"""
dscope_tms.py - capture and measure TMS pulses with a DScope.

Typical wiring
  CH0  search-coil probe (twisted pair, differential if you can)
  CH1  reference: second pickup coil fixed near the TMS coil, a Rogowski
       coil, or the stimulator's trigger-out. CH1 is the trigger.

Commands
  list        show connected devices
  info        open the scope and print its settings
  measure     capture pulses and print/save measurements (default)
  selftest    run the analysis on simulated pulses (no hardware needed)

Examples
  python dscope_tms.py list
  python dscope_tms.py measure --count 5 --plot
  python dscope_tms.py measure --trig-src ch0 --trig-level 0.2 --ch0-vdiv 200
  python dscope_tms.py measure --autorange --count 10 --out runs/probe_test
  python dscope_tms.py measure --fire-serial /dev/tty.usbmodem1101 --count 20
  python dscope_tms.py selftest --plot

Close DSView first: only one program can use the scope at a time.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import numpy as np

import pulse_analysis as pa

FIELDS = ["pulse", "channel", "host_time", "triggered", "vpeak_V",
          "t_peak_s", "vmax_V", "vmin_V", "vpp_V", "area_Vs",
          "flux_peak_Vs", "noise_rms_V", "snr", "clipped", "ref_amp",
          "ref_r2", "dBdt_peak_Tps", "B_peak_T", "B_per_A_TpA", "vdiv_mV",
          "samplerate"]


def current_ch(args):
    """Channel that carries the coil current (Rogowski), or None."""
    return args.ref if getattr(args, "ref_type", "voltage") == "current" \
        else None


def unit_of(ch, args):
    return "A" if ch == current_ch(args) else "V"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def fmt_si(x, unit, digits=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    if x == 0:
        return f"0 {unit}"
    for p, s in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"),
                 (1e-6, "u"), (1e-9, "n")):
        if abs(x) >= p:
            return f"{x / p:.{digits}g} {s}{unit}"
    return f"{x:.{digits}g} {unit}"


class Firer:
    """Optional: tell a Teensy/Arduino to fire the stimulator over serial."""

    def __init__(self, port, cmd, baud):
        try:
            import serial  # pyserial
        except ImportError:
            sys.exit("--fire-serial needs pyserial:  pip install pyserial")
        self.s = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)                    # boards reset on connect
        self.cmd = cmd.encode().decode("unicode_escape").encode()

    def fire(self):
        self.s.write(self.cmd)
        self.s.flush()


def analyse_frame(frame, args):
    """Measurements for every enabled channel of one frame."""
    out = {}
    ref_ch = args.ref
    for ch, v in frame.volts.items():
        ref = None
        if ref_ch is not None and ref_ch != ch and ref_ch in frame.volts:
            ref = frame.volts[ref_ch]
        pw = tuple(args.pulse_window) if args.pulse_window else None
        out[ch] = pa.analyse(frame.t, v, clipped=frame.clipped[ch],
                             pulse_window=pw, smooth_s=args.smooth * 1e-6,
                             ref=ref, ref_is_current=current_ch(args) is not None,
                             na_m2=args.na if ch == 0 else None)
    if current_ch(args) in out and 0 in out:
        # analyse the current over the probe's pulse window, not its own
        r0 = out[0]
        out[current_ch(args)] = pa.analyse(
            frame.t, frame.volts[current_ch(args)],
            clipped=frame.clipped[current_ch(args)],
            pulse_window=(r0.window_start_s, r0.window_end_s),
            smooth_s=args.smooth * 1e-6)
    return out


def print_header(chs, args):
    cols = []
    for c in chs:
        if c == current_ch(args):
            cols.append(f"{'CH'+str(c)+' I peak':>11s}")
        else:
            cols.append(f"{'CH'+str(c)+' peak':>11s} {'flux':>10s}")
    print(f"{'#':>4s}  {'  '.join(cols)}  notes")


def print_row(i, frame, res, args):
    parts, notes = [], []
    for ch, r in res.items():
        u = unit_of(ch, args)
        if ch == current_ch(args):
            parts.append(f"{fmt_si(r.vpeak_V, u):>11s}")
        else:
            parts.append(f"{fmt_si(r.vpeak_V, u):>11s} "
                         f"{fmt_si(r.flux_peak_Vs, 'Vs'):>10s}")
        if r.clipped:
            notes.append(f"CH{ch} CLIPPED")
        elif abs(r.vpeak_V) < 0.15 * frame.full_scale(ch):
            notes.append(f"CH{ch} small")
        if r.ref_r2 is not None:
            fu = " Vs/A" if current_ch(args) is not None else ""
            notes.append(f"CH{ch} fit {r.ref_amp:+.4g}{fu} "
                         f"(r²={r.ref_r2:.3f})")
        if r.B_per_A_TpA is not None:
            notes.append(f"{r.B_per_A_TpA * 1e6:+.4g} µT/A")
        if r.B_peak_T is not None:
            notes.append(f"B≈{fmt_si(r.B_peak_T, 'T')} "
                         f"dB/dt≈{fmt_si(r.dBdt_peak_Tps, 'T/s')}")
    if not frame.triggered:
        notes.append("untriggered")
    print(f"{i:4d}  " + "  ".join(parts) + "  " + "; ".join(notes))


def summarise(rows, args):
    print("\nSummary (mean ± std over pulses)")
    chans = sorted({r["channel"] for r in rows})
    for ch in chans:
        rr = [r for r in rows if r["channel"] == ch]
        u = unit_of(ch, args)
        keys = (("vpeak_V", u), ("vpp_V", u)) if ch == current_ch(args) \
            else (("vpeak_V", "V"), ("vpp_V", "V"), ("flux_peak_Vs", "Vs"),
                  ("ref_amp", "Vs/A" if current_ch(args) is not None else ""),
                  ("B_peak_T", "T"), ("B_per_A_TpA", "T/A"))
        for key, unit in keys:
            vals = np.array([r[key] for r in rr if r[key] is not None],
                            dtype=float)
            if vals.size == 0:
                continue
            m, s = vals.mean(), vals.std(ddof=1) if vals.size > 1 else 0.0
            rel = f" ({100 * s / abs(m):.2f} %)" if m else ""
            print(f"  CH{ch} {key:13s} {fmt_si(m, unit):>12s} ± "
                  f"{fmt_si(s, unit)}{rel}")


def plot_frame(frame, res, title, block=True, fig=None, cur=None):
    """cur: index of a channel carrying coil current [A] (drawn on its own
    axis, and overlaid on the probe's B-shape scaled by the fit)."""
    import matplotlib.pyplot as plt
    if fig is None:
        fig = plt.figure(figsize=(11, 7), constrained_layout=True)
    fig.clf()
    ax0 = fig.add_subplot(2, 1, 1)
    ax1 = fig.add_subplot(2, 1, 2, sharex=ax0)
    axes = [ax0, ax1]
    t_us = frame.t * 1e6
    colors = {0: "#1f77b4", 1: "#d62728"}
    for ch, v in frame.volts.items():
        r = res[ch]
        c = colors.get(ch, "k")
        if ch == cur:
            axI = ax0.twinx()
            axI.plot(t_us, (v - r.baseline_V) / 1e3, lw=1.0, color=c,
                     label=f"CH{ch} current")
            axI.set_ylabel("coil current [kA]", color=c)
            axI.legend(loc="lower right", fontsize=8)
            fit = res.get(0)
            if fit is not None and fit.ref_amp is not None:
                ax1.plot(t_us, (v - r.baseline_V) * fit.ref_amp * 1e6,
                         lw=1.0, ls="--", color=c,
                         label=f"CH{ch} current × fit (r²={fit.ref_r2:.3f})")
            continue
        ax0.plot(t_us, v - r.baseline_V, lw=0.8, color=c, label=f"CH{ch}")
        ax0.plot(r.t_peak_s * 1e6, r.vpeak_V, "o", color=c)
        flux = pa.running_flux(frame.t, v, r.baseline_V)
        ax1.plot(t_us, flux * 1e6, lw=1.2, color=c, label=f"CH{ch} ∫V dt")
        if frame.clipped[ch].any():
            ax0.plot(t_us[frame.clipped[ch]],
                     (v - r.baseline_V)[frame.clipped[ch]], "x",
                     color="orange", ms=3, label=f"CH{ch} clipped")
    r0 = res[0] if 0 in res else next(iter(res.values()))
    for a in axes:
        a.axvspan(r0.window_start_s * 1e6, r0.window_end_s * 1e6,
                  color="0.85", zorder=0)
        a.axvline(0, color="k", lw=0.6, ls="--")
        a.grid(alpha=0.3)
        a.legend(loc="upper right", fontsize=8)
    axes[0].set_ylabel("probe voltage [V]  (∝ dB/dt)")
    axes[1].set_ylabel("∫V dt [µV·s]  (∝ B)")
    axes[1].set_xlabel("time from trigger [µs]")
    span = max(r0.window_end_s - r0.window_start_s, 50e-6)
    axes[1].set_xlim((r0.window_start_s - 0.5 * span) * 1e6,
                     (r0.window_end_s + 0.5 * span) * 1e6)
    fig.suptitle(title)
    if block:
        plt.show()
    else:
        plt.pause(0.05)
    return fig


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def open_scope(args):
    from dscope import DScope
    scope = DScope(res_dir=args.res, verbose=args.verbose,
                   log_level=3 if args.verbose else 1)
    info = scope.open(args.device)
    if info.get("dev_type") != 3 and args.device is None:
        print(f"note: opened '{info['name']}' (no real DScope found?)")
    return scope, info


def configure(scope, args, vdiv=None):
    vdiv = vdiv or {0: args.ch0_vdiv, 1: args.ch1_vdiv}
    chans = {0: dict(vdiv_mV=vdiv[0], coupling=args.coupling,
                     factor=args.ch0_factor)}
    if not args.no_ch1:
        chans[1] = dict(vdiv_mV=vdiv[1], coupling=args.coupling,
                        factor=args.ch1_factor)
    trig = dict(source=args.trig_src, level_V=args.trig_level,
                slope=args.slope, pre_pct=args.pre)
    return scope.configure(chans, window_s=args.window * 1e-3,
                           trigger=trig)


def take(scope, args, firer=None, quiet=False):
    scope.arm(settle_ms=args.settle)
    if firer:
        firer.fire()
    elif not quiet:
        print("  armed - fire the stimulator ...", end="", flush=True)
    frame = scope.wait(args.timeout, accept_untriggered=args.accept_any)
    if not firer and not quiet:
        print("\r" + " " * 40 + "\r", end="")
    return frame


def autorange(scope, args, firer):
    """Pick the most sensitive V/div that keeps each pulse on screen."""
    info = scope.info()
    vdivs = sorted(info["vdivs"])
    vdiv = {0: vdivs[-1], 1: vdivs[-1]}
    print("Autorange: needs a few pulses. Starting at the largest range.")
    for attempt in range(6):
        configure(scope, args, vdiv)
        frame = take(scope, args, firer)
        res = analyse_frame(frame, args)
        changed = False
        for ch, r in res.items():
            peak = max(abs(r.vmax_V), abs(r.vmin_V)) + abs(r.baseline_V)
            if r.clipped:
                new = vdivs[min(vdivs.index(vdiv[ch]) + 2, len(vdivs) - 1)]
            else:
                factor = frame._ch(ch)["factor"]
                # keep the peak within ~4 of the 5 half-screen divisions
                fits = [d for d in vdivs if peak <= 4 * d * factor / 1000]
                new = fits[0] if fits else vdivs[-1]
            print(f"  CH{ch}: peak {fmt_si(peak, 'V')} at "
                  f"{vdiv[ch]} mV/div -> {new} mV/div")
            if new != vdiv[ch]:
                vdiv[ch] = new
                changed = True
        if not changed:
            break
    args.ch0_vdiv, args.ch1_vdiv = vdiv[0], vdiv[1]
    configure(scope, args, vdiv)
    print(f"Autorange done: CH0 {vdiv[0]} mV/div, CH1 {vdiv[1]} mV/div\n")


def cmd_list(args):
    from dscope import DScope
    with DScope(res_dir=args.res, verbose=args.verbose) as s:
        for d in s.list():
            print(f"  [{d['index']}] {d['name']}")


def cmd_info(args):
    scope, info = open_scope(args)
    with scope:
        print(json.dumps(info, indent=2))


def cmd_measure(args):
    scope, info = open_scope(args)
    firer = Firer(args.fire_serial, args.fire_cmd, args.baud) \
        if args.fire_serial else None
    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(args.out, f"{args.label}_{stamp}" if args.label
                        else stamp)
    rows, live_fig = [], None

    with scope:
        print(f"Scope: {info['name']}")
        if args.autorange:
            autorange(scope, args, firer)
        info = configure(scope, args)
        n = info["samples"]
        sr = info["samplerate"]
        print(f"Frame: {n:,} samples/ch at {fmt_si(sr, 'S/s')} = "
              f"{fmt_si(n / sr, 's')}, pre-trigger {args.pre} %, "
              f"trigger {args.trig_src}"
              + (f" at {args.trig_level:g} V {args.slope}"
                 if args.trig_src != "auto" else ""))
        chs = [0] if args.no_ch1 else [0, 1]
        print_header(chs, args)

        waves = {}
        i = 0
        while args.count <= 0 or i < args.count:
            try:
                frame = take(scope, args, firer)
            except KeyboardInterrupt:
                print("\nstopped")
                break
            except Exception as e:  # timeout etc.
                if type(e).__name__ == "TriggerTimeout":
                    print(f"  no trigger within {args.timeout:g} s "
                          "(check trigger level/source) - retrying")
                    if args.stop_on_timeout:
                        break
                    continue
                raise
            res = analyse_frame(frame, args)
            print_row(i, frame, res, args)
            for ch, r in res.items():
                d = r.as_dict()
                d.update(pulse=i, channel=ch,
                         host_time=frame.meta["host_time"],
                         triggered=frame.triggered,
                         vdiv_mV=frame._ch(ch)["vdiv"],
                         samplerate=frame.samplerate)
                rows.append({k: d.get(k) for k in FIELDS})
            if args.save_waveforms:
                # keep a window around the pulse to save space
                r0 = res[0]
                span = max(r0.window_end_s - r0.window_start_s, 100e-6)
                sel = (frame.t >= r0.window_start_s - span) & \
                      (frame.t <= r0.window_end_s + span)
                waves[f"p{i}_t"] = frame.t[sel].astype(np.float32)
                for ch, v in frame.volts.items():
                    waves[f"p{i}_ch{ch}"] = v[sel].astype(np.float32)
            if args.live:
                live_fig = plot_frame(frame, res, f"pulse {i}", block=False,
                                      fig=live_fig, cur=current_ch(args))
            last = (frame, res)
            i += 1
            if args.interval > 0:
                time.sleep(args.interval)

    if not rows:
        return
    with open(base + "_pulses.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    if waves:
        np.savez_compressed(base + "_waveforms.npz", **waves)
    with open(base + "_settings.json", "w") as f:
        json.dump(dict(args=vars(args), scope=info), f, indent=2,
                  default=str)
    summarise(rows, args)
    print(f"\nsaved {base}_pulses.csv" +
          (f", {base}_waveforms.npz" if waves else ""))
    if args.plot:
        frame, res = last
        plot_frame(frame, res, f"last pulse ({len(rows)} rows saved)",
                   cur=current_ch(args))


def cmd_selftest(args):
    """Check the analysis against simulated pulses with known answers."""
    print("Self-test on simulated 8-bit captures of a biphasic TMS pulse\n")
    ok = True
    for vdiv, amp in ((500, 1.5), (200, 0.6), (50, 0.15)):
        t, v, raw, clip = pa.synthetic_pulse(amp_V=amp, vdiv_mV=vdiv,
                                             noise_V=0.02 * amp, seed=1)
        t2, v_true, _, _ = pa.synthetic_pulse(amp_V=amp, noise_V=0,
                                              seed=1, quantize=False)
        exact = pa.analyse(t2, v_true)
        for sm in (0.0, 1.0):
            r = pa.analyse(t, v, clipped=clip, smooth_s=sm * 1e-6)
            e_pk = 100 * (abs(r.vpeak_V) - abs(exact.vpeak_V)) / \
                abs(exact.vpeak_V)
            e_fx = 100 * (r.flux_peak_Vs - exact.flux_peak_Vs) / \
                abs(exact.flux_peak_Vs)
            print(f"  {vdiv:4d} mV/div, {amp:4.2f} V pulse, smooth "
                  f"{sm:.1f} us: peak error {e_pk:+6.2f} %, flux error "
                  f"{e_fx:+6.2f} %, SNR {r.snr:6.1f}, clipped {r.clipped}")
            if abs(e_fx) > 3 or r.clipped:
                ok = False
    t, v, raw, clip = pa.synthetic_pulse(amp_V=4.0, vdiv_mV=200, seed=2)
    r = pa.analyse(t, v, clipped=clip)
    print(f"\n  overload case (4 V at 200 mV/div): clipped flagged = "
          f"{r.clipped}")
    ok &= r.clipped
    # reference fit: probe = 0.37 x reference
    t, ref, _, _ = pa.synthetic_pulse(amp_V=2.0, vdiv_mV=500, seed=3)
    _, prb, _, _ = pa.synthetic_pulse(amp_V=0.74, vdiv_mV=200, seed=4)
    r = pa.analyse(t, prb, ref=ref, smooth_s=1e-6)
    print(f"  reference fit: amplitude {r.ref_amp:.4f} (expected 0.3700), "
          f"r² {r.ref_r2:.4f}")
    ok &= abs(r.ref_amp - 0.37) < 0.01
    # current reference (Rogowski): probe sees k * dI/dt, CH1 sees I in amps
    t, dv, _, _ = pa.synthetic_pulse(amp_V=1.0, noise_V=0, seed=5,
                                     quantize=False)
    i_shape = pa.running_flux(t, dv)                      # ∝ I(t)
    I = 4000.0 * i_shape / np.abs(i_shape).max()          # 4 kA peak
    k_true = 2.5e-9                                       # V·s per A
    probe = np.gradient(k_true * I, t)
    rng = np.random.default_rng(6)
    probe_q = np.round((probe + rng.normal(0, 0.01, t.size)) / 0.0197) \
        * 0.0197                                          # 500 mV/div LSB
    I_q = np.round((I + rng.normal(0, 20, t.size)) / 39.4) * 39.4
    r = pa.analyse(t, probe_q, ref=I_q, ref_is_current=True, smooth_s=1e-6,
                   na_m2=6.5e-5)
    print(f"  current-reference fit: {r.ref_amp:.4e} V·s/A (expected "
          f"{k_true:.4e}), r² {r.ref_r2:.4f}, field {r.B_per_A_TpA*1e6:.2f}"
          f" µT/A (expected {k_true / 6.5e-5 * 1e6:.2f})")
    ok &= abs(r.ref_amp / k_true - 1) < 0.02 and r.ref_r2 > 0.99
    print("\nSELF-TEST " + ("PASSED" if ok else "FAILED"))
    if args.plot:
        class F:  # minimal stand-in for a Frame
            pass
        f = F()
        t, v, raw, clip = pa.synthetic_pulse(amp_V=0.6, vdiv_mV=200, seed=1)
        f.t, f.volts, f.clipped = t, {0: v}, {0: clip}
        plot_frame(f, {0: pa.analyse(t, v, clipped=clip, smooth_s=1e-6)},
                   "simulated pulse")
    return 0 if ok else 1


# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Capture and measure TMS pulses with a DScope.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("command", nargs="?", default="measure",
                   choices=["list", "info", "measure", "selftest"])
    g = p.add_argument_group("scope")
    g.add_argument("--device", default=None,
                   help="device index or name part (default: first DScope)")
    g.add_argument("--res", default=None,
                   help="DSView 'res' folder with the firmware files")
    g.add_argument("--ch0-vdiv", type=int, default=500, help="CH0 mV/div")
    g.add_argument("--ch1-vdiv", type=int, default=1000, help="CH1 mV/div")
    g.add_argument("--ch0-factor", type=float, default=1,
                   help="CH0 probe attenuation (1 = direct, 10 = 10x probe)")
    g.add_argument("--ch1-factor", type=float, default=1,
                   help="CH1 scale: e.g. 10000 for a Rogowski giving 1 V per "
                        "10 kA (CH1 then reads in amps)")
    g.add_argument("--no-ch1", action="store_true",
                   help="use CH0 only (doubles the sample rate)")
    g.add_argument("--coupling", choices=["dc", "ac"], default="dc")
    g.add_argument("--window", type=float, default=4.0,
                   help="minimum frame length [ms]; sets the sample rate")
    g.add_argument("--autorange", action="store_true",
                   help="find the best V/div first (uses a few pulses)")
    g = p.add_argument_group("trigger")
    g.add_argument("--trig-src", default="ch1",
                   choices=["ch0", "ch1", "auto", "and", "or"])
    g.add_argument("--trig-level", type=float, default=0.3,
                   help="trigger level, in the trigger channel's units "
                        "(V, or A when that channel has a current factor)")
    g.add_argument("--slope", choices=["rising", "falling"], default="rising")
    g.add_argument("--pre", type=int, default=20,
                   help="pre-trigger part of the frame [%%]")
    g.add_argument("--timeout", type=float, default=30.0,
                   help="seconds to wait for each trigger")
    g.add_argument("--settle", type=int, default=30,
                   help="ms after arming before the scope is ready")
    g.add_argument("--accept-any", action="store_true",
                   help="also accept frames without the trigger flag")
    g = p.add_argument_group("firing (optional)")
    g.add_argument("--fire-serial", default=None,
                   help="serial port of a board that fires the stimulator")
    g.add_argument("--fire-cmd", default="F\\n",
                   help="text sent to fire (escape codes allowed)")
    g.add_argument("--baud", type=int, default=115200)
    g = p.add_argument_group("analysis")
    g.add_argument("--smooth", type=float, default=0.5,
                   help="moving-average length [us] before measuring")
    g.add_argument("--pulse-window", type=float, nargs=2, default=None,
                   metavar=("T0", "T1"),
                   help="analysis window [s] from trigger (default: auto)")
    g.add_argument("--ref-type", choices=["voltage", "current"],
                   default="voltage",
                   help="what the reference channel measures: 'voltage' "
                        "(pickup coil, same shape as the probe) or 'current'"
                        " (Rogowski/current probe: the probe's ∫V dt is fitted"
                        " to it, giving V·s/A and, with --na, T/A)")
    g.add_argument("--ref", type=int, default=1,
                   help="reference channel for the amplitude fit "
                        "(-1 = none)")
    g.add_argument("--na", type=float, default=None,
                   help="probe turns x area [m^2] -> report dB/dt and B")
    g = p.add_argument_group("run")
    g.add_argument("--count", type=int, default=5,
                   help="number of pulses (0 = until Ctrl-C)")
    g.add_argument("--interval", type=float, default=0.0,
                   help="extra pause between pulses [s]")
    g.add_argument("--stop-on-timeout", action="store_true")
    g.add_argument("--out", default="dscope_runs", help="output folder")
    g.add_argument("--label", default="", help="prefix for output files")
    g.add_argument("--save-waveforms", action="store_true",
                   help="also save the waveforms (.npz)")
    g.add_argument("--plot", action="store_true",
                   help="plot the last pulse at the end")
    g.add_argument("--live", action="store_true",
                   help="update a plot after every pulse")
    g.add_argument("--verbose", action="store_true",
                   help="show the capture library's log")
    args = p.parse_args()
    if args.ref is not None and args.ref < 0:
        args.ref = None
    if args.no_ch1 and args.ref == 1:
        args.ref = None
    if args.no_ch1 and args.trig_src in ("ch1", "and", "or"):
        print("--no-ch1: triggering on CH0 instead of", args.trig_src)
        args.trig_src = "ch0"

    if args.command == "list":
        cmd_list(args)
    elif args.command == "info":
        cmd_info(args)
    elif args.command == "selftest":
        sys.exit(cmd_selftest(args))
    else:
        cmd_measure(args)


if __name__ == "__main__":
    main()
