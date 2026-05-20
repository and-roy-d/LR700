import numpy as np
import matplotlib.pyplot as plt
import pathlib
from scipy.signal import savgol_filter  # For robust local trend estimation

plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 14


def estimate_local_trend_savgol(
        data_array,
        window_size=101,  # Size of the rolling window for the filter
        polyorder=2  # Polynomial order for Savitzky-Golay (0 for moving average, 1 for linear, etc.)
):
    """
    Estimates a robust local trend from data using a Savitzky-Golay filter.
    This aims to capture the underlying 'sloping steps' without being pulled
    up by the fixed ~1mOhm shifts.

    Args:
        data_array (np.ndarray): The 1D array of data.
        window_size (int): The size of the rolling window. Must be odd. Should be
                           large enough to span multiple 'shifted' regions if they are short,
                           or at least several data points within a single sloping segment.
        polyorder (int): Polynomial order for Savitzky-Golay. Use 0 for a moving average,
                         1 for local linear fit, 2 for local quadratic fit.
                         Higher orders can capture curvature but are more sensitive to noise.

    Returns:
        np.ndarray: The estimated local trend.
    """
    if window_size % 2 == 0:
        window_size += 1  # Ensure window_size is odd
    if window_size > len(data_array):
        window_size = len(data_array) - 1
        if window_size % 2 == 0:
            window_size -= 1
        print(f"  Adjusted Savgol window_size to {window_size} due to data length.")
    if polyorder >= window_size:  # polyorder must be less than window_size
        polyorder = window_size - 1
        if polyorder < 0: polyorder = 0
        print(f"  Adjusted Savgol polyorder to {polyorder} due to window size.")

    # Apply Savitzky-Golay filter for smoothing (deriv=0)
    # mode='nearest' handles edges by extending the nearest value
    local_trend = savgol_filter(data_array,
                                window_length=window_size,
                                polyorder=polyorder,
                                deriv=0,  # This means we are smoothing, not taking derivative
                                mode='nearest')

    print(f"  Estimated local trend using Savitzky-Golay (window={window_size}, polyorder={polyorder}).")
    return local_trend


def collapse_staircase_fixed_shift(
        data_array,  # This will now receive the 'residuals' (data - local_trend)
        shift_detect_min_diff=0.5,  # Ignore |diffs| smaller than this for shift detection (mOhm)
        shift_detect_max_diff=5.0,  # Ignore |diffs| larger than this for shift detection (mOhm)
        shift_tolerance_factor=0.4,  # Tolerance relative to detected shift (e.g., 0.4 = +/- 40%)
        min_abs_tolerance=0.2  # Minimum absolute tolerance for checks (mOhm)
):
    """
    Detects and collapses a local 'upper staircase' onto a 'lower staircase'
    in a 1D numpy array, assuming a fixed shift value. This function is now
    intended to be used on 'flattened' residuals after local trend removal.

    Args:
        data_array (np.ndarray): The 1D array of data to process (e.g., resistance in mOhms).
                                 Expected to be residuals (original - local_trend).
        shift_detect_min_diff (float): Minimum absolute difference to consider for shift detection.
        shift_detect_max_diff (float): Maximum absolute difference to consider for shift detection.
        shift_tolerance_factor (float): Tolerance for matching the shift (relative to shift size).
        min_abs_tolerance (float): Minimum absolute tolerance for checks (mOhm).


    Returns:
        tuple: (np.ndarray, float or None)
               - collapsed_array: The data array after collapsing.
               - detected_shift: The detected fixed shift magnitude, or None if not detected.
    """
    print(f"  Attempting fixed-shift staircase collapse on array of shape {data_array.shape}...")
    if len(data_array) < 2:
        print("  Warning: Data array too short for collapse analysis.")
        return data_array.copy(), None

    # --- Detect Fixed Shift Amount (Using Absolute Differences) ---
    diffs = np.diff(data_array)
    abs_diffs = np.abs(diffs)

    significant_abs_diffs = abs_diffs[
        (abs_diffs > shift_detect_min_diff) & (abs_diffs < shift_detect_max_diff)
        ]
    print(
        f"  Found {len(significant_abs_diffs)} significant absolute differences in the range ({shift_detect_min_diff:.2f}, {shift_detect_max_diff:.2f}).")

    min_diff_count_for_detection = 5
    if len(significant_abs_diffs) < min_diff_count_for_detection:
        print(
            f"  Could not detect a reliable fixed shift: Only {len(significant_abs_diffs)} significant absolute differences found. Minimum required: {min_diff_count_for_detection}.")
        return data_array.copy(), None

    counts, bin_edges = np.histogram(significant_abs_diffs, bins=20,
                                     range=(shift_detect_min_diff, shift_detect_max_diff))

    min_counts_in_peak_bin = 3
    if np.max(counts) < min_counts_in_peak_bin:
        print(
            f"  Could not detect a reliable fixed shift: No absolute difference value occurred frequently enough (max bin count: {np.max(counts)}, required: {min_counts_in_peak_bin}).")
        return data_array.copy(), None

    most_frequent_bin_index = np.argmax(counts)
    peak_bin_lower_edge = bin_edges[most_frequent_bin_index]
    peak_bin_upper_edge = bin_edges[most_frequent_bin_index + 1]

    diffs_in_peak_bin = significant_abs_diffs[
        (significant_abs_diffs >= peak_bin_lower_edge) &
        (significant_abs_diffs < peak_bin_upper_edge)
        ]

    if len(diffs_in_peak_bin) >= min_counts_in_peak_bin:
        refined_shift = np.mean(diffs_in_peak_bin)
        print(
            f"  Refined shift (mean of {len(diffs_in_peak_bin)} diffs in peak bin [{peak_bin_lower_edge:.3f}, {peak_bin_upper_edge:.3f})): {refined_shift:.4f}")
        detected_shift = refined_shift
    else:
        detected_shift_bin_center = (bin_edges[most_frequent_bin_index] + bin_edges[most_frequent_bin_index + 1]) / 2
        print(
            f"  Warning: Could not calculate reliable mean from peak bin (found {len(diffs_in_peak_bin)} points). Using bin center: {detected_shift_bin_center:.4f}")
        detected_shift = detected_shift_bin_center
    print(f"  Most frequent *absolute* difference bin centered around: {detected_shift:.4f}")

    # --- Collapse Data ---
    collapsed_array = data_array.copy()
    shift = detected_shift
    tolerance_abs = max(min_abs_tolerance, shift * shift_tolerance_factor)
    print(f"  Using absolute tolerance for checks: {tolerance_abs:.4f}")

    points_collapsed = 0
    on_upper_level = False

    for i in range(1, len(data_array)):
        diff_from_prev = data_array[i] - data_array[i - 1]

        if not on_upper_level:
            if abs(diff_from_prev - shift) < tolerance_abs:
                collapsed_array[i] = data_array[i] - shift
                points_collapsed += 1
                on_upper_level = True
        else:
            if abs(diff_from_prev + shift) < tolerance_abs:
                on_upper_level = False
            elif abs(diff_from_prev) < tolerance_abs:
                collapsed_array[i] = data_array[i] - shift
                points_collapsed += 1
            else:
                on_upper_level = False

    print(f"  Collapsed {points_collapsed} points.")
    return collapsed_array, detected_shift


if __name__ == "__main__":
    # --- IMPORTANT: Adjust this `folder_to_process` to your actual data folder ---
    # Try to resolve local 'Data' folder relative to project root or fallback to absolute path
    local_data = pathlib.Path(__file__).resolve().parents[1] / 'Data'
    folder_to_process = local_data
    if not folder_to_process.exists():
        folder_to_process = pathlib.Path('C:\\Users\\trxuser\\OneDrive - UCB-O365\\Code\\RvsT\\myriad_RvsT_20241223\\F2')

    print(f"Attempting to process data from folder: {folder_to_process}")

    # Find the .npy file in the specified folder
    npy_files = list(folder_to_process.glob("*.npy"))

    if not npy_files:
        print(f"Error: No .npy files found in the specified folder: {folder_to_process}")
        exit()

    file_to_load = npy_files[0]  # Take the first .npy file found
    print(f"Processing file: {file_to_load}")

    # The device name can be derived from the folder name
    device = folder_to_process.name

    # Load the data
    try:
        loaded_array = np.load(file_to_load, allow_pickle=True)

        # --- CRITICAL FIX: Convert structured NumPy array to a Python dictionary ---
        data = {}
        # Check if it's a structured array (has named fields)
        if loaded_array.dtype.names:
            for field_name in loaded_array.dtype.names:
                data[field_name] = loaded_array[field_name]
        else:
            # If it's a simple array (e.g., just one column), handle differently
            # This part assumes your file *is* a structured array, but good for robustness.
            # If it were a plain array, you'd need to know what each column means.
            # For this problem, we assume it's structured.
            print(f"Warning: Loaded .npy file {file_to_load} is not a structured array.")
            print("Attempting to treat it as a dictionary if it's a pickled object array.")
            data = loaded_array.item()  # Fallback, might raise ValueError if not a single item
            if not isinstance(data, dict):
                print("Error: Loaded data is not a structured array nor a pickled dictionary.")
                exit()

    except Exception as e:
        print(f"Error loading or converting {file_to_load}: {e}")
        exit()

    # Check if 'r_ohm' and other expected keys exist in the loaded data (now a dictionary)
    if not all(key in data for key in ['r_ohm', 't_K', 'times_s']):
        print(f"Error: Expected keys ('r_ohm', 't_K', 'times_s') not found in {file_to_load}.")
        print(f"Available keys: {list(data.keys())}")
        exit()

    R_original = data['r_ohm']
    T = data['t_K']
    times_s = data['times_s']
    R_original_mOhm = R_original * 1000

    # --- Step 1: Robust Local Trend Estimation ---
    local_trend_mOhm = estimate_local_trend_savgol(
        R_original_mOhm,
        window_size=101,  # Tune this for your data
        polyorder=2  # Tune this for your data
    )

    # --- Step 2: Calculate Residuals ---
    R_residuals_mOhm = R_original_mOhm - local_trend_mOhm

    # --- Step 3: Apply Fixed Shift Collapse to the RESIDUALS ---
    R_collapsed_residuals_mOhm, shift_detected = collapse_staircase_fixed_shift(
        R_residuals_mOhm,
        shift_detect_min_diff=0.5,
        shift_detect_max_diff=4.0,
        shift_tolerance_factor=0.35,
        min_abs_tolerance=0.15
    )

    # --- Step 4: Add the Estimated Local Trend Back ---
    R_corrected_mOhm = R_collapsed_residuals_mOhm + local_trend_mOhm

    # Convert to Ohms for saving
    R_corrected_Ohm = R_corrected_mOhm / 1000.0

    # --- Save the corrected data as a new column in the ORIGINAL file ---
    # Now 'data' is a Python dictionary, so this assignment will work.
    data['r_ohm_corrected'] = R_corrected_Ohm

    try:
        np.save(file_to_load, data, allow_pickle=True)  # Save the modified dictionary back to original path
        print(f"Successfully added 'r_ohm_corrected' column to: {file_to_load}")
    except Exception as e:
        print(f"Error adding 'r_ohm_corrected' column to {file_to_load}: {e}")

    # Plotting to visualize the effect
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
    ax1.plot(times_s, R_corrected_Ohm * 1000, "g.", label='Corrected')
    ax1.plot(times_s, R_original_mOhm, "k.", label='Raw', alpha=0.2)
    ax1.plot(times_s, local_trend_mOhm, "b--", label='Estimated Local Trend', alpha=0.7)
    ax2.plot(times_s, T * 1000, "k.")
    ax1.set_xlabel("time_s")
    ax1.set_ylabel(r"R (m$\Omega$)")
    ax2.set_ylabel("Scepter temp (mK)")
    ax1.grid(which='major', ls='--', alpha=0.5)
    ax2.grid(which='major', ls='--', alpha=0.5)
    ax1.legend()
    fig.suptitle(f'Device: {device} ({file_to_load.name}) - Hybrid Correction')

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.plot(T * 1000, R_corrected_Ohm * 1000, "g.", label='Corrected')
    ax.plot(T * 1000, R_original_mOhm, "k.", alpha=0.1, label='Raw')
    ax.plot(T * 1000, local_trend_mOhm, "b--", label='Estimated Local Trend', alpha=0.7)
    ax.set_xlabel("MXC temp (mK)")
    ax.set_ylabel(r"R (m$\Omega$)")
    ax.set_title(f'Device: {device} (R vs T) - Hybrid Correction')
    ax.legend()
    ax.grid(which='major', ls='--', alpha=0.5)
    plt.tight_layout()
    plt.show()