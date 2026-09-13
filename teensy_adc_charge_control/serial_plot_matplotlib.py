"""
Live serial voltage plotter -- robust version.

Builds on serial_debug.py: same port default, same malformed-line filtering,
but adds a live matplotlib plot of raw vs filtered voltage.

Uses a plain plt.ion() + manual redraw loop instead of matplotlib.animation,
since that's simpler to reason about and avoids backend/threading quirks.

Usage:
    python serial_plot_live.py
    python serial_plot_live.py --port /dev/cu.usbmodem135769401 --baud 115200

Stop with Ctrl+C (close the plot window also stops the loop).
"""

import argparse
import re
import sys
import time
from collections import deque
from statistics import median

import serial
import matplotlib.pyplot as plt

plt.rcParams['path.simplify'] = True
plt.rcParams['path.simplify_threshold'] = 0.05

# Matches a clean "float,float" line -- anything else is a torn/malformed read
CLEAN_LINE_RE = re.compile(r"^[-+]?\d*\.?\d+,[-+]?\d*\.?\d+$")
# Matches a clean single "float" line, used when --single is passed
SINGLE_LINE_RE = re.compile(r"^[-+]?\d*\.?\d+$")


def parse_args():
    p = argparse.ArgumentParser(description="Live plot raw vs filtered voltage from serial port")
    p.add_argument("--port", default="/dev/cu.usbmodem176191001", help="Serial port")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    p.add_argument("--window", type=int, default=1000,
                   help="Number of most recent samples to show on screen (default 1000)")
    p.add_argument("--ymin", type=float, default=None, help="Fixed y-axis min (optional)")
    p.add_argument("--ymax", type=float, default=None, help="Fixed y-axis max (optional)")
    p.add_argument("--save-csv", type=str, default=None,
                   help="Optional path to log all clean samples to a CSV file")
    p.add_argument("--refresh", type=float, default=0.05,
                   help="Seconds between plot redraws (default 0.05)")
    p.add_argument("--max-abs-volts", type=float, default=None,
                   help="Discard any sample where |raw| or |filtered| exceeds this value "
                        "(e.g. --max-abs-volts 10 drops anything outside -10..+10V)")
    p.add_argument("--max-jump", type=float, default=None,
                   help="Discard any sample where |raw| or |filtered| jumps more than this "
                        "much from the recent rolling average (e.g. --max-jump 0.5)")
    p.add_argument("--jump-window", type=int, default=5,
                   help="Number of recent accepted samples used for the rolling average "
                        "that --max-jump compares against (default 5)")
    p.add_argument("--debug", action="store_true",
                   help="Print every received line to the console, including malformed ones")
    p.add_argument("--single", action="store_true",
                   help="Parse one voltage value per line instead of 'raw,filtered' pairs "
                        "-- plots a single trace")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"Active filters: max_abs_volts={args.max_abs_volts}, "
          f"max_jump={args.max_jump}, jump_window={args.jump_window}, "
          f"single={args.single}", flush=True)

    print(f"Opening {args.port} @ {args.baud} baud ...", flush=True)
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"FAILED to open port: {e}", flush=True)
        print("Common causes: wrong port name, another program (Arduino Serial Monitor, "
              "minicom, screen, etc.) already has it open, or a permissions issue "
              "(Linux: add your user to the 'dialout' group).", flush=True)
        sys.exit(1)

    print("Port opened. Waiting for board to settle...", flush=True)
    time.sleep(1.5)
    ser.reset_input_buffer()
    ser.timeout = 0.05  # short timeout so the plot loop stays responsive between reads

    csv_file = None
    if args.save_csv:
        csv_file = open(args.save_csv, "w", buffering=1)
        csv_file.write("t,voltage\n" if args.single else "t,raw,filtered\n")

    xs = deque(maxlen=args.window)
    ys_raw = deque(maxlen=args.window)
    ys_filt = deque(maxlen=args.window)
    sample_count = 0
    malformed_count = 0
    out_of_range_count = 0
    jump_rejected_count = 0
    recent_raw = deque(maxlen=args.jump_window)
    recent_filt = deque(maxlen=args.jump_window)
    start_time = time.time()

    plt.ion()
    fig, ax = plt.subplots()
    if args.single:
        line_filt, = ax.plot([], [], lw=1.5, color="tab:blue", label="voltage")
        line_raw = None
    else:
        line_raw, = ax.plot([], [], lw=1, alpha=0.5, color="tab:orange", label="raw")
        line_filt, = ax.plot([], [], lw=1.5, color="tab:blue", label="filtered")
    ax.set_xlabel("Sample #")
    ax.set_ylabel("Voltage (V)")
    ax.set_title(f"Live serial voltage -- {args.port} @ {args.baud} baud")
    if args.ymin is not None or args.ymax is not None:
        ax.set_ylim(args.ymin, args.ymax)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    fig.show()

    last_draw = 0.0
    last_heartbeat = 0.0
    print("Streaming... close the plot window or Ctrl+C in the terminal to stop.\n", flush=True)

    try:
        while plt.fignum_exists(fig.number):
            # Don't rely on in_waiting -- it's unreliable for USB CDC serial on macOS
            # (can report 0 even when bytes are actually available). Just call readline()
            # directly, repeatedly, until it comes back empty (short timeout above).
            for _ in range(200):  # cap per-iteration reads so the plot still redraws regularly
                try:
                    raw_bytes = ser.readline()
                except serial.SerialException as e:
                    print(f"\nSerial error: {e}", flush=True)
                    print("Device likely disconnected/reset. Attempting to reconnect...",
                          flush=True)
                    ser.close()
                    reconnected = False
                    for attempt in range(30):  # retry for ~15s
                        time.sleep(0.5)
                        try:
                            ser.open()
                            ser.reset_input_buffer()
                            reconnected = True
                            print("Reconnected.", flush=True)
                            break
                        except serial.SerialException:
                            continue
                    if not reconnected:
                        print("Could not reconnect after 15s. Check the USB cable/port "
                              "and whether the Teensy is resetting or crashing.", flush=True)
                        raise
                    break  # go back to outer loop and keep going
                if not raw_bytes:
                    break  # nothing more waiting right now
                decoded = raw_bytes.decode(errors="replace").strip()
                if not decoded:
                    continue

                if args.single:
                    if not SINGLE_LINE_RE.match(decoded):
                        malformed_count += 1
                        if args.debug:
                            print(f"  <-- MALFORMED: {decoded!r}", flush=True)
                        continue
                    raw_val = filt_val = float(decoded)  # one value, used for both checks
                else:
                    if not CLEAN_LINE_RE.match(decoded):
                        malformed_count += 1
                        if args.debug:
                            print(f"  <-- MALFORMED: {decoded!r}", flush=True)
                        continue  # skip torn/corrupted lines rather than trying to salvage them
                    raw_val_str, filt_val_str = decoded.split(",")
                    raw_val = float(raw_val_str)
                    filt_val = float(filt_val_str)

                if args.max_abs_volts is not None and (
                    abs(raw_val) > args.max_abs_volts or abs(filt_val) > args.max_abs_volts
                ):
                    out_of_range_count += 1
                    if args.debug:
                        print(f"  <-- OUT OF RANGE: raw={raw_val} filtered={filt_val}",
                              flush=True)
                    continue  # drop the whole sample if either value is a wild outlier

                if args.max_jump is not None and recent_raw:
                    baseline_raw = median(recent_raw)
                    baseline_filt = median(recent_filt)
                    if (abs(raw_val - baseline_raw) > args.max_jump
                            or abs(filt_val - baseline_filt) > args.max_jump):
                        jump_rejected_count += 1
                        if args.debug:
                            if args.single:
                                print(f"  <-- JUMP REJECTED: voltage={filt_val} "
                                      f"(baseline {baseline_filt:.4f})", flush=True)
                            else:
                                print(f"  <-- JUMP REJECTED: raw={raw_val} (baseline "
                                      f"{baseline_raw:.4f}) filtered={filt_val} (baseline "
                                      f"{baseline_filt:.4f})", flush=True)
                        continue  # skip -- don't add to recent_raw/recent_filt either,
                        # so a single wild sample can't drag the rolling baseline with it

                recent_raw.append(raw_val)
                recent_filt.append(filt_val)

                xs.append(sample_count)
                ys_raw.append(raw_val)
                ys_filt.append(filt_val)
                if csv_file:
                    if args.single:
                        csv_file.write(f"{sample_count},{filt_val}\n")
                    else:
                        csv_file.write(f"{sample_count},{raw_val},{filt_val}\n")
                if args.debug:
                    if args.single:
                        print(f"[{sample_count}] voltage={filt_val}", flush=True)
                    else:
                        print(f"[{sample_count}] raw={raw_val}  filtered={filt_val}", flush=True)
                sample_count += 1

            now = time.time()
            if now - last_draw > args.refresh and xs:
                last_draw = now
                if not args.single:
                    line_raw.set_data(xs, ys_raw)
                line_filt.set_data(xs, ys_filt)
                ax.relim()
                ax.autoscale_view(scalex=True, scaley=(args.ymin is None and args.ymax is None))
                ax.set_title(
                    f"Live serial voltage -- {args.port} @ {args.baud} baud  "
                    f"(n={sample_count}, malformed={malformed_count}, "
                    f"out_of_range={out_of_range_count}, jump_rejected={jump_rejected_count})"
                )
                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            if not xs and time.time() - start_time > 5:
                print("Still no valid data after 5s. Check the port, baud rate, and that "
                      "nothing else has the port open. Run with --debug to see raw lines.",
                      flush=True)
                start_time = time.time() + 1e9  # only warn once

            if args.debug and now - last_heartbeat > 1.0:
                last_heartbeat = now
                print(f"  (heartbeat: loop alive, t={now - start_time:.1f}s, "
                      f"samples={sample_count}, malformed={malformed_count}, "
                      f"out_of_range={out_of_range_count}, "
                      f"jump_rejected={jump_rejected_count})", flush=True)

            plt.pause(0.0001)  # yields to the GUI event loop briefly

    except KeyboardInterrupt:
        pass
    finally:
        total = sample_count + malformed_count + out_of_range_count + jump_rejected_count
        malformed_pct = (malformed_count / total * 100) if total else 0
        oor_pct = (out_of_range_count / total * 100) if total else 0
        jump_pct = (jump_rejected_count / total * 100) if total else 0
        print(f"\nStopped. {sample_count} clean samples, {malformed_count} malformed "
              f"({malformed_pct:.1f}%), {out_of_range_count} out-of-range ({oor_pct:.1f}%), "
              f"{jump_rejected_count} jump-rejected ({jump_pct:.1f}%).")
        ser.close()
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
