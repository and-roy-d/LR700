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
Once you have collected your `.npy` data logs under the `Data/` directory, use the automated post-processing utilities in the `analysis/` folder to filter data, collapse staircase steps, extract critical temperatures ($T_c$), and generate publication-quality figures:

#### A. Standard Cooldown Processing & Plotting
To batch-process standard measurements across all devices:
```bash
python analysis/lrplot_processandsaveall.py
```
This automatically corrects staircase noise using the shift-collapsing algorithm, extracts the superconducting transitions, and saves separate CSV summaries and SVG/PNG plots.

#### B. Field-Dependent $T_c$ Analysis (New)
To analyze superconducting transitions under different applied magnetic fields, use the field-dependent script:
```bash
python analysis/field_dependent_tc.py                 # Defaults to today's date
python analysis/field_dependent_tc.py --date 20260522 # Run on an editable target date
```
* **Current & Field Parsing**: Extracts the applied magnet current from file labels like `Coil0A1` (denoting `0.1` A, where `A` is the decimal point).
* **Calibration**: Computes the external magnetic field $B_{ext}$ using a slope of **$87\text{ }\mu\text{T/A}$** ($B_{ext} = I \times 87$).
* **Device Grouping**: Automatically clusters measurement sweeps by device (e.g. `F1`).
* **Multi-Field Plotting**: Renders all curves for the same device on a single high-performance chart using a high-contrast sequential color scheme (Royal Blue -> Teal -> Amber -> Crimson Red) as field increases, styled cleanly with thin lines connected to light, semi-transparent dots (`o-` style) with no distracting vertical lines or text labels to ensure premium visual excellence.
* **Transition Alignment**: Automatically aligns detected transition temperatures for multi-layer devices (like `F1` and `A2`) to their closest nominal transition (83 mK, 110 mK, or 161 mK) to robustly track transitions across different fields while filtering out spurious noise.
* **Three-Subpanel Summary Exports**: Produces a summary spreadsheet (`tc_vs_bext_summary.csv`) and a beautiful, high-precision summary plot (`Tc_vs_Bext_summary.png`) featuring **3 vertically stacked subpanels** (one for each transition) sharing the $B_{ext}$ X-axis so that each transition's field suppression is individually zoomed and clearly resolved.
