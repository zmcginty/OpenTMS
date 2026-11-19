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
  Calibrate:  the Calibration tab streams each ADC's input-level voltage (ADS8699 pin;
              Teensy A0/A4 signal before their dividers; no 500x probe scale), records
              meter readings against them, and fits the *_CAL_SCALE/OFFSET constants to
              paste into the firmware. It can also force the DAC to a chosen voltage
              (only while the setpoint is 0) so the A4 input can be calibrated against
              it. See the CalibrationPanel comments below.

Usage:
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401
    python serial_plot_pyqtgraph.py /dev/cu.usbmodem135769401 --raw --raw-csv run1_raw.csv
    python serial_plot_pyqtgraph.py COM5 --fast-hz 100 --status-hz 20 --hline dac_threshold_cap_v

Rates: 'batch' (every ADS8699 batch), 'off', or a number in Hz.

HOW IT WORKS
  Everything runs on the Qt GUI thread. A QTimer calls update() every --refresh-ms:
      update()
        handle_packets()        link.poll() drains the serial port; each packet is routed
                                by ID to on_fast / on_status / on_ack / on_hello, or
                                written to CSV (RAW) / printed (LOG)
        refresh_status_panel()  redraws labels from `state` (only what changed)
        redraw the plot         from the xs / ys_* ring buffers (deques)
  `state` is a plain dict holding everything the handlers and the redraw share.
  Nothing blocks: the port is opened with timeout=0, so a quiet link just means an
  empty poll.

WHERE TO ADD THINGS
  New telemetry packet ... add an elif to handle_packets() (after adding it to
                           opentms_protocol.py)
  New command button ..... create the widget next to sp_box, connect it to
                           lambda: send(Cmd.XXX, args...)
  New STATUS field ....... appears automatically in the Telemetry panel; give it a
                           readable name in FRIENDLY_NAMES
  Another plotted curve .. add a deque next to ys_cap, append in on_fast(), and a
                           plot.plot(...) curve + setData() in update()

Dependencies:
    pip install pyqtgraph pyqt5 pyserial numpy
"""

import argparse
import itertools
import signal
import sys
import time
from collections import deque

import numpy as np
import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from opentms_protocol import (
    AckStatus, Cmd, Link, LogLevel, PROTO_VERSION, RATE_EVERY_BATCH, RATE_OFF, RAW_FIELDS, Tlm,
    SF_DAC_OVERRIDE, SF_RAW_ON, describe_ack, parse_rate, rate_str, status_alerts,
)

FRIENDLY_NAMES = {
    "v_cap_int_avg":       "Cap V (Teensy A0)",
    "set_v":               "Charge setpoint (cap V)",
    "dac_set_v":           "DAC commanded (V)",
    "dac_int":             "DAC measured, Teensy A4 (V)",
    "dac_err_int":         "DAC error (V)",
    "dac_threshold_cap_v": "Comparator threshold, measured (cap V)",
    "loop_hz":             "Fast loop rate (Hz)",
    "pp_cap_v":            "ADS8699 p-p (cap V)",
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
RECONNECT_EVERY_S = 0.5
PLOT_HZ = 30              # capacitor plot redraw rate (data is still received every tick)
LAG_WARN_S = 0.5          # link lag above this turns the link line orange


def parse_args():
    p = argparse.ArgumentParser(description="Live plot + control for OpenTMS Charge_Control")
    p.add_argument("port", nargs="?", default="/dev/cu.usbmodem176191001",
                   help="Serial port device (default: %(default)s)")
    p.add_argument("--fast-hz", type=parse_rate, default=RATE_EVERY_BATCH,
                   help="TLM_FAST rate: 'batch' (default), 'off', or Hz")
    p.add_argument("--status-hz", type=parse_rate, default=10,
                   help="TLM_STATUS rate (default 10 Hz)")
    p.add_argument("--raw", action="store_true", help="Start with TLM_RAW on every batch")
    p.add_argument("--cal", action="store_true", help="Open on the Calibration tab")
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


# ---------------------------------------------------------------------------
# Calibration tab
#
# Fits   meter_volts = scale * raw_volts + offset   for each ADC channel, at the 0-5 V
# input level: no 500x probe scale, Teensy dividers already undone by the firmware using
# CAPSENSE_R1/R2 and DACSENSE_R1/R2. The result is the *_CAL_SCALE / *_CAL_OFFSET pair to
# paste into Charge_Control.ino. Fits always use the RAW (uncalibrated) values, so the
# result replaces the old constants rather than stacking on top of them. If you change a
# divider constant in the firmware, the raw values change too: redo that channel's cal.
#
# Procedure: apply a steady voltage, wait for the window mean to settle, type what the
# meter reads, Add point. Repeat across the range (e.g. 0, 1, 2.5, 4, 5 V).
# ---------------------------------------------------------------------------

# key, label, raw field, calibrated field, firmware constant prefix, Teensy channel?
CAL_CHANNELS = [
    ("ads", "ADS8699 input",       "ads_raw_v", "ads_cal_v", "ADS8699_CAL",    False),
    ("a0",  "Teensy A0 (probe)",   "a0_raw_v",  "a0_cal_v",  "INTADC_CAP_CAL", True),
    ("a4",  "Teensy A4 (DAC)",     "a4_raw_v",  "a4_cal_v",  "INTADC_DAC_CAL", True),
]
CAL_HISTORY_S = 30.0         # samples kept for the plot / averaging window
CAL_MIN_SAMPLES = 5          # refuse to record a point averaged from fewer samples
CAL_COLORS = {"ads": "c", "a0": "y", "a4": "m"}


DAC_FULL_SCALE_V = 5.0      # AD5683 with 2.5 V ref and 2x gain


class CalibrationPanel(QtWidgets.QWidget):
    def __init__(self, send):
        super().__init__()
        self.send = send        # main window's send(cmd, *args) -> bool
        self.samples = {c[0]: deque() for c in CAL_CHANNELS}   # (t, raw, cal)
        self.points = []        # dicts: ch, raw, std, n, meter
        self.t0 = time.monotonic()
        self._last_live = 0.0
        self._last_plot = 0.0
        self.override_active = False   # firmware's DAC override state (from ACK / STATUS)

        outer = QtWidgets.QVBoxLayout(self)

        # DAC output control. Forcing the DAC moves the comparator's charge cutoff, so the
        # firmware only allows it while the setpoint is 0, holds the charge pin low, and
        # cancels it on any setpoint change. This panel also releases it when you leave
        # the tab or close the window.
        dac_box = QtWidgets.QGroupBox("DAC output -- forces the comparator threshold; "
                                      "only allowed while charging is OFF (setpoint 0)")
        dac_row = QtWidgets.QHBoxLayout(dac_box)
        dac_row.addWidget(QtWidgets.QLabel("DAC volts:"))
        self.dac_spin = QtWidgets.QDoubleSpinBox()
        self.dac_spin.setRange(0.0, DAC_FULL_SCALE_V - 0.0001)
        self.dac_spin.setDecimals(4)
        self.dac_spin.setSingleStep(0.1)
        self.dac_spin.setKeyboardTracking(False)
        self.dac_spin.setMinimumWidth(100)
        dac_row.addWidget(self.dac_spin)
        self.dac_set_btn = QtWidgets.QPushButton("Set DAC")
        self.dac_set_btn.setStyleSheet("font-weight: bold;")
        dac_row.addWidget(self.dac_set_btn)
        self.dac_release_btn = QtWidgets.QPushButton("Release DAC")
        dac_row.addWidget(self.dac_release_btn)
        dac_row.addSpacing(20)
        self.dac_label = QtWidgets.QLabel("DAC: setpoint control")
        self.dac_label.setStyleSheet("font-family: monospace; font-size: 14px;")
        dac_row.addWidget(self.dac_label)
        dac_row.addStretch()
        outer.addWidget(dac_box)
        self.dac_set_btn.clicked.connect(self.set_dac)
        self.dac_spin.lineEdit().returnPressed.connect(self.set_dac)
        self.dac_release_btn.clicked.connect(self.release_dac)

        # Live readouts
        live_box = QtWidgets.QGroupBox("Live input voltages (window mean)")
        grid = QtWidgets.QGridLayout(live_box)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Averaging window (s):"))
        self.window_spin = QtWidgets.QDoubleSpinBox()
        self.window_spin.setRange(0.2, CAL_HISTORY_S)
        self.window_spin.setValue(2.0)
        self.window_spin.setSingleStep(0.5)
        top.addWidget(self.window_spin)
        top.addStretch()
        grid.addLayout(top, 0, 0, 1, 5)
        mono = "font-family: monospace; font-size: 14px;"
        for col, text in enumerate(["Channel", "Raw mean (V)", "Std dev (mV)", "Samples",
                                    "With current firmware cal (V)", "Latest raw (V)"]):
            h = QtWidgets.QLabel(f"<b>{text}</b>")
            grid.addWidget(h, 1, col)
        self.live_labels = {}
        for row, (key, label, *_rest) in enumerate(CAL_CHANNELS, start=2):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            cells = []
            for col in range(1, 6):
                lbl = QtWidgets.QLabel("--")
                lbl.setStyleSheet(mono)
                grid.addWidget(lbl, row, col)
                cells.append(lbl)
            self.live_labels[key] = cells
        outer.addWidget(live_box)

        # Point entry
        entry = QtWidgets.QHBoxLayout()
        entry.addWidget(QtWidgets.QLabel("Channel:"))
        self.ch_combo = QtWidgets.QComboBox()
        for key, label, *_rest in CAL_CHANNELS:
            self.ch_combo.addItem(label, key)
        entry.addWidget(self.ch_combo)
        entry.addWidget(QtWidgets.QLabel("Meter reading (V):"))
        self.meter_spin = QtWidgets.QDoubleSpinBox()
        self.meter_spin.setRange(-10.0, 10.0)
        self.meter_spin.setDecimals(5)
        self.meter_spin.setSingleStep(0.1)
        self.meter_spin.setKeyboardTracking(False)
        self.meter_spin.setMinimumWidth(110)
        entry.addWidget(self.meter_spin)
        self.add_btn = QtWidgets.QPushButton("Add point")
        self.add_btn.setStyleSheet("font-weight: bold;")
        entry.addWidget(self.add_btn)
        self.remove_btn = QtWidgets.QPushButton("Remove selected / last")
        entry.addWidget(self.remove_btn)
        self.clear_btn = QtWidgets.QPushButton("Clear channel")
        entry.addWidget(self.clear_btn)
        self.save_btn = QtWidgets.QPushButton("Save CSV")
        entry.addWidget(self.save_btn)
        entry.addStretch()
        outer.addLayout(entry)
        self.msg_label = QtWidgets.QLabel("")
        self.msg_label.setStyleSheet("font-family: monospace; font-size: 12px; color: gray;")
        outer.addWidget(self.msg_label)

        self.add_btn.clicked.connect(self.add_point)
        self.meter_spin.lineEdit().returnPressed.connect(self.add_point)
        self.remove_btn.clicked.connect(self.remove_point)
        self.clear_btn.clicked.connect(self.clear_channel)
        self.save_btn.clicked.connect(self.save_csv)

        # Points table + fit results side by side
        mid = QtWidgets.QHBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Channel", "Raw mean (V)", "Std (mV)", "n",
                                              "Meter (V)", "Residual (mV)"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        mid.addWidget(self.table, stretch=3)
        fit_box = QtWidgets.QVBoxLayout()
        self.fit_text = QtWidgets.QPlainTextEdit()
        self.fit_text.setReadOnly(True)
        self.fit_text.setStyleSheet("font-family: monospace; font-size: 12px;")
        self.fit_text.setPlaceholderText("Add at least 2 points per channel to fit.")
        fit_box.addWidget(self.fit_text)
        self.copy_btn = QtWidgets.QPushButton("Copy firmware lines")
        fit_box.addWidget(self.copy_btn)
        self.copy_btn.clicked.connect(self.copy_fit)
        mid.addLayout(fit_box, stretch=4)
        outer.addLayout(mid, stretch=1)

        # Plot of raw input volts
        self.plot_widget = pg.PlotWidget()
        p = self.plot_widget.getPlotItem()
        p.setLabel("bottom", "Time (s)")
        p.setLabel("left", "Raw input voltage (V)")
        p.showGrid(x=True, y=True, alpha=0.3)
        p.addLegend()
        self.curves = {key: p.plot(pen=pg.mkPen(CAL_COLORS[key], width=1.2), name=label)
                       for key, label, *_rest in CAL_CHANNELS}
        for c in self.curves.values():          # draw ~1 point per pixel, not all 8000
            c.setDownsampling(auto=True, method="peak")
            c.setClipToView(True)
        outer.addWidget(self.plot_widget, stretch=2)

    # ----- data in -----
    def on_cal(self, f):
        t = time.monotonic()
        for key, _label, raw_f, cal_f, _prefix, teensy in CAL_CHANNELS:
            if teensy and not f["int_fresh"]:
                continue   # repeated Teensy value; don't count it twice
            dq = self.samples[key]
            dq.append((t, f[raw_f], f[cal_f]))
            while dq and t - dq[0][0] > CAL_HISTORY_S:
                dq.popleft()

    def window_stats(self, key):
        """(raw mean, raw std, n, calibrated mean) over the averaging window, or None."""
        dq = self.samples[key]
        if not dq:
            return None
        t_min = time.monotonic() - self.window_spin.value()
        # walk back from the newest sample only as far as the window reaches
        recent = list(itertools.takewhile(lambda s: s[0] >= t_min, reversed(dq)))
        recent = [(r, c) for (_t, r, c) in recent]
        if not recent:
            return None
        arr = np.array(recent, dtype=np.float64)
        std = float(arr[:, 0].std(ddof=1)) if len(arr) > 1 else 0.0
        return float(arr[:, 0].mean()), std, len(arr), float(arr[:, 1].mean())

    # ----- DAC override -----
    def set_dac(self):
        self.dac_spin.interpretText()
        if self.send(Cmd.SET_DAC_OVERRIDE, 1, self.dac_spin.value()):
            # the A4 channel measures the DAC: select it so Add point records against it
            self.ch_combo.setCurrentIndex(self.ch_combo.findData("a4"))

    def release_dac(self):
        self.send(Cmd.SET_DAC_OVERRIDE, 0, 0.0)

    def on_dac_ack(self, f):
        status = f["status"]
        if status == AckStatus.OK:
            req = f["request"]
            self.override_active = bool(req.args[0]) if req is not None else self.override_active
            self.set_msg(f"DAC {'forced to' if self.override_active else 'released, now'} "
                         f"{f['value']:.4f} V (nearest DAC code)")
        elif status == AckStatus.REFUSED:
            self.set_msg("DAC override refused: charging is enabled. Press Charge OFF "
                         "(setpoint 0) first.", True)
        elif status == AckStatus.OUT_OF_RANGE:
            self.set_msg(f"DAC override refused: voltage outside 0-{DAC_FULL_SCALE_V:g} V.", True)
        else:
            self.set_msg(f"DAC override failed: {AckStatus(status).name}", True)

    def on_status(self, f):
        self.override_active = bool(f["flags"] & SF_DAC_OVERRIDE)
        if self.override_active:
            self.dac_label.setStyleSheet("font-family: monospace; font-size: 14px; "
                                         "color: orange; font-weight: bold;")
            self.dac_label.setText(f"DAC FORCED: {f['dac_set_v']:.4f} V   "
                                   f"A4 reads {f['dac_int']:.4f} V (calibrated)")
        else:
            self.dac_label.setStyleSheet("font-family: monospace; font-size: 14px;")
            self.dac_label.setText(f"DAC: setpoint control ({f['dac_set_v']:.4f} V)")

    # ----- actions -----
    def set_msg(self, text, error=False):
        self.msg_label.setStyleSheet("font-family: monospace; font-size: 12px; "
                                     + ("color: #e04040; font-weight: bold;" if error
                                        else "color: gray;"))
        self.msg_label.setText(text)

    def add_point(self):
        self.meter_spin.interpretText()
        key = self.ch_combo.currentData()
        stats = self.window_stats(key)
        if stats is None or stats[2] < CAL_MIN_SAMPLES:
            self.set_msg(f"Not enough samples for {self.ch_combo.currentText()} yet "
                         f"(need {CAL_MIN_SAMPLES}) -- is the device streaming?", True)
            return
        raw, std, n, _cal = stats
        meter = self.meter_spin.value()
        self.points.append({"ch": key, "raw": raw, "std": std, "n": n, "meter": meter})
        self.set_msg(f"Added {self.ch_combo.currentText()}: raw {raw:.6f} V "
                     f"(std {std * 1000:.3f} mV, n={n}) -> meter {meter:.5f} V")
        self.rebuild()

    def remove_point(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.points:
            rows = [len(self.points) - 1]
        for r in rows:
            del self.points[r]
        self.rebuild()

    def clear_channel(self):
        key = self.ch_combo.currentData()
        self.points = [p for p in self.points if p["ch"] != key]
        self.rebuild()

    def save_csv(self):
        path = time.strftime("calibration_%Y%m%d_%H%M%S.csv")
        with open(path, "w") as fh:
            fh.write("channel,raw_mean_v,raw_std_v,n,meter_v\n")
            for p in self.points:
                fh.write(f"{p['ch']},{p['raw']:.9f},{p['std']:.9f},{p['n']},{p['meter']:.6f}\n")
            for line in self.fit_text.toPlainText().splitlines():
                fh.write(f"# {line}\n")
        self.set_msg(f"Saved {len(self.points)} points + fit to {path}")
        print(f"Calibration saved to {path}", flush=True)

    def copy_fit(self):
        text = self.fit_text.toPlainText()
        QtWidgets.QApplication.clipboard().setText(text)
        self.set_msg("Firmware lines copied to the clipboard (also printed to the console).")
        print(text, flush=True)

    # ----- fitting -----
    @staticmethod
    def fit(points):
        """Least-squares meter = scale * raw + offset. Returns (scale, offset) or None."""
        raw = np.array([p["raw"] for p in points])
        meter = np.array([p["meter"] for p in points])
        if len(points) < 2 or np.ptp(raw) < 1e-6:
            return None
        scale, offset = np.polyfit(raw, meter, 1)
        return float(scale), float(offset)

    def rebuild(self):
        fits = {}
        lines = []
        for key, label, _rf, _cf, prefix, _t in CAL_CHANNELS:
            pts = [p for p in self.points if p["ch"] == key]
            if not pts:
                continue
            res = self.fit(pts)
            if res is None:
                lines.append(f"// {label}: {len(pts)} point(s) -- need 2 at different voltages")
                lines.append("")
                continue
            scale, offset = res
            fits[key] = res
            worst = max(abs(p["meter"] - (scale * p["raw"] + offset)) for p in pts)
            lines.append(f"// {label}: {len(pts)} points, max residual {worst * 1000:.3f} mV "
                         f"(fit {time.strftime('%Y-%m-%d')})")
            lines.append(f"// Y = {scale:.6f}*X {'-' if offset < 0 else '+'} {abs(offset):.6g}")
            if not 0.8 <= scale <= 1.25 or abs(offset) > 0.25:
                # A real cal is close to Y = X. Far from it usually means points were added
                # on the wrong channel, the input didn't actually change between points, or
                # the divider constants in the firmware don't match the board.
                lines.append("// WARNING: far from Y = X -- check the channel, that the input "
                             "really moved between points, and the divider constants. "
                             "Don't paste this blindly.")
            lines.append(f"const float {prefix}_OFFSET = {offset:.8g}f;")
            lines.append(f"const float {prefix}_SCALE  = {scale:.8g}f;")
            lines.append("")
        self.fit_text.setPlainText("\n".join(lines).rstrip())

        labels = {c[0]: c[1] for c in CAL_CHANNELS}
        self.table.setRowCount(len(self.points))
        for row, p in enumerate(self.points):
            if p["ch"] in fits:
                s, o = fits[p["ch"]]
                resid = f"{(p['meter'] - (s * p['raw'] + o)) * 1000:+.3f}"
            else:
                resid = "--"
            cells = [labels[p["ch"]], f"{p['raw']:.6f}", f"{p['std'] * 1000:.3f}",
                     str(p["n"]), f"{p['meter']:.5f}", resid]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    # ----- display (called from the main update timer) -----
    def refresh(self):
        now = time.monotonic()
        if now - self._last_live > 0.2:
            self._last_live = now
            for key, *_rest in CAL_CHANNELS:
                stats = self.window_stats(key)
                cells = self.live_labels[key]
                if stats is None:
                    for c in cells:
                        c.setText("--")
                    continue
                raw, std, n, cal = stats
                cells[0].setText(f"{raw:.6f}")
                cells[1].setText(f"{std * 1000:.3f}")
                cells[2].setText(str(n))
                cells[3].setText(f"{cal:.6f}")
                # newest single sample: shows a slowly settling input in real time,
                # where the window mean always lags by up to one window
                cells[4].setText(f"{self.samples[key][-1][1]:.6f}")
        if now - self._last_plot > 0.1:
            self._last_plot = now
            for key, curve in self.curves.items():
                dq = self.samples[key]
                if dq:
                    arr = np.array(dq, dtype=np.float64)
                    curve.setData(arr[:, 0] - self.t0, arr[:, 1])


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
        "connected": True,
        "next_retry": 0.0,
        "cal_active": False,
        "plotted_count": 0,
        "last_plot": 0.0,
        # link lag: how far behind the device the plotter is (see on_fast)
        "lag_s": None,
        "lag_hist": deque(),
        "lag_min_offset": None,
        "lag_t_us_last": None,
        "lag_wraps": 0,
    }

    # All commands go through send(): it reports failures in the GUI instead of raising.
    # The ACK arrives later and is handled in on_ack().
    def send(cmd, *a):
        if not state["connected"]:
            set_reply(f"NOT SENT {cmd.name}: device disconnected", True)
            return False
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

    # Sent on connect and again after a reconnect (the device may have rebooted and
    # reverted to its default rates).
    def startup_commands():
        send(Cmd.REQUEST_TELEM, Tlm.HELLO)
        send(Cmd.SET_TELEM_RATE, Tlm.FAST, args.fast_hz)
        send(Cmd.SET_TELEM_RATE, Tlm.STATUS, args.status_hz)
        if args.raw:
            send(Cmd.SET_TELEM_RATE, Tlm.RAW, RATE_EVERY_BATCH)
        if args.avg is not None:
            send(Cmd.SET_AVG, args.avg)
        if state["cal_active"]:
            send(Cmd.SET_TELEM_RATE, Tlm.CAL, RATE_EVERY_BATCH)
        send(Cmd.GET_SETPOINT)

    # --- pyqtgraph setup ---
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=False)

    win = QtWidgets.QWidget()
    win.setWindowTitle(f"OpenTMS Charge_Control -- {args.port}")
    win.resize(1150, 950)
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

    # --- Tabs: capacitor plot | calibration ---
    # The Calibration tab turns TLM_CAL on while it's showing and off when you leave it.
    tabs = QtWidgets.QTabWidget()
    layout.addWidget(tabs, stretch=1)
    plot_widget = pg.PlotWidget()
    tabs.addTab(plot_widget, "Capacitor voltage")
    cal_panel = CalibrationPanel(send)
    tabs.addTab(cal_panel, "Calibration")

    def on_tab_changed(index):
        state["cal_active"] = tabs.widget(index) is cal_panel
        if not state["cal_active"] and cal_panel.override_active:
            cal_panel.release_dac()   # never leave the DAC forced when you can't see it
        send(Cmd.SET_TELEM_RATE, Tlm.CAL,
             RATE_EVERY_BATCH if state["cal_active"] else RATE_OFF)
    tabs.currentChanged.connect(on_tab_changed)

    # --- Plot ---
    plot = plot_widget.getPlotItem()
    plot.setLabel("bottom", "Sample #")
    plot.setLabel("left", "Capacitor voltage (V)")
    plot.showGrid(x=True, y=True, alpha=0.3)
    plot.addLegend()
    plot.enableAutoRange(x=False, y=False)
    curve_cap = plot.plot(pen=pg.mkPen("c", width=1.5), name="v_cap (ADS8699)")
    curve_int = plot.plot(pen=pg.mkPen("y", width=1.2), name="v_cap_int (Teensy A0)")
    for c in (curve_cap, curve_int):            # draw ~1 point per pixel
        c.setDownsampling(auto=True, method="peak")
        c.setClipToView(True)

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
    if args.cal:
        tabs.setCurrentWidget(cal_panel)   # fires on_tab_changed -> turns TLM_CAL on

    # --- Link lag ---
    # offset = host clock - device clock (from the device's micros() in each FAST packet).
    # The smallest offset seen is the true transport delay; anything above it is backlog
    # sitting in USB / OS buffers waiting for this program to read it.
    def reset_lag():
        state["lag_s"] = None
        state["lag_hist"].clear()
        state["lag_min_offset"] = None
        state["lag_t_us_last"] = None
        state["lag_wraps"] = 0

    def measure_lag(t_us):
        last = state["lag_t_us_last"]
        if last is not None and t_us < last and last - t_us > 2**31:
            state["lag_wraps"] += 1               # micros() wraps every ~71.6 minutes
        state["lag_t_us_last"] = t_us
        dev_s = (state["lag_wraps"] * 2**32 + t_us) / 1e6
        offset = time.monotonic() - dev_s
        if state["lag_min_offset"] is None or offset < state["lag_min_offset"]:
            state["lag_min_offset"] = offset
        lag = offset - state["lag_min_offset"]
        # Report the worst lag of the last second: one read can drain old backlog and
        # then fresh packets, so the newest packet alone would hide a burst.
        hist = state["lag_hist"]
        now = time.monotonic()
        hist.append((now, lag))
        while hist and now - hist[0][0] > 1.0:
            hist.popleft()
        state["lag_s"] = max(l for _t, l in hist)

    # --- Packet handling ---
    # One handler per telemetry type. `f` is the packet's field dict, with the names
    # from TLM_LAYOUTS in opentms_protocol.py.
    def on_fast(f):
        seq = f["batch_seq"]
        if state["last_batch_seq"] is not None and seq < state["last_batch_seq"]:
            print("batch_seq went backwards -- device rebooted", flush=True)
            reset_lag()
        state["last_batch_seq"] = seq
        measure_lag(f["t_us"])
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
        cal_panel.on_status(f)

    # f["request"] is the command this ACK answers (attached by Link.poll()), so we know
    # e.g. which telemetry ID a SET_TELEM_RATE ACK refers to.
    def on_ack(f):
        req = f["request"]
        ok = f["status"] == AckStatus.OK
        set_reply(describe_ack(f), not ok or f.get("late_s") is not None)
        if req is None:
            return
        if req.cmd in (Cmd.SET_SETPOINT, Cmd.CHARGE_OFF, Cmd.GET_SETPOINT):
            state["fw_setpoint"] = f["value"]   # firmware value, even when it refused
        elif req.cmd == Cmd.SET_TELEM_RATE and ok and req.args[0] == Tlm.RAW:
            state["raw_on"] = int(f["value"]) != RATE_OFF
        elif req.cmd == Cmd.SET_DAC_OVERRIDE:
            cal_panel.on_dac_ack(f)

    def on_hello(f):
        if f["proto_version"] != PROTO_VERSION:
            set_reply(f"PROTOCOL MISMATCH: firmware v{f['proto_version']}, this script "
                      f"v{PROTO_VERSION} -- telemetry will not decode correctly", True)
        print(f"HELLO: protocol v{f['proto_version']}, setpoint max {f['setpoint_max']:.0f} V, "
              f"n_avg {f['n_avg']}, rates fast={rate_str(f['rate_fast'])} "
              f"raw={rate_str(f['rate_raw'])} status={rate_str(f['rate_status'])}", flush=True)
        state["raw_on"] = f["rate_raw"] != RATE_OFF

    # Routes every received packet by ID. A new telemetry packet needs an elif here.
    def handle_packets():
        try:
            pkts = link.poll()
        except (serial.SerialException, OSError) as e:
            mark_disconnected(e)
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
            elif pkt.id == Tlm.CAL:
                cal_panel.on_cal(f)
        for late in link.overdue(ACK_TIMEOUT_S):
            lag = state["lag_s"]
            set_reply(f"NO ACK for {late.cmd.name} within {ACK_TIMEOUT_S:g} s "
                      f"(link lag {'--' if lag is None else f'{lag * 1000:.0f} ms'})", True)

    # Port lost (unplugged, Teensy rebooting or being reflashed): close it and let
    # update() retry every RECONNECT_EVERY_S. Nothing here waits, so the window stays
    # responsive and Ctrl+C keeps working while the board is away.
    def mark_disconnected(err):
        if state["connected"]:
            print(f"\nSerial error: {err}\nDevice disconnected -- retrying every "
                  f"{RECONNECT_EVERY_S:.1f} s (Ctrl+C to quit)", flush=True)
        state["connected"] = False
        state["next_retry"] = time.time() + RECONNECT_EVERY_S
        try:
            ser.close()
        except Exception:
            pass

    def try_reconnect():
        if time.time() < state["next_retry"]:
            return
        state["next_retry"] = time.time() + RECONNECT_EVERY_S
        try:
            ser.open()
            ser.reset_input_buffer()
        except (serial.SerialException, OSError):
            return
        link.reset()
        reset_lag()
        state["connected"] = True
        print("Reconnected.", flush=True)
        startup_commands()   # device may have rebooted: re-apply rates

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
        lag = state["lag_s"]
        lagging = lag is not None and lag > LAG_WARN_S
        link_label.setStyleSheet("font-family: monospace; font-size: 12px; color: "
                                 + ("orange;" if stale or lagging or st["crc_errors"]
                                    or st["lost"] else "gray;"))
        lag_text = "--" if lag is None else f"{lag * 1000:.0f} ms"
        if not state["connected"]:
            link_label.setStyleSheet("font-family: monospace; font-size: 12px; "
                                     "color: #e04040; font-weight: bold;")
            link_label.setText(f"link: DISCONNECTED -- retrying {args.port} every "
                               f"{RECONNECT_EVERY_S:.1f} s")
        else:
            link_label.setText(
                f"link: lag {lag_text} | {st['packets']} pkts, lost {st['lost']}, "
                f"crc_err {st['crc_errors']}, framing {st['framing_errors']}, "
                f"len_err {st['length_errors']}, unknown {st['unknown']} | "
                f"RAW rows {state['raw_count']} | status {age}")

        refresh_setpoint_panel()

    def update():
        prev = state["sample_count"]
        if state["connected"]:
            handle_packets()
        else:
            try_reconnect()
        refresh_status_panel()
        if state["cal_active"]:
            cal_panel.refresh()

        if state["sample_count"] > prev and not state["first_data_reported"]:
            state["first_data_reported"] = True
            print(f"First FAST packet after {time.time() - state['start_time']:.1f}s.",
                  flush=True)

        # Redraw the capacitor plot only while its tab is showing, at most PLOT_HZ, and
        # convert only the visible samples (newest n) rather than the whole history.
        now = time.monotonic()
        if (state["sample_count"] > state["plotted_count"] and not state["cal_active"]
                and now - state["last_plot"] >= 1.0 / PLOT_HZ):
            state["last_plot"] = now
            state["plotted_count"] = state["sample_count"]
            n = min(state["visible_window"], len(xs))

            def newest(dq):
                return np.fromiter(itertools.islice(reversed(dq), n), np.float64, count=n)[::-1]
            x = newest(xs)
            y_cap = newest(ys_cap)
            curve_cap.setData(x, y_cap)
            series = [y_cap]
            if state["show_int"]:
                y_int = newest(ys_int)
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
            if state["cal_active"]:
                link.send(Cmd.SET_TELEM_RATE, Tlm.CAL, RATE_OFF)
            if cal_panel.override_active:
                link.send(Cmd.SET_DAC_OVERRIDE, 0, 0.0)
                print("DAC override released.", flush=True)
            ser.flush()   # make sure those last commands leave before the port closes
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
