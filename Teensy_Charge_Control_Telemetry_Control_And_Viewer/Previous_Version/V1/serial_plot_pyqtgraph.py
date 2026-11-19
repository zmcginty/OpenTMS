"""
Live plotter + control panel for OpenTMS Charge_Control (binary protocol v1).

Needs opentms_protocol.py in the same folder.

  Plot:       TLM_FAST v_cap (ADS8699) and v_cap_int (Teensy A0), one point per packet.
  Telemetry:  TLM_STATUS fields, health flags, and link quality (packets, lost, CRC errors).
  Setpoint:   Apply / Charge OFF send CMD_SET_SETPOINT / CMD_CHARGE_OFF; the firmware's
              confirmed setpoint (from ACKs and STATUS) is shown and drawn as a dashed line.
              Commands with no ACK within 1 s are flagged.
  RAW:        "Firmware RAW stream" turns TLM_RAW on every batch; with --raw-csv the
              integer rows are written to a CSV.
  Logs:       TLM_LOG messages (boot config results, faults) print to the console.

Usage:
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401 --raw --raw-csv run1_raw.csv
    python serial_plot_pyqtgraph.py COM5 --fast-hz 100 --status-hz 20 --hline read_dac_set_volt

Rates: 'batch' (every ADS8699 batch), 'off', or a number in Hz.

Dependencies:
    pip install pyqtgraph pyqt5 pyserial numpy
"""

import argparse
import signal
import sys
import time
from collections import deque

import numpy as np
import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from opentms_protocol import (
    AckStatus, Cmd, Link, LogLevel, RATE_EVERY_BATCH, RATE_OFF, RAW_FIELDS, Tlm,
    SF_RAW_ON, describe_ack, parse_rate, rate_str, status_alerts,
)

FRIENDLY_NAMES = {
    "v_cap_slow":        "Cap V (ADS1115)",
    "v_cap_int_avg":     "Cap V (Teensy A0)",
    "set_v":             "Charge setpoint (cap V)",
    "dac_meas":          "DAC (ADS1115, V)",
    "dac_int":           "DAC (Teensy A1, V)",
    "read_dac_set_volt": "DAC setpoint, measured (cap V)",
    "loop_hz":           "Fast loop rate (Hz)",
    "pp_cap_v":          "ADS8699 p-p (cap V)",
    "dac_err_ads":       "DAC error, ADS1115 (V)",
    "dac_err_int":       "DAC error, Teensy (V)",
    "n_avg":             "ADS8699 batch size",
    "pp_codes":          "ADS8699 p-p (codes)",
    "parity_errors":     "Parity errors (total)",
    "rvs_timeouts":      "RVS timeouts (total)",
    "cfg_faults":        "Config faults (total)",
    "intadc_errors":     "Teensy ADC errors (total)",
    "tx_dropped":        "Device TX dropped (total)",
    "rx_errors":         "Device RX errors (total)",
}
HIDDEN_STATUS_FIELDS = {"t_ms", "flags"}
INT_FIELDS = {"n_avg", "pp_codes", "parity_errors", "rvs_timeouts", "cfg_faults",
              "intadc_errors", "tx_dropped", "rx_errors"}

STATUS_COLUMNS = 3
STATUS_STALE_AFTER_S = 1.0
SETPOINT_FIELD = "set_v"
ACK_TIMEOUT_S = 1.0


def parse_args():
    p = argparse.ArgumentParser(description="Live plot + control for OpenTMS Charge_Control")
    p.add_argument("port", nargs="?", default="/dev/cu.usbmodem176191001",
                   help="Serial port device (default: %(default)s)")
    p.add_argument("--fast-hz", type=parse_rate, default=RATE_EVERY_BATCH,
                   help="TLM_FAST rate: 'batch' (default), 'off', or Hz")
    p.add_argument("--status-hz", type=parse_rate, default=10,
                   help="TLM_STATUS rate (default 10 Hz)")
    p.add_argument("--raw", action="store_true", help="Start with TLM_RAW on every batch")
    p.add_argument("--raw-csv", type=str, default=None,
                   help="Write TLM_RAW integer rows to this CSV")
    p.add_argument("--save-csv", type=str, default=None,
                   help="Write TLM_FAST samples to this CSV")
    p.add_argument("--avg", type=int, default=None, help="Set ADS8699 batch size at connect")
    p.add_argument("--hline", type=str, action="append", default=[],
                   help="Draw this STATUS field as a horizontal line (repeatable)")
    p.add_argument("--setpoint-max", type=float, default=1000.0,
                   help="Upper limit of the setpoint box, cap volts (default 1000). "
                        "The firmware enforces its own limit regardless.")
    p.add_argument("--window", type=int, default=5000,
                   help="Initial number of most recent samples visible")
    p.add_argument("--max-history", type=int, default=None,
                   help="Total samples kept in memory (default: 5x --window)")
    p.add_argument("--min-yspan", type=float, default=None,
                   help="Floor on the y-axis autoscale span, in volts")
    p.add_argument("--ymin", type=float, default=None, help="Fixed y-axis min")
    p.add_argument("--ymax", type=float, default=None, help="Fixed y-axis max")
    p.add_argument("--refresh-ms", type=int, default=20,
                   help="Milliseconds between redraws (default 20)")
    p.add_argument("--debug", action="store_true", help="Print every packet")
    return p.parse_args()


def fmt_value(name, val):
    if name in INT_FIELDS:
        return f"{int(val)}"
    return f"{val:.4f}"


def main():
    args = parse_args()
    if args.max_history is None:
        args.max_history = max(args.window * 5, 50000)
    args.max_history = max(args.max_history, args.window)
    if SETPOINT_FIELD not in args.hline:
        args.hline.append(SETPOINT_FIELD)

    print(f"Opening {args.port} ...", flush=True)
    try:
        ser = serial.Serial(args.port, timeout=0, write_timeout=0.5)
    except serial.SerialException as e:
        print(f"FAILED to open port: {e}", flush=True)
        print("Common causes: wrong port name, another program has it open, "
              "or permissions (Linux: add your user to 'dialout').", flush=True)
        sys.exit(1)
    time.sleep(0.5)
    ser.reset_input_buffer()
    link = Link(ser)

    csv_file = None
    if args.save_csv:
        csv_file = open(args.save_csv, "w", buffering=1 << 16)
        csv_file.write("sample,batch_seq,t_us,v_cap,v_cap_int,flags\n")
    raw_csv = None
    if args.raw_csv:
        raw_csv = open(args.raw_csv, "w", buffering=1 << 16)
        raw_csv.write(",".join(RAW_FIELDS) + "\n")

    xs = deque(maxlen=args.max_history)
    ys_cap = deque(maxlen=args.max_history)
    ys_int = deque(maxlen=args.max_history)

    state = {
        "sample_count": 0,
        "raw_count": 0,
        "start_time": time.time(),
        "first_data_reported": False,
        "visible_window": args.window,
        "follow": True,
        "show_int": True,
        "include_hlines_in_y": True,
        "status_fields": {},
        "status_alerts": [],
        "status_time": None,
        "status_dirty": False,
        "last_fast": None,
        "fw_setpoint": None,
        "sp_requested": None,
        "sp_requested_time": 0.0,
        "sp_box_synced": False,
        "sp_box_touched": False,
        "last_reply": None,
        "reply_is_error": False,
        "reply_dirty": False,
        "raw_on": None,
        "last_batch_seq": None,
    }

    def send(cmd, *a):
        try:
            seq = link.send(cmd, *a)
            if args.debug:
                print(f">> {cmd.name} {a} seq={seq}", flush=True)
            return True
        except Exception as e:   # serial errors, struct.error on a bad argument
            set_reply(f"SEND FAILED {cmd.name}: {e}", True)
            return False

    def set_reply(text, is_error):
        state["last_reply"] = text
        state["reply_is_error"] = is_error
        state["reply_dirty"] = True
        print(text, flush=True)

    def startup_commands():
        send(Cmd.REQUEST_TELEM, Tlm.HELLO)
        send(Cmd.SET_TELEM_RATE, Tlm.FAST, args.fast_hz)
        send(Cmd.SET_TELEM_RATE, Tlm.STATUS, args.status_hz)
        if args.raw:
            send(Cmd.SET_TELEM_RATE, Tlm.RAW, RATE_EVERY_BATCH)
        if args.avg is not None:
            send(Cmd.SET_AVG, args.avg)
        send(Cmd.GET_SETPOINT)

    # --- pyqtgraph setup ---
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=False)

    win = QtWidgets.QWidget()
    win.setWindowTitle(f"OpenTMS Charge_Control -- {args.port}")
    win.resize(1100, 860)
    layout = QtWidgets.QVBoxLayout(win)

    controls = QtWidgets.QHBoxLayout()
    controls.addWidget(QtWidgets.QLabel("Visible window (samples):"))
    window_spin = QtWidgets.QSpinBox()
    window_spin.setRange(10, args.max_history)
    window_spin.setSingleStep(max(1, args.window // 20))
    window_spin.setValue(args.window)
    controls.addWidget(window_spin)
    follow_checkbox = QtWidgets.QCheckBox("Follow latest")
    follow_checkbox.setChecked(True)
    controls.addWidget(follow_checkbox)
    int_checkbox = QtWidgets.QCheckBox("Show Teensy A0")
    int_checkbox.setChecked(True)
    controls.addWidget(int_checkbox)
    hline_y_checkbox = QtWidgets.QCheckBox("Include setpoint in Y scale")
    hline_y_checkbox.setChecked(True)
    controls.addWidget(hline_y_checkbox)
    raw_checkbox = QtWidgets.QCheckBox("Firmware RAW stream")
    controls.addWidget(raw_checkbox)
    controls.addStretch()
    layout.addLayout(controls)

    window_spin.valueChanged.connect(lambda v: state.__setitem__("visible_window", v))
    follow_checkbox.stateChanged.connect(lambda c: state.__setitem__("follow", bool(c)))
    hline_y_checkbox.stateChanged.connect(
        lambda c: state.__setitem__("include_hlines_in_y", bool(c)))
    raw_checkbox.clicked.connect(
        lambda checked: send(Cmd.SET_TELEM_RATE, Tlm.RAW,
                             RATE_EVERY_BATCH if checked else RATE_OFF))

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
    layout.addWidget(sp_box)

    def apply_setpoint():
        sp_spin.interpretText()
        v = round(sp_spin.value(), 1)
        if send(Cmd.SET_SETPOINT, v):
            state["sp_requested"] = v
            state["sp_requested_time"] = time.time()

    def charge_off():
        sp_spin.setValue(0.0)
        if send(Cmd.CHARGE_OFF):
            state["sp_requested"] = 0.0
            state["sp_requested_time"] = time.time()

    sp_apply_btn.clicked.connect(apply_setpoint)
    sp_off_btn.clicked.connect(charge_off)
    sp_spin.lineEdit().returnPressed.connect(apply_setpoint)
    sp_spin.lineEdit().textEdited.connect(lambda _t: state.__setitem__("sp_box_touched", True))
    sp_spin.valueChanged.connect(lambda _v: state.__setitem__("sp_box_touched", True))

    # --- Telemetry panel ---
    status_box = QtWidgets.QGroupBox("Telemetry")
    status_outer = QtWidgets.QVBoxLayout(status_box)
    status_grid = QtWidgets.QGridLayout()
    status_grid.setHorizontalSpacing(24)
    status_outer.addLayout(status_grid)

    value_style = "font-family: monospace; font-size: 14px;"
    big_label = QtWidgets.QLabel("v_cap: --")
    big_label.setStyleSheet("font-family: monospace; font-size: 20px; font-weight: bold;")
    status_grid.addWidget(big_label, 0, 0, 1, STATUS_COLUMNS)

    flags_label = QtWidgets.QLabel("")
    flags_label.setStyleSheet("font-family: monospace; font-size: 14px; "
                              "color: #e04040; font-weight: bold;")
    status_outer.addWidget(flags_label)
    link_label = QtWidgets.QLabel("link: waiting for telemetry...")
    link_label.setStyleSheet("font-family: monospace; font-size: 12px; color: gray;")
    status_outer.addWidget(link_label)
    field_labels = {}
    layout.addWidget(status_box)

    # --- Plot ---
    plot_widget = pg.PlotWidget()
    layout.addWidget(plot_widget, stretch=1)
    plot = plot_widget.getPlotItem()
    plot.setLabel("bottom", "Sample #")
    plot.setLabel("left", "Capacitor voltage (V)")
    plot.showGrid(x=True, y=True, alpha=0.3)
    plot.addLegend()
    plot.enableAutoRange(x=False, y=False)
    curve_cap = plot.plot(pen=pg.mkPen("c", width=1.5), name="v_cap (ADS8699)")
    curve_int = plot.plot(pen=pg.mkPen("y", width=1.2), name="v_cap_int (Teensy A0)")

    def on_int_changed(checked):
        state["show_int"] = bool(checked)
        curve_int.setVisible(bool(checked))
    int_checkbox.stateChanged.connect(on_int_changed)

    hline_colors = ["m", "g", "r", "w", "y"]
    hlines = {}

    def set_hline(name, val):
        line = hlines.get(name)
        if line is None:
            color = hline_colors[len(hlines) % len(hline_colors)]
            line = pg.InfiniteLine(
                pos=val, angle=0, movable=False,
                pen=pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine),
                label=f"{name} = {{value:.2f}}",
                labelOpts={"position": 0.05, "color": color})
            plot.addItem(line)
            hlines[name] = line
        else:
            line.setValue(val)

    win.show()
    startup_commands()

    # --- Packet handling ---
    def on_fast(f):
        seq = f["batch_seq"]
        if state["last_batch_seq"] is not None and seq < state["last_batch_seq"]:
            print("batch_seq went backwards -- device rebooted", flush=True)
        state["last_batch_seq"] = seq
        xs.append(state["sample_count"])
        ys_cap.append(f["v_cap"])
        ys_int.append(f["v_cap_int"])
        state["last_fast"] = f
        if csv_file:
            csv_file.write(f"{state['sample_count']},{seq},{f['t_us']},"
                           f"{f['v_cap']!r},{f['v_cap_int']!r},{f['flags']}\n")
        state["sample_count"] += 1

    def on_status(f):
        state["status_fields"] = f
        state["status_alerts"] = status_alerts(f)
        state["status_time"] = time.time()
        state["status_dirty"] = True
        state["fw_setpoint"] = f[SETPOINT_FIELD]
        state["raw_on"] = bool(f["flags"] & SF_RAW_ON)

    def on_ack(f):
        req = f["request"]
        ok = f["status"] == AckStatus.OK
        set_reply(describe_ack(f), not ok)
        if req is None:
            return
        if req.cmd in (Cmd.SET_SETPOINT, Cmd.CHARGE_OFF, Cmd.GET_SETPOINT):
            state["fw_setpoint"] = f["value"]   # firmware value, even when it refused
        elif req.cmd == Cmd.SET_TELEM_RATE and ok and req.args[0] == Tlm.RAW:
            state["raw_on"] = int(f["value"]) != RATE_OFF

    def on_hello(f):
        print(f"HELLO: protocol v{f['proto_version']}, setpoint max {f['setpoint_max']:.0f} V, "
              f"n_avg {f['n_avg']}, rates fast={rate_str(f['rate_fast'])} "
              f"raw={rate_str(f['rate_raw'])} status={rate_str(f['rate_status'])}", flush=True)
        state["raw_on"] = f["rate_raw"] != RATE_OFF

    def handle_packets():
        try:
            pkts = link.poll()
        except (serial.SerialException, OSError) as e:
            print(f"\nSerial error: {e}", flush=True)
            reconnect()
            return
        for pkt in pkts:
            if args.debug:
                print(f"<< {pkt.name} seq={pkt.seq} {pkt.fields}", flush=True)
            f = pkt.fields
            if f is None:
                continue
            if pkt.id == Tlm.FAST:
                on_fast(f)
            elif pkt.id == Tlm.RAW:
                state["raw_count"] += 1
                if raw_csv:
                    raw_csv.write(",".join(str(f[k]) for k in RAW_FIELDS) + "\n")
            elif pkt.id == Tlm.STATUS:
                on_status(f)
            elif pkt.id == Tlm.ACK:
                on_ack(f)
            elif pkt.id == Tlm.LOG:
                try:
                    lvl = LogLevel(f["level"]).name
                except ValueError:
                    lvl = str(f["level"])
                print(f"[{lvl}] {f['text']}", flush=True)
            elif pkt.id == Tlm.HELLO:
                on_hello(f)
        for late in link.overdue(ACK_TIMEOUT_S):
            set_reply(f"NO ACK for {late.cmd.name}", True)

    def reconnect():
        print("Device likely disconnected/reset. Attempting to reconnect...", flush=True)
        ser.close()
        for _ in range(30):
            time.sleep(0.5)
            try:
                ser.open()
                ser.reset_input_buffer()
                link.reset()
                print("Reconnected.", flush=True)
                startup_commands()   # device may have rebooted: re-apply rates
                return
            except serial.SerialException:
                continue
        print("Could not reconnect after 15s. Check the USB cable/port.", flush=True)
        timer.stop()

    # --- Panel refresh ---
    def refresh_setpoint_panel():
        if state["reply_dirty"]:
            state["reply_dirty"] = False
            sp_reply_label.setStyleSheet(
                "font-family: monospace; font-size: 12px; "
                + ("color: #e04040; font-weight: bold;" if state["reply_is_error"]
                   else "color: gray;"))
            sp_reply_label.setText(state["last_reply"] or "")

        fw = state["fw_setpoint"]
        if fw is None:
            return
        set_hline(SETPOINT_FIELD, fw)
        if not state["sp_box_synced"]:
            state["sp_box_synced"] = True
            if not state["sp_box_touched"]:
                sp_spin.blockSignals(True)
                sp_spin.setValue(fw)
                sp_spin.blockSignals(False)

        req = state["sp_requested"]
        pending = req is not None and abs(fw - req) > 0.05
        if fw < 1.0:
            text, color = f"Firmware setpoint: {fw:.1f} V  (charging OFF)", "gray"
        else:
            text, color = f"Firmware setpoint: {fw:.1f} V", "#30c030"
        if pending:
            late = (time.time() - state["sp_requested_time"]) > ACK_TIMEOUT_S * 2
            text += f"  (requested {req:.1f} V{' -- NOT CONFIRMED' if late else '...'})"
            color = "orange"
        sp_fw_label.setStyleSheet(
            f"font-family: monospace; font-size: 15px; font-weight: bold; color: {color};")
        sp_fw_label.setText(text)

    def refresh_status_panel():
        lf = state["last_fast"]
        if lf is not None:
            big_label.setText(f"v_cap: {lf['v_cap']:.3f} V    v_cap_int: {lf['v_cap_int']:.3f} V"
                              f"    chg: {1 if lf['flags'] & 1 else 0}")

        if state["raw_on"] is not None and raw_checkbox.isChecked() != state["raw_on"]:
            raw_checkbox.blockSignals(True)
            raw_checkbox.setChecked(state["raw_on"])
            raw_checkbox.blockSignals(False)

        if state["status_dirty"]:
            state["status_dirty"] = False
            for name, val in state["status_fields"].items():
                if name in HIDDEN_STATUS_FIELDS:
                    continue
                lbl = field_labels.get(name)
                if lbl is None:
                    lbl = QtWidgets.QLabel()
                    lbl.setStyleSheet(value_style)
                    idx = len(field_labels)
                    status_grid.addWidget(lbl, 1 + idx // STATUS_COLUMNS, idx % STATUS_COLUMNS)
                    field_labels[name] = lbl
                lbl.setText(f"{FRIENDLY_NAMES.get(name, name)}: {fmt_value(name, val)}")
                if name in args.hline and name != SETPOINT_FIELD:
                    set_hline(name, val)
            flags_label.setText("  ".join(state["status_alerts"]))

        st = link.decoder.stats()
        age = (f"{time.time() - state['status_time']:.1f} s ago"
               if state["status_time"] is not None else "never")
        stale = state["status_time"] is None or \
            time.time() - state["status_time"] > STATUS_STALE_AFTER_S
        link_label.setStyleSheet("font-family: monospace; font-size: 12px; color: "
                                 + ("orange;" if stale or st["crc_errors"] or st["lost"]
                                    else "gray;"))
        link_label.setText(
            f"link: {st['packets']} pkts, lost {st['lost']}, crc_err {st['crc_errors']}, "
            f"framing {st['framing_errors']}, len_err {st['length_errors']}, "
            f"unknown {st['unknown']} | RAW rows {state['raw_count']} | status {age}")

        refresh_setpoint_panel()

    def update():
        prev = state["sample_count"]
        handle_packets()
        refresh_status_panel()

        if state["sample_count"] > prev:
            if not state["first_data_reported"]:
                state["first_data_reported"] = True
                print(f"First FAST packet after {time.time() - state['start_time']:.1f}s.",
                      flush=True)
            n = min(state["visible_window"], len(xs))
            x = np.fromiter(xs, dtype=np.float64)[-n:]
            y_cap = np.fromiter(ys_cap, dtype=np.float64)[-n:]
            curve_cap.setData(x, y_cap)
            series = [y_cap]
            if state["show_int"]:
                y_int = np.fromiter(ys_int, dtype=np.float64)[-n:]
                curve_int.setData(x, y_int)
                series.append(y_int)

            if state["follow"] and n > 0:
                plot.setXRange(x[0], x[-1], padding=0.02)
                combined = np.concatenate(series)
                finite = combined[np.isfinite(combined)]
                if finite.size:
                    y_lo, y_hi = float(finite.min()), float(finite.max())
                    if args.ymin is not None or args.ymax is not None:
                        plot.setYRange(args.ymin if args.ymin is not None else y_lo,
                                       args.ymax if args.ymax is not None else y_hi,
                                       padding=0.05)
                    else:
                        if state["include_hlines_in_y"]:
                            for line in hlines.values():
                                y_lo = min(y_lo, float(line.value()))
                                y_hi = max(y_hi, float(line.value()))
                        if args.min_yspan is not None and (y_hi - y_lo) < args.min_yspan:
                            c = (y_hi + y_lo) / 2
                            y_lo, y_hi = c - args.min_yspan / 2, c + args.min_yspan / 2
                        plot.setYRange(y_lo, y_hi, padding=0.1)
        elif not state["first_data_reported"] and time.time() - state["start_time"] > 5:
            st = link.decoder.stats()
            hint = (" Bytes are arriving but not decoding -- old ASCII firmware?"
                    if st["framing_errors"] or st["crc_errors"] else "")
            print("No FAST packets after 5s. Check the port and that nothing else has it "
                  "open. Run with --debug to see packets." + hint, flush=True)
            state["start_time"] = time.time() + 1e9

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(args.refresh_ms)

    def handle_sigint(signum, frame):
        print("\nCtrl+C received, shutting down...", flush=True)
        app.quit()
    signal.signal(signal.SIGINT, handle_sigint)

    print("Streaming... close the window or Ctrl+C to stop.\n", flush=True)
    try:
        app.exec_()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if state["raw_on"]:
                link.send(Cmd.SET_TELEM_RATE, Tlm.RAW, RATE_OFF)
        except Exception:
            pass
        print(f"\nStopped. {state['sample_count']} FAST samples, "
              f"{state['raw_count']} RAW rows. Link: {link.decoder.stats()}")
        ser.close()
        if csv_file:
            csv_file.close()
        if raw_csv:
            raw_csv.close()


if __name__ == "__main__":
    main()
