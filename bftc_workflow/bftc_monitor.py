#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "dash",
# ]
# ///
"""
BFTC 4-Channel Temperature Monitor & Regulator (Dash / browser edition)
=======================================================================
Polls BFTC thermometer channels on a background thread, logs to CSV,
and serves a live-updating Plotly chart accessible from any browser on
the same network — no display or X forwarding required.

Includes full UI control to regulate the MXC heater on a selected MXC
thermometer channel using the hardware PID loop.

Requirements:
    pip install requests dash

Usage:
    python bftc_monitor.py                            # all defaults
    python bftc_monitor.py --ip 169.169.10.10:5001
    python bftc_monitor.py --channels 1 2 5 6 --interval 60
    python bftc_monitor.py --port 8051 --log-dir /home/user/logs

Then open  http://<linux-machine-ip>:8050  in any browser on the network.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import requests

os.environ.setdefault("NO_PROXY", "169.169.10.10,132.163.157.220,localhost,127.0.0.1")

# ---------------------------------------------------------------------------
# Inline BFTC client (standalone — no dependency on rest of repo)
# ---------------------------------------------------------------------------

class BFTC:
    def __init__(self, ip: str = "132.163.130.125:5001", timeout: float = 10.0):
        self.ip = ip
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"http://{self.ip}/{endpoint}"
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str) -> dict:
        url = f"http://{self.ip}/{endpoint}"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _time_window(self, minutes_ago: float) -> tuple[str, str]:
        now = datetime.now()
        start = now - timedelta(minutes=minutes_ago)
        fmt = "%Y-%m-%dT%H:%M:%S"
        return start.strftime(fmt), now.strftime(fmt)

    def get_temperature_history(self, channel: int, minutes: float = 2.0) -> list[float]:
        start_str, stop_str = self._time_window(minutes)
        payload = {
            "channel_nr": channel,
            "fields": ["temperature"],
            "start_time": start_str,
            "stop_time": stop_str,
        }
        try:
            data = self._post("channel/historical-data", payload)
            return data.get("measurements", {}).get("temperature", [])
        except Exception:
            # Let the caller handle missing history silently
            return []

    def get_temperature(self, channel: int, minutes: float = 2.0) -> float | None:
        history = self.get_temperature_history(channel, minutes)
        return history[-1] if history else None

    def set_channel_active(self, channel: int, active: bool) -> dict:
        return self._post("channel/update", {"active": active, "channel_nr": channel})

    def solo_channel(self, channel: int, all_channels: list[int]) -> None:
        for ch in all_channels:
            try:
                self.set_channel_active(ch, ch == channel)
            except Exception as exc:
                print(f"  [solo ch{ch}] {exc}", file=sys.stderr)

    def enable_all_channels(self, all_channels: list[int]) -> None:
        for ch in all_channels:
            try:
                self.set_channel_active(ch, True)
            except Exception as exc:
                print(f"  [enable ch{ch}] {exc}", file=sys.stderr)

    def set_heater_power(self, power_W: float, heater_nr: int = 4) -> dict:
        return self._post(
            "heater/update",
            {"active": True, "pid_mode": 0, "power": power_W, "heater_nr": heater_nr},
        )

    def set_heater_pid(
        self, setpoint_K: float, heater_nr: int = 4, channel: int | None = None, all_channels: list[int] | None = None
    ) -> dict:
        if channel is not None and all_channels is not None:
            self.solo_channel(channel, all_channels)
        return self._post(
            "heater/update",
            {
                "active": True,
                "pid_mode": 1,
                "setpoint": setpoint_K,
                "heater_nr": heater_nr,
            },
        )

    def stop_heater(self, heater_nr: int = 4) -> None:
        try:
            self.set_heater_power(0.0, heater_nr)
        except Exception as exc:
            print(f"  [stop heater power] {exc}", file=sys.stderr)
        try:
            self._post("heater/update", {"active": False, "heater_nr": heater_nr})
        except Exception as exc:
            print(f"  [stop heater active] {exc}", file=sys.stderr)

    def get_heater_power_history(self, heater_nr: int = 4, minutes: float = 2.0) -> list[float]:
        start_str, stop_str = self._time_window(minutes)
        payload = {
            "heater_nr": heater_nr,
            "fields": ["power"],
            "start_time": start_str,
            "stop_time": stop_str,
        }
        try:
            data = self._post("heater/historical-data", payload)
            return data.get("measurements", {}).get("power", [])
        except Exception:
            return []

    def get_latest_heater_power_uW(self, heater_nr: int = 4, minutes: float = 2.0) -> float | None:
        history = self.get_heater_power_history(heater_nr, minutes)
        return history[-1] * 1e6 if history else None


# ---------------------------------------------------------------------------
# Shared in-memory store  (background thread writes, Dash callbacks read)
# ---------------------------------------------------------------------------

MAX_POINTS = 20000   # large buffer for 10s resolution logs over 24+ hours

class DataStore:
    def __init__(self, channels: list[int]):
        self.lock = threading.Lock()
        self.timestamps: deque[datetime] = deque(maxlen=MAX_POINTS)
        self.series: dict[int, deque[float | None]] = {
            ch: deque(maxlen=MAX_POINTS) for ch in channels
        }
        self.heater_series: deque[float | None] = deque(maxlen=MAX_POINTS)      # MXC heater
        self.still_heater_series: deque[float | None] = deque(maxlen=MAX_POINTS) # Still heater
        self.last_row: dict[int, float | None] = {ch: None for ch in channels}
        self.last_heater: float | None = None       # MXC heater last power
        self.last_still_heater: float | None = None # Still heater last power

        # Logging configuration
        self.write_logs: bool = True
        self.log_dir: str = str(Path.home() / "bftc_logs")

        # Dynamic Configurations
        self.time_offset_hours: float = -1.0  # adjust timezone / DST discrepancy (defaults to -1.0)
        self.plot_window_minutes: float = 1440.0 # how much history to display (default 24h)
        self.poll_interval: int = 10          # default logging interval in seconds

        # Channel scan configurations
        self.channel_active: dict[int, bool] = {ch: True for ch in channels}

        # MXC Heater Regulation settings
        self.mxc_reg_active: bool = False
        self.mxc_pid_mode: int = 0  # 0 = Manual, 1 = PID
        self.mxc_manual_power_uW: float = 0.0
        self.mxc_reg_setpoint_mK: float | None = None
        self.mxc_reg_channel: int | None = None

        # Still Heater Regulation settings
        self.still_reg_active: bool = False
        self.still_pid_mode: int = 0  # 0 = Manual, 1 = PID
        self.still_manual_power_uW: float = 0.0
        self.still_reg_setpoint_mK: float | None = None
        self.still_reg_channel: int | None = None

        # BFTC Scanner IP
        self.bftc_ip: str = "132.163.130.125:5001"

    def load_history_from_logs(self) -> None:
        """Pre-populate memory deques from existing CSV files within the 24h window."""
        with self.lock:
            # Clear existing data first
            self.timestamps.clear()
            for ch in self.series:
                self.series[ch].clear()
            self.heater_series.clear()
            self.still_heater_series.clear()

            log_path = Path(self.log_dir)
            if not log_path.exists():
                print(f"Log directory '{log_path}' does not exist. Starting fresh.")
                return

            print(f"Scanning '{log_path}' to pre-load 24h history...")
            # Calculate 24h cutoff using the current offset
            now_adjusted = datetime.now() + timedelta(hours=self.time_offset_hours)
            cutoff = now_adjusted - timedelta(hours=24)

            # Target date folders for the last 3 days to cover 24h reliably
            target_dates = [
                (now_adjusted - timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(2, -1, -1)
            ]

            all_files = []
            for date_str in target_dates:
                date_dir = log_path / date_str
                if date_dir.exists():
                    all_files.extend(list(date_dir.glob("bftc_monitor_*.csv")))

            all_files.sort()
            data_points = []

            for filepath in all_files:
                try:
                    with open(filepath, "r", newline="", encoding="utf-8") as f:
                        reader = csv.reader(f)
                        header = next(reader, None)
                        if not header:
                            continue

                        try:
                            ts_idx = header.index("timestamp")
                        except ValueError:
                            continue

                        ch_indices = {}
                        for ch in self.series.keys():
                            col_name = f"ch{ch}_mK"
                            if col_name in header:
                                ch_indices[ch] = header.index(col_name)

                        mxc_idx = header.index("mxc_heater_uW") if "mxc_heater_uW" in header else None
                        still_idx = header.index("still_heater_uW") if "still_heater_uW" in header else None

                        for row in reader:
                            if not row:
                                continue
                            try:
                                ts_str = row[ts_idx]
                                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")

                                # Ignore points older than 24h
                                if ts < cutoff:
                                    continue

                                readings = {}
                                for ch, idx in ch_indices.items():
                                    val_str = row[idx]
                                    # Convert CSV mK back to K for in-memory store
                                    readings[ch] = float(val_str) / 1000.0 if val_str else None

                                mxc_val = float(row[mxc_idx]) if (mxc_idx is not None and row[mxc_idx]) else None
                                still_val = float(row[still_idx]) if (still_idx is not None and row[still_idx]) else None

                                data_points.append((ts, readings, mxc_val, still_val))
                            except Exception:
                                pass
                except Exception as e:
                    print(f"Error reading log file {filepath}: {e}", file=sys.stderr)

            # Sort and append up to maximum capacity
            data_points.sort(key=lambda x: x[0])
            appended_count = 0
            for ts, readings, mxc_val, still_val in data_points[-self.timestamps.maxlen:]:
                self.timestamps.append(ts)
                for ch, val in readings.items():
                    if ch in self.series:
                        self.series[ch].append(val)
                self.heater_series.append(mxc_val)
                self.still_heater_series.append(still_val)
                self.last_row = readings
                self.last_heater = mxc_val
                self.last_still_heater = still_val
                appended_count += 1

            print(f"Successfully pre-loaded {appended_count} data points from CSV logs.")

    def append(self, ts: datetime, readings: dict[int, float | None], mxc_heater_uW: float | None, still_heater_uW: float | None) -> None:
        with self.lock:
            self.timestamps.append(ts)
            for ch, val in readings.items():
                if ch in self.series:
                    self.series[ch].append(val)
            self.heater_series.append(mxc_heater_uW)
            self.still_heater_series.append(still_heater_uW)
            self.last_row = readings
            self.last_heater = mxc_heater_uW
            self.last_still_heater = still_heater_uW

    def snapshot(self):
        with self.lock:
            return (
                list(self.timestamps),
                {ch: list(vals) for ch, vals in self.series.items()},
                list(self.heater_series),
                list(self.still_heater_series),
                dict(self.last_row),
                self.last_heater,
                self.last_still_heater,
                self.mxc_reg_active,
                self.mxc_pid_mode,
                self.mxc_manual_power_uW,
                self.mxc_reg_setpoint_mK,
                self.mxc_reg_channel,
                self.still_reg_active,
                self.still_pid_mode,
                self.still_manual_power_uW,
                self.still_reg_setpoint_mK,
                self.still_reg_channel,
                self.write_logs,
                self.log_dir,
                dict(self.channel_active),
                self.bftc_ip,
                self.time_offset_hours,
                self.plot_window_minutes,
                self.poll_interval,
            )

    def get_bftc_ip(self) -> str:
        with self.lock:
            return self.bftc_ip

    def set_bftc_ip(self, ip: str) -> None:
        with self.lock:
            self.bftc_ip = ip

    def get_logging_config(self) -> tuple[bool, str]:
        with self.lock:
            return self.write_logs, self.log_dir

    def set_logging_config(self, write_logs: bool, log_dir: str) -> None:
        with self.lock:
            self.write_logs = write_logs
            self.log_dir = log_dir

    def get_channel_active(self, ch: int) -> bool:
        with self.lock:
            return self.channel_active.get(ch, True)

    def set_channel_active(self, ch: int, active: bool) -> None:
        with self.lock:
            self.channel_active[ch] = active

    def set_mxc_regulation(self, active: bool, pid_mode: int, manual_power_uW: float, setpoint_mK: float | None = None, channel: int | None = None) -> None:
        with self.lock:
            self.mxc_reg_active = active
            self.mxc_pid_mode = pid_mode
            self.mxc_manual_power_uW = manual_power_uW
            if active and pid_mode == 1:
                self.mxc_reg_setpoint_mK = setpoint_mK
                self.mxc_reg_channel = channel

    def set_still_regulation(self, active: bool, pid_mode: int, manual_power_uW: float, setpoint_mK: float | None = None, channel: int | None = None) -> None:
        with self.lock:
            self.still_reg_active = active
            self.still_pid_mode = pid_mode
            self.still_manual_power_uW = manual_power_uW
            if active and pid_mode == 1:
                self.still_reg_setpoint_mK = setpoint_mK
                self.still_reg_channel = channel

    def get_time_offset(self) -> float:
        with self.lock:
            return self.time_offset_hours

    def set_time_offset(self, val: float) -> None:
        with self.lock:
            self.time_offset_hours = val

    def get_plot_window(self) -> float:
        with self.lock:
            return self.plot_window_minutes

    def set_plot_window(self, val: float) -> None:
        with self.lock:
            self.plot_window_minutes = val

    def get_poll_interval(self) -> int:
        with self.lock:
            return self.poll_interval

    def set_poll_interval(self, val: int) -> None:
        with self.lock:
            self.poll_interval = val


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

def open_csv(log_dir: Path, channels: list[int]):
    date_str = datetime.now().strftime("%Y-%m-%d")
    target_dir = log_dir / date_str
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    path = target_dir / f"bftc_monitor_{stamp}.csv"
    fh = open(path, "w", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    writer.writerow(["timestamp", "elapsed_s"] + [f"ch{ch}_mK" for ch in channels] + ["mxc_heater_uW", "still_heater_uW"])
    fh.flush()
    print(f"Logging to: {path}")
    return path, fh, writer, date_str


# ---------------------------------------------------------------------------
# Background polling thread
# ---------------------------------------------------------------------------

def poll_loop(bf: BFTC, channels: list[int], interval: int,
              store: DataStore, start_time: float,
              stop_event: threading.Event) -> None:
    current_fh = None
    current_writer = None
    current_log_dir = None
    current_date_str = None

    while not stop_event.is_set():
        # Get active time offset and calculate corrected local timestamp
        offset = store.get_time_offset()
        ts = datetime.now() + timedelta(hours=offset)
        elapsed = round(time.time() - start_time, 1)
        readings: dict[int, float | None] = {}

        # Update BFTC IP dynamically if changed in UI
        active_ip = store.get_bftc_ip()
        if bf.ip != active_ip:
            print(f"[{ts.strftime('%H:%M:%S')}] BFTC IP changed from {bf.ip} to {active_ip}. Updating connection...")
            bf.ip = active_ip

        # 1. Print header
        print(f"[{ts.strftime('%H:%M:%S')}] polling instrumentation (IP: {bf.ip}):")

        # 2. Poll channels
        for ch in channels:
            is_active = store.get_channel_active(ch)
            if is_active:
                try:
                    t = bf.get_temperature(ch)
                except Exception as exc:
                    print(f"  Error reading CH {ch}: {exc}", file=sys.stderr)
                    t = None
                readings[ch] = t
                if t is not None:
                    if t < 1.0:
                        status = f"{t * 1000.0:.3f} mK"
                    else:
                        status = f"{t:.4f} K"
                else:
                    status = "N/A"
            else:
                readings[ch] = None
                status = "DISABLED"

            name_str = f" ({CHANNEL_NAMES.get(ch)})" if ch in CHANNEL_NAMES else ""
            print(f"  CH {ch}{name_str}: {status}")

        # 3. Poll heaters
        try:
            mxc_heater_uW = bf.get_latest_heater_power_uW(heater_nr=4)
        except Exception as exc:
            print(f"  Error reading MXC heater: {exc}", file=sys.stderr)
            mxc_heater_uW = None

        try:
            still_heater_uW = bf.get_latest_heater_power_uW(heater_nr=1)
        except Exception as exc:
            print(f"  Error reading Still heater: {exc}", file=sys.stderr)
            still_heater_uW = None

        if mxc_heater_uW is not None:
            print(f"  MXC Heater Power: {mxc_heater_uW:.2f} uW")
        else:
            print("  MXC Heater Power: N/A")

        if still_heater_uW is not None:
            print(f"  Still Heater Power: {still_heater_uW:.2f} uW")
        else:
            print("  Still Heater Power: N/A")

        # 4. Save readings to store
        store.append(ts, readings, mxc_heater_uW, still_heater_uW)

        # 5. Check and handle dynamic CSV logging
        write_logs, active_log_dir_str = store.get_logging_config()
        if write_logs:
            active_log_dir = Path(active_log_dir_str)
            active_date = ts.strftime("%Y-%m-%d")
            if current_fh is None or current_log_dir != active_log_dir or current_date_str != active_date:
                # Rotate/open new file in date-structured subfolder
                if current_fh is not None:
                    try:
                        current_fh.close()
                    except Exception:
                        pass
                try:
                    _, current_fh, current_writer, current_date_str = open_csv(active_log_dir, channels)
                    current_log_dir = active_log_dir
                except Exception as exc:
                    print(f"  [logger error] Failed to open CSV log file: {exc}", file=sys.stderr)
                    current_fh = None
                    current_writer = None
                    current_log_dir = None
                    current_date_str = None

            if current_writer is not None:
                try:
                    row = [ts.strftime("%Y-%m-%d %H:%M:%S"), elapsed]
                    for ch in channels:
                        t = readings[ch]
                        row.append(f"{t * 1000:.4f}" if t is not None else "")
                    row.append(f"{mxc_heater_uW:.4f}" if mxc_heater_uW is not None else "")
                    row.append(f"{still_heater_uW:.4f}" if still_heater_uW is not None else "")
                    current_writer.writerow(row)
                    current_fh.flush()
                    print(f"  Logged to CSV in: {current_log_dir / current_date_str}")
                except Exception as exc:
                    print(f"  [logger error] Failed to write row: {exc}", file=sys.stderr)
        else:
            if current_fh is not None:
                try:
                    current_fh.close()
                except Exception:
                    pass
                print("  Logging paused. Log file closed.")
                current_fh = None
                current_writer = None
                current_log_dir = None
                current_date_str = None

        # Sleep for dynamic poll interval
        active_interval = store.get_poll_interval()
        print(f"Next poll in {active_interval}s\n")
        stop_event.wait(timeout=active_interval)

    if current_fh is not None:
        try:
            current_fh.close()
        except Exception:
            pass
    print("Poll thread stopped.")

# ---------------------------------------------------------------------------
# Dash application
# ---------------------------------------------------------------------------

CHANNEL_COLORS = {1: "#ef4444", 2: "#22c55e", 5: "#ca8a04", 6: "#3b82f6"}

CHANNEL_NAMES = {
    1: "40 K flange",
    2: "4 K flange",
    5: "Still flange",
    6: "MXC flange"
}

def build_app(bf: BFTC, channels: list[int], store: DataStore, interval: int):
    from dash import Dash, dcc, html, callback_context
    from dash.dependencies import Input, Output, State
    import plotly.graph_objs as go

    app = Dash(__name__, title="BFTC Monitor")

    app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            body {
                margin: 0;
                background-color: #f8fafc;
            }
            .Select-control {
                background-color: #ffffff !important;
                border-color: #cbd5e1 !important;
            }
            .Select-value-label {
                color: #0f172a !important;
            }
            .Select-menu-outer {
                background-color: #ffffff !important;
                border-color: #cbd5e1 !important;
            }
            .VirtualisedSelectFocusedOption {
                background-color: #f1f5f9 !important;
            }
            .Select-option {
                background-color: #ffffff !important;
                color: #334155 !important;
            }
            .Select-option.is-focused {
                background-color: #f1f5f9 !important;
                color: #0f172a !important;
            }
            /* Interactive heater cards */
            .heater-card {
                transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s;
                cursor: pointer;
            }
            .heater-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 16px rgba(0, 0, 0, 0.06) !important;
                border-color: #f59e0b !important;
            }
            /* Vanilla CSS Toggle switch */
            .switch-container {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .switch-label {
                color: #64748b;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            .switch-outer {
                width: 34px;
                height: 18px;
                border-radius: 9px;
                position: relative;
                cursor: pointer;
                transition: background-color 0.2s;
            }
            .switch-circle {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background-color: #ffffff;
                position: absolute;
                top: 3px;
                transition: left 0.2s;
            }
            /* Segmented control buttons for modes */
            .mode-radio label {
                padding: 6px 16px !important;
                background-color: #f1f5f9 !important;
                color: #475569 !important;
                border: 1px solid #cbd5e1 !important;
                cursor: pointer;
                transition: all 0.2s;
            }
            .mode-radio input:checked + label {
                background-color: #4f46e5 !important;
                color: #ffffff !important;
                border-color: #4f46e5 !important;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

    # Prepopulate dropdown options for CSV locations
    log_options = [
        {"label": str(Path.home() / "bftc_logs"), "value": str(Path.home() / "bftc_logs")},
        {"label": str(Path.cwd() / "bftc_logs"), "value": str(Path.cwd() / "bftc_logs")},
        {"label": str(Path.cwd()), "value": str(Path.cwd())},
    ]

    app.layout = html.Div(
        style={
            "fontFamily": "Inter, Arial, sans-serif",
            "backgroundColor": "#f8fafc",
            "minHeight": "100vh",
            "padding": "24px"
        },
        children=[
            # Dummy state stores for button clicks
            dcc.Store(id="dummy-ch-toggle-store"),
            dcc.Store(id="dummy-heater-toggle-store"),

            # Top Control & Title Bar
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "20px",
                    "backgroundColor": "#ffffff",
                    "borderRadius": "12px",
                    "padding": "12px 20px",
                    "marginBottom": "20px",
                    "border": "1px solid #e2e8f0",
                    "boxShadow": "0 1px 3px rgba(0,0,0,0.05)",
                    "flexWrap": "wrap"
                },
                children=[
                    # Title & Live indicator
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "12px"},
                        children=[
                            html.H1("BFTC Standalone Monitor & Regulator",
                                    style={"color": "#0f172a", "margin": 0, "fontSize": "18px", "fontWeight": 600}),
                            html.Span(id="live-badge",
                                      style={"padding": "3px 10px", "borderRadius": "12px", "fontSize": "11px",
                                             "backgroundColor": "#e0f2fe", "color": "#0369a1", "fontWeight": 600}),
                        ]
                    ),

                    # Scanner IP connection input
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px", "marginLeft": "24px"},
                        children=[
                            html.Span("Scanner IP:", style={"color": "#475569", "fontSize": "13px", "fontWeight": 600}),
                            dcc.Input(
                                id="bftc-ip-input",
                                type="text",
                                value=store.get_bftc_ip(),
                                style={
                                    "backgroundColor": "#ffffff",
                                    "color": "#0f172a",
                                    "border": "1px solid #cbd5e1",
                                    "borderRadius": "6px",
                                    "padding": "6px 12px",
                                    "width": "160px",
                                    "fontSize": "13px",
                                    "height": "34px",
                                    "boxSizing": "border-box"
                                }
                            ),
                            html.Button(
                                "Connect",
                                id="bftc-ip-btn",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#4f46e5",
                                    "color": "#ffffff",
                                    "border": "none",
                                    "borderRadius": "6px",
                                    "padding": "0 14px",
                                    "fontWeight": "600",
                                    "fontSize": "13px",
                                    "cursor": "pointer",
                                    "height": "34px",
                                    "transition": "background-color 0.2s"
                                }
                            ),
                            html.Span(id="bftc-ip-status", style={"fontSize": "12px", "color": "#10b981", "fontWeight": 500, "marginLeft": "4px"})
                        ]
                    ),

                    html.Div(style={"flex": "1"}),

                    # Logging controls
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "16px", "flexWrap": "wrap"},
                        children=[
                            # Checkbox
                            dcc.Checklist(
                                id="write-log-checkbox",
                                options=[{"label": " Write log files?", "value": "write"}],
                                value=["write"],
                                style={"color": "#475569", "fontSize": "13px", "fontWeight": 500}
                            ),
                            # Dropdown
                            html.Div(
                                style={"width": "260px"},
                                children=[
                                    dcc.Dropdown(
                                        id="log-location-dropdown",
                                        options=log_options,
                                        value=str(Path.home() / "bftc_logs"),
                                        clearable=False,
                                        searchable=True,
                                    )
                                ]
                            )
                        ]
                    )
                ]
            ),

            # Secondary Control & Settings Bar
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "20px",
                    "backgroundColor": "#ffffff",
                    "borderRadius": "12px",
                    "padding": "12px 20px",
                    "marginBottom": "20px",
                    "border": "1px solid #e2e8f0",
                    "boxShadow": "0 1px 3px rgba(0,0,0,0.05)",
                    "flexWrap": "wrap"
                },
                children=[
                    # Time Window (Minutes)
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px"},
                        children=[
                            html.Span("Plot Window (mins):", style={"color": "#475569", "fontSize": "13px", "fontWeight": 600}),
                            dcc.Input(
                                id="plot-window-input",
                                type="text",
                                value=str(int(store.get_plot_window())),
                                style={
                                    "backgroundColor": "#ffffff",
                                    "color": "#0f172a",
                                    "border": "1px solid #cbd5e1",
                                    "borderRadius": "6px",
                                    "padding": "6px 12px",
                                    "width": "100px",
                                    "fontSize": "13px",
                                    "height": "34px",
                                    "boxSizing": "border-box"
                                }
                            ),
                        ]
                    ),

                    # Time Offset (Hours)
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px"},
                        children=[
                            html.Span("Time Offset (hours):", style={"color": "#475569", "fontSize": "13px", "fontWeight": 600}),
                            dcc.Input(
                                id="time-offset-input",
                                type="text",
                                value=str(store.get_time_offset()),
                                style={
                                    "backgroundColor": "#ffffff",
                                    "color": "#0f172a",
                                    "border": "1px solid #cbd5e1",
                                    "borderRadius": "6px",
                                    "padding": "6px 12px",
                                    "width": "100px",
                                    "fontSize": "13px",
                                    "height": "34px",
                                    "boxSizing": "border-box"
                                }
                            ),
                        ]
                    ),

                    # Poll / Log Interval (Seconds)
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px"},
                        children=[
                            html.Span("Log Interval (s):", style={"color": "#475569", "fontSize": "13px", "fontWeight": 600}),
                            dcc.Input(
                                id="poll-interval-input",
                                type="text",
                                value=str(store.get_poll_interval()),
                                style={
                                    "backgroundColor": "#ffffff",
                                    "color": "#0f172a",
                                    "border": "1px solid #cbd5e1",
                                    "borderRadius": "6px",
                                    "padding": "6px 12px",
                                    "width": "100px",
                                    "fontSize": "13px",
                                    "height": "34px",
                                    "boxSizing": "border-box"
                                }
                            ),
                        ]
                    ),

                    # Reload History Button
                    html.Button(
                        "Reload History from Logs",
                        id="reload-history-btn",
                        n_clicks=0,
                        style={
                            "backgroundColor": "#6366f1",
                            "color": "#ffffff",
                            "border": "none",
                            "borderRadius": "6px",
                            "padding": "0 16px",
                            "fontWeight": "600",
                            "fontSize": "13px",
                            "cursor": "pointer",
                            "height": "34px",
                            "marginLeft": "auto",
                            "transition": "background-color 0.2s"
                        }
                    ),
                    html.Span(id="reload-status-span", style={"fontSize": "12px", "color": "#10b981", "fontWeight": 500, "marginLeft": "4px"})
                ]
            ),

            # Status Cards Section (4 Channels + 2 Heaters)
            html.Div(
                style={
                    "display": "flex",
                    "gap": "12px",
                    "marginBottom": "20px",
                    "flexWrap": "wrap"
                },
                children=[
                    # Channel 1 Card
                    html.Div(
                        id="card-ch1",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1", "minWidth": "140px", "borderLeft": "4px solid #ef4444", 
                               "borderTop": "1px solid #e2e8f0", "borderRight": "1px solid #e2e8f0", "borderBottom": "1px solid #e2e8f0",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("CH 1 (40 K flange)", style={"color": "#ef4444", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px", "textTransform": "uppercase"}),
                            html.Div(id="read-ch1", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(
                                className="switch-container",
                                style={"marginTop": "8px"},
                                children=[
                                    html.Span("SCAN", className="switch-label"),
                                    html.Div(
                                        id="toggle-ch1",
                                        n_clicks=0,
                                        children=html.Div(id="circle-ch1")
                                    )
                                ]
                            )
                        ]
                    ),
                    # Channel 2 Card
                    html.Div(
                        id="card-ch2",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1", "minWidth": "140px", "borderLeft": "4px solid #22c55e", 
                               "borderTop": "1px solid #e2e8f0", "borderRight": "1px solid #e2e8f0", "borderBottom": "1px solid #e2e8f0",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("CH 2 (4 K flange)", style={"color": "#22c55e", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px", "textTransform": "uppercase"}),
                            html.Div(id="read-ch2", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(
                                className="switch-container",
                                style={"marginTop": "8px"},
                                children=[
                                    html.Span("SCAN", className="switch-label"),
                                    html.Div(
                                        id="toggle-ch2",
                                        n_clicks=0,
                                        children=html.Div(id="circle-ch2")
                                    )
                                ]
                            )
                        ]
                    ),
                    # Channel 5 Card
                    html.Div(
                        id="card-ch5",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1", "minWidth": "140px", "borderLeft": "4px solid #ca8a04", 
                               "borderTop": "1px solid #e2e8f0", "borderRight": "1px solid #e2e8f0", "borderBottom": "1px solid #e2e8f0",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("CH 5 (Still flange)", style={"color": "#ca8a04", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px", "textTransform": "uppercase"}),
                            html.Div(id="read-ch5", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(
                                className="switch-container",
                                style={"marginTop": "8px"},
                                children=[
                                    html.Span("SCAN", className="switch-label"),
                                    html.Div(
                                        id="toggle-ch5",
                                        n_clicks=0,
                                        children=html.Div(id="circle-ch5")
                                    )
                                ]
                            )
                        ]
                    ),
                    # Channel 6 Card
                    html.Div(
                        id="card-ch6",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1", "minWidth": "140px", "borderLeft": "4px solid #3b82f6", 
                               "borderTop": "1px solid #e2e8f0", "borderRight": "1px solid #e2e8f0", "borderBottom": "1px solid #e2e8f0",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("CH 6 (MXC flange)", style={"color": "#3b82f6", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px", "textTransform": "uppercase"}),
                            html.Div(id="read-ch6", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(
                                className="switch-container",
                                style={"marginTop": "8px"},
                                children=[
                                    html.Span("SCAN", className="switch-label"),
                                    html.Div(
                                        id="toggle-ch6",
                                        n_clicks=0,
                                        children=html.Div(id="circle-ch6")
                                    )
                                ]
                            )
                        ]
                    ),
                    # MXC Heater Card (Interactive)
                    html.Div(
                        id="mxc-heater-card",
                        n_clicks=0,
                        className="heater-card",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1.2", "minWidth": "160px", "border": "1px solid #e2e8f0", "borderLeft": "4px solid #f59e0b",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("MXC HEATER (CH 6 / HT 4)", style={"color": "#f59e0b", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px"}),
                            html.Div(id="read-mxc", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(id="sub-mxc", style={"color": "#475569", "fontSize": "11px", "marginTop": "4px", "fontWeight": 500})
                        ]
                    ),
                    # Still Heater Card (Interactive)
                    html.Div(
                        id="still-heater-card",
                        n_clicks=0,
                        className="heater-card",
                        style={"backgroundColor": "#ffffff", "borderRadius": "10px", "padding": "12px 18px",
                               "flex": "1.2", "minWidth": "160px", "border": "1px solid #e2e8f0", "borderLeft": "4px solid #ec4899",
                               "boxShadow": "0 1px 3px rgba(0,0,0,0.05)"},
                        children=[
                            html.Div("STILL HEATER (CH 5 / HT 1)", style={"color": "#ec4899", "fontWeight": 700, "fontSize": "11px", "letterSpacing": "0.5px"}),
                            html.Div(id="read-still", style={"color": "#0f172a", "fontWeight": 600, "fontSize": "18px", "marginTop": "4px"}),
                            html.Div(id="sub-still", style={"color": "#475569", "fontSize": "11px", "marginTop": "4px", "fontWeight": 500})
                        ]
                    )
                ]
            ),

            # Separated Plots Section (2x2 Grid)
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(auto-fit, minmax(450px, 1fr))",
                    "gap": "16px",
                    "marginBottom": "20px"
                },
                children=[
                    dcc.Graph(
                        id="chart-ch1",
                        config={"displayModeBar": False},
                        style={"borderRadius": "12px", "overflow": "hidden", "backgroundColor": "#ffffff", "border": "1px solid #e2e8f0"}
                    ),
                    dcc.Graph(
                        id="chart-ch2",
                        config={"displayModeBar": False},
                        style={"borderRadius": "12px", "overflow": "hidden", "backgroundColor": "#ffffff", "border": "1px solid #e2e8f0"}
                    ),
                    dcc.Graph(
                        id="chart-ch5",
                        config={"displayModeBar": False},
                        style={"borderRadius": "12px", "overflow": "hidden", "backgroundColor": "#ffffff", "border": "1px solid #e2e8f0"}
                    ),
                    dcc.Graph(
                        id="chart-ch6",
                        config={"displayModeBar": False},
                        style={"borderRadius": "12px", "overflow": "hidden", "backgroundColor": "#ffffff", "border": "1px solid #e2e8f0"}
                    ),
                ]
            ),

            # Bottom Heater Controls (Tabbed Panel)
            html.Div(
                style={
                    "backgroundColor": "#ffffff",
                    "borderRadius": "12px",
                    "padding": "20px",
                    "marginBottom": "20px",
                    "boxShadow": "0 1px 3px rgba(0,0,0,0.05)",
                    "border": "1px solid #e2e8f0"
                },
                children=[
                    dcc.Tabs(
                        id="heater-tabs",
                        value="mxc-tab",
                        colors={"border": "#e2e8f0", "primary": "#4f46e5", "background": "#f8fafc"},
                        style={"height": "40px", "marginBottom": "20px"},
                        children=[
                            # MXC tab
                            dcc.Tab(
                                label="MXC Heater Control (HT 4)",
                                value="mxc-tab",
                                style={"backgroundColor": "#f1f5f9", "color": "#475569", "border": "1px solid #cbd5e1", "fontWeight": 600, "fontSize": "13px"},
                                selected_style={"backgroundColor": "#ffffff", "color": "#f59e0b", "border": "1px solid #cbd5e1", "borderTop": "3px solid #f59e0b", "fontWeight": 600, "fontSize": "13px"},
                                children=[
                                    html.Div(
                                        style={"padding": "16px 8px"},
                                        children=[
                                            html.Div(
                                                style={"display": "flex", "alignItems": "center", "gap": "16px", "marginBottom": "20px"},
                                                children=[
                                                    html.Span("Heater Master Status: ", style={"color": "#334155", "fontSize": "14px", "fontWeight": 600}),
                                                    html.Div(
                                                        id="mxc-master-toggle",
                                                        n_clicks=0,
                                                        children=html.Div(id="mxc-master-circle")
                                                    ),
                                                    dcc.RadioItems(
                                                        id="mxc-mode-select",
                                                        options=[
                                                            {"label": "Manual Power", "value": "manual"},
                                                            {"label": "PID Regulation", "value": "pid"}
                                                        ],
                                                        value="manual",
                                                        className="mode-radio",
                                                        labelStyle={"display": "inline-block", "marginRight": "0"}
                                                    )
                                                ]
                                            ),

                                            # Manual Mode Settings
                                            html.Div(
                                                id="mxc-manual-section",
                                                children=[
                                                    html.Label("Constant Heater Power (uW)", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                    dcc.Input(
                                                        id="mxc-manual-power-input",
                                                        type="number",
                                                        value=0.0,
                                                        min=0.0,
                                                        step=0.01,
                                                        style={"backgroundColor": "#ffffff", "color": "#0f172a", "border": "1px solid #cbd5e1", "borderRadius": "6px", "padding": "8px 12px", "width": "200px"}
                                                    )
                                                ]
                                            ),

                                            # PID Mode Settings
                                            html.Div(
                                                id="mxc-pid-section",
                                                children=[
                                                    html.Div(
                                                        style={"display": "flex", "gap": "16px", "flexWrap": "wrap"},
                                                        children=[
                                                            html.Div(
                                                                style={"width": "200px"},
                                                                children=[
                                                                    html.Label("Coupled Thermometer", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                                    dcc.Dropdown(
                                                                        id="mxc-coupled-dropdown",
                                                                        options=[{"label": f"CH {ch} ({CHANNEL_NAMES.get(ch, '')})" if ch in CHANNEL_NAMES else f"CH {ch}", "value": ch} for ch in channels],
                                                                        value=6,
                                                                        clearable=False,
                                                                    )
                                                                ]
                                                            ),
                                                            html.Div(
                                                                style={"width": "200px"},
                                                                children=[
                                                                    html.Label("Target Setpoint (mK)", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                                    dcc.Input(
                                                                        id="mxc-setpoint-input",
                                                                        type="number",
                                                                        placeholder="e.g. 50.0",
                                                                        min=0.0,
                                                                        step=0.1,
                                                                        style={"backgroundColor": "#ffffff", "color": "#0f172a", "border": "1px solid #cbd5e1", "borderRadius": "6px", "padding": "8px 12px", "width": "100%", "boxSizing": "border-box", "height": "38px"}
                                                                    )
                                                                ]
                                                            )
                                                        ]
                                                    )
                                                ]
                                            ),

                                            # Buttons & Action Output
                                            html.Div(
                                                style={"display": "flex", "gap": "12px", "alignItems": "center", "marginTop": "24px", "paddingTop": "16px", "borderTop": "1px solid #e2e8f0"},
                                                children=[
                                                    html.Button("Engage / Update", id="mxc-engage-btn", n_clicks=0, style={"backgroundColor": "#22c55e", "color": "#ffffff", "border": "none", "borderRadius": "6px", "padding": "10px 24px", "fontWeight": "600", "cursor": "pointer", "transition": "background-color 0.2s"}),
                                                    html.Button("Stop / Zero", id="mxc-stop-btn", n_clicks=0, style={"backgroundColor": "#ef4444", "color": "#ffffff", "border": "none", "borderRadius": "6px", "padding": "10px 24px", "fontWeight": "600", "cursor": "pointer", "transition": "background-color 0.2s"}),
                                                    html.Div(id="mxc-reg-status-display", style={"marginLeft": "16px", "fontSize": "13px", "color": "#334155"}),
                                                    html.Div(id="mxc-reg-action-status", style={"marginLeft": "auto", "fontSize": "13px", "fontWeight": 600})
                                                ]
                                            )
                                        ]
                                    )
                                ]
                            ),
                            # Still tab
                            dcc.Tab(
                                label="Still Heater Control (HT 1)",
                                value="still-tab",
                                style={"backgroundColor": "#f1f5f9", "color": "#475569", "border": "1px solid #cbd5e1", "fontWeight": 600, "fontSize": "13px"},
                                selected_style={"backgroundColor": "#ffffff", "color": "#ec4899", "border": "1px solid #cbd5e1", "borderTop": "3px solid #ec4899", "fontWeight": 600, "fontSize": "13px"},
                                children=[
                                    html.Div(
                                        style={"padding": "16px 8px"},
                                        children=[
                                            html.Div(
                                                style={"display": "flex", "alignItems": "center", "gap": "16px", "marginBottom": "20px"},
                                                children=[
                                                    html.Span("Heater Master Status: ", style={"color": "#334155", "fontSize": "14px", "fontWeight": 600}),
                                                    html.Div(
                                                        id="still-master-toggle",
                                                        n_clicks=0,
                                                        children=html.Div(id="still-master-circle")
                                                    ),
                                                    dcc.RadioItems(
                                                        id="still-mode-select",
                                                        options=[
                                                            {"label": "Manual Power", "value": "manual"},
                                                            {"label": "PID Regulation", "value": "pid"}
                                                        ],
                                                        value="manual",
                                                        className="mode-radio",
                                                        labelStyle={"display": "inline-block", "marginRight": "0"}
                                                    )
                                                ]
                                            ),

                                            # Manual Mode Settings
                                            html.Div(
                                                id="still-manual-section",
                                                children=[
                                                    html.Label("Constant Heater Power (uW)", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                    dcc.Input(
                                                        id="still-manual-power-input",
                                                        type="number",
                                                        value=0.0,
                                                        min=0.0,
                                                        step=0.01,
                                                        style={"backgroundColor": "#ffffff", "color": "#0f172a", "border": "1px solid #cbd5e1", "borderRadius": "6px", "padding": "8px 12px", "width": "200px"}
                                                    )
                                                ]
                                            ),

                                            # PID Mode Settings
                                            html.Div(
                                                id="still-pid-section",
                                                children=[
                                                    html.Div(
                                                        style={"display": "flex", "gap": "16px", "flexWrap": "wrap"},
                                                        children=[
                                                            html.Div(
                                                                style={"width": "200px"},
                                                                children=[
                                                                    html.Label("Coupled Thermometer", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                                    dcc.Dropdown(
                                                                        id="still-coupled-dropdown",
                                                                        options=[{"label": f"CH {ch} ({CHANNEL_NAMES.get(ch, '')})" if ch in CHANNEL_NAMES else f"CH {ch}", "value": ch} for ch in channels],
                                                                        value=5,
                                                                        clearable=False,
                                                                    )
                                                                ]
                                                            ),
                                                            html.Div(
                                                                style={"width": "200px"},
                                                                children=[
                                                                    html.Label("Target Setpoint (mK)", style={"color": "#475569", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                                                    dcc.Input(
                                                                        id="still-setpoint-input",
                                                                        type="number",
                                                                        placeholder="e.g. 800.0",
                                                                        min=0.0,
                                                                        step=0.1,
                                                                        style={"backgroundColor": "#ffffff", "color": "#0f172a", "border": "1px solid #cbd5e1", "borderRadius": "6px", "padding": "8px 12px", "width": "100%", "boxSizing": "border-box", "height": "38px"}
                                                                    )
                                                                ]
                                                            )
                                                        ]
                                                    )
                                                ]
                                            ),

                                            # Buttons & Action Output
                                            html.Div(
                                                style={"display": "flex", "gap": "12px", "alignItems": "center", "marginTop": "24px", "paddingTop": "16px", "borderTop": "1px solid #e2e8f0"},
                                                children=[
                                                    html.Button("Engage / Update", id="still-engage-btn", n_clicks=0, style={"backgroundColor": "#22c55e", "color": "#ffffff", "border": "none", "borderRadius": "6px", "padding": "10px 24px", "fontWeight": "600", "cursor": "pointer", "transition": "background-color 0.2s"}),
                                                    html.Button("Stop / Zero", id="still-stop-btn", n_clicks=0, style={"backgroundColor": "#ef4444", "color": "#ffffff", "border": "none", "borderRadius": "6px", "padding": "10px 24px", "fontWeight": "600", "cursor": "pointer", "transition": "background-color 0.2s"}),
                                                    html.Div(id="still-reg-status-display", style={"marginLeft": "16px", "fontSize": "13px", "color": "#334155"}),
                                                    html.Div(id="still-reg-action-status", style={"marginLeft": "auto", "fontSize": "13px", "fontWeight": 600})
                                                ]
                                            )
                                        ]
                                    )
                                ]
                            )
                        ]
                    )
                ]
            ),

            # Interval timer
            dcc.Interval(
                id="interval",
                interval=interval * 1000,
                n_intervals=0
            ),

            # Footer
            html.Div(
                id="footer",
                style={"color": "#64748b", "fontSize": "12px", "marginTop": "16px", "textAlign": "right"}
            )
        ]
    )

    # Helper function to generate custom slider toggle styles dynamically
    def get_slider_styles(active: bool):
        outer = {
            "width": "34px",
            "height": "18px",
            "borderRadius": "9px",
            "position": "relative",
            "cursor": "pointer",
            "transition": "background-color 0.2s",
            "display": "inline-block",
            "verticalAlign": "middle",
            "backgroundColor": "#22c55e" if active else "#475569"
        }
        circle = {
            "width": "12px",
            "height": "12px",
            "borderRadius": "50%",
            "backgroundColor": "#ffffff",
            "position": "absolute",
            "top": "3px",
            "transition": "left 0.2s",
            "left": "19px" if active else "3px"
        }
        return outer, circle

    # 1. Main Live Update Interval Callback
    @app.callback(
        Output("chart-ch1", "figure"),
        Output("chart-ch2", "figure"),
        Output("chart-ch5", "figure"),
        Output("chart-ch6", "figure"),
        Output("read-ch1", "children"),
        Output("read-ch2", "children"),
        Output("read-ch5", "children"),
        Output("read-ch6", "children"),
        Output("toggle-ch1", "style"), Output("circle-ch1", "style"),
        Output("toggle-ch2", "style"), Output("circle-ch2", "style"),
        Output("toggle-ch5", "style"), Output("circle-ch5", "style"),
        Output("toggle-ch6", "style"), Output("circle-ch6", "style"),
        Output("read-mxc", "children"),
        Output("sub-mxc", "children"),
        Output("read-still", "children"),
        Output("sub-still", "children"),
        Output("mxc-master-toggle", "style"), Output("mxc-master-circle", "style"),
        Output("still-master-toggle", "style"), Output("still-master-circle", "style"),
        Output("live-badge", "children"),
        Output("live-badge", "style"),
        Output("footer", "children"),
        Output("mxc-reg-status-display", "children"),
        Output("still-reg-status-display", "children"),
        Input("interval", "n_intervals")
    )
    def update_live_dash(_n):
        (
            timestamps, series, mxc_heater_series, still_heater_series, last_row,
            last_mxc_heater, last_still_heater,
            mxc_reg_active, mxc_pid_mode, mxc_manual_power_uW, mxc_reg_setpoint_mK, mxc_reg_channel,
            still_reg_active, still_pid_mode, still_manual_power_uW, still_reg_setpoint_mK, still_reg_channel,
            write_logs, log_dir, channel_active, _bftc_ip,
            time_offset_hours, plot_window_minutes, poll_interval
        ) = store.snapshot()

        # Filter data points to match plot_window_minutes
        if timestamps:
            cutoff_time = timestamps[-1] - timedelta(minutes=plot_window_minutes)
            
            # Find the starting index that is within the plot window
            idx = 0
            for i, ts in enumerate(timestamps):
                if ts >= cutoff_time:
                    idx = i
                    break
            
            # Slice all arrays from idx to the end
            timestamps_filtered = list(timestamps)[idx:]
            series_filtered = {ch: list(vals)[idx:] for ch, vals in series.items()}
            mxc_heater_series_filtered = list(mxc_heater_series)[idx:] if mxc_heater_series else []
            still_heater_series_filtered = list(still_heater_series)[idx:] if still_heater_series else []
        else:
            timestamps_filtered = []
            series_filtered = {ch: [] for ch in series}
            mxc_heater_series_filtered = []
            still_heater_series_filtered = []

        # Generate individual Plots
        def make_figure(ch: int, temp_series: list, title: str, color: str, unit: str, heater_trace=None):
            traces = [
                go.Scatter(
                    x=timestamps_filtered,
                    y=temp_series,
                    mode="lines+markers",
                    name="Temperature",
                    line=dict(color=color, width=2),
                    marker=dict(size=4, color=color),
                    connectgaps=False,
                    hovertemplate="%{x|%H:%M:%S}<br><b>%{y:.3f} " + unit + "</b><extra></extra>"
                )
            ]
            
            y2_dict = None
            if heater_trace is not None:
                traces.append(heater_trace)
                y2_dict = dict(
                    title="Heater Power (µW)",
                    gridcolor="#e2e8f0",
                    showgrid=False,
                    linecolor="#cbd5e1",
                    tickfont=dict(color="#475569"),
                    zeroline=False,
                    overlaying="y",
                    side="right",
                )

            fig = go.Figure(
                data=traces,
                layout=go.Layout(
                    title=dict(text=title, font=dict(size=13, color="#0f172a", weight=600)),
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#f8fafc",
                    font=dict(color="#334155", family="Inter, Arial"),
                    xaxis=dict(
                        title="Time",
                        gridcolor="#e2e8f0",
                        linecolor="#cbd5e1",
                        tickfont=dict(color="#475569"),
                        tickformat="%H:%M:%S",
                    ),
                    yaxis=dict(
                        title=f"Temperature ({unit})",
                        gridcolor="#e2e8f0",
                        linecolor="#cbd5e1",
                        tickfont=dict(color="#475569"),
                        zeroline=False,
                    ),
                    yaxis2=y2_dict,
                    legend=dict(
                        bgcolor="#ffffff",
                        bordercolor="#cbd5e1",
                        borderwidth=1,
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1,
                        font=dict(color="#475569")
                    ),
                    margin=dict(l=55, r=55, t=40, b=45),
                    hovermode="x unified",
                    uirevision="static"
                )
            )
            return fig

        # Generate individual traces and graphs
        # CH 1: 40 K (K)
        ch1_temps = series_filtered.get(1, [])
        fig_ch1 = make_figure(1, ch1_temps, "CH 1 (40 K Flange) Temperature", "#ef4444", "K")

        # CH 2: 4 K (K)
        ch2_temps = series_filtered.get(2, [])
        fig_ch2 = make_figure(2, ch2_temps, "CH 2 (4 K Flange) Temperature", "#22c55e", "K")

        # CH 5: Still (mK)
        ch5_temps_mK = [v * 1000.0 if v is not None else None for v in series_filtered.get(5, [])]
        fig_ch5 = make_figure(5, ch5_temps_mK, "CH 5 (Still Flange) Temperature", "#ca8a04", "mK", None)

        # CH 6: MXC (mK)
        ch6_temps_mK = [v * 1000.0 if v is not None else None for v in series_filtered.get(6, [])]
        fig_ch6 = make_figure(6, ch6_temps_mK, "CH 6 (MXC Flange) Temperature", "#3b82f6", "mK", None)

        # Format Card Readings
        def format_temp_card(ch: int):
            if not channel_active.get(ch, True):
                return "DISABLED"
            v = last_row.get(ch)
            if v is None:
                return "—"
            if v < 1.0:
                return f"{v * 1000.0:.3f} mK"
            return f"{v:.4f} K"

        read_ch1_str = format_temp_card(1)
        read_ch2_str = format_temp_card(2)
        read_ch5_str = format_temp_card(5)
        read_ch6_str = format_temp_card(6)

        # Slide toggle styles
        t_ch1_out, t_ch1_cir = get_slider_styles(channel_active.get(1, True))
        t_ch2_out, t_ch2_cir = get_slider_styles(channel_active.get(2, True))
        t_ch5_out, t_ch5_cir = get_slider_styles(channel_active.get(5, True))
        t_ch6_out, t_ch6_cir = get_slider_styles(channel_active.get(6, True))

        # Heater Card readings & subtexts
        mxc_read_str = f"{last_mxc_heater:.2f} µW" if last_mxc_heater is not None else "0.00 µW"
        if not mxc_reg_active:
            mxc_sub_str = "INACTIVE"
        elif mxc_pid_mode == 0:
            mxc_sub_str = f"MANUAL: {mxc_manual_power_uW:.2f} µW"
        else:
            mxc_sub_str = f"PID: {mxc_reg_setpoint_mK:.1f} mK (CH {mxc_reg_channel})"

        still_read_str = f"{last_still_heater:.2f} µW" if last_still_heater is not None else "0.00 µW"
        if not still_reg_active:
            still_sub_str = "INACTIVE"
        elif still_pid_mode == 0:
            still_sub_str = f"MANUAL: {still_manual_power_uW:.2f} µW"
        else:
            still_sub_str = f"PID: {still_reg_setpoint_mK:.1f} mK (CH {still_reg_channel})"

        # Bottom tab master switch styles
        mxc_sw_out, mxc_sw_cir = get_slider_styles(mxc_reg_active)
        still_sw_out, still_sw_cir = get_slider_styles(still_reg_active)

        # Header live badges
        badge_text = "● LIVE"
        badge_style = {
            "padding": "3px 10px",
            "borderRadius": "12px",
            "fontSize": "11px",
            "fontWeight": 600,
            "backgroundColor": "#22c55e33" if not write_logs else "#3b82f633",
            "color": "#22c55e" if not write_logs else "#3b82f6",
            "marginLeft": "16px"
        }
        if write_logs:
            badge_text = "● LIVE & LOGGING"

        # Footer
        n_pts = len(timestamps)
        last_ts = timestamps[-1].strftime("%H:%M:%S") if timestamps else "—"
        footer_str = (f"Last poll update: {last_ts}  |  "
                      f"{n_pts} points in memory  |  "
                      f"Interval: {interval}s  |  "
                      f"Active Dir: {log_dir}")

        # Regulation Status Displays (bottom panel)
        if mxc_reg_active:
            mxc_status_node = html.Span([
                "Status: ",
                html.Strong("ACTIVE", style={"color": "#22c55e"}),
                f" in " + ("PID Mode" if mxc_pid_mode == 1 else "Manual Mode") + " | ",
                f"Coupled: CH {mxc_reg_channel} | " if mxc_pid_mode == 1 else "",
                f"Setpoint: {mxc_reg_setpoint_mK:.1f} mK | " if mxc_pid_mode == 1 else "",
                f"Heater: {last_mxc_heater:.2f} µW" if last_mxc_heater is not None else ""
            ])
        else:
            mxc_status_node = html.Span([
                "Status: ",
                html.Strong("INACTIVE / IDLE", style={"color": "#94a3b8"}),
                f" | Heater: {last_mxc_heater:.2f} µW" if last_mxc_heater is not None else ""
            ])

        if still_reg_active:
            still_status_node = html.Span([
                "Status: ",
                html.Strong("ACTIVE", style={"color": "#22c55e"}),
                f" in " + ("PID Mode" if still_pid_mode == 1 else "Manual Mode") + " | ",
                f"Coupled: CH {still_reg_channel} | " if still_pid_mode == 1 else "",
                f"Setpoint: {still_reg_setpoint_mK:.1f} mK | " if still_pid_mode == 1 else "",
                f"Heater: {last_still_heater:.2f} µW" if last_still_heater is not None else ""
            ])
        else:
            still_status_node = html.Span([
                "Status: ",
                html.Strong("INACTIVE / IDLE", style={"color": "#94a3b8"}),
                f" | Heater: {last_still_heater:.2f} µW" if last_still_heater is not None else ""
            ])

        return (
            fig_ch1, fig_ch2, fig_ch5, fig_ch6,
            read_ch1_str, read_ch2_str, read_ch5_str, read_ch6_str,
            t_ch1_out, t_ch1_cir,
            t_ch2_out, t_ch2_cir,
            t_ch5_out, t_ch5_cir,
            t_ch6_out, t_ch6_cir,
            mxc_read_str, mxc_sub_str,
            still_read_str, still_sub_str,
            mxc_sw_out, mxc_sw_cir,
            still_sw_out, still_sw_cir,
            badge_text, badge_style,
            footer_str,
            mxc_status_node, still_status_node
        )

    # 2. Tabs Visibility callbacks
    @app.callback(
        Output("mxc-manual-section", "style"),
        Output("mxc-pid-section", "style"),
        Input("mxc-mode-select", "value")
    )
    def mxc_mode_vis(mode):
        if mode == "manual":
            return {"display": "block"}, {"display": "none"}
        return {"display": "none"}, {"display": "block"}

    @app.callback(
        Output("still-manual-section", "style"),
        Output("still-pid-section", "style"),
        Input("still-mode-select", "value")
    )
    def still_mode_vis(mode):
        if mode == "manual":
            return {"display": "block"}, {"display": "none"}
        return {"display": "none"}, {"display": "block"}

    # 3. Dynamic Channel Scan toggles callback
    @app.callback(
        Output("dummy-ch-toggle-store", "data"),
        Input("toggle-ch1", "n_clicks"),
        Input("toggle-ch2", "n_clicks"),
        Input("toggle-ch5", "n_clicks"),
        Input("toggle-ch6", "n_clicks"),
        prevent_initial_call=True
    )
    def handle_ch_toggles(*_args):
        ctx = callback_context
        if not ctx.triggered:
            return ""
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        ch_map = {
            "toggle-ch1": 1,
            "toggle-ch2": 2,
            "toggle-ch5": 5,
            "toggle-ch6": 6
        }
        ch = ch_map.get(trigger_id)
        if ch is not None:
            active = store.get_channel_active(ch)
            new_active = not active
            try:
                bf.set_channel_active(ch, new_active)
            except Exception as e:
                print(f"Error setting BFTC channel active: {e}", file=sys.stderr)
            store.set_channel_active(ch, new_active)
        return ""

    # 4. Heater Card clicks -> Tab Focus redirection
    @app.callback(
        Output("heater-tabs", "value"),
        Input("mxc-heater-card", "n_clicks"),
        Input("still-heater-card", "n_clicks"),
        State("heater-tabs", "value"),
        prevent_initial_call=True
    )
    def select_heater_tab(mxc_clicks, still_clicks, current_tab):
        ctx = callback_context
        if not ctx.triggered:
            return current_tab
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        if trigger_id == "mxc-heater-card":
            return "mxc-tab"
        elif trigger_id == "still-heater-card":
            return "still-tab"
        return current_tab

    # 5. Logging and custom settings callback
    @app.callback(
        Output("reload-status-span", "children"),
        Input("write-log-checkbox", "value"),
        Input("log-location-dropdown", "value"),
        Input("plot-window-input", "value"),
        Input("time-offset-input", "value"),
        Input("reload-history-btn", "n_clicks"),
        prevent_initial_call=False
    )
    def update_settings_and_reload(write_val, location_val, plot_window, time_offset, reload_clicks):
        ctx = callback_context
        triggered_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""

        # Update logging configs
        write_enabled = "write" in write_val if write_val else False
        if location_val:
            _, old_log_dir = store.get_logging_config()
            store.set_logging_config(write_enabled, location_val)
            # If log directory changed, trigger history reload
            if triggered_id == "log-location-dropdown" and old_log_dir != location_val:
                try:
                    store.load_history_from_logs()
                    return f"Log dir changed. Loaded history from: {location_val}"
                except Exception as e:
                    return f"Error loading new log history: {e}"

        # Update plot window
        if plot_window is not None:
            try:
                val = float(plot_window)
                if val >= 1:
                    store.set_plot_window(val)
            except ValueError:
                pass

        # Update time offset
        if time_offset is not None:
            try:
                val = float(time_offset)
                store.set_time_offset(val)
            except ValueError:
                pass

        # Manual reload button click
        if triggered_id == "reload-history-btn" and reload_clicks:
            try:
                store.load_history_from_logs()
                return "History successfully reloaded!"
            except Exception as e:
                return f"Reload error: {e}"

        return ""

    # 5b. Log/poll Interval settings callback
    @app.callback(
        Output("interval", "interval"),
        Input("poll-interval-input", "value"),
        prevent_initial_call=True
    )
    def update_interval_property(poll_sec):
        if poll_sec:
            try:
                val = int(float(poll_sec))
                if val >= 1:
                    store.set_poll_interval(val)
                    return val * 1000
            except ValueError:
                pass
        return 10000 # fallback to 10s

    # 6. Heater Master Active Toggles in bottom tabs callback
    @app.callback(
        Output("dummy-heater-toggle-store", "data"),
        Input("mxc-master-toggle", "n_clicks"),
        Input("still-master-toggle", "n_clicks"),
        State("mxc-mode-select", "value"),
        State("mxc-manual-power-input", "value"),
        State("mxc-coupled-dropdown", "value"),
        State("mxc-setpoint-input", "value"),
        State("still-mode-select", "value"),
        State("still-manual-power-input", "value"),
        State("still-coupled-dropdown", "value"),
        State("still-setpoint-input", "value"),
        prevent_initial_call=True
    )
    def handle_heater_toggles(mxc_clicks, still_clicks,
                               mxc_mode, mxc_pow, mxc_coup, mxc_set,
                               still_mode, still_pow, still_coup, still_set):
        ctx = callback_context
        if not ctx.triggered:
            return ""
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if trigger_id == "mxc-master-toggle":
            active = store.mxc_reg_active
            new_active = not active
            try:
                if new_active:
                    if mxc_mode == "manual":
                        bf.set_heater_power((mxc_pow or 0.0) / 1e6, heater_nr=4)
                        store.set_mxc_regulation(True, 0, mxc_pow or 0.0)
                    else:
                        setpoint_K = (mxc_set or 50.0) / 1000.0
                        coupled = mxc_coup or 6
                        bf.set_heater_pid(setpoint_K, heater_nr=4, channel=coupled, all_channels=channels)
                        store.set_mxc_regulation(True, 1, 0.0, mxc_set or 50.0, coupled)
                else:
                    bf.stop_heater(heater_nr=4)
                    bf.enable_all_channels(channels)
                    store.set_mxc_regulation(False, 0 if mxc_mode == "manual" else 1, mxc_pow or 0.0, mxc_set, mxc_coup)
            except Exception as e:
                print(f"Error toggling MXC heater scan state: {e}", file=sys.stderr)

        elif trigger_id == "still-master-toggle":
            active = store.still_reg_active
            new_active = not active
            try:
                if new_active:
                    if still_mode == "manual":
                        bf.set_heater_power((still_pow or 0.0) / 1e6, heater_nr=1)
                        store.set_still_regulation(True, 0, still_pow or 0.0)
                    else:
                        setpoint_K = (still_set or 800.0) / 1000.0
                        coupled = still_coup or 5
                        bf.set_heater_pid(setpoint_K, heater_nr=1, channel=coupled, all_channels=channels)
                        store.set_still_regulation(True, 1, 0.0, still_set or 800.0, coupled)
                else:
                    bf.stop_heater(heater_nr=1)
                    bf.enable_all_channels(channels)
                    store.set_still_regulation(False, 0 if still_mode == "manual" else 1, still_pow or 0.0, still_set, still_coup)
            except Exception as e:
                print(f"Error toggling Still heater scan state: {e}", file=sys.stderr)
        return ""

    # 7. Symmetrical Heater Regulation action buttons
    @app.callback(
        Output("mxc-reg-action-status", "children"),
        Output("mxc-reg-action-status", "style"),
        Output("still-reg-action-status", "children"),
        Output("still-reg-action-status", "style"),
        Input("mxc-engage-btn", "n_clicks"),
        Input("mxc-stop-btn", "n_clicks"),
        Input("still-engage-btn", "n_clicks"),
        Input("still-stop-btn", "n_clicks"),
        State("mxc-mode-select", "value"),
        State("mxc-manual-power-input", "value"),
        State("mxc-coupled-dropdown", "value"),
        State("mxc-setpoint-input", "value"),
        State("still-mode-select", "value"),
        State("still-manual-power-input", "value"),
        State("still-coupled-dropdown", "value"),
        State("still-setpoint-input", "value"),
        prevent_initial_call=True
    )
    def handle_reg_actions(mxc_eng, mxc_stop, still_eng, still_stop,
                           mxc_mode, mxc_pow, mxc_coup, mxc_set,
                           still_mode, still_pow, still_coup, still_set):
        ctx = callback_context
        if not ctx.triggered:
            return "", {"color": "#cbd5e1"}, "", {"color": "#cbd5e1"}
        
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        mxc_msg, mxc_style = "", {"color": "#cbd5e1"}
        still_msg, still_style = "", {"color": "#cbd5e1"}

        if trigger_id == "mxc-engage-btn":
            if mxc_mode == "manual":
                power_val = mxc_pow if mxc_pow is not None else 0.0
                try:
                    bf.set_heater_power(power_val / 1e6, heater_nr=4)
                    store.set_mxc_regulation(True, 0, power_val)
                    mxc_msg = f"Set constant MXC power to {power_val:.2f} µW!"
                    mxc_style = {"color": "#22c55e"}
                except Exception as e:
                    mxc_msg = f"Error: {e}"
                    mxc_style = {"color": "#ef4444"}
            else: # PID
                if mxc_coup is None or mxc_set is None or mxc_set <= 0:
                    mxc_msg = "Error: Invalid coupled channel or setpoint > 0 mK."
                    mxc_style = {"color": "#ef4444"}
                else:
                    try:
                        bf.set_heater_pid(mxc_set / 1000.0, heater_nr=4, channel=mxc_coup, all_channels=channels)
                        store.set_mxc_regulation(True, 1, 0.0, mxc_set, mxc_coup)
                        mxc_msg = f"Engaged MXC PID at {mxc_set:.1f} mK on CH {mxc_coup}!"
                        mxc_style = {"color": "#22c55e"}
                    except Exception as e:
                        mxc_msg = f"Error: {e}"
                        mxc_style = {"color": "#ef4444"}

        elif trigger_id == "mxc-stop-btn":
            try:
                bf.stop_heater(heater_nr=4)
                bf.enable_all_channels(channels)
                store.set_mxc_regulation(False, 0, 0.0, None, None)
                mxc_msg = "Stopped MXC regulation and zeroed heater power."
                mxc_style = {"color": "#22c55e"}
            except Exception as e:
                mxc_msg = f"Error: {e}"
                mxc_style = {"color": "#ef4444"}

        elif trigger_id == "still-engage-btn":
            if still_mode == "manual":
                power_val = still_pow if still_pow is not None else 0.0
                try:
                    bf.set_heater_power(power_val / 1e6, heater_nr=1)
                    store.set_still_regulation(True, 0, power_val)
                    still_msg = f"Set constant Still power to {power_val:.2f} µW!"
                    still_style = {"color": "#22c55e"}
                except Exception as e:
                    still_msg = f"Error: {e}"
                    still_style = {"color": "#ef4444"}
            else: # PID
                if still_coup is None or still_set is None or still_set <= 0:
                    still_msg = "Error: Invalid coupled channel or setpoint > 0 mK."
                    still_style = {"color": "#ef4444"}
                else:
                    try:
                        bf.set_heater_pid(still_set / 1000.0, heater_nr=1, channel=still_coup, all_channels=channels)
                        store.set_still_regulation(True, 1, 0.0, still_set, still_coup)
                        still_msg = f"Engaged Still PID at {still_set:.1f} mK on CH {still_coup}!"
                        still_style = {"color": "#22c55e"}
                    except Exception as e:
                        still_msg = f"Error: {e}"
                        still_style = {"color": "#ef4444"}

        elif trigger_id == "still-stop-btn":
            try:
                bf.stop_heater(heater_nr=1)
                bf.enable_all_channels(channels)
                store.set_still_regulation(False, 0, 0.0, None, None)
                still_msg = "Stopped Still regulation and zeroed heater power."
                still_style = {"color": "#22c55e"}
            except Exception as e:
                still_msg = f"Error: {e}"
                still_style = {"color": "#ef4444"}

        return mxc_msg, mxc_style, still_msg, still_style

    # 8. BFTC Scanner IP update callback
    @app.callback(
        Output("bftc-ip-status", "children"),
        Output("bftc-ip-status", "style"),
        Input("bftc-ip-btn", "n_clicks"),
        State("bftc-ip-input", "value"),
        prevent_initial_call=True
    )
    def update_scanner_ip(n_clicks, ip_val):
        if not ip_val:
            return "IP cannot be empty", {"fontSize": "12px", "color": "#ef4444", "fontWeight": 500, "marginLeft": "4px"}
        ip_val = ip_val.strip()
        try:
            store.set_bftc_ip(ip_val)
            return "Connected!", {"fontSize": "12px", "color": "#10b981", "fontWeight": 500, "marginLeft": "4px"}
        except Exception as e:
            return f"Error: {e}", {"fontSize": "12px", "color": "#ef4444", "fontWeight": 500, "marginLeft": "4px"}

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BFTC browser-based temperature monitor & regulator")
    parser.add_argument("--ip", default="132.163.130.125:5001",
                        help="BFTC IP:port  (default: 132.163.130.125:5001)")
    parser.add_argument("--channels", nargs="+", type=int, default=[1, 2, 5, 6],
                        help="Channel numbers  (default: 1 2 5 6)")
    parser.add_argument("--interval", type=int, default=10,
                        help="Poll interval in seconds  (default: 10)")
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "bftc_logs",
                        help="CSV log directory  (default: ~/bftc_logs)")
    parser.add_argument("--port", type=int, default=8050,
                        help="Dash server port  (default: 8050)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address  (default: 0.0.0.0 = all interfaces)")
    parser.add_argument("--gui", action="store_true",
                        help="Launch in a standalone desktop window using pywebview")
    args = parser.parse_args()

    bf = BFTC(args.ip)
    store = DataStore(args.channels)
    store.set_bftc_ip(args.ip)
    
    # Initialize background logging path/configs
    store.set_logging_config(True, str(args.log_dir))
    store.set_poll_interval(args.interval)

    # Automatically pre-load history from existing log files on startup
    try:
        store.load_history_from_logs()
    except Exception as e:
        print(f"Error loading logs on startup: {e}", file=sys.stderr)
    
    start_time = time.time()
    stop_event = threading.Event()

    poll_thread = threading.Thread(
        target=poll_loop,
        args=(bf, args.channels, args.interval, store, start_time, stop_event),
        daemon=True,
        name="bftc-poll",
    )
    poll_thread.start()

    app = build_app(bf, args.channels, store, args.interval)

    if args.gui:
        try:
            import webview
        except ImportError:
            print("Error: pywebview is not installed. Please run:\n  pip install pywebview", file=sys.stderr)
            sys.exit(1)

        # Run Dash server in background thread
        server_thread = threading.Thread(
            target=app.run,
            kwargs={"host": "127.0.0.1", "port": args.port, "debug": False},
            daemon=True
        )
        server_thread.start()

        # Wait a moment for server to spin up
        time.sleep(1.5)

        print("\nLaunching native GUI window...")
        webview.create_window("BFTC Monitor", f"http://127.0.0.1:{args.port}")
        try:
            webview.start()
        finally:
            stop_event.set()
            poll_thread.join(timeout=5)
    else:
        local_ip = os.popen("hostname -I 2>/dev/null | awk '{print $1}'").read().strip()
        display_ip = local_ip or "<this-machine-ip>"
        print(f"\nDash server starting — open in any browser on the network:")
        print(f"  http://{display_ip}:{args.port}\n")

        try:
            app.run(host=args.host, port=args.port, debug=False)
        finally:
            stop_event.set()
            poll_thread.join(timeout=5)


if __name__ == "__main__":
    main()
