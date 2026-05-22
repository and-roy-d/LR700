import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import plotly.graph_objects as go
import numpy as np
import pathlib
import os
import sys
import datetime
import pytz

from ramp_controller import controller
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "bftc_workflow"))
import bftc

DEFAULT_LR700_PORT = "COM14" if sys.platform.startswith("win") else "/dev/ttyUSB0"
DEFAULT_LS370_PORT = "COM6" if sys.platform.startswith("win") else "/dev/ttyUSB1"

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
            html.Div(className="input-group", children=[html.Label("COM Port / Serial Port"), dcc.Input(id='lr700-port', type='text', value=DEFAULT_LR700_PORT)]),
            html.Div(className="input-group", children=[html.Label("GPIB Address"), dcc.Input(id='lr700-gpib', type='number', value=17)]),
        ]),

        # Instrument Selection
        html.Div(className="card", children=[
            html.H3("Instrument Selection"),
            html.Div(className="input-group", children=[
                dcc.Dropdown(
                    id='instrument-dropdown',
                    options=[
                        {'label': 'Myriad/Miniebit', 'value': 'Myriad/Miniebit'},
                        {'label': 'KPAC', 'value': 'KPAC'},
                        {'label': '2120 OG', 'value': '2120 OG'}
                    ],
                    value='Myriad/Miniebit',
                    clearable=False,
                    style={"color": "#000"} # Fix dropdown text color
                )
            ]),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Button("Test Connection", id="btn-test-conn", className="btn btn-warning", style={"width": "100%", "padding": "8px"}),
                html.Div(id='conn-status-text', style={"marginTop": "8px", "color": "#f59e0b", "fontSize": "0.85em", "fontWeight": "bold"}, children="")
            ])
        ]),

        # Myriad/Miniebit Settings
        html.Div(id='bluefors-settings', className="card", children=[
            html.H3("Myriad/Miniebit Settings"),
            html.Div(className="input-group", children=[html.Label("IP Address"), dcc.Input(id='bf-ip', type='text', value='169.169.10.10:5001')]),
            html.Div(className="input-group", children=[
                html.Label("Thermometer Source (Channel)"),
                dcc.Dropdown(id='bf-source', 
                             options=[
                                 {'label': 'CH 1 (40 K flange)', 'value': 1},
                                 {'label': 'CH 2 (4 K flange)', 'value': 2},
                                 {'label': 'CH 5 (Still flange)', 'value': 5},
                                 {'label': 'CH 6 (MXC flange)', 'value': 6}
                             ], 
                             value=6, 
                             clearable=False, style={"color":"#000"})
            ]),
            html.Div(style={"marginTop": "8px"}, children=[
                dcc.Checklist(
                    id='bf-solo-channel',
                    options=[{'label': ' Solo selected channel (turn off others)', 'value': 'solo'}],
                    value=[],
                    style={"color": "#f8fafc", "fontSize": "0.85em"}
                ),
                html.Div(id='bf-solo-status', style={"marginTop": "4px", "color": "#f59e0b", "fontSize": "0.8em"})
            ]),
            html.Div(className="input-group", children=[html.Label("Target Temp (mK)"), dcc.Input(id='bf-target', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Initial Power (uW)"), dcc.Input(id='bf-init-power', type='number', value=0)]),
            html.Div(className="input-group", children=[html.Label("Power Step (uW)"), dcc.Input(id='bf-step', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Step Delay (s)"), dcc.Input(id='bf-delay', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Max Power Limit (uW)"), dcc.Input(id='bf-max-power', type='number', value=500)]),
            html.Div(className="input-group", children=[html.Label("Timeout (s)"), dcc.Input(id='bf-timeout', type='number', value=3600)]),
        ]),

        # KPAC Settings
        html.Div(id='lakeshore-settings', className="card hidden", children=[
            html.H3("KPAC Settings"),
            html.Div(className="input-group", children=[html.Label("COM Port / Serial Port"), dcc.Input(id='ls-port', type='text', value=DEFAULT_LS370_PORT)]),
            html.Div(className="input-group", children=[
                html.Label("GPIB Address (Optional)"),
                dcc.Input(id='ls-gpib', type='number', placeholder='e.g., 15 (leave blank for RS-232)', value=15)
            ]),
            html.Div(className="input-group", children=[html.Label("Baudrate"), dcc.Input(id='ls-baudrate', type='number', value=9600)]),
            html.Div(className="input-group", children=[html.Label("Channel"), dcc.Input(id='ls-channel', type='number', value=5)]),
            html.Div(style={"marginTop": "8px"}, children=[
                dcc.Checklist(
                    id='ls-solo-channel',
                    options=[{'label': ' Solo selected channel (turn off others)', 'value': 'solo'}],
                    value=[],
                    style={"color": "#f8fafc", "fontSize": "0.85em"}
                ),
                html.Div(id='ls-solo-status', style={"marginTop": "4px", "color": "#f59e0b", "fontSize": "0.8em"})
            ]),
            html.Div(className="input-group", children=[html.Label("Target Setpoint (K)"), dcc.Input(id='ls-setpoint', type='number', value=0.010)]),
            html.Div(className="input-group", children=[html.Label("Ramp Rate (K/min)"), dcc.Input(id='ls-rate', type='number', value=0.001)]),
            
            # Advanced controls (hidden from UI to keep it simple, but kept in DOM to preserve Dash callbacks)
            html.Div(style={"display": "none"}, children=[
                html.Div(className="input-group", children=[
                    html.Label("Control Loop Mode"),
                    dcc.Dropdown(
                        id='ls-cmode',
                        options=[
                            {'label': 'Closed Loop PID', 'value': 1},
                            {'label': 'Open Loop', 'value': 3}
                        ],
                        value=1,
                        clearable=False,
                        style={"color": "#000"}
                    )
                ]),
                html.Div(className="input-group", children=[
                    html.Label("Heater Range"),
                    dcc.Dropdown(
                        id='ls-hrng',
                        options=[
                            {'label': 'Off', 'value': 0},
                            {'label': '31.6 µA', 'value': 1},
                            {'label': '100 µA', 'value': 2},
                            {'label': '316 µA', 'value': 3},
                            {'label': '1.00 mA', 'value': 4},
                            {'label': '3.16 mA', 'value': 5},
                            {'label': '10.0 mA', 'value': 6},
                            {'label': '31.6 mA', 'value': 7},
                            {'label': '100 mA', 'value': 8}
                        ],
                        value=6,
                        clearable=False,
                        style={"color": "#000"}
                    )
                ]),
                html.Div(style={"display": "flex", "gap": "10px", "marginTop": "10px"}, children=[
                    html.Div(className="input-group", style={"flex": "1"}, children=[
                        html.Label("P"),
                        dcc.Input(id='ls-p', type='number', value=10.0)
                    ]),
                    html.Div(className="input-group", style={"flex": "1"}, children=[
                        html.Label("I"),
                        dcc.Input(id='ls-i', type='number', value=20.0)
                    ]),
                    html.Div(className="input-group", style={"flex": "1"}, children=[
                        html.Label("D"),
                        dcc.Input(id='ls-d', type='number', value=0.0)
                    ])
                ])
            ])
        ]),

        # 2120 OG Settings Card
        html.Div(id='og-settings', className="card hidden", children=[
            html.H3("2120 OG Settings"),
            html.Div(className="input-group", children=[html.Label("Lakeshore Port"), dcc.Input(id='og-port', type='text', value=DEFAULT_LS370_PORT)]),
            html.Div(className="input-group", children=[html.Label("Lakeshore GPIB Address"), dcc.Input(id='og-gpib', type='number', value=15)]),
            html.Div(className="input-group", children=[html.Label("Baudrate"), dcc.Input(id='og-baudrate', type='number', value=9600)]),
            html.Div(className="input-group", children=[html.Label("Channel"), dcc.Input(id='og-channel', type='number', value=5)]),
            html.Div(style={"marginTop": "8px"}, children=[
                dcc.Checklist(
                    id='og-solo-channel',
                    options=[{'label': ' Solo selected channel (turn off others)', 'value': 'solo'}],
                    value=[],
                    style={"color": "#f8fafc", "fontSize": "0.85em"}
                ),
                html.Div(id='og-solo-status', style={"marginTop": "4px", "color": "#f59e0b", "fontSize": "0.8em"})
            ]),
            html.Div(className="input-group", children=[html.Label("Target Temp (mK)"), dcc.Input(id='og-target-temp', type='number', value=50.0)]),
            html.Div(className="input-group", children=[
                html.Label("Heater Range"),
                dcc.Dropdown(
                    id='og-hrng',
                    options=[
                        {'label': 'Off', 'value': 0},
                        {'label': '31.6 µA', 'value': 1},
                        {'label': '100 µA', 'value': 2},
                        {'label': '316 µA', 'value': 3},
                        {'label': '1.00 mA', 'value': 4},
                        {'label': '3.16 mA', 'value': 5},
                        {'label': '10.0 mA', 'value': 6},
                        {'label': '31.6 mA', 'value': 7},
                        {'label': '100 mA', 'value': 8}
                    ],
                    value=5,
                    clearable=False,
                    style={"color": "#000"}
                )
            ]),
            # Initial output with 'Use current output' checkbox
            html.Div(style={"display": "flex", "gap": "8px", "alignItems": "flex-end", "marginBottom": "12px"}, children=[
                html.Div(style={"flex": "1"}, children=[
                    html.Label("Initial Output (%)", style={"display": "block", "fontSize": "0.85em", "color": "#94a3b8", "marginBottom": "4px"}),
                    dcc.Input(id='og-init-output', type='number', value=0.0,
                              style={"width": "100%", "padding": "8px", "borderRadius": "4px",
                                     "border": "1px solid #475569", "backgroundColor": "#1e293b",
                                     "color": "white", "boxSizing": "border-box"})
                ]),
                html.Div(style={"paddingBottom": "6px"}, children=[
                    dcc.Checklist(
                        id='og-use-current-output',
                        options=[{'label': ' Use current', 'value': 'use'}],
                        value=[],
                        style={"color": "#f8fafc", "fontSize": "0.8em", "whiteSpace": "nowrap"}
                    )
                ])
            ]),
            html.Div(className="input-group", children=[html.Label("Output Step (%)"), dcc.Input(id='og-output-step', type='number', value=1.0)]),
            html.Div(className="input-group", children=[html.Label("Step Delay (s)"), dcc.Input(id='og-step-delay', type='number', value=10)]),
            html.Div(className="input-group", children=[html.Label("Max Output (%)"), dcc.Input(id='og-max-output', type='number', value=100.0)]),
            html.Div(className="input-group", children=[html.Label("Heater Resistance (Ohms)"), dcc.Input(id='og-resistance', type='number', value=120.0)]),
            # Ramp Mode dropdown
            html.Div(className="input-group", children=[
                html.Label("Ramp Mode"),
                dcc.Dropdown(
                    id='og-ramp-mode',
                    options=[
                        {'label': 'Constant Current Ramp', 'value': 'constant_current'},
                        {'label': 'Linear Power Steps (Constant dP/dt)', 'value': 'linear_power'},
                        {'label': 'Software PI Control', 'value': 'software_pi'},
                    ],
                    value='constant_current',
                    clearable=False,
                    style={"color": "#000"}
                )
            ]),
            # PI parameters — hidden unless Software PI is selected
            html.Div(id='og-pi-params', style={"display": "none"}, children=[
                html.Div(className="input-group", children=[
                    html.Label("Ramp Rate (mK/min)"),
                    dcc.Input(id='og-ramp-rate', type='number', value=2.0)
                ]),
                html.Div(style={"display": "flex", "gap": "8px"}, children=[
                    html.Div(className="input-group", style={"flex": "1"}, children=[
                        html.Label("Kp (µW/mK)"),
                        dcc.Input(id='og-kp', type='number', value=5.0)
                    ]),
                    html.Div(className="input-group", style={"flex": "1"}, children=[
                        html.Label("Ki (µW/mK·s)"),
                        dcc.Input(id='og-ki', type='number', value=0.1)
                    ])
                ])
            ]),
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
                html.Div([html.Span("Control Mode: ", style={"color": "#94a3b8"}), html.Span("-", id="status-cmode", style={"fontWeight": "bold", "color": "#38bdf8"})]),
                html.Div([html.Span("Heater Range: ", style={"color": "#94a3b8"}), html.Span("-", id="status-hrng", style={"fontWeight": "bold", "color": "#38bdf8"})]),
                html.Div([html.Span("Active PID: ", style={"color": "#94a3b8"}), html.Span("-", id="status-pid", style={"fontWeight": "bold", "color": "#38bdf8"})])
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
     Output('lakeshore-settings', 'className'),
     Output('og-settings', 'className')],
    Input('instrument-dropdown', 'value')
)
def toggle_settings(instrument):
    if instrument == 'Myriad/Miniebit':
        return 'card', 'card hidden', 'card hidden'
    elif instrument == 'KPAC':
        return 'card hidden', 'card', 'card hidden'
    else:
        return 'card hidden', 'card hidden', 'card'

@app.callback(
    Output('logging-settings', 'className'),
    Input('log-checkbox', 'value')
)
def toggle_logging_settings(checkbox_val):
    if checkbox_val and 'yes' in checkbox_val:
        return 'card'
    return 'card hidden'

@app.callback(
    Output('bf-solo-status', 'children'),
    [Input('bf-solo-channel', 'value')],
    [State('bf-source', 'value'), State('bf-ip', 'value')],
    prevent_initial_call=True
)
def toggle_solo_channel(solo_val, bf_source, bf_ip):
    """Turn off all channels except the selected one, or restore all."""
    print(f"[Solo CB] solo_val={solo_val!r}, bf_source={bf_source!r}, bf_ip={bf_ip!r}")
    try:
        from bftc_workflow.bftc import BFTC
        bf = BFTC(bf_ip) if bf_ip else BFTC()
        bf_source = int(bf_source)
        if solo_val and 'solo' in solo_val:
            bf.solo_channel(bf_source)
            return f"Solo CH {bf_source} — others disabled"
        else:
            bf.enable_all_channels()
            return "All channels re-enabled"
    except Exception as e:
        print(f"  ERROR: {e}")
        return f"Error: {e}"

@app.callback(
    Output('ls-solo-status', 'children'),
    [Input('ls-solo-channel', 'value')],
    [State('ls-channel', 'value'), State('ls-port', 'value'), State('ls-baudrate', 'value'), State('ls-gpib', 'value')],
    prevent_initial_call=True
)
def toggle_ls_solo_channel(solo_val, ls_channel, ls_port, ls_baudrate, ls_gpib):
    """Turn off all channels except the selected one, or restore all."""
    print(f"[LS Solo CB] solo_val={solo_val!r}, ls_channel={ls_channel!r}, ls_port={ls_port!r}, ls_gpib={ls_gpib!r}")
    try:
        ls_channel = int(ls_channel)
        
        import sys
        import pathlib
        workflow_path = str(pathlib.Path(__file__).resolve().parent / "lakeshore_workflow")
        if workflow_path not in sys.path:
            sys.path.insert(0, workflow_path)
        from lakeshore_370_temperature_test import LakeShore370
        
        with LakeShore370(port=ls_port, baudrate=ls_baudrate, gpib_address=ls_gpib) as ls:
            if solo_val and 'solo' in solo_val:
                ls.solo_channel(ls_channel)
                return f"Solo CH {ls_channel} — others disabled"
            else:
                ls.enable_all_channels()
                return "All channels re-enabled"
    except Exception as e:
        print(f"  ERROR: {e}")
        return f"Error: {e}"

@app.callback(
    Output('og-solo-status', 'children'),
    [Input('og-solo-channel', 'value')],
    [State('og-channel', 'value'), State('og-port', 'value'), State('og-baudrate', 'value'), State('og-gpib', 'value')],
    prevent_initial_call=True
)
def toggle_og_solo_channel(solo_val, og_channel, og_port, og_baudrate, og_gpib):
    """Turn off all channels except the selected one, or restore all for 2120 OG."""
    print(f"[OG Solo CB] solo_val={solo_val!r}, og_channel={og_channel!r}, og_port={og_port!r}, og_gpib={og_gpib!r}")
    try:
        og_channel = int(og_channel)
        
        import sys
        import pathlib
        workflow_path = str(pathlib.Path(__file__).resolve().parent / "lakeshore_workflow")
        if workflow_path not in sys.path:
            sys.path.insert(0, workflow_path)
        from lakeshore_370_temperature_test import LakeShore370
        
        # Parse inputs correctly
        gpib_val = int(og_gpib) if og_gpib else None
        baud_val = int(og_baudrate) if og_baudrate else 9600
        
        with LakeShore370(port=og_port, baudrate=baud_val, gpib_address=gpib_val) as ls:
            if solo_val and 'solo' in solo_val:
                ls.solo_channel(og_channel)
                return f"Solo CH {og_channel} — others disabled"
            else:
                ls.enable_all_channels()
                return "All channels re-enabled"
    except Exception as e:
        print(f"  ERROR: {e}")
        return f"Error: {e}"

@app.callback(
    Output('og-pi-params', 'style'),
    Input('og-ramp-mode', 'value')
)
def toggle_og_pi_params(ramp_mode):
    """Show PI parameter fields only when Software PI mode is selected."""
    if ramp_mode == 'software_pi':
        return {"display": "block", "marginTop": "4px", "padding": "8px",
                "backgroundColor": "#1e293b", "borderRadius": "6px",
                "border": "1px solid #475569"}
    return {"display": "none"}

@app.callback(
    Output('og-init-output', 'disabled'),
    Input('og-use-current-output', 'value')
)
def toggle_og_init_output_disabled(use_current):
    """Disable the Initial Output field when 'Use current' is checked."""
    return bool(use_current and 'use' in use_current)

@app.callback(
    Output('conn-status-text', 'children'),
    Input('btn-test-conn', 'n_clicks'),
    [State('instrument-dropdown', 'value'),
     State('bf-source', 'value'), State('bf-ip', 'value'),
     State('ls-port', 'value'), State('ls-baudrate', 'value'), State('ls-channel', 'value'), State('ls-gpib', 'value'),
     State('lr700-adapter', 'value'), State('lr700-port', 'value'), State('lr700-gpib', 'value'),
     # 2120 OG states
     State('og-port', 'value'), State('og-baudrate', 'value'), State('og-channel', 'value'), State('og-gpib', 'value')],
    prevent_initial_call=True
)
def test_connection(n_clicks, instrument, bf_source, bf_ip, ls_port, ls_baudrate, ls_channel, ls_gpib, lr700_adapter, lr700_port, lr700_gpib,
                    og_port, og_baudrate, og_channel, og_gpib):
    print(f"[Test Conn] instrument={instrument}, bf_ip={bf_ip}, bf_source={bf_source}, ls_port={ls_port}, ls_gpib={ls_gpib}, lr700_port={lr700_port}, lr700_adapter={lr700_adapter}")
    try:
        # Map parameters based on active instrument
        if instrument == "2120 OG":
            active_bf_ip = None
            active_ls_port = og_port
            active_ls_baud = og_baudrate
            active_ls_gpib = og_gpib
            active_ls_channel = og_channel
            active_bf_source = None
        elif instrument == "Myriad/Miniebit":
            active_bf_ip = bf_ip
            active_ls_port = None
            active_ls_baud = 9600
            active_ls_gpib = None
            active_ls_channel = None
            active_bf_source = bf_source
        else: # KPAC
            active_bf_ip = None
            active_ls_port = ls_port
            active_ls_baud = ls_baudrate
            active_ls_gpib = ls_gpib
            active_ls_channel = ls_channel
            active_bf_source = None

        result = controller.check_connection(
            instrument, bf_source=active_bf_source, bf_ip=active_bf_ip, 
            ls_port=active_ls_port, ls_baudrate=active_ls_baud, ls_channel=active_ls_channel, ls_gpib=active_ls_gpib,
            lr700_adapter=lr700_adapter, lr700_port=lr700_port, lr700_gpib=lr700_gpib
        )
        print(f"[Test Conn] result: {result}")
        return result
    except Exception as e:
        print(f"[Test Conn] ERROR: {e}")
        return f"Error: {e}"

@app.callback(
    [Output('status-state', 'children'),
     Output('status-msg', 'children'),
     Output('status-temp', 'children'),
     Output('status-power', 'children'),
     Output('btn-pause', 'children'),
     Output('log-status-text', 'children'),
     Output('status-cmode', 'children'),
     Output('status-hrng', 'children'),
     Output('status-pid', 'children'),
     Output('og-init-output', 'value')],
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
     State('ls-setpoint', 'value'), State('ls-rate', 'value'), State('ls-gpib', 'value'),
     State('lr700-adapter', 'value'), State('lr700-port', 'value'), State('lr700-gpib', 'value'),
     State('log-dir', 'value'), State('log-prefix', 'value'), State('log-interval', 'value'),
     State('ls-cmode', 'value'), State('ls-hrng', 'value'),
     State('ls-p', 'value'), State('ls-i', 'value'), State('ls-d', 'value'),
     # 2120 OG states
     State('og-port', 'value'), State('og-gpib', 'value'), State('og-baudrate', 'value'), State('og-channel', 'value'),
     State('og-target-temp', 'value'), State('og-hrng', 'value'), State('og-init-output', 'value'),
     State('og-output-step', 'value'), State('og-step-delay', 'value'), State('og-max-output', 'value'), State('og-resistance', 'value'),
     State('og-solo-channel', 'value'),
     State('og-ramp-mode', 'value'), State('og-use-current-output', 'value'),
     State('og-ramp-rate', 'value'), State('og-kp', 'value'), State('og-ki', 'value')]
)
def handle_controls(start_c, pause_c, stop_c, log_start_c, log_stop_c, n_int,
                    instrument, bf_ip, bf_source, bf_target, bf_init, bf_step, bf_delay, bf_timeout, bf_max,
                    ls_port, ls_baudrate, ls_channel, ls_setpoint, ls_rate, ls_gpib,
                    lr700_adapter, lr700_port, lr700_gpib,
                    log_dir, log_prefix, log_interval,
                    ls_cmode, ls_hrng, ls_p, ls_i, ls_d,
                    og_port, og_gpib, og_baudrate, og_channel,
                    og_target_temp, og_hrng, og_init_output, og_output_step,
                    og_step_delay, og_max_output, og_resistance, og_solo_channel,
                    og_ramp_mode, og_use_current_output, og_ramp_rate, og_kp, og_ki):

    ctx = callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]

        if trigger_id == 'btn-start':
            if instrument == 'Myriad/Miniebit':
                controller.start_myriad(bf_ip, bf_source, bf_target*1e-3, bf_init*1e-6, bf_step*1e-6, bf_delay, bf_timeout, bf_max*1e-6)
            elif instrument == 'KPAC':
                controller.start_kpac(
                    ls_port, ls_baudrate, ls_channel, ls_setpoint, ls_rate, gpib_address=ls_gpib,
                    control_mode=ls_cmode, heater_range=ls_hrng, p_val=ls_p, i_val=ls_i, d_val=ls_d
                )
            elif instrument == '2120 OG':
                og_port_val = og_port if og_port else DEFAULT_LS370_PORT
                og_baudrate_val = int(og_baudrate) if og_baudrate else 9600
                og_channel_val = int(og_channel) if og_channel else 5
                og_gpib_val = int(og_gpib) if og_gpib else None
                og_target_temp_val = float(og_target_temp) if og_target_temp else 50.0
                og_hrng_val = int(og_hrng) if og_hrng is not None else 5
                og_output_step_val = float(og_output_step) if og_output_step is not None else 1.0
                og_step_delay_val = float(og_step_delay) if og_step_delay is not None else 10.0
                og_max_output_val = float(og_max_output) if og_max_output is not None else 100.0
                og_resistance_val = float(og_resistance) if og_resistance else 120.0
                og_solo_ch_bool = True if (og_solo_channel and 'solo' in og_solo_channel) else False
                og_ramp_mode_val = og_ramp_mode if og_ramp_mode else 'constant_current'
                og_use_curr_bool = True if (og_use_current_output and 'use' in og_use_current_output) else False
                og_ramp_rate_val = float(og_ramp_rate) if og_ramp_rate is not None else 2.0
                og_kp_val = float(og_kp) if og_kp is not None else 5.0
                og_ki_val = float(og_ki) if og_ki is not None else 0.1

                # Resolve starting output: query live MOUT? if 'Use current' is checked
                if og_use_curr_bool:
                    try:
                        from lakeshore_workflow.lakeshore_370_temperature_test import LakeShore370
                        with LakeShore370(port=og_port_val, baudrate=og_baudrate_val,
                                          gpib_address=og_gpib_val) as ls:
                            og_init_output_val = float(ls.query("MOUT?"))
                        print(f"[Start 2120 OG] Read live MOUT: {og_init_output_val:.4g}%")
                    except Exception as e:
                        print(f"[Start 2120 OG] Could not read MOUT?, using field value: {e}")
                        og_init_output_val = float(og_init_output) if og_init_output is not None else 0.0
                else:
                    og_init_output_val = float(og_init_output) if og_init_output is not None else 0.0

                resolved_init_output = round(og_init_output_val, 4)

                controller.start_2120_og(
                    og_port_val, og_baudrate_val, og_channel_val, og_gpib_val,
                    og_target_temp_val, og_hrng_val, og_init_output_val, og_output_step_val,
                    og_step_delay_val, og_max_output_val, og_resistance_val,
                    solo_channel=og_solo_ch_bool,
                    ramp_mode=og_ramp_mode_val,
                    ramp_rate_mk_per_min=og_ramp_rate_val,
                    kp=og_kp_val,
                    ki=og_ki_val,
                )
                
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
            
            # Map parameters based on active instrument
            if instrument == '2120 OG':
                active_bf_ip = None
                active_ls_port = og_port
                active_ls_baud = int(og_baudrate) if og_baudrate else 9600
                active_ls_gpib = int(og_gpib) if og_gpib else None
                active_ls_channel = int(og_channel) if og_channel else 5
                active_bf_source = None
                heater_resistance = float(og_resistance) if og_resistance else 120.0
            elif instrument == 'Myriad/Miniebit':
                active_bf_ip = bf_ip
                active_ls_port = None
                active_ls_baud = 9600
                active_ls_gpib = None
                active_ls_channel = None
                active_bf_source = bf_source
                heater_resistance = 120.0
            else: # KPAC
                active_bf_ip = None
                active_ls_port = ls_port
                active_ls_baud = int(ls_baudrate) if ls_baudrate else 9600
                active_ls_gpib = int(ls_gpib) if ls_gpib else None
                active_ls_channel = int(ls_channel) if ls_channel else 5
                active_bf_source = None
                heater_resistance = 120.0
 
            controller.start_logging(
                instrument, log_dir_val, log_prefix_val, log_interval_val,
                bf_ip=active_bf_ip, bf_source=active_bf_source, 
                ls_port=active_ls_port, ls_baudrate=active_ls_baud, ls_channel=active_ls_channel, ls_gpib=active_ls_gpib,
                lr700_adapter=lr700_adapter, lr700_port=lr700_port, lr700_gpib=lr700_gpib,
                heater_resistance=heater_resistance
            )
            
        elif trigger_id == 'btn-log-stop':
            controller.stop_logging()
 
    status = controller.get_status()
    temp_str = f"{status['current_temp']*1000:.2f} mK" if status['current_temp'] is not None else "-"
    if status['current_power_or_setpoint'] is not None:
        if status['instrument'] in ['Myriad/Miniebit', '2120 OG']:
            power_str = f"{status['current_power_or_setpoint']*1e6:.2f} uW"
        else:
            power_str = f"{status['current_power_or_setpoint']*1000:.2f} mK (Set)"
    else:
        power_str = "-"

    pause_btn_text = "Resume" if status['state'] == "PAUSED" else "Pause"
    log_status_text = controller.log_message

    cmode_status = status.get('current_control_mode') if status.get('current_control_mode') else "-"
    hrng_status = status.get('current_heater_range') if status.get('current_heater_range') else "-"
    pid_status = status.get('current_pid') if status.get('current_pid') else "-"

    # Only push og-init-output update when we resolved a live MOUT value on Start
    init_out_update = resolved_init_output if 'resolved_init_output' in dir() else no_update

    return (status['state'], status['message'], temp_str, power_str, pause_btn_text,
            log_status_text, cmode_status, hrng_status, pid_status, init_out_update)

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
    import argparse
    import sys
    import threading
    import time

    parser = argparse.ArgumentParser(description="Tc Checker Control App")
    parser.add_argument("--port", type=int, default=8052, help="Port to run on (default: 8052)")
    parser.add_argument("--gui", action="store_true", help="Launch in a standalone desktop window")
    args = parser.parse_args()

    if args.gui:
        try:
            import webview
        except ImportError:
            print("Error: pywebview is not installed. Please run:\n  pip install pywebview", file=sys.stderr)
            sys.exit(1)

        # Run Dash server in background thread
        server_thread = threading.Thread(
            target=app.run,
            kwargs={"debug": False, "use_reloader": False, "port": args.port},
            daemon=True
        )
        server_thread.start()

        # Wait a moment for server to spin up
        time.sleep(1.5)

        print("Launching native GUI window...")
        webview.create_window("Tc Checker Control", f"http://127.0.0.1:{args.port}")
        webview.start()
    else:
        app.run(debug=True, use_reloader=False, port=args.port)
