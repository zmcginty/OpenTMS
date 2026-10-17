"""
pulse_analysis.py - measurements on one captured TMS pulse.

Works on plain numpy arrays, so it can be used on live frames from
dscope.py, on saved .npz files, or on simulated data.

Search-coil background
----------------------
A pickup coil gives v(t) = N*A * dB/dt.  So
  * peak |v|            -> peak dB/dt          (= peak |v| / NA)
  * integral of v dt    -> change in B         (= flux / NA)
The running integral of the baseline-corrected voltage is the B-field
waveform (up to the constant N*A). Give `na_m2` (turns x area in m^2) to get
results in T/s and T.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


def smooth(v: np.ndarray, n: int) -> np.ndarray:
    """Centred moving average over n samples (n <= 1 returns v)."""
    if n <= 1:
        return v
    k = np.ones(int(n)) / int(n)
    pad = int(n) // 2
    vp = np.pad(v, (pad, int(n) - 1 - pad), mode="edge")
    return np.convolve(vp, k, mode="valid")


@dataclass
class PulseResult:
    baseline_V: float
    noise_rms_V: float
    vmax_V: float
    t_vmax_s: float
    vmin_V: float
    t_vmin_s: float
    vpp_V: float
    vpeak_V: float              # signed value of the larger excursion
    t_peak_s: float
    area_Vs: float              # integral of v over the pulse window
    flux_peak_Vs: float         # largest |running integral| (signed)
    t_flux_peak_s: float
    window_start_s: float
    window_end_s: float
    snr: float
    clipped: bool
    ref_amp: float | None = None    # fit of this channel onto the reference
    ref_r2: float | None = None     # shape match 0..1 (1 = identical shape)
    dBdt_peak_Tps: float | None = None
    B_peak_T: float | None = None
    B_per_A_TpA: float | None = None  # current reference + na_m2 only

    def as_dict(self):
        return asdict(self)


def find_pulse_window(t, v0, noise, frac=0.05, k_noise=6.0, pad_s=20e-6):
    """Region where |v0| rises above max(frac*peak, k_noise*noise)."""
    a = np.abs(v0)
    thr = max(frac * a.max(), k_noise * noise)
    idx = np.where(a > thr)[0]
    if idx.size == 0:
        return t[0], t[-1]
    return t[idx[0]] - pad_s, t[idx[-1]] + pad_s


def analyse(t: np.ndarray, v: np.ndarray, *,
            clipped: np.ndarray | None = None,
            baseline_window: tuple | None = None,
            pulse_window: tuple | None = None,
            smooth_s: float = 0.0,
            ref: np.ndarray | None = None,
            ref_is_current: bool = False,
            na_m2: float | None = None) -> PulseResult:
    """
    t, v            time [s] (0 = trigger) and voltage [V]
    baseline_window (t0, t1) used for the zero level and noise; default:
                    the pre-trigger part, stopping 10 us before the trigger
    pulse_window    (t0, t1) analysed region; default: found automatically
    smooth_s        moving-average length [s] applied before analysis.
                    ~0.5-1 us removes most 8-bit quantisation noise at
                    50-100 MS/s without changing a ~100 us TMS pulse.
    ref             reference waveform on the same time base (e.g. the
                    other channel); gives a least-squares amplitude
    ref_is_current  the reference is the coil CURRENT (Rogowski with
                    integrator, in A): then the probe's running integral
                    (proportional to B) is fitted to it, giving ref_amp in
                    V*s per A, and with na_m2 the field per amp B_per_A [T/A]
    na_m2           search-coil turns x area [m^2] -> dB/dt [T/s] and B [T]
    """
    dt = float(np.median(np.diff(t)))
    n_s = int(round(smooth_s / dt)) if smooth_s > 0 else 1
    vs = smooth(v.astype(np.float64), n_s)

    if baseline_window is None:
        pre = t < -10e-6
        if pre.sum() < 20:                    # little/no pre-trigger data
            pre = np.zeros_like(t, dtype=bool)
            pre[: max(20, len(t) // 20)] = True
    else:
        pre = (t >= baseline_window[0]) & (t <= baseline_window[1])
    baseline = float(np.median(vs[pre]))
    v0 = vs - baseline
    noise = float(np.std(v0[pre])) if pre.sum() > 2 else 0.0

    if pulse_window is None:
        w0, w1 = find_pulse_window(t, np.where(pre, 0.0, v0), noise)
    else:
        w0, w1 = pulse_window
    w = (t >= w0) & (t <= w1)
    if w.sum() < 3:
        w = ~pre
    tw, vw = t[w], v0[w]

    i_max, i_min = int(np.argmax(vw)), int(np.argmin(vw))
    vmax, vmin = float(vw[i_max]), float(vw[i_min])
    if abs(vmax) >= abs(vmin):
        vpeak, t_peak = vmax, float(tw[i_max])
    else:
        vpeak, t_peak = vmin, float(tw[i_min])

    flux = np.concatenate([[0.0], np.cumsum(0.5 * (vw[1:] + vw[:-1])
                                            * np.diff(tw))])
    i_f = int(np.argmax(np.abs(flux)))

    res = PulseResult(
        baseline_V=baseline, noise_rms_V=noise,
        vmax_V=vmax, t_vmax_s=float(tw[i_max]),
        vmin_V=vmin, t_vmin_s=float(tw[i_min]),
        vpp_V=vmax - vmin, vpeak_V=vpeak, t_peak_s=t_peak,
        area_Vs=float(flux[-1]), flux_peak_Vs=float(flux[i_f]),
        t_flux_peak_s=float(tw[i_f]),
        window_start_s=float(tw[0]), window_end_s=float(tw[-1]),
        snr=abs(vpeak) / noise if noise > 0 else float("inf"),
        clipped=bool(clipped[w].any()) if clipped is not None else False)

    if ref is not None:
        rs = smooth(ref.astype(np.float64), n_s)
        r0 = rs - np.median(rs[pre])
        rw = r0[w]
        y = flux if ref_is_current else vw     # probe B-shape vs current
        den = float(np.dot(rw, rw))
        if den > 0:
            a = float(np.dot(y, rw) / den)
            resid = y - a * rw
            tot = float(np.dot(y, y))
            res.ref_amp = a
            res.ref_r2 = 1.0 - float(np.dot(resid, resid)) / tot \
                if tot > 0 else 0.0

    if na_m2:
        res.dBdt_peak_Tps = vpeak / na_m2
        res.B_peak_T = res.flux_peak_Vs / na_m2
        if ref_is_current and res.ref_amp is not None:
            res.B_per_A_TpA = res.ref_amp / na_m2
    return res


def running_flux(t, v, baseline=0.0):
    """Cumulative integral of (v - baseline) dt, same length as t."""
    v0 = v - baseline
    return np.concatenate([[0.0], np.cumsum(0.5 * (v0[1:] + v0[:-1])
                                            * np.diff(t))])


# --------------------------------------------------------------------------
# Synthetic TMS pulse (for testing without hardware)
# --------------------------------------------------------------------------
def synthetic_pulse(samplerate=50e6, duration=4e-3, pre=0.2, f=3.5e3,
                    amp_V=1.0, decay=0.15, noise_V=0.01, vdiv_mV=500,
                    bits=8, offset_V=0.0, seed=None, quantize=True):
    """
    Search-coil voltage for a damped biphasic TMS pulse, quantised like an
    8-bit scope at the given V/div. Returns t, v, raw, clipped.
    With quantize=False, v is the exact (noise-free if noise_V=0) signal.
    """
    rng = np.random.default_rng(seed)
    n = int(duration * samplerate)
    t = (np.arange(n) - pre * n) / samplerate
    w = 2 * np.pi * f
    on = (t >= 0) & (t <= 1.0 / f)               # one full cycle
    # coil current ~ sin(wt) * damping; probe sees dI/dt
    i_t = np.where(on, np.sin(w * t) * np.exp(-decay * w * t), 0.0)
    v = np.gradient(i_t, t)
    v = amp_V * v / np.abs(v).max() + offset_V
    v = v + rng.normal(0, noise_V, n)
    if not quantize:
        return t, v, None, np.zeros(n, dtype=bool)
    span = 254
    lsb = vdiv_mV * 10 / 1000 / span
    zero = 128
    raw = np.clip(np.round(zero - v / lsb), 1, 255).astype(np.uint8)
    vq = (zero - raw.astype(float)) * lsb
    clipped = (raw <= 1) | (raw >= 255)
    return t, vq, raw, clipped
