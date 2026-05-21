#!/usr/bin/env python3
"""
BFTC 4-Channel Temperature Monitor
====================================
Polls all four thermometer channels on the Bluefors Temperature Controller
every N seconds via the REST API, writes a CSV log, and shows a live plot.

Requirements (Linux):
    pip install requests matplotlib

Usage:
    python bftc_monitor.py                          # defaults
    python bftc_monitor.py --ip 169.169.10.10:5001
    python bftc_monitor.py --channels 1 2 5 6 --interval 60
    python bftc_monitor.py --no-plot --log-dir /home/user/data
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

os.environ.setdefault("NO_PROXY", "169.169.10.10,132.163.157.220,localhost,127.0.0.1")

# ---------------------------------------------------------------------------
# Minimal inline BFTC client (no dependency on the rest of the repo)
# ---------------------------------------------------------------------------

class BFTC:
    def __init__(self, ip: str = "169.169.10.10:5001", timeout: float = 10.0):
        self.ip = ip
        self.timeout = timeout

    def get_temperature(self, channel: int, minutes: float = 2.0) -> float | None:
        """Return the most recent temperature (K) for *channel*, or None."""
        now = datetime.now()
        from datetime import timedelta
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
                json=payload, timeout=self.timeout
            )
            r.raise_for_status()
            temps = r.json().get("measurements", {}).get("temperature", [])
            return temps[-1] if temps else None
        except Exception as exc:
            print(f"  [ch{channel}] Error: {exc}", file=sys.stderr)
            return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def open_csv(log_dir: Path, channels: list[int]) -> tuple[Path, object, object]:
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
# Live plotter
# ---------------------------------------------------------------------------

def make_plotter(channels: list[int]):
    """Return an update function that re-draws the live plot."""
    import matplotlib
    matplotlib.use("TkAgg")          # change to "Qt5Agg" if TkAgg unavailable
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.set_xlabel("Time")
    ax.set_ylabel("Temperature (mK)")
    ax.set_title("BFTC Live Temperature Monitor")
    ax.grid(alpha=0.4)
    fig.autofmt_xdate()

    times:  list = []
    series: dict[int, list] = {ch: [] for ch in channels}
    lines:  dict[int, object] = {}

    colors = ["#4e9af1", "#e05a5a", "#54c08a", "#f0a050"]
    for ch, color in zip(channels, colors):
        (line,) = ax.plot([], [], "o-", ms=3, lw=1, label=f"CH {ch}", color=color)
        lines[ch] = line

    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.ion()
    plt.show()

    def update(ts: datetime, readings: dict[int, float | None]):
        times.append(ts)
        for ch in channels:
            val = readings.get(ch)
            series[ch].append(val * 1000 if val is not None else float("nan"))
            lines[ch].set_xdata(times)
            lines[ch].set_ydata(series[ch])

        ax.relim()
        ax.autoscale_view()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.canvas.draw()
        fig.canvas.flush_events()

    return update


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(
    ip: str,
    channels: list[int],
    interval: int,
    log_dir: Path,
    no_plot: bool,
):
    bf = BFTC(ip)
    _, fh, writer = open_csv(log_dir, channels)
    updater = None if no_plot else make_plotter(channels)
    start = time.time()

    print(f"Polling channels {channels} every {interval}s from {ip}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            ts = datetime.now()
            elapsed = round(time.time() - start, 1)

            readings: dict[int, float | None] = {}
            row = [ts.strftime("%Y-%m-%d %H:%M:%S"), elapsed]
            for ch in channels:
                t = bf.get_temperature(ch)
                readings[ch] = t
                val_mK = f"{t * 1000:.4f}" if t is not None else ""
                row.append(val_mK)
                status = f"{t * 1000:.2f} mK" if t is not None else "N/A"
                print(f"  CH {ch}: {status}")

            writer.writerow(row)
            fh.flush()

            if updater:
                try:
                    updater(ts, readings)
                except Exception as exc:
                    print(f"  [plot] {exc}", file=sys.stderr)

            print(f"[{ts.strftime('%H:%M:%S')}] logged — sleeping {interval}s\n")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        fh.close()
        print("Log file closed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BFTC 4-channel temperature monitor")
    parser.add_argument("--ip", default="169.169.10.10:5001",
                        help="BFTC IP:port (default: 169.169.10.10:5001)")
    parser.add_argument("--channels", nargs="+", type=int, default=[1, 2, 5, 6],
                        help="Channel numbers to monitor (default: 1 2 5 6)")
    parser.add_argument("--interval", type=int, default=60,
                        help="Polling interval in seconds (default: 60)")
    parser.add_argument("--log-dir", type=Path,
                        default=Path.home() / "bftc_logs",
                        help="Directory for CSV log files")
    parser.add_argument("--no-plot", action="store_true",
                        help="Disable the live plot (headless / SSH mode)")
    args = parser.parse_args()

    run(
        ip=args.ip,
        channels=args.channels,
        interval=args.interval,
        log_dir=args.log_dir,
        no_plot=args.no_plot,
    )
