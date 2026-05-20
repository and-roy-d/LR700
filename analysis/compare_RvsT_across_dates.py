import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pathlib

# --- Configuration ---
# Try to resolve 'Data' folder relative to project root or fallback to absolute path
data_parent_folder = pathlib.Path(__file__).resolve().parents[1] / 'Data'
if not data_parent_folder.exists():
    data_parent_folder = pathlib.Path('C:\\Users\\trxuser\\Desktop\\Python\\Instruments\\btfc\\Data\\')
date_folders = ['20250808', '20250822']

device_label_map = {
    'A1': 'A1: Au1 top: 7, 8, 9, bilayer', 'A2': 'A2: Tracer 20A 0,2 0fil z073',
    'B1': 'B1: Tracer20A 0,0 0fil z073', 'B2': 'B2: Au1 middle: 7, 8, 9 bilayer',
    'C1': 'C1: Au1 inner: 7, 8, 9, bilayer', 'C2': 'C2: Au1 outer: 7, 8, 9, bilayer',
    'D1': 'D1: Au1 outer: 7, 8, 9, bilayer', 'D2': 'D2: Au1 inner: 7, 8, 9, bilayer',
    'E1': 'E1: Au1 middle: 7, 8, 9 bilayer', 'E2': 'E2: Tracer 20A 0,3 0fil z073',
    'F1': 'F1: Tracer 20A 0,1 0fil z073', 'F2': 'F2: Au1 top: 7, 8, 9, bilayer',
}

colors = ['tab:blue', 'tab:orange']

# --- Plotting ---
def plot_comparison(data_parent_folder, date_folders, device_label_map, colors):
    device_names = list(device_label_map.keys())
    num_devices = len(device_names)
    cols = 4
    rows = int(np.ceil(num_devices / cols))

    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 4), dpi=300)
    axes = axes.flatten()

    for i, device in enumerate(device_names):
        ax = axes[i]

        for date, color in zip(date_folders, colors):
            device_folder = data_parent_folder / date / device
            if not device_folder.exists():
                continue

            for csv_file in device_folder.glob('*.csv'):
                try:
                    df = pd.read_csv(csv_file)
                    T = df['t_K'].values * 1000  # Convert to mK
                    R = df['r_ohm'].values * 1000 # Convert to mOhm
                    ax.plot(T, R, '.', markersize=4, alpha=0.7, label=f"{date}", color=color)
                except Exception as e:
                    print(f"Failed to read {csv_file.name}: {e}")
                    continue

        ax.set_title(device_label_map.get(device, device), fontsize=12)
        ax.set_xlabel('T (mK)', fontsize=10)
        ax.set_ylabel('R (mΩ)', fontsize=10)
        ax.grid(alpha=0.4)
        ax.tick_params(axis='both', labelsize=9)
        ax.legend(fontsize=8)

    # Hide any unused axes
    for j in range(num_devices, len(axes)):
        axes[j].axis('off')
    blurb = "Aug 08: Control wafer, Aug 22: Side 1 (Left) removed ~ 45 nm Au, Side 2 (Right) added ~ 45 nm Au"
    fig.suptitle("R vs T Comparison Over Cooldowns \n " + blurb, fontsize=18)
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    results_folder = data_parent_folder / 'Results'
    results_folder.mkdir(exist_ok=True)
    output_file = results_folder / '_Comparison_R_vs_T_Overlay.png'
    fig.savefig(output_file)
    print(f"Saved comparison plot to: {output_file}")
    # plt.show()

# --- Run ---
if __name__ == "__main__":
    plot_comparison(data_parent_folder, date_folders, device_label_map, colors)
