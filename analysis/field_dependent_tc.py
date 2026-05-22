#!/usr/bin/env python3
"""
Field-Dependent Tc Analysis Script
===================================
Scans a specified date folder (defaulting to today) for LR700 .npy data files,
groups them by device (e.g. F1), parses the applied field coil current (e.g. Coil0A1 -> 0.1 A),
calculates the external magnetic field Bext (using 87 uT/A slope), finds their transition
temperatures (Tcs), and generates publication-quality field-dependent plots.

Usage:
    python analysis/field_dependent_tc.py                 # run for today's date
    python analysis/field_dependent_tc.py --date 20260522 # run for a specific date
"""

import argparse
import pathlib
import re
import sys
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, find_peaks

# Configure premium plot aesthetics
plt.rcParams['font.sans-serif'] = ['Arial', 'Helvetica', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 12
plt.rcParams['figure.titlesize'] = 14
plt.rcParams['axes.labelsize'] = 12
plt.rcParams['xtick.labelsize'] = 10
plt.rcParams['ytick.labelsize'] = 10

SLOPE_UT_PER_A = 87.0

filament_widths = [20, 30, 40]
# Standard device mapping for labels
DEVICE_LABEL_MAP =  {
        'A1': f'A1: AA28 Au1 top: {filament_widths}',
        'B1': 'B1: Tracer20A 20 um', 
        'C1': f'C1: AA28 Au1 center: {filament_widths}', 
        'D1': f'D1: AA28 Au1 outer: {filament_widths}', 
        'E1': f'E1: AA28 Au1 middle: {filament_widths}', 
        'F1': 'F1: Tracer 23 40 um: Au1, Au2, Au3', 
        'A2': 'A2: Tracer 23 40 um: Au1, Au2, Au3',
        'B2': f'B2: AA28 Au2 middle: {filament_widths}',
        'C2': f'C2: AA28 Au2 outer: {filament_widths}',
        'D2': f'D2: AA28 Au2 center: {filament_widths}',
        'E2': 'RuOx',
        'F2': f'F2: AA28 Au2 top: {filament_widths}',
    }


# ---------------------------------------------------------------------------
# Transition Finder & Signal Processing Utilities
# ---------------------------------------------------------------------------

def estimate_local_trend_savgol(data, window=101, poly=2):
    if len(data) < 5:
        return data
    if window % 2 == 0:
        window += 1
    window = min(window, len(data) - 1)
    poly = min(poly, window - 1)
    return savgol_filter(data, window, poly, mode='nearest')


def collapse_staircase_fixed_shift(data):
    if len(data) < 10:
        return data
    diffs = np.diff(data)
    diffs = diffs[np.abs(diffs) > 0.3]
    if len(diffs) < 5:
        return data
    shift = np.median(np.abs(diffs))
    if not (0.3 < shift < 5):
        return data

    out = data.copy()
    level = 0.0
    for i in range(1, len(data)):
        if abs(data[i] - data[i - 1] - shift) < 0.3:
            level += shift
        elif abs(data[i] - data[i - 1] + shift) < 0.3:
            level -= shift
        out[i] -= level
    return out


def find_transition_mK(T_K, R_mOhm, prominence=0.8, min_step_mOhm=10.0, max_width_mK=50.0) -> float | None:
    """Finds the primary transition critical temperature (Tc) in mK."""
    if len(T_K) < 20:
        return None

    # Sort Data by Temperature
    idx = np.argsort(T_K)
    T = T_K[idx]
    R = R_mOhm[idx]

    # Smooth R
    window = min(15, len(R) - 2)
    if window % 2 == 0:
        window += 1
    R_smooth = savgol_filter(R, window_length=window, polyorder=3)

    # Calculate derivative with respect to Index to ignore noise density
    dR_dn = np.gradient(R_smooth)

    # Find peaks in gradient
    peaks, properties = find_peaks(dR_dn, prominence=prominence, height=0, width=1, distance=15)

    edge_buffer = len(R) * 0.05
    valid_transitions = []

    for i, p in enumerate(peaks):
        if p < edge_buffer or p > (len(R) - edge_buffer):
            continue

        w_low = int(properties['left_ips'][i])
        w_high = int(properties['right_ips'][i])

        bottom = max(0, w_low - 5)
        top = min(len(R) - 1, w_high + 5)

        width_mK = abs(T[top] - T[bottom]) * 1000.0
        R_sc = np.median(R[max(0, bottom - 10):bottom + 1])
        R_n = np.median(R[top:min(len(R), top + 11)])
        step = R_n - R_sc

        if step >= min_step_mOhm and width_mK <= max_width_mK:
            valid_transitions.append((p, T[p] * 1000.0, step))

    if not valid_transitions:
        return None

    # Return the transition with the largest resistance step (most prominent)
    valid_transitions.sort(key=lambda x: x[2], reverse=True)
    return valid_transitions[0][1]


# ---------------------------------------------------------------------------
# Filename Parsing
# ---------------------------------------------------------------------------

def parse_filename(filepath: pathlib.Path) -> tuple[str, float, str] | None:
    """
    Parses a filepath to extract:
      - Device ID (e.g. 'F1')
      - Applied Current in Amperes (e.g. 'Coil0A1' -> 0.1 A)
      - Ramp direction ('Up' or 'Down')
    """
    name = filepath.name

    # 1. Parse Device ID (must be [A-F][1-2])
    device_match = re.search(r'([A-F][1-2])', name)
    if not device_match:
        return None
    device = device_match.group(1)

    # 2. Parse Current (look for CoilXXX)
    current_match = re.search(r'Coil(\d+A\d+|\d+)', name, re.IGNORECASE)
    if current_match:
        curr_str = current_match.group(1).upper()
        if 'A' in curr_str:
            current = float(curr_str.replace('A', '.'))
        else:
            current = float(curr_str)
    else:
        # Default to 0 if Coil is not found in filename
        current = 0.0

    # 3. Determine ramp direction by reading file content
    try:
        data = np.load(filepath, allow_pickle=True)
        t_raw = data['t_K']
        if len(t_raw) > 1 and t_raw[-1] > t_raw[0]:
            direction = 'Up'
        else:
            direction = 'Down'
    except Exception as e:
        print(f"Warning: Could not read {filepath} to determine ramp direction: {e}")
        direction = 'Unknown'

    return device, current, direction


# ---------------------------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------------------------

def analyze_field_dependence(date_str: str, data_root: pathlib.Path):
    date_folder = data_root / date_str
    if not date_folder.exists():
        print(f"Error: Data folder '{date_folder}' does not exist.")
        sys.exit(1)

    print(f"\n==============================================")
    print(f"Scanning files in: {date_folder}")
    print(f"Date: {date_str}")
    print(f"==============================================")

    # Find all .npy files recursively
    npy_files = list(date_folder.glob('**/*.npy'))
    if not npy_files:
        print("No .npy data files found.")
        return

    # Parse and group files by device
    device_groups: dict[str, list[dict]] = {}

    for f in npy_files:
        parsed = parse_filename(f)
        if not parsed:
            continue
        device, current, direction = parsed
        bext = current * SLOPE_UT_PER_A

        if device not in device_groups:
            device_groups[device] = []

        device_groups[device].append({
            'file': f,
            'current': current,
            'bext': bext,
            'direction': direction
        })

    # Filter out devices that don't have multiple field values
    devices_to_plot = {}
    for dev, items in device_groups.items():
        unique_fields = set(item['bext'] for item in items)
        if len(unique_fields) >= 1: # We want to plot even single fields if they request it, but highlight multiple fields
            devices_to_plot[dev] = sorted(items, key=lambda x: x['bext'])

    if not devices_to_plot:
        print("No devices with valid measurement files found.")
        return

    # Create output directory for plots
    output_dir = date_folder / "field_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving analysis plots to: {output_dir}\n")

    summary_data = []

    # Process each device
    for dev, runs in sorted(devices_to_plot.items()):
        print(f"Device: {dev} ({len(runs)} runs)")
        
        fig, ax = plt.subplots(figsize=(7, 5), dpi=300)
        
        # Color spectrum for fields
        unique_bexts = sorted(list(set(run['bext'] for run in runs)))
        cmap = plt.cm.plasma
        
        # Let's map unique bexts to colors
        def get_color(bext_val):
            if len(unique_bexts) <= 1:
                return "tab:blue"
            normalized = (bext_val - unique_bexts[0]) / (unique_bexts[-1] - unique_bexts[0] if unique_bexts[-1] != unique_bexts[0] else 1)
            # Use middle range of plasma map to ensure readability
            return cmap(0.1 + normalized * 0.7)

        for run in runs:
            filepath = run['file']
            bext = run['bext']
            direction = run['direction']
            
            # Load and average duplicate temp points
            try:
                data = np.load(filepath, allow_pickle=True)
                df = pd.DataFrame({
                    'T_K': data['t_K'],
                    'R_mOhm': data['r_ohm'] * 1000.0
                }).groupby('T_K', as_index=False).mean().dropna()
                
                T_mK = df['T_K'].values * 1000.0
                R_mOhm = df['R_mOhm'].values
                
                # Apply staircase correction
                R_corrected = collapse_staircase_fixed_shift(R_mOhm)
                
                # Find Tc
                tc_mK = find_transition_mK(df['T_K'].values, R_corrected)
                
                # Plot raw data
                color = get_color(bext)
                label = f"$B_{{ext}}$ = {bext:.1f} µT ({direction})"
                ax.plot(T_mK, R_corrected, '.', ms=4, color=color, alpha=0.6, label=label)
                
                if tc_mK:
                    print(f"  -> Bext = {bext:.1f} uT: Found Tc = {tc_mK:.2f} mK")
                    ax.axvline(tc_mK, color=color, ls='--', alpha=0.5)
                    # Text offset slightly so they don't overlay
                    ax.text(tc_mK + 0.3, ax.get_ylim()[0] + (ax.get_ylim()[1] - ax.get_ylim()[0])*0.05, 
                            f"{tc_mK:.1f} mK", rotation=90, fontsize=9, color=color, fontweight='bold')
                    
                    summary_data.append({
                        'Device': dev,
                        'Current_A': run['current'],
                        'Bext_uT': bext,
                        'Direction': direction,
                        'Tc_mK': tc_mK
                    })
                else:
                    print(f"  -> Bext = {bext:.1f} uT: No transition found")
                    
            except Exception as e:
                print(f"  Error processing {filepath.name}: {e}")

        # Set labels and premium styling
        dev_title = DEVICE_LABEL_MAP.get(dev, f"Device {dev}")
        ax.set_title(f"{dev_title}\nTcs for various applied external fields", pad=12)
        ax.set_xlabel("Temperature (mK)")
        ax.set_ylabel("Resistance (mΩ)")
        ax.grid(True, linestyle=':', alpha=0.6)
        
        # Legend styling
        ax.legend(loc='best', framealpha=0.9, edgecolor='#e2e8f0', fontsize=10)
        
        plt.tight_layout()
        plot_path = output_dir / f"{dev}_field_dependent.png"
        fig.savefig(plot_path, dpi=300)
        plt.close(fig)
        print(f"  Saved plot to: {plot_path.name}")

    # Generate overall Tc vs Bext summary curve for devices with multiple transitions
    if summary_data:
        df_sum = pd.DataFrame(summary_data)
        
        # Save CSV summary table
        csv_path = output_dir / "tc_vs_bext_summary.csv"
        df_sum.to_csv(csv_path, index=False)
        print(f"\nSaved CSV summary table to: {csv_path.name}")
        
        # Create Tc vs Bext summary plot
        fig, ax = plt.subplots(figsize=(8, 5.5), dpi=300)
        
        plotted_any = False
        for dev in df_sum['Device'].unique():
            df_dev = df_sum[df_sum['Device'] == dev].sort_values('Bext_uT')
            # Only plot if we have at least 2 points to make a line
            if len(df_dev) >= 1:
                plotted_any = True
                dev_label = DEVICE_LABEL_MAP.get(dev, f"Device {dev}").split(":")[0]
                ax.plot(df_dev['Bext_uT'], df_dev['Tc_mK'], 'o-', ms=6, lw=2, label=dev_label)
                
                # Print neat console summary
                print(f"\n{dev_label} Summary:")
                for _, r in df_dev.iterrows():
                    print(f"  Bext = {r['Bext_uT']:5.1f} uT  |  Tc = {r['Tc_mK']:6.2f} mK")

        if plotted_any:
            ax.set_title("Superconducting Tc vs Applied External Magnetic Field", pad=12, fontweight='bold')
            ax.set_xlabel("Applied External Field $B_{ext}$ (µT)")
            ax.set_ylabel("Critical Temperature $T_c$ (mK)")
            ax.grid(True, linestyle=':', alpha=0.6)
            ax.legend(loc='best', framealpha=0.9, edgecolor='#e2e8f0')
            
            plt.tight_layout()
            summary_plot_path = output_dir / "Tc_vs_Bext_summary.png"
            fig.savefig(summary_plot_path, dpi=300)
            plt.close(fig)
            print(f"\nSaved overall summary plot to: {summary_plot_path.name}")

    print("\nAnalysis completed successfully!")


# ---------------------------------------------------------------------------
# Argument Parser and Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze and plot field-dependent Tc measurements from .npy logs.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                        help="Date folder to analyze (format: YYYYMMDD, default: today)")
    parser.add_argument("--data-dir", default="Data",
                        help="Path to the parent Data directory (default: 'Data')")
    args = parser.parse_args()

    data_path = pathlib.Path(args.data_dir)
    analyze_field_dependence(args.date, data_path)
