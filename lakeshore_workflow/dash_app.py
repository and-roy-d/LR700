from __future__ import annotations

import datetime
import os
from pathlib import Path

import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import numpy as np
import plotly.graph_objects as go
import pytz


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"
LOCAL_TZ = pytz.timezone("America/Denver")


def latest_data_file() -> Path | None:
    if not DATA_DIR.exists():
        return None

    subdirs = [path for path in DATA_DIR.iterdir() if path.is_dir()]
    if not subdirs:
        return None

    latest_subdir = max(subdirs, key=os.path.getmtime)
    candidates = list(latest_subdir.glob("*.npy"))
    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


def load_data() -> np.ndarray:
    latest_file = latest_data_file()
    if latest_file is None:
        raise FileNotFoundError(f"No .npy data files found in {DATA_DIR}")
    return np.load(latest_file, allow_pickle=False)


def local_times_from_timestamps(time_s: np.ndarray) -> list[datetime.datetime]:
    utc_times = [datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) for ts in time_s]
    return [utc_time.astimezone(LOCAL_TZ) for utc_time in utc_times]


def point_colors(num_points: int) -> list[str]:
    num_highlight = min(5, num_points)
    base_color = "orange"
    current_color = "red"
    return [base_color] * max(0, num_points - num_highlight) + [current_color] * num_highlight


def empty_figure(title: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(title=title, template="plotly_white", margin=dict(l=50, r=30, t=50, b=50))
    return fig


def time_axis(fig: go.Figure) -> None:
    fig.update_xaxes(
        tickformatstops=[
            dict(dtickrange=[None, 1000], value="%H:%M:%S.%L"),
            dict(dtickrange=[1000, 60000], value="%H:%M:%S"),
            dict(dtickrange=[60000, 3600000], value="%H:%M"),
            dict(dtickrange=[3600000, 86400000], value="%m-%d %H:%M"),
            dict(dtickrange=[86400000, None], value="%Y-%m-%d"),
        ]
    )


def build_figures(data: np.ndarray) -> tuple[go.Figure, go.Figure, go.Figure, go.Figure, go.Figure, go.Figure]:
    num_points = len(data["time_s"])
    colors = point_colors(num_points)
    local_times = local_times_from_timestamps(data["time_s"])

    fig_rt = go.Figure()
    fig_rt.add_trace(
        go.Scatter(
            x=data["t_K"] * 1000,
            y=data["r_ohm"] * 1000,
            mode="markers",
            name="R vs T",
            marker=dict(size=4, color=colors),
        )
    )
    fig_rt.update_layout(
        title="R vs T",
        xaxis_title="Temperature (mK)",
        yaxis_title="Resistance (mOhm)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )

    fig_xt = go.Figure()
    fig_xt.add_trace(
        go.Scatter(
            x=data["t_K"] * 1000,
            y=data["x_ohm"] * 1000,
            mode="markers",
            name="X vs T",
            marker=dict(size=4, color=colors),
        )
    )
    fig_xt.update_layout(
        title="X vs T",
        xaxis_title="Temperature (mK)",
        yaxis_title="Reactance (mOhm)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )

    fig_r = go.Figure()
    fig_r.add_trace(
        go.Scatter(
            x=local_times,
            y=data["r_ohm"] * 1000,
            mode="markers",
            name="Resistance",
            marker=dict(size=4, color=colors),
        )
    )
    fig_r.update_layout(
        title="R vs Time",
        xaxis_title="Local Time (MST/MDT)",
        yaxis_title="Resistance (mOhm)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )
    time_axis(fig_r)

    fig_x = go.Figure()
    fig_x.add_trace(
        go.Scatter(
            x=local_times,
            y=data["x_ohm"] * 1000,
            mode="markers",
            name="Reactance",
            marker=dict(size=4, color=colors),
        )
    )
    fig_x.update_layout(
        title="X vs Time",
        xaxis_title="Local Time (MST/MDT)",
        yaxis_title="Reactance (mOhm)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )
    time_axis(fig_x)

    fig_t = go.Figure()
    fig_t.add_trace(
        go.Scatter(
            x=local_times,
            y=data["t_K"] * 1000,
            mode="markers",
            name="Temperature",
            marker=dict(size=4, color=colors),
        )
    )
    fig_t.update_layout(
        title="T vs Time",
        xaxis_title="Local Time (MST/MDT)",
        yaxis_title="Temperature (mK)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )
    time_axis(fig_t)

    fig_rx = go.Figure()
    fig_rx.add_trace(
        go.Scatter(
            x=data["r_ohm"] * 1000,
            y=data["x_ohm"] * 1000,
            mode="markers",
            name="X vs R",
            marker=dict(size=4, color=colors),
        )
    )
    fig_rx.update_layout(
        title="X vs R",
        xaxis_title="Resistance (mOhm)",
        yaxis_title="Reactance (mOhm)",
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=50),
    )

    return fig_rt, fig_xt, fig_r, fig_x, fig_t, fig_rx


def run_dash_app() -> None:
    app = dash.Dash(__name__)

    app.layout = html.Div(
        [
            html.Div(
                [
                    html.Div([dcc.Graph(id="graph-rt", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                    html.Div([dcc.Graph(id="graph-xt", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                ]
            ),
            html.Div(
                [
                    html.Div([dcc.Graph(id="graph-r", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                    html.Div([dcc.Graph(id="graph-x", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                ]
            ),
            html.Div(
                [
                    html.Div([dcc.Graph(id="graph-t", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                    html.Div([dcc.Graph(id="graph-rx", config={"displayModeBar": False})], style={"width": "50%", "display": "inline-block"}),
                ]
            ),
            dcc.Interval(id="interval-component", interval=5 * 1000, n_intervals=0),
        ]
    )

    @app.callback(
        [
            Output("graph-rt", "figure"),
            Output("graph-xt", "figure"),
            Output("graph-r", "figure"),
            Output("graph-x", "figure"),
            Output("graph-t", "figure"),
            Output("graph-rx", "figure"),
        ],
        Input("interval-component", "n_intervals"),
    )
    def update_graph(_n):
        try:
            data = load_data()
            required = {"r_ohm", "x_ohm", "t_K", "time_s"}
            names = set(data.dtype.names or [])
            missing = required - names
            if missing:
                raise ValueError(f"Missing required fields: {sorted(missing)}")
            return build_figures(data)
        except Exception as exc:
            print(f"Warning: Error finding or loading Lake Shore data: {exc}. Skipping update.")
            return (
                empty_figure("R vs T"),
                empty_figure("X vs T"),
                empty_figure("R vs Time"),
                empty_figure("X vs Time"),
                empty_figure("T vs Time"),
                empty_figure("X vs R"),
            )

    print("Lake Shore Dash app running on http://127.0.0.1:8051/")
    app.run(host="127.0.0.1", port=8051, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_dash_app()
