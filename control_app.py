import dash
from dash import dcc, html, Input, Output, State, callback_context
import plotly.graph_objects as go
import numpy as np
import pathlib
import os
import datetime
import pytz

from ramp_controller import controller

# Define available data columns for the custom plot
DATA_COLUMNS = [
    {"label": "Time (Local)", "value": "time_local"},
    {"label": "Temperature (K)", "value": "t_K"},
    {"label": "Resistance (Ohms)", "value": "r_ohm"},
    {"label": "Heater Power (uW)", "value": "p_uW"},
]

app = dash.Dash(__name__, title="Tc checker")

# --- CSS Styles ---
app.index_string = '''
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
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #0f172a;
                color: #f8fafc;
                display: flex;
                height: 100vh;
                overflow: hidden;
            }
            .left-panel {
                width: 350px;
                background-color: #1e293b;
                border-right: 1px solid #334155;
                padding: 20px;
                display: flex;
                flex-direction: column;
                gap: 20px;
                overflow-y: auto;
                box-shadow: 2px 0 10px rgba(0,0,0,0.5);
                z-index: 10;
            }
            .right-panel {
                flex-grow: 1;
                padding: 20px;
                display: flex;
                flex-direction: column;
                gap: 20px;
                overflow-y: auto;
            }
            .card {
                background-color: #334155;
                border-radius: 8px;
                padding: 15px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            .card h3 {
                margin-top: 0;
                font-size: 1.1em;
                color: #e2e8f0;
                border-bottom: 1px solid #475569;
                padding-bottom: 5px;
            }
            .input-group {
                margin-bottom: 12px;
            }
            .input-group label {
                display: block;
                font-size: 0.85em;
                color: #94a3b8;
                margin-bottom: 4px;
            }
            .input-group input, .input-group select {
                width: 100%;
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #475569;
                background-color: #1e293b;
                color: white;
                box-sizing: border-box;
            }
            .input-group input:focus, .input-group select:focus {
                outline: none;
                border-color: #3b82f6;
                box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
            }
            .btn {
                padding: 10px 15px;
                border-radius: 6px;
                border: none;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s;
            }
            .btn-primary { background-color: #3b82f6; color: white; }
            .btn-primary:hover { background-color: #2563eb; }
            .btn-warning { background-color: #f59e0b; color: white; }
            .btn-warning:hover { background-color: #d97706; }
            .btn-danger { background-color: #ef4444; color: white; }
            .btn-danger:hover { background-color: #dc2626; }
            
            .btn-group {
                display: flex;
                gap: 10px;
                margin-top: 15px;
            }
            .btn-group .btn { flex: 1; }
            
            .status-box {
                margin-top: 20px;
                padding: 15px;
                border-radius: 8px;
                background-color: #0f172a;
                border: 1px solid #334155;
            }
            .status-val { font-size: 1.2em; font-weight: bold; color: #38bdf8; }
            .hidden { display: none !important; }
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
'''

app.layout = html.Div(style={"display": "flex", "height": "100vh", "width": "100vw", "overflow": "hidden"}, children=[
    # Left Control Panel
    html.Div(className="left-panel", children=[
        html.H2("Tc checker", style={"margin": "0 0 10px 0", "color": "#f8fafc"}),
        
        # LR700 Settings (Shared)
        html.Div(className="card", children=[
            html.H3("LR700 Connection"),
            html.Div(className="input-group", children=[
                html.Label("Adapter Type"),
                dcc.Dropdown(
                    id='lr700-adapter',
                    options=[{'label': 'Prologix (COM)', 'value': 'prologix'}, {'label': 'NI GPIB (PyVISA)', 'value': 'pyvisa'}],
                    value='prologix',
                    clearable=False,
                    style={"color": "#000"}
                )
            ]),
            html.Div(className="input-group", children=[html.Label("COM Port"), dcc.Input(id='lr700-port', type='text', value='COM3')]),
            html.Div(className="input-group", children=[html.Label("GPIB Address"), dcc.Input(id='lr700-gpib', type='number', value=17)]),
        ]),

        # Instrument Selection
        html.Div(className="card", children=[
            html.H3("Instrument Selection"),
            html.Div(className="input-group", children=[
                dcc.Dropdown(
                    id='instrument-dropdown',
                    options=[
                        {'label': 'Bluefors', 'value': 'Bluefors'},
                        {'label': 'LakeShore', 'value': 'LakeShore'}
                    ],
                    value='Bluefors',
                    clearable=False,
                    style={"color": "#000"} # Fix dropdown text color
                )
            ]),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Button("Test Connection", id="btn-test-conn", className="btn btn-warning", style={"width": "100%", "padding": "8px"}),
                html.Div(id='conn-status-text', style={"marginTop": "8px", "color": "#f59e0b", "fontSize": "0.85em", "fontWeight": "bold"}, children="")
            ])
        ]),

        # Bluefors Settings
        html.Div(id='bluefors-settings', className="card", children=[
            html.H3("Bluefors Settings"),
            html.Div(className="input-group", children=[html.Label("IP Address"), dcc.Input(id='bf-ip', type='text', value='132.163.157.220:5001')]),
            html.Div(className="input-group", children=[
                html.Label("Thermometer Source (Channel)"),
                dcc.Dropdown(id='bf-source', 
                             options=[{'label': f'Channel {i}', 'value': i} for i in range(1, 7)], 
                             value=6, 
                             clearable=False, style={"color":"#000"})
            ]),
            html.Div(className="input-group", children=[html.Label("Target Temp (mK)"), dcc.Input(id='bf-target', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Initial Power (uW)"), dcc.Input(id='bf-init-power', type='number', value=0)]),
            html.Div(className="input-group", children=[html.Label("Power Step (uW)"), dcc.Input(id='bf-step', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Step Delay (s)"), dcc.Input(id='bf-delay', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Max Power Limit (uW)"), dcc.Input(id='bf-max-power', type='number', value=500)]),
            html.Div(className="input-group", children=[html.Label("Timeout (s)"), dcc.Input(id='bf-timeout', type='number', value=3600)]),
        ]),

        # LakeShore Settings
        html.Div(id='lakeshore-settings', className="card hidden", children=[
            html.H3("LakeShore Settings"),
            html.Div(className="input-group", children=[html.Label("COM Port"), dcc.Input(id='ls-port', type='text', value='COM6')]),
            html.Div(className="input-group", children=[html.Label("Baudrate"), dcc.Input(id='ls-baudrate', type='number', value=9600)]),
            html.Div(className="input-group", children=[html.Label("Channel"), dcc.Input(id='ls-channel', type='number', value=4)]),
            html.Div(className="input-group", children=[html.Label("Target Setpoint (K)"), dcc.Input(id='ls-setpoint', type='number', value=0.010)]),
            html.Div(className="input-group", children=[html.Label("Ramp Rate (K/min)"), dcc.Input(id='ls-rate', type='number', value=0.001)]),
        ]),

        # Controls
        html.Div(className="card", children=[
            html.H3("Ramp Controls"),
            html.Div(className="btn-group", children=[
                html.Button("Start", id="btn-start", className="btn btn-primary"),
                html.Button("Pause", id="btn-pause", className="btn btn-warning"),
                html.Button("Stop", id="btn-stop", className="btn btn-danger")
            ])
        ]),

        # Checkbox for separate logging
        html.Div(style={"marginTop": "15px"}, children=[
            dcc.Checklist(
                id='log-checkbox',
                options=[{'label': ' Separate logging?', 'value': 'yes'}],
                style={"color": "#f8fafc", "fontWeight": "500"}
            )
        ]),

        # Logging Settings
        html.Div(id='logging-settings', className="card hidden", style={"marginTop": "10px"}, children=[
            html.H3("Logging Settings"),
            html.Div(className="input-group", children=[
                html.Label("Save Directory (Leave blank for default)"), 
                dcc.Input(id='log-dir', type='text', placeholder='Data/YYYYMMDD')
            ]),
            html.Div(className="input-group", children=[
                html.Label("Device Name / Prefix"), 
                dcc.Input(id='log-prefix', type='text', placeholder='e.g., A1')
            ]),
            html.Div(className="input-group", children=[
                html.Label("Interval (s)"), 
                dcc.Input(id='log-interval', type='number', value=1)
            ]),
            html.Div(className="btn-group", children=[
                html.Button("Start Logging", id="btn-log-start", className="btn btn-primary"),
                html.Button("Stop Logging", id="btn-log-stop", className="btn btn-danger")
            ]),
            html.Div(id='log-status-text', style={"marginTop": "10px", "color": "#38bdf8", "fontSize": "0.9em", "fontWeight": "bold"}, children="Logger Idle")
        ]),

        # Status
        html.Div(className="status-box", children=[
            html.Div([html.Span("State: ", style={"color": "#94a3b8"}), html.Span("IDLE", id="status-state", className="status-val")]),
            html.Div([html.Span("Msg: ", style={"color": "#94a3b8"}), html.Span("Ready", id="status-msg", style={"fontSize":"0.9em"})]),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Div([html.Span("Current Temp: ", style={"color": "#94a3b8"}), html.Span("-", id="status-temp", className="status-val")]),
                html.Div([html.Span("Power/Setpoint: ", style={"color": "#94a3b8"}), html.Span("-", id="status-power", className="status-val")]),
            ])
        ])
    ]),

    # Right Plot Panel
    html.Div(className="right-panel", children=[
        html.Div(className="card", style={"flexGrow": "1", "display": "flex", "flexDirection": "column", "height": "100%"}, children=[
            html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "10px"}, children=[
                html.H3("Live Data", style={"borderBottom": "none", "margin": "0"}),
                html.Div(style={"display": "flex", "gap": "10px"}, children=[
                    dcc.Dropdown(id='custom-x', options=DATA_COLUMNS, value='t_K', clearable=False, style={"width": "200px", "color": "#000"}),
                    html.Span(" vs ", style={"alignSelf": "center", "fontWeight": "bold"}),
                    dcc.Dropdown(id='custom-y', options=DATA_COLUMNS, value='r_ohm', clearable=False, style={"width": "200px", "color": "#000"}),
                ])
            ]),
            dcc.Graph(id='plot-custom', config={'displayModeBar': False}, style={"flexGrow": "1"})
        ])
    ]),

    # Background pollers
    dcc.Interval(id='interval-status', interval=1000, n_intervals=0),
    dcc.Interval(id='interval-data', interval=5000, n_intervals=0)
])

# --- Callbacks ---

@app.callback(
    [Output('bluefors-settings', 'className'),
     Output('lakeshore-settings', 'className')],
    Input('instrument-dropdown', 'value')
)
def toggle_settings(instrument):
    if instrument == 'Bluefors':
        return 'card', 'card hidden'
    return 'card hidden', 'card'

@app.callback(
    Output('logging-settings', 'className'),
    Input('log-checkbox', 'value')
)
def toggle_logging_settings(checkbox_val):
    if checkbox_val and 'yes' in checkbox_val:
        return 'card'
    return 'card hidden'

@app.callback(
    Output('conn-status-text', 'children'),
    Input('btn-test-conn', 'n_clicks'),
    [State('instrument-dropdown', 'value'),
     State('bf-source', 'value'), State('bf-ip', 'value'),
     State('ls-port', 'value'), State('ls-baudrate', 'value'), State('ls-channel', 'value'),
     State('lr700-adapter', 'value'), State('lr700-port', 'value'), State('lr700-gpib', 'value')],
    prevent_initial_call=True
)
def test_connection(n_clicks, instrument, bf_source, bf_ip, ls_port, ls_baudrate, ls_channel, lr700_adapter, lr700_port, lr700_gpib):
    return controller.check_connection(
        instrument, bf_source=bf_source, bf_ip=bf_ip, 
        ls_port=ls_port, ls_baudrate=ls_baudrate, ls_channel=ls_channel,
        lr700_adapter=lr700_adapter, lr700_port=lr700_port, lr700_gpib=lr700_gpib
    )

@app.callback(
    [Output('status-state', 'children'),
     Output('status-msg', 'children'),
     Output('status-temp', 'children'),
     Output('status-power', 'children'),
     Output('btn-pause', 'children'),
     Output('log-status-text', 'children')],
    [Input('btn-start', 'n_clicks'),
     Input('btn-pause', 'n_clicks'),
     Input('btn-stop', 'n_clicks'),
     Input('btn-log-start', 'n_clicks'),
     Input('btn-log-stop', 'n_clicks'),
     Input('interval-status', 'n_intervals')],
    [State('instrument-dropdown', 'value'),
     State('bf-ip', 'value'), State('bf-source', 'value'), State('bf-target', 'value'), State('bf-init-power', 'value'), 
     State('bf-step', 'value'), State('bf-delay', 'value'), State('bf-timeout', 'value'), State('bf-max-power', 'value'),
     State('ls-port', 'value'), State('ls-baudrate', 'value'), State('ls-channel', 'value'), 
     State('ls-setpoint', 'value'), State('ls-rate', 'value'),
     State('lr700-adapter', 'value'), State('lr700-port', 'value'), State('lr700-gpib', 'value'),
     State('log-dir', 'value'), State('log-prefix', 'value'), State('log-interval', 'value')]
)
def handle_controls(start_c, pause_c, stop_c, log_start_c, log_stop_c, n_int, 
                    instrument, bf_ip, bf_source, bf_target, bf_init, bf_step, bf_delay, bf_timeout, bf_max,
                    ls_port, ls_baudrate, ls_channel, ls_setpoint, ls_rate,
                    lr700_adapter, lr700_port, lr700_gpib,
                    log_dir, log_prefix, log_interval):
    
    ctx = callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if trigger_id == 'btn-start':
            if instrument == 'Bluefors':
                controller.start_bluefors(bf_ip, bf_source, bf_target*1e-3, bf_init*1e-6, bf_step*1e-6, bf_delay, bf_timeout, bf_max*1e-6)
            else:
                controller.start_lakeshore(ls_port, ls_baudrate, ls_channel, ls_setpoint, ls_rate)
                
        elif trigger_id == 'btn-pause':
            st = controller.get_status()['state']
            if st == "RAMPING":
                controller.pause()
            elif st == "PAUSED":
                controller.resume()
                
        elif trigger_id == 'btn-stop':
            controller.stop()

        elif trigger_id == 'btn-log-start':
            log_dir_val = log_dir if log_dir else ""
            log_prefix_val = log_prefix if log_prefix else ""
            log_interval_val = log_interval if log_interval else 1
            controller.start_logging(
                instrument, log_dir_val, log_prefix_val, log_interval_val,
                bf_ip=bf_ip, bf_source=bf_source, ls_port=ls_port, ls_baudrate=ls_baudrate, ls_channel=ls_channel,
                lr700_adapter=lr700_adapter, lr700_port=lr700_port, lr700_gpib=lr700_gpib
            )
            
        elif trigger_id == 'btn-log-stop':
            controller.stop_logging()

    status = controller.get_status()
    temp_str = f"{status['current_temp']*1000:.2f} mK" if status['current_temp'] is not None else "-"
    if status['current_power_or_setpoint'] is not None:
        if status['instrument'] == 'Bluefors':
            power_str = f"{status['current_power_or_setpoint']*1e6:.2f} uW"
        else:
            power_str = f"{status['current_power_or_setpoint']*1000:.2f} mK (Set)"
    else:
        power_str = "-"

    pause_btn_text = "Resume" if status['state'] == "PAUSED" else "Pause"
    log_status_text = controller.log_message
    
    return status['state'], status['message'], temp_str, power_str, pause_btn_text, log_status_text

def fetch_latest_data():
    try:
        script_path = pathlib.Path(__file__).parent
        data_base_dir = script_path / "Data"
        subdirs = list(data_base_dir.glob("*"))
        if not subdirs: return None
        latest_subdir = max(subdirs, key=os.path.getmtime)
        files_in_latest_dir = list(latest_subdir.glob("*"))
        if not files_in_latest_dir: return None
        latest_file = max(files_in_latest_dir, key=os.path.getmtime)
        return np.load(latest_file)
    except Exception:
        return None

def get_plot_color_array(num_points):
    num_highlight = min(5, num_points)
    plot_color = '#3b82f6' # Blue
    current_color = '#ef4444' # Red
    return [plot_color] * (num_points - num_highlight) + [current_color] * num_highlight

def process_axis_data(data, axis_key):
    if axis_key == 'time_local':
        utc_times = [datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) for ts in data["time_s"]]
        mst_timezone = pytz.timezone("America/Denver")
        return [utc_time.astimezone(mst_timezone) for utc_time in utc_times]
    elif axis_key == 't_K':
        return data["t_K"] * 1000 # mK
    elif axis_key == 'r_ohm':
        return data["r_ohm"] * 1000 # mOhm
    elif axis_key == 'p_uW':
        return data["p_uW"]
    return data.get(axis_key, [])

def get_axis_title(axis_key):
    for col in DATA_COLUMNS:
        if col["value"] == axis_key:
            title = col["label"]
            if axis_key == 't_K': return "Temperature (mK)"
            if axis_key == 'r_ohm': return "Resistance (mΩ)"
            return title
    return axis_key

@app.callback(
    Output('plot-custom', 'figure'),
    [Input('interval-data', 'n_intervals'),
     Input('custom-x', 'value'),
     Input('custom-y', 'value')]
)
def update_plots(n, x_key, y_key):
    data = fetch_latest_data()
    if data is None:
        return go.Figure(layout={"template":"plotly_dark", "plot_bgcolor":"#1e293b", "paper_bgcolor":"#1e293b"})

    num_points = len(data["time_s"])
    colors = get_plot_color_array(num_points)

    # Custom Plot
    x_data = process_axis_data(data, x_key)
    y_data = process_axis_data(data, y_key)
    
    fig_custom = go.Figure()
    fig_custom.add_trace(go.Scatter(
        x=x_data, y=y_data, mode='markers',
        marker=dict(size=4, color=colors)
    ))
    fig_custom.update_layout(
        margin=dict(l=50, r=20, t=20, b=50),
        xaxis_title=get_axis_title(x_key),
        yaxis_title=get_axis_title(y_key),
        template="plotly_dark",
        plot_bgcolor="#1e293b",
        paper_bgcolor="#1e293b",
        font=dict(color="#e2e8f0")
    )

    if x_key == 'time_local':
        fig_custom.update_xaxes(tickformatstops=[
            dict(dtickrange=[None, 1000], value="%H:%M:%S.%L"),
            dict(dtickrange=[1000, 60000], value="%H:%M:%S"),
            dict(dtickrange=[60000, 3600000], value="%H:%M"),
            dict(dtickrange=[3600000, 86400000], value="%m-%d %H:%M"),
            dict(dtickrange=[86400000, None], value="%Y-%m-%d")
        ])

    return fig_custom


if __name__ == '__main__':
    app.run(debug=True, port=8052)
