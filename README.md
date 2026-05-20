# LR700 Instrument and Temperature Controller Workflows

A fully reorganized, cross-platform (Windows & Linux compatible) Python framework for cryostat heater ramping, Lake Shore 370 temperature logging, and automated R vs T transition plotting.

---

## 🚀 Key Features
- **Unified Launch Control**: Start everything with a single command via the interactive Graphical User Interface (`control_app.py`).
- **Three Instrument Profiles**: Dedicated settings cards for Myriad/Miniebit (BFTC API), KPAC (closed-loop PID), and 2120 OG (open-loop LS370).
- **Three 2120 OG Ramp Modes**: Constant Current (default), Linear Power Steps (constant dP/dt), and Software PI Control (linear temperature trajectory).
- **Cross-Platform Compatibility**: Automatically detects OS (Windows vs Linux) to choose default serial COM ports (`COM14` / `COM6` vs `/dev/ttyUSB0` / `/dev/ttyUSB1`).
- **Thread-Safe Port Sharing**: Ramping and logging threads share serial ports via short-lived on-demand connections coordinated by `port_locker.py`.
- **Consolidated Driver**: Unified, lazy-loading PyVISA `lr700.py` driver supporting dual-channel Resistance (R) and Reactance (X) measurements.
- **Robust Staircase Collapsing**: Advanced signal processing utilities to correct staircase noise in resistance data.

---

## 📁 Repository Directory Structure

```text
.
├── bftc_workflow/            # Bluefors/BFTC workflow modules
│   ├── bftc.py               # BFTC HTTP client API wrapper
│   ├── data_logger.py        # Logging thread routine (BFTC temperature + LR700)
│   ├── ramp_and_log.py       # Linear temperature ramping logic
│   └── ramp_heater.py        # Heater power coordinator
├── lakeshore_workflow/       # Lake Shore 370 temperature logger modules
│   ├── data_logger.py        # LS370 logging thread routine (supports open-loop power logging)
│   └── lakeshore_370_temperature_test.py # Lake Shore 370 serial driver
├── analysis/                 # Data post-processing and transition plotting
│   ├── compare_RvsT_across_dates.py      # Compare R vs T plots across cooldown runs
│   ├── lrplot.py             # Single-file staircase collapsing plotter
│   ├── lrplot_panel.py       # Batch staircase collapsing utility
│   └── lrplot_processandsaveall.py # Advanced transition finder & batch plotter
├── control_app.py            # Main interactive Dash Control Panel (RUN THIS!)
├── ramp_controller.py        # Thread-safe background orchestrator for active workflows
├── port_locker.py            # Per-port threading locks for serial port sharing
├── lr700.py                  # Consolidated PyVISA driver for LR700 (R & X)
├── prologix_lr700_test.py    # Prologix Serial-to-GPIB driver for LR700
├── pyproject.toml            # Project configurations and package dependencies
└── README.md                 # Documentation
```

---

## 🛠️ Workflows

### 1. Main Interactive Control GUI (Launch)
To launch the application, run:
```bash
python control_app.py
```
This is the single graphical portal for all three cryostat setups. It allows configuring physical connections, testing hardware states, entering ramping limits, and tracking live resistance and temperature measurements with a high-performance Plotly scatter graph.

*Automatically sets default COM ports on Windows and `/dev/ttyUSB` on Linux.*

---

### 2. Instrument Profiles

#### Myriad/Miniebit
Controls a dilution refrigerator via the **Bluefors BFTC HTTP API**. Steps heater power in µW increments until the mixing chamber temperature setpoint is reached. Supports solo channel selection on the BFTC scanner.

#### KPAC
Closed-loop **PID temperature control** using a Lake Shore 370. Sets a hardware ramp rate (K/min) and a target setpoint; the LS370 hardware ramp brings the system to temperature.

#### 2120 OG
Open-loop heater control via a **Lake Shore 370** (manual output percentage `MOUT`). Reads mixing chamber temperature on a configurable channel. Three ramping strategies are available:

| Ramp Mode | Description |
|-----------|-------------|
| **Constant Current Ramp** *(default)* | Steps `MOUT %` by a fixed amount each interval. Current increases linearly; power increases quadratically. |
| **Linear Power Steps** | Steps physical heater power linearly (constant dP/dt). Converts target power back to `MOUT` via `MOUT = 100 × √(P / P_full)` to compensate for the quadratic current–power relationship. |
| **Software PI Control** | Tracks a user-defined linear temperature trajectory (mK/min) using a software proportional-integral feedback loop. Adjusts `MOUT` every step interval based on the error between actual and target temperature. |

**Additional 2120 OG features:**
- **Solo Channel**: Disables all scanner channels except the active measurement channel to maximise scanner speed.
- **Use Current Output**: When checked, clicking **Start** queries the live `MOUT?` value from the instrument and uses it as the starting output %, automatically populating the *Initial Output (%)* field so every subsequent ramp continues from the exact instrument state.
- **Output Hold on Target**: When the setpoint is reached, the ramp exits cleanly leaving `HTRRNG` and `MOUT` physically held. Clicking **Stop** explicitly resets both to zero.

---

### 3. Post-Ramp Data Processing & Analysis
Once you have collected your `.npy` data logs under the `Data/` directory, use the automated signal-processing utilities in `analysis/` to process the files, collapse staircase noise, find transition temperatures ($T_c$), and export publication-ready plots:
```bash
python analysis/lrplot_processandsaveall.py
```
*(The scripts inside `analysis/` automatically search the project root's `Data/` folder by default, regardless of what folder you launch them from.)*
