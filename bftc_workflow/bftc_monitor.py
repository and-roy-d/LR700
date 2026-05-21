#!/usr/bin/env python3
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
    def __init__(self, ip: str = "169.169.10.10:5001", timeout: float = 10.0):
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

MAX_POINTS = 1440   # keep last 24 h at 1-min resolution

class DataStore:
    def __init__(self, channels: list[int]):
        self.lock = threading.Lock()
        self.timestamps: deque[datetime] = deque(maxlen=MAX_POINTS)
        self.series: dict[int, deque[float | None]] = {
            ch: deque(maxlen=MAX_POINTS) for ch in channels
        }
        self.heater_series: deque[float | None] = deque(maxlen=MAX_POINTS)
        self.last_row: dict[int, float | None] = {ch: None for ch in channels}
        self.last_heater: float | None = None

        # Regulation settings
        self.reg_active: bool = False
        self.reg_setpoint_mK: float | None = None
        self.reg_channel: int | None = None

    def append(self, ts: datetime, readings: dict[int, float | None], heater_uW: float | None) -> None:
        with self.lock:
            self.timestamps.append(ts)
            for ch, val in readings.items():
                if ch in self.series:
                    self.series[ch].append(val)
            self.heater_series.append(heater_uW)
            self.last_row = readings
            self.last_heater = heater_uW

    def snapshot(self):
        with self.lock:
            return (
                list(self.timestamps),
                {ch: list(vals) for ch, vals in self.series.items()},
                list(self.heater_series),
                dict(self.last_row),
                self.last_heater,
                self.reg_active,
                self.reg_setpoint_mK,
                self.reg_channel,
            )

    def set_regulation(self, active: bool, setpoint_mK: float | None = None, channel: int | None = None) -> None:
        with self.lock:
            self.reg_active = active
            if active:
                self.reg_setpoint_mK = setpoint_mK
                self.reg_channel = channel


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

def open_csv(log_dir: Path, channels: list[int]):
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = log_dir / f"bftc_monitor_{stamp}.csv"
    fh = open(path, "w", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    writer.writerow(["timestamp", "elapsed_s"] + [f"ch{ch}_mK" for ch in channels] + ["heater_uW"])
    fh.flush()
    print(f"Logging to: {path}")
    return path, fh, writer


# ---------------------------------------------------------------------------
# Background polling thread
# ---------------------------------------------------------------------------

def poll_loop(bf: BFTC, channels: list[int], interval: int,
              store: DataStore, fh, writer, start_time: float,
              stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        ts = datetime.now()
        elapsed = round(time.time() - start_time, 1)
        readings: dict[int, float | None] = {}

        for ch in channels:
            t = bf.get_temperature(ch)
            readings[ch] = t
            status = f"{t * 1000:.2f} mK" if t is not None else "N/A"
            print(f"  CH {ch}: {status}")

        heater_uW = bf.get_latest_heater_power_uW()
        if heater_uW is not None:
            print(f"  Heater Power: {heater_uW:.2f} uW")
        else:
            print("  Heater Power: N/A")

        store.append(ts, readings, heater_uW)

        row = [ts.strftime("%Y-%m-%d %H:%M:%S"), elapsed]
        for ch in channels:
            t = readings[ch]
            row.append(f"{t * 1000:.4f}" if t is not None else "")
        row.append(f"{heater_uW:.4f}" if heater_uW is not None else "")
        writer.writerow(row)
        fh.flush()

        print(f"[{ts.strftime('%H:%M:%S')}] logged — next poll in {interval}s\n")
        stop_event.wait(timeout=interval)

    fh.close()
    print("Poll thread stopped, log closed.")


# ---------------------------------------------------------------------------
# Dash application
# ---------------------------------------------------------------------------

CHANNEL_COLORS = {1: "#4e9af1", 2: "#e05a5a", 5: "#54c08a", 6: "#f0a050"}

def build_app(bf: BFTC, channels: list[int], store: DataStore, interval: int):
    from dash import Dash, dcc, html, callback_context
    from dash.dependencies import Input, Output, State
    import plotly.graph_objs as go

    app = Dash(__name__, title="BFTC Monitor")

    app.layout = html.Div(
        style={"fontFamily": "Inter, Arial, sans-serif",
               "backgroundColor": "#0f172a", "minHeight": "100vh",
               "padding": "24px"},
        children=[
            # Dark Mode Dropdown CSS Fixes
            html.Style("""
                .Select-control {
                    background-color: #0f172a !important;
                    border-color: #475569 !important;
                }
                .Select-value-label {
                    color: #cbd5e1 !important;
                }
                .Select-menu-outer {
                    background-color: #0f172a !important;
                    border-color: #475569 !important;
                }
                .VirtualisedSelectFocusedOption {
                    background-color: #1e293b !important;
                }
                .Select-option {
                    background-color: #0f172a !important;
                    color: #cbd5e1 !important;
                }
                .Select-option.is-focused {
                    background-color: #1e293b !important;
                    color: #f1f5f9 !important;
                }
            """),

            html.Div(
                style={"display": "flex", "alignItems": "center",
                       "marginBottom": "20px"},
                children=[
                    html.H1("BFTC Temperature Monitor",
                            style={"color": "#f1f5f9", "margin": 0,
                                   "fontSize": "22px", "fontWeight": 600}),
                    html.Span(id="live-badge",
                              style={"marginLeft": "16px", "padding": "3px 10px",
                                     "borderRadius": "12px", "fontSize": "12px",
                                     "backgroundColor": "#22c55e33",
                                     "color": "#22c55e", "fontWeight": 600}),
                ],
            ),

            # Status cards — one per channel
            html.Div(id="status-cards",
                     style={"display": "flex", "gap": "12px",
                            "marginBottom": "20px", "flexWrap": "wrap"}),

            # MXC Heater Regulation panel
            html.Div(
                style={
                    "backgroundColor": "#1e293b",
                    "borderRadius": "12px",
                    "padding": "20px",
                    "marginBottom": "20px",
                    "boxShadow": "0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06)",
                    "border": "1px solid #334155"
                },
                children=[
                    html.H3("MXC Heater Regulation",
                            style={"color": "#f1f5f9", "margin": "0 0 16px 0", "fontSize": "15px", "fontWeight": 600}),

                    html.Div(
                        style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap"},
                        children=[
                            # Channel Select
                            html.Div(
                                style={"flex": "1", "minWidth": "160px"},
                                children=[
                                    html.Label("MXC Thermo Channel", style={"color": "#94a3b8", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                    dcc.Dropdown(
                                        id="reg-channel-dropdown",
                                        options=[{"label": f"Channel {ch}", "value": ch} for ch in channels],
                                        value=channels[-1] if channels else None,
                                        clearable=False,
                                    )
                                ]
                            ),

                            # Setpoint Input
                            html.Div(
                                style={"flex": "1", "minWidth": "160px"},
                                children=[
                                    html.Label("Target Setpoint (mK)", style={"color": "#94a3b8", "fontSize": "12px", "display": "block", "marginBottom": "6px"}),
                                    dcc.Input(
                                        id="reg-setpoint-input",
                                        type="number",
                                        placeholder="e.g. 50.0",
                                        min=0,
                                        step=0.1,
                                        style={
                                            "backgroundColor": "#0f172a",
                                            "color": "#cbd5e1",
                                            "border": "1px solid #475569",
                                            "borderRadius": "6px",
                                            "padding": "8px 12px",
                                            "width": "100%",
                                            "boxSizing": "border-box",
                                            "height": "38px"
                                        }
                                    )
                                ]
                            ),

                            # Engage Button
                            html.Button(
                                "Engage PID",
                                id="engage-btn",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#22c55e",
                                    "color": "#ffffff",
                                    "border": "none",
                                    "borderRadius": "6px",
                                    "padding": "0 20px",
                                    "height": "38px",
                                    "fontWeight": "600",
                                    "cursor": "pointer",
                                    "transition": "background-color 0.2s",
                                    "boxShadow": "0 2px 4px rgba(34, 197, 94, 0.2)"
                                }
                            ),

                            # Stop Button
                            html.Button(
                                "Stop / Zero Heater",
                                id="stop-btn",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#ef4444",
                                    "color": "#ffffff",
                                    "border": "none",
                                    "borderRadius": "6px",
                                    "padding": "0 20px",
                                    "height": "38px",
                                    "fontWeight": "600",
                                    "cursor": "pointer",
                                    "transition": "background-color 0.2s",
                                    "boxShadow": "0 2px 4px rgba(239, 68, 68, 0.2)"
                                }
                            ),
                        ]
                    ),

                    # Status Display and Action messages
                    html.Div(
                        style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginTop": "16px", "paddingTop": "12px", "borderTop": "1px solid #334155"},
                        children=[
                            html.Div(
                                id="reg-status-display",
                                style={"fontSize": "13px", "color": "#cbd5e1"}
                            ),
                            html.Div(
                                id="reg-action-status",
                                style={"fontSize": "13px", "fontWeight": 500}
                            )
                        ]
                    )
                ]
            ),

            # Main chart
            dcc.Graph(
                id="temp-chart",
                config={"displayModeBar": True},
                style={"borderRadius": "12px", "overflow": "hidden",
                       "backgroundColor": "#1e293b"},
            ),

            # Interval driver
            dcc.Interval(
                id="interval",
                interval=interval * 1000,   # ms
                n_intervals=0,
            ),

            # Footer
            html.Div(
                id="footer",
                style={"color": "#64748b", "fontSize": "12px",
                       "marginTop": "12px", "textAlign": "right"},
            ),
        ],
    )

    @app.callback(
        Output("temp-chart", "figure"),
        Output("status-cards", "children"),
        Output("live-badge", "children"),
        Output("footer", "children"),
        Output("reg-status-display", "children"),
        Input("interval", "n_intervals"),
    )
    def update(_n):
        timestamps, series, heater_series, last_row, last_heater, reg_active, reg_setpoint_mK, reg_channel = store.snapshot()

        # --- Build traces ---
        traces = []
        for ch in channels:
            vals_mK = [
                v * 1000 if v is not None else None
                for v in series.get(ch, [])
            ]
            color = CHANNEL_COLORS.get(ch, "#aaaaaa")
            traces.append(go.Scatter(
                x=timestamps,
                y=vals_mK,
                mode="lines+markers",
                name=f"CH {ch}",
                line=dict(color=color, width=2),
                marker=dict(size=4, color=color),
                connectgaps=False,
                hovertemplate="%{x|%H:%M:%S}<br><b>%{y:.3f} mK</b><extra>CH " + str(ch) + "</extra>",
            ))

        # Add heater power trace
        if heater_series:
            traces.append(go.Scatter(
                x=timestamps,
                y=list(heater_series),
                mode="lines",
                name="Heater Power",
                yaxis="y2",
                line=dict(color="#f59e0b", width=2, dash="dash"),
                hovertemplate="%{x|%H:%M:%S}<br><b>%{y:.2f} µW</b><extra>Heater</extra>",
            ))

        figure = go.Figure(
            data=traces,
            layout=go.Layout(
                paper_bgcolor="#1e293b",
                plot_bgcolor="#0f172a",
                font=dict(color="#cbd5e1", family="Inter, Arial"),
                xaxis=dict(
                    title="Time",
                    gridcolor="#1e293b",
                    linecolor="#334155",
                    tickformat="%H:%M:%S",
                ),
                yaxis=dict(
                    title="Temperature (mK)",
                    gridcolor="#1e293b",
                    linecolor="#334155",
                    zeroline=False,
                ),
                yaxis2=dict(
                    title="Heater Power (µW)",
                    gridcolor="#1e293b",
                    showgrid=False,
                    linecolor="#334155",
                    zeroline=False,
                    overlaying="y",
                    side="right",
                ),
                legend=dict(
                    bgcolor="#1e293b",
                    bordercolor="#334155",
                    borderwidth=1,
                ),
                margin=dict(l=60, r=60, t=20, b=60),
                hovermode="x unified",
                uirevision="static",   # keep zoom/pan between updates
            ),
        )

        # --- Status cards ---
        cards = []
        for ch in channels:
            t = last_row.get(ch)
            val_str = f"{t * 1000:.3f} mK" if t is not None else "—"
            color = CHANNEL_COLORS.get(ch, "#aaaaaa")
            cards.append(html.Div(
                style={
                    "backgroundColor": "#1e293b",
                    "borderRadius": "10px",
                    "padding": "14px 20px",
                    "minWidth": "140px",
                    "borderLeft": f"4px solid {color}",
                    "boxShadow": "0 2px 8px rgba(0,0,0,0.3)",
                },
                children=[
                    html.Div(f"CH {ch}",
                             style={"color": color, "fontWeight": 700,
                                    "fontSize": "12px", "letterSpacing": "1px",
                                    "textTransform": "uppercase"}),
                    html.Div(val_str,
                             style={"color": "#f1f5f9", "fontWeight": 600,
                                    "fontSize": "20px", "marginTop": "4px"}),
                ],
            ))

        n_pts = len(timestamps)
        last_ts = timestamps[-1].strftime("%H:%M:%S") if timestamps else "—"
        badge = "● LIVE"
        footer = (f"Last update: {last_ts}  |  "
                  f"{n_pts} point{'s' if n_pts != 1 else ''} in memory  |  "
                  f"Poll interval: {interval}s")

        # --- Regulation status display ---
        if reg_active:
            status_text = html.Span([
                "Status: ",
                html.Strong("REGULATING", style={"color": "#22c55e"}),
                f" on CH {reg_channel} at ",
                html.Strong(f"{reg_setpoint_mK:.1f} mK"),
                f" | Heater: {last_heater:.2f} µW" if last_heater is not None else ""
            ])
        else:
            status_text = html.Span([
                "Status: ",
                html.Strong("MANUAL / IDLE", style={"color": "#94a3b8"}),
                f" | Heater: {last_heater:.2f} µW" if last_heater is not None else ""
            ])

        return figure, cards, badge, footer, status_text

    @app.callback(
        Output("reg-action-status", "children"),
        Output("reg-action-status", "style"),
        Input("engage-btn", "n_clicks"),
        Input("stop-btn", "n_clicks"),
        State("reg-channel-dropdown", "value"),
        State("reg-setpoint-input", "value"),
        prevent_initial_call=True
    )
    def handle_regulation_buttons(engage_clicks, stop_clicks, channel, setpoint_mK):
        ctx = callback_context
        if not ctx.triggered:
            return "", {"color": "#94a3b8"}

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if trigger_id == "engage-btn":
            if channel is None:
                return "Error: Please select a channel first.", {"color": "#ef4444"}
            if setpoint_mK is None or setpoint_mK <= 0:
                return "Error: Please specify a valid setpoint > 0 mK.", {"color": "#ef4444"}

            try:
                setpoint_K = setpoint_mK / 1000.0
                bf.set_heater_pid(setpoint_K, heater_nr=4, channel=channel, all_channels=channels)
                store.set_regulation(True, setpoint_mK, channel)
                return f"Engaged PID at {setpoint_mK:.1f} mK on CH {channel}!", {"color": "#22c55e"}
            except Exception as e:
                return f"Error engaging PID: {e}", {"color": "#ef4444"}

        elif trigger_id == "stop-btn":
            try:
                bf.stop_heater(heater_nr=4)
                bf.enable_all_channels(channels)
                store.set_regulation(False)
                return "Stopped regulation and zeroed heater power.", {"color": "#22c55e"}
            except Exception as e:
                return f"Error stopping PID: {e}", {"color": "#ef4444"}

        return "", {"color": "#94a3b8"}

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BFTC browser-based temperature monitor & regulator")
    parser.add_argument("--ip", default="169.169.10.10:5001",
                        help="BFTC IP:port  (default: 169.169.10.10:5001)")
    parser.add_argument("--channels", nargs="+", type=int, default=[1, 2, 5, 6],
                        help="Channel numbers  (default: 1 2 5 6)")
    parser.add_argument("--interval", type=int, default=60,
                        help="Poll interval in seconds  (default: 60)")
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "bftc_logs",
                        help="CSV log directory  (default: ~/bftc_logs)")
    parser.add_argument("--port", type=int, default=8050,
                        help="Dash server port  (default: 8050)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address  (default: 0.0.0.0 = all interfaces)")
    args = parser.parse_args()

    bf = BFTC(args.ip)
    store = DataStore(args.channels)
    _path, fh, writer = open_csv(args.log_dir, args.channels)
    start_time = time.time()
    stop_event = threading.Event()

    poll_thread = threading.Thread(
        target=poll_loop,
        args=(bf, args.channels, args.interval, store, fh, writer,
              start_time, stop_event),
        daemon=True,
        name="bftc-poll",
    )
    poll_thread.start()

    app = build_app(bf, args.channels, store, args.interval)

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
