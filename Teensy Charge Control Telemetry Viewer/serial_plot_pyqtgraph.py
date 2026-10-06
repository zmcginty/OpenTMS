"""
Live serial voltage plotter -- pyqtgraph version (fast).

Parses one of three line formats:
  - default:  "raw,filtered" pairs
  - --single: one bare float per line
  - --key K:  labeled "K:value" fields anywhere in a line, e.g.
              "v_cap:123.456"  or  "v_cap:1.2 v_cap_slow:1.1 ..."
              Lines that don't contain K but do contain other "name:value"
              fields (e.g. a slower status/telemetry line) are shown in the
              telemetry panel above the graph instead of being plotted.

Telemetry panel (--key mode):
  Shows the latest value of every "name:value" field from the status line,
  any extra tokens on that line as flags (e.g. STALE, DAC_MISMATCH(...)),
  and how long ago the last status line arrived.

Charge setpoint control (--key mode):
  A setpoint box with Apply / Charge OFF buttons sends "SET <volts>" or
  "OFF" to the charge controller over the same serial port. The firmware's
  confirmed setpoint (from "ACK SET ..." replies and the "set_v:" status
  field) is shown next to it and drawn on the graph as a dashed line.
  Nothing is sent until you press Apply (or Enter in the box).

--hline NAME (repeatable):
  Draws a telemetry field as a horizontal dashed line on the graph, e.g.
  --hline read_dac_set_volt to see the measured comparator threshold.

Usage:
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401 --key v_cap
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401 --key v_cap --hline read_dac_set_volt
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401 --single
    python serial_plot_pyqtgraph.py COM5 --max-abs-volts 10 --max-jump 0.01 --debug

Dependencies:
    pip install pyqtgraph pyqt5 pyserial numpy
"""

import argparse
import re
import signal
import sys
import time
from collections import deque
from statistics import median

import numpy as np
import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

# Matches a clean "float,float" line -- anything else is a torn/malformed read
CLEAN_LINE_RE = re.compile(r"^[-+]?\d*\.?\d+,[-+]?\d*\.?\d+$")
# Matches a clean single "float" line, used when --single is passed
SINGLE_LINE_RE = re.compile(r"^[-+]?\d*\.?\d+$")
FLOAT_PATTERN = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
# Any "name:float" field in a line (used to parse the telemetry/status line)
FIELD_RE = re.compile(rf"(?:^|\s)([A-Za-z_]\w*):({FLOAT_PATTERN})(?=\s|$)")
# Firmware reply to SET / OFF / GET
ACK_SET_RE = re.compile(rf"^ACK SET ({FLOAT_PATTERN})")

# Friendlier names for known telemetry fields. Anything not listed is shown
# under its raw name, so new firmware fields appear automatically.
FRIENDLY_NAMES = {
    "v_cap_slow":        "Cap V (slow ADC)",
    "set_v":             "Charge setpoint (cap V)",
    "dac_meas":          "DAC measured (V)",
    "read_dac_set_volt": "DAC setpoint, measured (cap V)",
    "chg":               "Charge output (1 = on)",
    "loop_hz":           "Fast loop rate (Hz)",
}

STATUS_COLUMNS = 3          # telemetry fields per row in the panel
STATUS_STALE_AFTER_S = 1.0  # status age turns orange after this long
SETPOINT_FIELD = "set_v"    # status field holding the firmware's active setpoint
SETPOINT_CONFIRM_S = 2.0    # warn if the firmware hasn't confirmed a SET by then


def make_key_regex(key):
    """Matches 'key:<float>' as a whole field: at line start or after
    whitespace, and followed by whitespace or end of line. So --key v_cap
    matches 'v_cap:1.23' but not 'v_cap_slow:1.23'."""
    return re.compile(rf"(?:^|\s){re.escape(key)}:({FLOAT_PATTERN})(?=\s|$)")


def parse_args():
    p = argparse.ArgumentParser(description="Fast live plot of voltage from serial")
    p.add_argument("port", nargs="?", default="/dev/cu.usbmodem176191001",
                    help="Serial port device, e.g. /dev/cu.usbmodem176191001, "
                         "/dev/ttyACM0, COM5 (default: %(default)s)")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    p.add_argument("--key", type=str, default=None,
                   help="Plot the value of a labeled 'key:value' field, e.g. --key v_cap. "
                        "Other labeled lines go to the telemetry panel.")
    p.add_argument("--hline", type=str, action="append", default=[],
                   help="Draw this telemetry field as a horizontal line on the graph "
                        "(repeatable), e.g. --hline read_dac_set_volt")
    p.add_argument("--setpoint-max", type=float, default=1000.0,
                   help="Upper limit of the setpoint box, in cap volts (default 1000). "
                        "The firmware enforces its own SETPOINT_MAX_V regardless.")
    p.add_argument("--no-setpoint", action="store_true",
                   help="Hide the charge setpoint control")
    p.add_argument("--show-status", action="store_true",
                   help="With --key: also echo non-key lines to the console")
    p.add_argument("--window", type=int, default=5000,
                   help="Initial number of most recent samples visible on screen "
                        "(adjustable live via the spinbox in the GUI)")
    p.add_argument("--max-history", type=int, default=None,
                   help="Total samples kept in memory, must be >= --window "
                        "(default: 5x --window)")
    p.add_argument("--min-yspan", type=float, default=None,
                   help="Floor on the y-axis autoscale span, in volts, so the view "
                        "doesn't keep zooming in tighter as the signal settles")
    p.add_argument("--ymin", type=float, default=None, help="Fixed y-axis min (optional)")
    p.add_argument("--ymax", type=float, default=None, help="Fixed y-axis max (optional)")
    p.add_argument("--save-csv", type=str, default=None,
                   help="Optional path to log all clean samples to a CSV file")
    p.add_argument("--refresh-ms", type=int, default=20,
                   help="Milliseconds between plot redraws (default 20 = ~50 fps)")
    p.add_argument("--max-abs-volts", type=float, default=None,
                   help="Discard any sample whose absolute value exceeds this")
    p.add_argument("--max-jump", type=float, default=None,
                   help="Discard any sample that jumps more than this much from the "
                        "recent rolling median")
    p.add_argument("--jump-window", type=int, default=5,
                   help="Number of recent accepted samples used for the rolling median "
                        "that --max-jump compares against (default 5)")
    p.add_argument("--single", action="store_true",
                   help="Parse one bare voltage value per line instead of 'raw,filtered' pairs")
    p.add_argument("--warmup", type=int, default=5,
                   help="Discard this many samples at startup before recording/plotting "
                        "anything (default 5)")
    p.add_argument("--debug", action="store_true",
                   help="Print every received line / rejection reason to the console")
    return p.parse_args()


def main():
    args = parse_args()
    if args.max_history is None:
        args.max_history = max(args.window * 5, 50000)
    if args.max_history < args.window:
        args.max_history = args.window

    # --key yields one value per line, same as --single for plotting purposes.
    one_value = args.single or args.key is not None
    key_re = make_key_regex(args.key) if args.key else None
    value_label = args.key if args.key else "voltage"
    setpoint_enabled = bool(args.key) and not args.no_setpoint
    if setpoint_enabled and SETPOINT_FIELD not in args.hline:
        args.hline.append(SETPOINT_FIELD)   # always draw the setpoint on the graph

    print(f"Active filters: max_abs_volts={args.max_abs_volts}, "
          f"max_jump={args.max_jump}, jump_window={args.jump_window}, "
          f"single={args.single}, key={args.key}, hlines={args.hline}", flush=True)

    print(f"Opening {args.port} @ {args.baud} baud ...", flush=True)
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05, write_timeout=0.5)
    except serial.SerialException as e:
        print(f"FAILED to open port: {e}", flush=True)
        print("Common causes: wrong port name, another program already has it open, "
              "or a permissions issue (Linux: add your user to the 'dialout' group).",
              flush=True)
        sys.exit(1)

    print("Port opened. Waiting for board to settle...", flush=True)
    time.sleep(1.5)
    ser.reset_input_buffer()

    csv_file = None
    if args.save_csv:
        csv_file = open(args.save_csv, "w", buffering=1)
        csv_file.write(f"t,{value_label}\n" if one_value else "t,raw,filtered\n")

    # --- Data buffers ---
    xs = deque(maxlen=args.max_history)
    ys_raw = deque(maxlen=args.max_history)
    ys_filt = deque(maxlen=args.max_history)
    recent_raw = deque(maxlen=args.jump_window)
    recent_filt = deque(maxlen=args.jump_window)

    state = {
        "sample_count": 0,
        "malformed_count": 0,
        "other_count": 0,          # --key mode: lines without the key
        "out_of_range_count": 0,
        "jump_rejected_count": 0,
        "start_time": time.time(),
        "last_heartbeat": 0.0,
        "first_data_reported": False,
        "read_buffer": b"",  # leftover partial line carried between calls
        "visible_window": args.window,
        "follow": True,  # when True, view auto-scrolls/auto-scales to the latest data
        "warmup_remaining": args.warmup,
        "warmup_skipped_count": 0,
        # Telemetry (slow status line)
        "status_fields": {},       # name -> latest float, in first-seen order
        "status_flags": [],        # non-field tokens from the latest status line
        "status_time": None,       # time.time() of the latest status line
        "status_dirty": False,     # panel needs a refresh
        "last_value": None,        # latest plotted (fast) value
        # Setpoint control
        "sp_requested": None,      # last setpoint we sent (cap V)
        "sp_requested_time": 0.0,
        "sp_box_synced": False,    # box initialised from the firmware's value yet?
        "sp_box_touched": False,   # user has edited the box
        "last_reply": None,        # latest ACK/ERR line from the firmware
        "reply_dirty": False,
        "include_hlines_in_y": True,
    }

    # --- pyqtgraph setup ---
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=False)  # antialiasing costs fps, off by default for speed

    win = QtWidgets.QWidget()
    win.setWindowTitle(f"Live serial voltage -- {args.port} @ {args.baud} baud")
    win.resize(1100, 800)
    layout = QtWidgets.QVBoxLayout(win)

    # Control bar: adjustable visible window + a "follow latest" toggle so the
    # user can freely pan/zoom with the mouse without auto-scroll fighting them.
    controls = QtWidgets.QHBoxLayout()
    controls.addWidget(QtWidgets.QLabel("Visible window (samples):"))
    window_spin = QtWidgets.QSpinBox()
    window_spin.setRange(10, args.max_history)
    window_spin.setSingleStep(max(1, args.window // 20))
    window_spin.setValue(args.window)
    controls.addWidget(window_spin)

    follow_checkbox = QtWidgets.QCheckBox("Follow latest (auto-scroll/auto-scale)")
    follow_checkbox.setChecked(True)
    controls.addWidget(follow_checkbox)

    hline_y_checkbox = QtWidgets.QCheckBox("Include setpoint in Y scale")
    hline_y_checkbox.setChecked(True)
    if args.hline:
        controls.addWidget(hline_y_checkbox)
    controls.addStretch()
    layout.addLayout(controls)

    def on_window_changed(v):
        state["visible_window"] = v
    window_spin.valueChanged.connect(on_window_changed)

    def on_follow_changed(checked):
        state["follow"] = bool(checked)
    follow_checkbox.stateChanged.connect(on_follow_changed)

    def on_hline_y_changed(checked):
        state["include_hlines_in_y"] = bool(checked)
    hline_y_checkbox.stateChanged.connect(on_hline_y_changed)

    # --- Charge setpoint control ---
    sp_box = QtWidgets.QGroupBox("Charge setpoint")
    sp_row = QtWidgets.QHBoxLayout(sp_box)
    sp_row.addWidget(QtWidgets.QLabel("Set to (cap V):"))
    sp_spin = QtWidgets.QDoubleSpinBox()
    sp_spin.setRange(0.0, args.setpoint_max)
    sp_spin.setDecimals(1)
    sp_spin.setSingleStep(10.0)
    sp_spin.setKeyboardTracking(False)
    sp_spin.setMinimumWidth(110)
    sp_row.addWidget(sp_spin)
    sp_apply_btn = QtWidgets.QPushButton("Apply")
    sp_row.addWidget(sp_apply_btn)
    sp_off_btn = QtWidgets.QPushButton("Charge OFF (0 V)")
    sp_off_btn.setStyleSheet("font-weight: bold;")
    sp_row.addWidget(sp_off_btn)
    sp_row.addSpacing(20)
    sp_fw_label = QtWidgets.QLabel("Firmware setpoint: waiting...")
    sp_fw_label.setStyleSheet("font-family: monospace; font-size: 15px; font-weight: bold;")
    sp_row.addWidget(sp_fw_label)
    sp_row.addSpacing(20)
    sp_reply_label = QtWidgets.QLabel("")
    sp_reply_label.setStyleSheet("font-family: monospace; font-size: 12px; color: gray;")
    sp_row.addWidget(sp_reply_label)
    sp_row.addStretch()

    if setpoint_enabled:
        layout.addWidget(sp_box)

    def send_command(text):
        try:
            ser.write((text + "\n").encode("ascii"))
            print(f">> {text}", flush=True)
            return True
        except (serial.SerialException, serial.SerialTimeoutException) as e:
            print(f"Failed to send '{text}': {e}", flush=True)
            state["last_reply"] = f"SEND FAILED: {e}"
            state["reply_dirty"] = True
            return False

    def apply_setpoint():
        sp_spin.interpretText()   # pick up a value typed but not yet committed
        v = round(sp_spin.value(), 1)
        if send_command(f"SET {v:.1f}"):
            state["sp_requested"] = v
            state["sp_requested_time"] = time.time()

    def charge_off():
        sp_spin.setValue(0.0)
        if send_command("OFF"):
            state["sp_requested"] = 0.0
            state["sp_requested_time"] = time.time()

    def on_spin_edited():
        state["sp_box_touched"] = True

    sp_apply_btn.clicked.connect(apply_setpoint)
    sp_off_btn.clicked.connect(charge_off)
    sp_spin.lineEdit().returnPressed.connect(apply_setpoint)
    sp_spin.lineEdit().textEdited.connect(lambda _t: on_spin_edited())
    sp_spin.valueChanged.connect(lambda _v: on_spin_edited())

    # --- Telemetry panel ---
    # A boxed grid of "name: value" labels, created on the fly as new fields
    # show up, plus a flags line and a status-age line.
    status_box = QtWidgets.QGroupBox("Telemetry")
    status_outer = QtWidgets.QVBoxLayout(status_box)
    status_grid = QtWidgets.QGridLayout()
    status_grid.setHorizontalSpacing(24)
    status_outer.addLayout(status_grid)

    value_style = "font-family: monospace; font-size: 14px;"
    big_label = QtWidgets.QLabel(f"{value_label} (fast): --")
    big_label.setStyleSheet("font-family: monospace; font-size: 20px; font-weight: bold;")
    status_grid.addWidget(big_label, 0, 0, 1, STATUS_COLUMNS)

    flags_label = QtWidgets.QLabel("")
    flags_label.setStyleSheet("font-family: monospace; font-size: 14px; "
                              "color: #e04040; font-weight: bold;")
    status_outer.addWidget(flags_label)

    age_label = QtWidgets.QLabel("status: waiting for telemetry...")
    age_label.setStyleSheet("font-family: monospace; font-size: 12px; color: gray;")
    status_outer.addWidget(age_label)

    field_labels = {}  # field name -> QLabel

    if args.key:
        layout.addWidget(status_box)

    plot_widget = pg.PlotWidget()
    layout.addWidget(plot_widget, stretch=1)
    plot = plot_widget.getPlotItem()
    plot.setLabel("bottom", "Sample #")
    plot.setLabel("left", "Voltage (V)")
    plot.showGrid(x=True, y=True, alpha=0.3)
    plot.addLegend()
    # We drive the view range manually (see update()) instead of pyqtgraph's
    # continuous autoRange, which otherwise re-fits every frame and makes the
    # plot look like it keeps "shrinking" as the signal settles/smooths out.
    plot.enableAutoRange(x=False, y=False)

    if one_value:
        curve_filt = plot.plot(pen=pg.mkPen("c", width=1.5), name=value_label)
        curve_raw = None
    else:
        curve_raw = plot.plot(pen=pg.mkPen((255, 170, 60, 150), width=1), name="raw")
        curve_filt = plot.plot(pen=pg.mkPen("c", width=1.5), name="filtered")

    # Horizontal reference lines for --hline fields, created when the field
    # first appears in telemetry.
    hline_colors = ["m", "y", "g", "r", "w"]
    hlines = {}  # field name -> pg.InfiniteLine

    win.show()

    # Ask the firmware for its current setpoint (the status line reports it
    # too, so this is just to show it sooner).
    if setpoint_enabled:
        send_command("GET")

    def handle_status_line(decoded):
        """Parse a non-key line. Returns True if it looked like telemetry
        (had at least one name:value field)."""
        fields = FIELD_RE.findall(decoded)
        if not fields:
            return False
        for name, val in fields:
            state["status_fields"][name] = float(val)
        leftover = FIELD_RE.sub(" ", decoded).split()
        state["status_flags"] = leftover
        state["status_time"] = time.time()
        state["status_dirty"] = True
        return True

    def handle_reply(decoded):
        """Handle an 'ACK ...' / 'ERR ...' reply from the firmware."""
        state["last_reply"] = decoded
        state["reply_dirty"] = True
        m = ACK_SET_RE.match(decoded)
        if m:
            state["status_fields"][SETPOINT_FIELD] = float(m.group(1))
            state["status_dirty"] = True
        print(decoded, flush=True)

    def refresh_setpoint_panel():
        if state["reply_dirty"]:
            state["reply_dirty"] = False
            reply = state["last_reply"] or ""
            is_err = not reply.startswith("ACK")
            sp_reply_label.setStyleSheet(
                "font-family: monospace; font-size: 12px; "
                + ("color: #e04040; font-weight: bold;" if is_err else "color: gray;"))
            sp_reply_label.setText(reply)

        fw = state["status_fields"].get(SETPOINT_FIELD)
        if fw is None:
            return
        if not state["sp_box_synced"]:
            state["sp_box_synced"] = True
            if not state["sp_box_touched"]:
                sp_spin.blockSignals(True)
                sp_spin.setValue(fw)
                sp_spin.blockSignals(False)

        req = state["sp_requested"]
        pending = (req is not None and abs(fw - req) > 0.05)
        if fw < 1.0:
            text, color = f"Firmware setpoint: {fw:.1f} V  (charging OFF)", "gray"
        else:
            text, color = f"Firmware setpoint: {fw:.1f} V", "#30c030"
        if pending:
            late = (time.time() - state["sp_requested_time"]) > SETPOINT_CONFIRM_S
            text += f"  (requested {req:.1f} V{' -- NOT CONFIRMED' if late else '...'})"
            color = "orange"
        sp_fw_label.setStyleSheet(
            f"font-family: monospace; font-size: 15px; font-weight: bold; color: {color};")
        sp_fw_label.setText(text)

    def refresh_status_panel():
        if state["last_value"] is not None:
            big_label.setText(f"{value_label} (fast): {state['last_value']:.3f}")

        if state["status_dirty"]:
            state["status_dirty"] = False
            for name, val in state["status_fields"].items():
                lbl = field_labels.get(name)
                if lbl is None:
                    lbl = QtWidgets.QLabel()
                    lbl.setStyleSheet(value_style)
                    idx = len(field_labels)
                    status_grid.addWidget(lbl, 1 + idx // STATUS_COLUMNS,
                                          idx % STATUS_COLUMNS)
                    field_labels[name] = lbl
                lbl.setText(f"{FRIENDLY_NAMES.get(name, name)}: {val:.4f}")

                if name in args.hline:
                    line = hlines.get(name)
                    if line is None:
                        color = hline_colors[len(hlines) % len(hline_colors)]
                        line = pg.InfiniteLine(
                            pos=val, angle=0, movable=False,
                            pen=pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine),
                            label=f"{name} = {{value:.2f}}",
                            labelOpts={"position": 0.05, "color": color},
                        )
                        plot.addItem(line)
                        hlines[name] = line
                    else:
                        line.setValue(val)

            flags_label.setText("  ".join(state["status_flags"]))

        if state["status_time"] is not None:
            age = time.time() - state["status_time"]
            color = "orange" if age > STATUS_STALE_AFTER_S else "gray"
            age_label.setStyleSheet(f"font-family: monospace; font-size: 12px; color: {color};")
            age_label.setText(f"status: last update {age:.1f} s ago")

        if setpoint_enabled:
            refresh_setpoint_panel()

    def read_and_process():
        """Drain whatever serial data is available, applying all filters.

        Reads large raw chunks and splits on newlines ourselves, rather than
        calling ser.readline() -- pyserial's readline() reads one byte at a
        time (one syscall per byte), which is disastrously slow at high line
        rates. A single ser.read(4096) call can harvest hundreds of lines.
        """
        try:
            chunk = ser.read(4096)
        except serial.SerialException as e:
            print(f"\nSerial error: {e}", flush=True)
            print("Device likely disconnected/reset. Attempting to reconnect...", flush=True)
            ser.close()
            reconnected = False
            for _attempt in range(30):
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
                print("Could not reconnect after 15s. Check the USB cable/port.", flush=True)
                timer.stop()
            elif setpoint_enabled:
                send_command("GET")   # board may have rebooted (setpoint back to 0 V)
            return

        if not chunk:
            return  # nothing arrived within the read timeout

        state["read_buffer"] += chunk
        *complete_lines, state["read_buffer"] = state["read_buffer"].split(b"\n")

        for raw_bytes in complete_lines:
            decoded = raw_bytes.decode(errors="replace").strip()
            if not decoded:
                continue

            if decoded.startswith(("ACK ", "ERR ")):
                handle_reply(decoded)
                continue

            if key_re is not None:
                m = key_re.search(decoded)
                if m is None:
                    # Not malformed -- a telemetry/status line, boot message, etc.
                    state["other_count"] += 1
                    is_status = handle_status_line(decoded)
                    if args.show_status or args.debug or not is_status:
                        # Always echo non-telemetry text (boot messages, warnings)
                        print(decoded, flush=True)
                    continue
                raw_val = filt_val = float(m.group(1))
            elif args.single:
                if not SINGLE_LINE_RE.match(decoded):
                    state["malformed_count"] += 1
                    if args.debug:
                        print(f"  <-- MALFORMED: {decoded!r}", flush=True)
                    continue
                raw_val = filt_val = float(decoded)
            else:
                if not CLEAN_LINE_RE.match(decoded):
                    state["malformed_count"] += 1
                    if args.debug:
                        print(f"  <-- MALFORMED: {decoded!r}", flush=True)
                    continue
                raw_val_str, filt_val_str = decoded.split(",")
                raw_val = float(raw_val_str)
                filt_val = float(filt_val_str)

            if state["warmup_remaining"] > 0:
                state["warmup_remaining"] -= 1
                state["warmup_skipped_count"] += 1
                if args.debug:
                    print(f"  <-- WARMUP SKIP: raw={raw_val} filtered={filt_val} "
                          f"({state['warmup_remaining']} left)", flush=True)
                continue  # discard unconditionally -- no baseline exists yet to judge it by

            if args.max_abs_volts is not None and (
                abs(raw_val) > args.max_abs_volts or abs(filt_val) > args.max_abs_volts
            ):
                state["out_of_range_count"] += 1
                if args.debug:
                    print(f"  <-- OUT OF RANGE: raw={raw_val} filtered={filt_val}", flush=True)
                continue

            if args.max_jump is not None and recent_raw:
                baseline_raw = median(recent_raw)
                baseline_filt = median(recent_filt)
                if (abs(raw_val - baseline_raw) > args.max_jump
                        or abs(filt_val - baseline_filt) > args.max_jump):
                    state["jump_rejected_count"] += 1
                    if args.debug:
                        print(f"  <-- JUMP REJECTED: raw={raw_val} (baseline "
                              f"{baseline_raw:.4f}) filtered={filt_val} (baseline "
                              f"{baseline_filt:.4f})", flush=True)
                    continue

            recent_raw.append(raw_val)
            recent_filt.append(filt_val)

            xs.append(state["sample_count"])
            ys_raw.append(raw_val)
            ys_filt.append(filt_val)
            state["last_value"] = filt_val
            if csv_file:
                if one_value:
                    csv_file.write(f"{state['sample_count']},{filt_val}\n")
                else:
                    csv_file.write(f"{state['sample_count']},{raw_val},{filt_val}\n")
            if args.debug:
                if one_value:
                    print(f"[{state['sample_count']}] {value_label}={filt_val}", flush=True)
                else:
                    print(f"[{state['sample_count']}] raw={raw_val}  filtered={filt_val}",
                          flush=True)
            state["sample_count"] += 1

    def update():
        prev_count = state["sample_count"]
        read_and_process()
        if args.key:
            refresh_status_panel()

        if state["sample_count"] > prev_count:
            if not state["first_data_reported"]:
                state["first_data_reported"] = True
                print(f"First valid sample received after "
                      f"{time.time() - state['start_time']:.1f}s -- streaming is working.",
                      flush=True)
            x_arr_full = np.fromiter(xs, dtype=np.float64)
            y_filt_full = np.fromiter(ys_filt, dtype=np.float64)
            y_raw_full = np.fromiter(ys_raw, dtype=np.float64) if curve_raw is not None else None

            n_visible = min(state["visible_window"], len(x_arr_full))
            x_slice = x_arr_full[-n_visible:]
            y_filt_slice = y_filt_full[-n_visible:]
            if curve_raw is not None:
                y_raw_slice = y_raw_full[-n_visible:]
                curve_raw.setData(x_slice, y_raw_slice)
            curve_filt.setData(x_slice, y_filt_slice)

            if state["follow"] and n_visible > 0:
                # Fixed-width scrolling x-range -- avoids autoRange constantly
                # refitting, which is what makes the view feel like it's shrinking.
                plot.setXRange(x_slice[0], x_slice[-1], padding=0.02)

                if args.ymin is not None or args.ymax is not None:
                    plot.setYRange(
                        args.ymin if args.ymin is not None else float(y_filt_slice.min()),
                        args.ymax if args.ymax is not None else float(y_filt_slice.max()),
                        padding=0.05,
                    )
                else:
                    combined = (np.concatenate([y_filt_slice, y_raw_slice])
                                if curve_raw is not None else y_filt_slice)
                    y_lo, y_hi = float(combined.min()), float(combined.max())
                    if state["include_hlines_in_y"]:
                        for line in hlines.values():
                            y_lo = min(y_lo, float(line.value()))
                            y_hi = max(y_hi, float(line.value()))
                    if args.min_yspan is not None and (y_hi - y_lo) < args.min_yspan:
                        center = (y_hi + y_lo) / 2
                        y_lo = center - args.min_yspan / 2
                        y_hi = center + args.min_yspan / 2
                    plot.setYRange(y_lo, y_hi, padding=0.1)

            win.setWindowTitle(
                f"Live serial voltage -- {args.port} @ {args.baud} baud  "
                f"(n={state['sample_count']}, malformed={state['malformed_count']}, "
                f"other={state['other_count']}, "
                f"out_of_range={state['out_of_range_count']}, "
                f"jump_rejected={state['jump_rejected_count']}, "
                f"warmup_skipped={state['warmup_skipped_count']})"
            )
        elif not state["first_data_reported"] and time.time() - state["start_time"] > 5:
            hint = (f" No '{args.key}:' fields seen -- check the key name."
                    if args.key and state["other_count"] > 0 else "")
            print("Still no valid data after 5s. Check the port, baud rate, and that "
                  "nothing else has the port open. Run with --debug to see raw lines."
                  + hint, flush=True)
            state["start_time"] = time.time() + 1e9  # only warn once

        if args.debug and time.time() - state["last_heartbeat"] > 1.0:
            state["last_heartbeat"] = time.time()
            print(f"  (heartbeat: loop alive, samples={state['sample_count']}, "
                  f"malformed={state['malformed_count']}, other={state['other_count']}, "
                  f"out_of_range={state['out_of_range_count']}, "
                  f"jump_rejected={state['jump_rejected_count']})", flush=True)

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(args.refresh_ms)

    # --- Ctrl+C handling ---
    # PyQt swallows exceptions raised inside Qt-invoked callbacks, so a plain
    # try/except KeyboardInterrupt around app.exec_() is unreliable. An
    # explicit SIGINT handler that calls app.quit() stops the event loop
    # cleanly so the finally block below still runs.
    def handle_sigint(signum, frame):
        print("\nCtrl+C received, shutting down...", flush=True)
        app.quit()

    signal.signal(signal.SIGINT, handle_sigint)

    print("Streaming... close the plot window or Ctrl+C in the terminal to stop.\n",
          flush=True)

    try:
        app.exec_()
    except KeyboardInterrupt:
        pass
    finally:
        s = state
        total = (s["sample_count"] + s["malformed_count"] + s["out_of_range_count"]
                  + s["jump_rejected_count"])
        malformed_pct = (s["malformed_count"] / total * 100) if total else 0
        oor_pct = (s["out_of_range_count"] / total * 100) if total else 0
        jump_pct = (s["jump_rejected_count"] / total * 100) if total else 0
        print(f"\nStopped. {s['sample_count']} clean samples, "
              f"{s['malformed_count']} malformed ({malformed_pct:.1f}%), "
              f"{s['out_of_range_count']} out-of-range ({oor_pct:.1f}%), "
              f"{s['jump_rejected_count']} jump-rejected ({jump_pct:.1f}%), "
              f"{s['warmup_skipped_count']} warmup-skipped, "
              f"{s['other_count']} other (non-key) lines.")
        ser.close()
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
