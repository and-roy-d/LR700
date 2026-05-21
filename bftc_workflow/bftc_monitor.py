#!/usr/bin/env python3
"""
BFTC 4-Channel Temperature Monitor  (Dash / browser edition)
=============================================================
Polls BFTC thermometer channels on a background thread, logs to CSV,
and serves a live-updating Plotly chart accessible from any browser on
the same network — no display or X forwarding required.

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

    def get_temperature(self, channel: int, minutes: float = 2.0) -> float | None:
        now = datetime.now()
        start = now - timedelta(minutes=minutes)
        fmt = "%Y-%m-%dT%H:%M:%S"
        payload = {
            "channel_nr": channel,
            "fields": ["temperature"],
            "start_time": start.strftime(fmt),
            "stop_time": now.strftime(fmt),
        }
        try:
            r = requests.post(
                f"http://{self.ip}/channel/historical-data",
                json=payload, timeout=self.timeout,
            )
            r.raise_for_status()
            temps = r.json().get("measurements", {}).get("temperature", [])
            return temps[-1] if temps else None
        except Exception as exc:
            print(f"  [ch{channel}] {exc}", file=sys.stderr)
            return None


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
        self.last_row: dict[int, float | None] = {ch: None for ch in channels}

    def append(self, ts: datetime, readings: dict[int, float | None]) -> None:
        with self.lock:
            self.timestamps.append(ts)
            for ch, val in readings.items():
                self.series[ch].append(val)
            self.last_row = readings

    def snapshot(self):
        with self.lock:
            return (
                list(self.timestamps),
                {ch: list(vals) for ch, vals in self.series.items()},
                dict(self.last_row),
            )


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

def open_csv(log_dir: Path, channels: list[int]):
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = log_dir / f"bftc_monitor_{stamp}.csv"
    fh = open(path, "w", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    writer.writerow(["timestamp", "elapsed_s"] + [f"ch{ch}_mK" for ch in channels])
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

        store.append(ts, readings)

        row = [ts.strftime("%Y-%m-%d %H:%M:%S"), elapsed]
        for ch in channels:
            t = readings[ch]
            row.append(f"{t * 1000:.4f}" if t is not None else "")
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

def build_app(channels: list[int], store: DataStore, interval: int):
    from dash import Dash, dcc, html, callback_context
    from dash.dependencies import Input, Output
    import plotly.graph_objs as go

    app = Dash(__name__, title="BFTC Monitor")

    app.layout = html.Div(
        style={"fontFamily": "Inter, Arial, sans-serif",
               "backgroundColor": "#0f172a", "minHeight": "100vh",
               "padding": "24px"},
        children=[
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
        Input("interval", "n_intervals"),
    )
    def update(_n):
        timestamps, series, last_row = store.snapshot()

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
                    tickformat="%H:%M",
                ),
                yaxis=dict(
                    title="Temperature (mK)",
                    gridcolor="#1e293b",
                    linecolor="#334155",
                    zeroline=False,
                ),
                legend=dict(
                    bgcolor="#1e293b",
                    bordercolor="#334155",
                    borderwidth=1,
                ),
                margin=dict(l=60, r=20, t=20, b=60),
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

        return figure, cards, badge, footer

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BFTC browser-based temperature monitor")
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

    app = build_app(args.channels, store, args.interval)

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
