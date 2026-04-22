import plotly.graph_objects as go
import time
import numpy as np
import pathlib
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import datetime
import pytz
import os

def run_dash_app():
    app = dash.Dash(__name__)

    app.layout = html.Div([
        # Main R vs T plot, full width
        html.Div([
            dcc.Graph(id='live-graph-rt', config={'displayModeBar': False}, style={'height': '60vh'})
        ], style={'width': '100%', 'display': 'inline-block'}),

        # Row with 3 plots side-by-side below
        html.Div([
            html.Div([
                dcc.Graph(id='live-graph-r', config={'displayModeBar': False}, style={'height': '30vh'})
            ], style={'width': '33%', 'display': 'inline-block'}),

            html.Div([
                dcc.Graph(id='live-graph-t', config={'displayModeBar': False}, style={'height': '30vh'})
            ], style={'width': '33%', 'display': 'inline-block'}),

            html.Div([
                dcc.Graph(id='live-graph-p', config={'displayModeBar': False}, style={'height': '30vh'})
            ], style={'width': '33%', 'display': 'inline-block'}),
        ], style={'width': '100%', 'display': 'inline-block'}),

        dcc.Interval(
            id='interval-component',
            interval=5 * 1000,  # 5 seconds
            n_intervals=0
        )
    ])

    @app.callback(
        [Output('live-graph-rt', 'figure'),
         Output('live-graph-r', 'figure'),
         Output('live-graph-t', 'figure'),
         Output('live-graph-p', 'figure')],
        Input('interval-component', 'n_intervals')
    )
    def update_graph(n):
        try:
            script_path = pathlib.Path(__file__).parent
            data_base_dir = script_path / "Data"

            subdirs = list(data_base_dir.glob("*"))
            if not subdirs:
                print(f"Warning: No subdirectories found in {data_base_dir}. Skipping update.")
                return go.Figure(), go.Figure(), go.Figure()
            latest_subdir = max(subdirs, key=os.path.getmtime)

            files_in_latest_dir = list(latest_subdir.glob("*"))
            if not files_in_latest_dir:
                print(f"Warning: No files found in the latest directory {latest_subdir}. Skipping update.")
                return go.Figure(), go.Figure(), go.Figure()
            latest_file = max(files_in_latest_dir, key=os.path.getmtime)

            # print(f"Loading latest file: {latest_file}")
            data = np.load(latest_file)

            num_points = len(data["time_s"])
            num_highlight = min(5, num_points)
            highlight_indices = list(range(max(0, num_points - num_highlight), num_points))

            plot_color = 'orange'
            current_color = 'red'
            colors = [plot_color] * (num_points - num_highlight) + [current_color] * num_highlight

            utc_times = [datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) for ts in data["time_s"]]
            mst_timezone = pytz.timezone("America/Denver")
            local_times = [utc_time.astimezone(mst_timezone) for utc_time in utc_times]

            fig_rt = go.Figure()
            fig_rt.add_trace(go.Scatter(
                x=data["t_K"]*1000,
                y=data["r_ohm"] * 1000,
                mode='markers',
                name='R vs T',
                marker=dict(size=4, color=colors)
            ))
            fig_rt.update_layout(title="R vs T", xaxis_title="Temperature (mK)", yaxis_title="Resistance (mΩ)",
                                 margin=dict(l=50, r=50, t=50, b=50), template="plotly_white")

            fig_r = go.Figure()
            fig_r.add_trace(go.Scatter(
                x=local_times,
                y=data["r_ohm"] * 1000,
                mode='markers',
                name='Resistance',
                marker=dict(size=4, color=colors)
            ))
            fig_r.update_layout(title="Live Resistance Data", xaxis_title="Local Time (MST/MDT)",
                                yaxis_title="Resistance (mΩ)", margin=dict(l=50, r=50, t=50, b=50),
                                template="plotly_white")
            fig_r.update_xaxes(tickformatstops=[
                dict(dtickrange=[None, 1000], value="%H:%M:%S.%L"),
                dict(dtickrange=[1000, 60000], value="%H:%M:%S"),
                dict(dtickrange=[60000, 3600000], value="%H:%M"),
                dict(dtickrange=[3600000, 86400000], value="%m-%d %H:%M"),
                dict(dtickrange=[86400000, None], value="%Y-%m-%d")
            ])

            fig_t = go.Figure()
            fig_t.add_trace(go.Scatter(
                x=local_times,
                y=data["t_K"] * 1000,
                mode='markers',
                name='Temperature',
                marker=dict(size=4, color=colors)
            ))
            fig_t.update_layout(title="Live Temperature Data", xaxis_title="Local Time (MST/MDT)",
                                yaxis_title="Temperature (mK)", margin=dict(l=50, r=50, t=50, b=50),
                                template="plotly_white")
            fig_t.update_xaxes(tickformatstops=[
                dict(dtickrange=[None, 1000], value="%H:%M:%S.%L"),
                dict(dtickrange=[1000, 60000], value="%H:%M:%S"),
                dict(dtickrange=[60000, 3600000], value="%H:%M"),
                dict(dtickrange=[3600000, 86400000], value="%m-%d %H:%M"),
                dict(dtickrange=[86400000, None], value="%Y-%m-%d")
            ])

            fig_p = go.Figure()
            fig_p.add_trace(go.Scatter(
                x=local_times,
                y=data["p_uW"],
                mode='markers',
                name='Power',
                marker=dict(size=4, color=colors)
            ))
            fig_p.update_layout(title="Heater Power vs Time", xaxis_title="Local Time (MST/MDT)",
                                yaxis_title="Power (μW)", margin=dict(l=50, r=50, t=50, b=50),
                                template="plotly_white")
            fig_p.update_xaxes(tickformatstops=[
                dict(dtickrange=[None, 1000], value="%H:%M:%S.%L"),
                dict(dtickrange=[1000, 60000], value="%H:%M:%S"),
                dict(dtickrange=[60000, 3600000], value="%H:%M"),
                dict(dtickrange=[3600000, 86400000], value="%m-%d %H:%M"),
                dict(dtickrange=[86400000, None], value="%Y-%m-%d")
            ])

            return fig_rt, fig_r, fig_t, fig_p

        except Exception as e:
            print(f"Warning: Error finding or loading data: {e}. Skipping update.")
            return go.Figure(), go.Figure(), go.Figure(), go.Figure()

    app.run(debug=False, port=8051)


if __name__ == '__main__':
    run_dash_app()
