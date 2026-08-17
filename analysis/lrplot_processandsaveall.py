import numpy as np
import matplotlib.pyplot as plt
import pathlib
import csv
from scipy.signal import savgol_filter, find_peaks
from scipy.ndimage import gaussian_filter1d
import pandas as pd
import os, shutil, re, time

plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 14

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


def find_all_transitions_hybrid(T_K, R_mOhm, *, prominence=1.0, min_step_mOhm=5.0, max_width_mK=50):
    """
    Robust transition finder using index-based gradient to avoid dT noise.
    """
    if len(T_K) < 20:
        return []

    # 1. Sort Data
    idx = np.argsort(T_K)
    T = T_K[idx]
    R = R_mOhm[idx]

    # 2. Smooth R to kill "grass" noise
    window = min(15, len(R) - 2)
    if window % 2 == 0: window += 1
    R_smooth = savgol_filter(R, window_length=window, polyorder=3)

    # 3. Calculate Derivative w.r.t INDEX
    dR_dn = np.gradient(R_smooth)

    # 4. Find Peaks in the Derivative
    peaks, properties = find_peaks(dR_dn,
                                   prominence=prominence,
                                   height=0,
                                   width=1,
                                   distance=15)

    transitions = []
    T_mK = T * 1000

    edge_buffer = len(R) * 0.05

    for i, p in enumerate(peaks):
        # A. Filter: Ignore edges
        if p < edge_buffer or p > (len(R) - edge_buffer):
            continue

        w_low = int(properties['left_ips'][i])
        w_high = int(properties['right_ips'][i])

        bottom = max(0, w_low - 5)
        top = min(len(R) - 1, w_high + 5)

        R_sc = np.median(R[max(0, bottom - 10):bottom + 1])
        R_n = np.median(R[top:min(len(R), top + 11)])

        step = R_n - R_sc
        width_mK = abs(T_mK[top] - T_mK[bottom])

        if step < min_step_mOhm:
            continue
        if width_mK > max_width_mK:
            continue

        transitions.append({
            'Tc_mK': T_mK[p],
            'R_mOhm': R[p]
        })

    return transitions


def organize_files_by_device(parent, device_labels, wait=0.2):
    parent = pathlib.Path(parent)
    pattern = re.compile(r'_(' + '|'.join(device_labels.keys()) + r')_')

    for f in parent.glob('*.npy'):
        m = pattern.search(f.name)
        if not m: continue
        d = parent / m.group(1)
        d.mkdir(exist_ok=True)
        try:
            # Use copy2 instead of move so active live logging files are not interrupted
            shutil.copy2(str(f), str(d / f.name))
        except Exception:
            pass


def process_all_data(parent_folder, *, device_labels, apply_correction=True,
                     analyze_transition=True, save_plots=True,
                     prominence=30, min_step_mOhm=15, max_width_mK=30):
    parent = pathlib.Path(parent_folder)
    organize_files_by_device(parent, device_labels)

    summary_rows = []

    # 1. Define Colors
    ramp_colors = {'Down': 'tab:blue', 'Up': 'tab:red'}

    for device in device_labels:
        dev_dir = parent / device
        npy_files = list(dev_dir.glob('*.npy')) if dev_dir.is_dir() else []
        if not npy_files:
            npy_files = [f for f in parent.glob(f'*_{device}_*.npy') if f.is_file()]

        for npy in npy_files:
            print(f'Processing {npy.name} for {device}')
            data = np.load(npy, allow_pickle=True)
            t_raw = data['t_K']
            # If end temp > start temp, it's an UP ramp
            if len(t_raw) > 1 and t_raw[-1] > t_raw[0]:
                direction = 'Up'
            else:
                direction = 'Down'

            # Export raw data as CSV alongside the .npy file
            csv_path = npy.with_suffix('.csv')
            has_x = 'x_ohm' in data.dtype.names
            with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                writer = csv.writer(cf)
                if has_x:
                    writer.writerow(['time_s', 't_K', 'r_ohm', 'x_ohm', 'p_uW'])
                    for i in range(len(data)):
                        writer.writerow([
                            data['time_s'][i], data['t_K'][i],
                            data['r_ohm'][i], data['x_ohm'][i], data['p_uW'][i]
                        ])
                else:
                    writer.writerow(['time_s', 't_K', 'r_ohm', 'p_uW'])
                    for i in range(len(data)):
                        writer.writerow([
                            data['time_s'][i], data['t_K'][i],
                            data['r_ohm'][i], data['p_uW'][i]
                        ])
            print(f'  CSV saved: {csv_path.name}')

            # Group and sort for analysis
            df = pd.DataFrame({
                'T_K': data['t_K'],
                'R_mOhm': data['r_ohm'] * 1000
            }).groupby('T_K', as_index=False).mean().dropna()

            T_K = df['T_K'].values
            R = df['R_mOhm'].values

            if apply_correction:
                trend = estimate_local_trend_savgol(R)
                R = collapse_staircase_fixed_shift(R - trend) + trend

            transitions = []
            if analyze_transition:
                transitions = find_all_transitions_hybrid(
                    T_K, R,
                    prominence=prominence,
                    min_step_mOhm=min_step_mOhm,
                    max_width_mK=max_width_mK
                )
                if transitions:
                    print(f'  Found {len(transitions)} transitions ({direction})')
                else:
                    print(f'  No transitions found ({direction})')

            # 3. Store data (Including 'direction')
            base_data = {
                'device': device,
                'T_curve_K': T_K,
                'R_curve_mOhm': R,
                'direction': direction  # <--- CRITICAL FIX: Ensure this key exists
            }

            if transitions:
                for tr in transitions:
                    row = base_data.copy()
                    row.update({'Tc_mK': tr['Tc_mK'], 'R_mOhm': tr['R_mOhm']})
                    summary_rows.append(row)
            else:
                row = base_data.copy()
                row.update({'Tc_mK': np.nan, 'R_mOhm': np.nan})
                summary_rows.append(row)

            # 4. Per-file plot
            if save_plots:
                fig, ax = plt.subplots(figsize=(6, 4))
                # Use the calculated color
                c = ramp_colors.get(direction, 'black')
                ax.plot(T_K * 1000, R, '.', ms=4, color=c, label=direction)

                for tr in transitions:
                    ax.axvline(tr['Tc_mK'], color='r', ls='--', alpha=0.6)
                    ax.text(tr['Tc_mK'], tr['R_mOhm'],
                            f"{tr['Tc_mK']:.1f} mK",
                            fontsize=12, ha='right', va='bottom', rotation=90, color='red')

                ax.set_xlabel('T (mK)')
                ax.set_ylabel('R (mΩ)')
                ax.set_title(f"{device_labels[device]}\n({direction} Ramp)")
                ax.legend()
                ax.grid(alpha=0.4)
                plt.tight_layout()
                fig.savefig(npy.with_suffix('.png'), dpi=300)
                plt.close(fig)

    if not summary_rows:
        print("No data files found at all.")
        return

    df_sum = pd.DataFrame(summary_rows)
    devices = list(device_labels.keys())

    n = len(devices)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))

    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 4.5), dpi=300)

    try:
        fig.suptitle(f"Transition Summary - {date}", fontsize=24, y=0.99)
    except NameError:
        fig.suptitle(f"Transition Summary", fontsize=16, y=0.99)

    axes = np.atleast_1d(axes).flatten()

    for ax, dev in zip(axes, devices):
        ax.set_title(device_labels[dev], fontsize=16)
        ax.set_xlabel('T (mK)')
        ax.set_ylabel('R (mΩ)')
        ax.grid(alpha=0.4)

        seen_labels = set()

        if dev in df_sum['device'].values:
            sub = df_sum[df_sum['device'] == dev]
            for _, row in sub.iterrows():

                # 5. Retrieve Direction safely
                direction = row['direction']
                color = ramp_colors.get(direction, 'gray')

                label = direction if direction not in seen_labels else "_nolegend_"
                if direction not in seen_labels:
                    seen_labels.add(direction)

                ax.plot(row['T_curve_K'] * 1000,
                        row['R_curve_mOhm'],
                        '.', ms=3, alpha=0.5,
                        color=color,
                        label=label)

                if not np.isnan(row['Tc_mK']):
                    ax.axvline(row['Tc_mK'], color='r', ls='--', alpha=0.4)
                    ax.text(row['Tc_mK'], row['R_mOhm'],
                            f"{row['Tc_mK']:.1f}",
                            fontsize=12, color=color, ha='right', va='bottom', rotation=0)

            if seen_labels:
                ax.legend(loc='best', fontsize=12)

        else:
            ax.text(0.5, 0.5, "No Data", transform=ax.transAxes, ha='center', color='gray')

    for ax in axes[len(devices):]:
        ax.axis('off')

    plt.tight_layout()
    plt.subplots_adjust(top=0.92)
    fig.savefig(parent / f'{date}_Summary_R_vs_T.png', dpi=300)
    print('Summary plot saved')


if __name__ == "__main__":
    from datetime import datetime

    date = "20260817"
    ruox_installed = True
    date_obj = datetime.strptime(date, "%Y%m%d")
    ruox_change_date = datetime.strptime("20250821", "%Y%m%d")

    # Use platform-independent paths relative to project root
    data_parent_folder = pathlib.Path(__file__).resolve().parents[1] / 'Data' / date
    if not data_parent_folder.exists():
        data_parent_folder = pathlib.Path('Data') / date
    filament_widths = " 30, 40, 50"

    device_label_map_myriad_nominal = {
        'A1': f'A1: Au1 top: {filament_widths}', 'A2': 'A2: Tracer 20A 0,2 0fil z073',
        'B1': 'B1: Tracer20A 0,0 0fil z073', 'B2': f'B2: Au1 middle: {filament_widths}',
        'C1': f'C1: Au1 inner: {filament_widths}', 'C2': f'C2: Au1 outer: {filament_widths}',
        'D1': f'D1: Au1 outer: {filament_widths}', 'D2': f'D2: Au1 inner: {filament_widths}',
        'E1': f'E1: Au1 middle: {filament_widths}', 'E2': 'E2: Tracer 20A 0,3 0fil z073',
        'F1': 'F1: Tracer 20A 0,1 0fil z073', 'F2': f'F2: Au1 top: {filament_widths}',
    }
    device_label_map_myriad_20260520 = {
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

    device_label_map_myriad_20260817 = {
        # Board A
        'A1': 'A1: AA36 Top Au1: 20,30,40 um',
        'B1': 'B1: Tracer 23 30 um: Au1, Au2, Au3',
        'C1': 'C1: AA36 Center Au1: 20,30,40 um',
        'D1': 'D1: AA36 Outer Au1: 20,30,40 um',
        'E1': 'E1: AA36 Middle Au1: 20,30,40 um',
        'F1': 'F1: AA36 Bottom Au1 SGP: 20,30,40 um',

        # Board B
        'A2': 'A2: AA36 Bottom AuRCC SGP: 20,30,40 um',
        'B2': 'B2: AA36 Middle AuRCC: 20,30,40 um',
        'C2': 'C2: AA36 Outer AuRCC: 20,30,40 um',
        'D2': 'D2: AA36 Center AuRCC: 20,30,40 um',
        'E2': 'E2: RuOx',
        'F2': 'F2: AA36 Top AuRCC: 20,30,40 um',
    }

    device_label_map_myriad_reversed = {
        'A2': f'A1: Au1 top: {filament_widths}', 'A1': 'A2: Tracer 20A 0,2 0fil z073',
        'B2': 'B1: Tracer20A 0,0 0fil z073', 'B1': f'B2: Au1 middle: {filament_widths}',
        'C2': f'C1: Au1 inner: {filament_widths}', 'C1': f'C2: Au1 outer: {filament_widths}',
        'D2': f'D1: Au1 outer: {filament_widths}', 'D1': f'D2: Au1 inner: {filament_widths}',
        'E2': f'E1: Au1 middle: {filament_widths}', 'E1': 'E2: Tracer 20A 0,3 0fil z073',
        'F2': 'F1: Tracer 20A 0,1 0fil z073', 'F1': f'F2: Au1 top: {filament_widths}',
    }

    device_label_map_kpac_nominal = {
        'A1': f'AA24: Au1 top: {filament_widths}', 'A2': 'A2: Tracer 20A 0,2 0fil z073',
        'B1': 'B1: Tracer20A 0,0 0fil z073', 'B2': f'B2: Au1 middle: {filament_widths}',
        'C1': f'C1: AA24 center: {filament_widths}', 'C2': f'C2: Au1 outer: {filament_widths}',
        'D1': f'D1: AA24 outer: {filament_widths}', 'D2': f'D2: Au1 inner: {filament_widths}',
        'E1': f'E1: AA25 0,0: Au1 (20 um), Au1Au2: {filament_widths}', 'E2': 'E2: Tracer 20A 0,3 0fil z073',
        'F1': f'F1: AA25 0,3: Au1 (20 um), Au1Au2: {filament_widths}', 'F2': f'F2: Au1 top: {filament_widths}',
    }

    device_label_map = device_label_map_myriad_20260817

    if ruox_installed and (date_obj > ruox_change_date) and ('E2' in device_label_map and device_label_map['E2'] == 'RuOx'):
        device_label_map['E2'] = 'E2: RuOx U09874'

    print("--- Running with Final Hybrid Transition Analysis ---")
    process_all_data(data_parent_folder,
                     save_plots=True,
                     apply_correction=False,
                     analyze_transition=True,
                     min_step_mOhm=10,  # Lowered to catch small steps
                     max_width_mK=50,  # Increased to catch softer steps
                     prominence=0.8,  # Threshold for dR/d(Index)
                     device_labels=device_label_map)
    # plt.show()