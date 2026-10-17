# DScope TMS pulse capture

Capture and measure TMS pulses with a DreamSourceLab DScope (written for the
DScope B20) from Python, without DSView's window.

```
dscope_daq/
  build.sh               builds the native capture helper
  helper/                dscope_helper.c + CMakeLists.txt (uses DSView's capture library)
  dscope.py              Python control of the scope (open, configure, arm, capture)
  pulse_analysis.py      peak, peak-to-peak, ∫V dt (∝ B), noise, reference fit
  dscope_tms.py          command-line tool: measure pulses, save CSV/waveforms, plot
  teensy_fire/           optional Teensy sketch: fire the stimulator on command
```

## 1. Install and build (macOS)

```bash
brew install cmake pkg-config glib libusb
pip install numpy matplotlib          # + pyserial if you use --fire-serial
cd dscope_daq
bash build.sh                         # downloads DSView's source the first time
```

On Linux, install `cmake pkg-config libglib2.0-dev libusb-1.0-0-dev zlib1g-dev`
first. Without root you also need DSView's udev rule.

## 2. First run

**Quit DSView first.** Only one program can use the scope at a time.

```bash
python dscope_tms.py selftest          # checks the analysis, no scope needed
python dscope_tms.py list              # should show "DSCope B20"
python dscope_tms.py info              # opens it and prints its settings
```

The scope needs DSView's firmware files. The helper looks for them inside
`/Applications/DSView.app` automatically. If `list` or `info` fails, find the
folder and pass it with `--res`:

```bash
find /Applications/DSView.app -name "DSCope20.bin"
python dscope_tms.py info --res "/Applications/DSView.app/Contents/MacOS/res"
```
(Or set it once with `export DSCOPE_RES=...`.) Add `--verbose` to see the
library's log.

## 3. Measuring pulses

Suggested wiring:
- **CH0**: the search-coil probe.
- **CH1**: a reference, used as the trigger. This can be a second pickup coil
  fixed near the TMS coil, a Rogowski coil, or the stimulator's trigger-out.

```bash
# 5 pulses, trigger on CH1 rising through 0.3 V, plot the last one
python dscope_tms.py measure --count 5 --plot

# let it pick the V/div ranges first (fire a few pulses when asked)
python dscope_tms.py measure --autorange --count 10 --save-waveforms

# probe only, trigger on the probe itself
python dscope_tms.py measure --no-ch1 --trig-level 0.1 --ch0-vdiv 200

# Teensy fires the stimulator after each arm
python dscope_tms.py measure --fire-serial /dev/tty.usbmodem1101 --count 20

# report dB/dt and B for a probe with N*A = 4 turns * 3.1 mm^2
python dscope_tms.py measure --na 1.25e-5
```

### With a Rogowski coil (coil current) on CH1

Give CH1 the Rogowski's scale factor so it reads in **amps**. The trigger
level is then in amps too. Tell the tool that the reference is a current:

```bash
# Rogowski giving 1 V per 10 kA: CH1 at 100 mV/div = 1 kA/div (±5 kA)
python dscope_tms.py measure --ref-type current --ch1-factor 10000 \
    --ch1-vdiv 100 --trig-src ch1 --trig-level 300 --count 5 --plot
# first half-cycle negative (Rogowski clipped on the other way)?
#   --trig-level -300 --slope falling
```

The probe sees dB/dt, while the Rogowski sees the current I, so the tool
fits the probe's **∫V dt** (∝ B) to the current. `ref_amp` is then in
**V·s per A**. With `--na`, the CSV column `B_per_A_TpA` gives the **field
per amp** at the probe position. This is independent of stimulator
intensity and pulse-to-pulse variation, and it compares directly with the
model: `tms_coil_fields.py` B at its `--current`, divided by that current.

For each pulse, the tool arms the scope, waits for the trigger, measures, and
prints one line. At the end it prints mean ± std and saves results to
`dscope_runs/`:
- `*_pulses.csv`: one row per pulse per channel.
- `*_settings.json`: the settings used.
- `*_waveforms.npz`: the waveforms, if you used `--save-waveforms`.

What gets measured on each channel, after subtracting the pre-trigger
baseline:

| column | meaning |
|---|---|
| `vpeak_V`, `t_peak_s` | the larger excursion (signed) and when it happened. For a search coil this is peak dB/dt × N·A |
| `vmax_V`, `vmin_V`, `vpp_V` | positive and negative peaks, and peak-to-peak |
| `flux_peak_Vs` | the largest value of the running ∫V dt. This is peak B × N·A, and **the most robust number**, because integrating averages out the 8‑bit noise |
| `area_Vs` | ∫V dt over the whole pulse (net change in B; ≈ 0 for a full biphasic pulse) |
| `ref_amp`, `ref_r2` | least-squares fit of CH0 onto the reference waveform on CH1. `ref_amp` is the probe-to-reference ratio, which cancels pulse-to-pulse variation and coil heating. `ref_r2` near 1 means the shapes match |
| `noise_rms_V`, `snr`, `clipped` | quality checks. Clipped pulses are flagged; use a larger V/div |
| `dBdt_peak_Tps`, `B_peak_T` | only with `--na`: absolute values for the probe on CH0 |

Useful options: `--window` (minimum frame length in ms; this sets the sample
rate), `--pre` (percentage of the frame before the trigger), `--smooth` (µs of
averaging before measuring; 0.5–1 µs removes the 8-bit noise bias on peaks),
`--pulse-window T0 T1` (fix the analysis window instead of detecting the pulse
automatically). Run `python dscope_tms.py -h` for the full list.

## 4. Using it from your own scan script

```python
from dscope import DScope
import pulse_analysis as pa

with DScope() as scope:
    scope.open()
    scope.configure(channels={0: dict(vdiv_mV=200), 1: dict(vdiv_mV=1000)},
                    window_s=4e-3,
                    trigger=dict(source="ch1", level_V=0.3, pre_pct=20))
    for point in scan_points:
        gantry.move_to(point)                  # your code
        scope.arm()
        fire_stimulator()                      # your code (or manual)
        f = scope.wait(timeout_s=10)
        r = pa.analyse(f.t, f.volts[0], clipped=f.clipped[0],
                       ref=f.volts[1], smooth_s=0.5e-6)
        save(point, r.flux_peak_Vs, r.ref_amp, r.clipped)
```

## How it works

DSView has no scripting interface, but its capture library (`libsigrok4DSL`)
has a C API. `dscope_helper` links that library and gives it a simple
line-based command protocol. `dscope.py` runs the helper and turns raw ADC
codes into volts with the same formula DSView uses:

    V = (zero_code - raw) × V/div × probe_factor × 10 / (ref_max - ref_min)

`arm` starts the scope in its normal triggered mode. `wait` returns the first
frame the scope marks as triggered, then stops the scope, so every
measurement comes from a fresh arm.

## Limits and things to check on your first run

- **Tested on the library's demo device only**, not on a real B20. Before
  trusting the numbers, compare one capture against DSView: apply the same
  V/div and the same pulse, and check that the peak voltages match.
- If triggered pulses on screen keep giving "no trigger" timeouts, try
  `--accept-any`. This takes the first frame regardless of the trigger flag.
  Use it only with a channel trigger, not `auto`.
- The B20 has **2 channels**. For a 3-axis probe, either fire one pulse per
  axis (switch axes between pulses with a relay or analog switch), or measure
  two axes per pulse without a reference channel.
- 8-bit ADC: aim for the pulse to fill at least half the screen
  (`--autorange` does this), and rely on `flux_peak_Vs` / `ref_amp` rather
  than single-sample peaks.
- Frame length: the scope captures a fixed number of samples per frame. The
  tool picks the fastest sample rate that still covers `--window`.
- License: the helper builds DSView's library, which is GPL‑3.0. Fine for
  your own use; the GPL applies if you distribute binaries.

## Troubleshooting

| symptom | fix |
|---|---|
| `device ... not found` | DSView still open? Unplug and replug the scope, then run `list` again |
| errors mentioning firmware / `.fw` / `.bin` | pass DSView's `res` folder with `--res` (see step 2) |
| `no trigger within ... s` | check the trigger channel and level (in volts), the slope, and that the reference signal actually reaches the level |
| every pulse `CLIPPED` | increase that channel's `--chN-vdiv`, or use `--autorange` |
| `helper exited unexpectedly` | run again with `--verbose` and look at the last log lines |
