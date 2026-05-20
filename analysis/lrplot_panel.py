import os
import numpy as np
import matplotlib.pyplot as plt

def collapse_staircase_fixed_shift(
    data_array,
    shift_detect_min_diff=0.5, # Ignore |diffs| smaller than this for shift detection (mOhm)
    shift_detect_max_diff=5.0, # Ignore |diffs| larger than this for shift detection (mOhm)
    shift_tolerance_factor=0.4, # Tolerance relative to detected shift (e.g., 0.4 = +/- 40%)
    min_abs_tolerance=0.2      # Minimum absolute tolerance for checks (mOhm)
    ):
    """
    Detects and collapses a local 'upper staircase' onto a 'lower staircase'
    in a 1D numpy array, assuming a fixed shift value. Uses absolute differences
    for potentially more robust shift detection. Handles segments where multiple
    adjacent points are on the upper level.

    Args:
        data_array (np.ndarray): The 1D array of data to process (e.g., resistance in mOhms).
        shift_detect_min_diff (float): Minimum absolute difference to consider for shift detection.
        shift_detect_max_diff (float): Maximum absolute difference to consider for shift detection.
        shift_tolerance_factor (float): Tolerance for matching the shift (relative to shift size).
        min_abs_tolerance (float): Minimum absolute tolerance for matching the shift.


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
    abs_diffs = np.abs(diffs) # <<< CHANGE: Work with absolute differences

    # Filter based on the magnitude of the difference
    significant_abs_diffs = abs_diffs[
        (abs_diffs > shift_detect_min_diff) & (abs_diffs < shift_detect_max_diff)
    ]
    print(f"  Found {len(significant_abs_diffs)} significant absolute differences in the range ({shift_detect_min_diff:.2f}, {shift_detect_max_diff:.2f}).")


    min_diff_count_for_detection = 5 # Consider maybe increasing this slightly if using abs diffs doubles the potential count
    if len(significant_abs_diffs) < min_diff_count_for_detection:
        print(f"  Could not detect a reliable fixed shift: Only {len(significant_abs_diffs)} significant absolute differences found. Minimum required: {min_diff_count_for_detection}.")
        return data_array.copy(), None

    # Histogramming the *absolute* differences
    # Consider adjusting bins/range if needed, but usually the same range works
    counts, bin_edges = np.histogram(significant_abs_diffs, bins=20, range=(shift_detect_min_diff, shift_detect_max_diff))
    print(f"  DEBUG: Histogram counts (abs diffs): {counts}")
    print(f"  DEBUG: Histogram bin_edges (abs diffs): {bin_edges}")

    min_counts_in_peak_bin = 3 # Or maybe adjust based on total significant diffs found
    if np.max(counts) < min_counts_in_peak_bin:
         print(f"  Could not detect a reliable fixed shift: No absolute difference value occurred frequently enough (max bin count: {np.max(counts)}, required: {min_counts_in_peak_bin}).")
         return data_array.copy(), None

    most_frequent_bin_index = np.argmax(counts)
    # detected_shift is the magnitude of the jump
    detected_shift_bin_center = (bin_edges[most_frequent_bin_index] + bin_edges[most_frequent_bin_index + 1]) / 2
    peak_bin_lower_edge = bin_edges[most_frequent_bin_index]
    peak_bin_upper_edge = bin_edges[most_frequent_bin_index + 1]

    # Find the actual difference values that fall into this specific bin
    diffs_in_peak_bin = significant_abs_diffs[
        (significant_abs_diffs >= peak_bin_lower_edge) &
        (significant_abs_diffs < peak_bin_upper_edge)  # Use < for upper edge, matching np.histogram behavior
        ]

    if len(diffs_in_peak_bin) >= min_counts_in_peak_bin:  # Check if enough points are in the bin
        refined_shift = np.mean(diffs_in_peak_bin)
        print(
            f"  Refined shift (mean of {len(diffs_in_peak_bin)} diffs in peak bin [{peak_bin_lower_edge:.3f}, {peak_bin_upper_edge:.3f})): {refined_shift:.4f}")
        detected_shift = refined_shift  # Use the mean as the detected shift
    else:
        # Fallback to bin center if too few points or calculation fails
        print(
            f"  Warning: Could not calculate reliable mean from peak bin (found {len(diffs_in_peak_bin)} points). Using bin center: {detected_shift_bin_center:.4f}")
        detected_shift = detected_shift_bin_center
    print(f"  Most frequent *absolute* difference bin centered around: {detected_shift:.4f}")

    # --- Collapse Data (Logic remains the same) ---
    # The collapsing logic already handles positive shifts (diff ~ +shift)
    # and negative shifts (diff ~ -shift) relative to the detected shift magnitude.
    collapsed_array = data_array.copy()
    shift = detected_shift # Use the detected shift magnitude (positive value)
    tolerance_abs = max(min_abs_tolerance, shift * shift_tolerance_factor)
    print(f"  Using absolute tolerance for checks: {tolerance_abs:.4f}")

    points_collapsed = 0
    on_upper_level = False

    for i in range(1, len(data_array)):
        diff_from_prev = data_array[i] - data_array[i-1]
        action_taken = "None" # For potential debugging

        if on_upper_level:
            # Still on the upper level? (difference is small)
            if abs(diff_from_prev) < tolerance_abs:
                collapsed_array[i] = data_array[i] - shift # Collapse current point
                points_collapsed += 1
                action_taken = f"Stayed Upper -> Collapsed to {collapsed_array[i]:.4f}"
            # Jumped down? (difference is close to -shift)
            elif abs(diff_from_prev + shift) < tolerance_abs:
                on_upper_level = False # Now on lower level
                # DO NOT collapse data[i] here, it's already on the lower level
                action_taken = "Jumped Down -> State=False"
            # Otherwise, something else happened (noise?), assume we're off the upper level
            else:
                 on_upper_level = False
                 action_taken = "Exited Upper (Other) -> State=False"
        # Currently on lower level, did we jump up? (difference is close to +shift)
        elif abs(diff_from_prev - shift) < tolerance_abs:
             on_upper_level = True # Now on upper level
             collapsed_array[i] = data_array[i] - shift # Collapse current point
             points_collapsed += 1
             action_taken = f"Jumped Up -> State=True, Collapsed to {collapsed_array[i]:.4f}"

        # Optional: Print debug info for transitions or specific points
        # if action_taken != "None":
        #      print(f"  i={i}, R_orig={data_array[i]:.4f}, diff={diff_from_prev:.4f} -> Action: {action_taken}, New State: on_upper={on_upper_level}")


    print(f"  Collapsed {points_collapsed} points.")
    return collapsed_array, detected_shift


def analyze_npy_files(parent_folder):
    """
    Loads .npy files from subfolders, performs fixed-shift staircase collapse,
    saves the corrected data to the .npy file, and saves an individual plot
    for each subfolder.
    """
    npy_files = {}
    subfolders = [f for f in os.listdir(parent_folder) if os.path.isdir(os.path.join(parent_folder, f))]
    subfolders = [f for f in subfolders if f not in ['B1', 'F1']] # Example filter

    print(f"Found subfolders: {subfolders}")

    for folder_name in subfolders:
        folder_path = os.path.join(parent_folder, folder_name)
        npy_files[folder_name] = []
        print(f"\nProcessing folder: {folder_name}")
        for file_name in os.listdir(folder_path):
            if file_name.endswith(".npy"):
                file_path = os.path.join(folder_path, file_name)
                print(f"- Found file: {file_path}")
                npy_files[folder_name].append(file_path)

    if not npy_files:
        print("No valid subfolders with .npy files found.")
        return

    # --- Loop through each folder to process data and create a plot ---
    for folder_name, file_paths in npy_files.items():
        if not file_paths:
            print(f"Skipping folder {folder_name} - no .npy files found.")
            continue

        # --- Create a new figure for this folder ---
        fig, ax = plt.subplots(1, 1, figsize=(7, 5)) # Single plot per folder
        print(f"  Generating plot for {folder_name}...")

        plot_has_data = False # Flag to check if any data was successfully plotted

        for file_path in file_paths:
            try:
                print(f"    Loading and processing: {os.path.basename(file_path)}")
                data = np.load(file_path)
                # print(f"      Original data shape: {data.shape}, dtype: {data.dtype}") # Debug print

                if 'r_ohm' not in data.dtype.names or 't_K' not in data.dtype.names:
                    print(f"      Warning: Skipping {file_path} - missing 'r_ohm' or 't_K'.")
                    continue
                if data.ndim == 0:
                     print(f"      Warning: Skipping {file_path} - data loaded as 0-dimensional. Reshaping.")
                     try:
                         data = data.reshape((1,))
                         # print(f"      Reshaped data shape: {data.shape}")
                     except Exception as reshape_e:
                         print(f"      Error reshaping 0-dim array: {reshape_e}. Skipping file.")
                         continue
                if data.shape[0] == 0:
                     print(f"      Warning: Skipping {file_path} - data array is empty.")
                     continue


                R_original = data['r_ohm']
                T = data['t_K']
                R_original_mOhm = R_original * 1000

                # Apply the FIXED SHIFT staircase collapse function
                R_collapsed_mOhm, shift_detected = collapse_staircase_fixed_shift(
                    R_original_mOhm,
                    shift_detect_min_diff=0.7,
                    shift_detect_max_diff=3.0,
                    shift_tolerance_factor=0.35,
                    min_abs_tolerance=0.15
                )

                # Convert collapsed data back to Ohms for saving
                R_corrected_Ohm = R_collapsed_mOhm / 1000.0
                # print(f"      Shape of R_corrected_Ohm: {R_corrected_Ohm.shape}") # Debug print

                # --- Save the corrected data back to the file (Robust Method) ---
                try:
                    original_field_names = list(data.dtype.names)
                    new_field_names = []
                    new_data_columns = []
                    new_dtypes = []

                    for name in original_field_names:
                        if name != 'r_ohm_corrected':
                            new_field_names.append(name)
                            new_data_columns.append(data[name])
                            new_dtypes.append((name, data.dtype[name]))

                    new_field_names.append('r_ohm_corrected')
                    new_data_columns.append(R_corrected_Ohm)
                    new_dtypes.append(('r_ohm_corrected', R_corrected_Ohm.dtype))

                    if not new_data_columns:
                         print(f"     Error: No data columns found to save for {file_path}")
                         continue

                    expected_length = len(R_corrected_Ohm)
                    valid_columns = True
                    for i, col in enumerate(new_data_columns):
                         # Check if the column itself is empty or has incorrect length
                        current_col_len = len(col) if hasattr(col, '__len__') else 0
                        if current_col_len != expected_length:
                             print(f"     Error: Column '{new_field_names[i]}' length mismatch. Expected {expected_length}, found {current_col_len}. Skipping save for {file_path}.")
                             valid_columns = False
                             break
                    if not valid_columns:
                        continue

                    records_list = list(zip(*new_data_columns))
                    data_to_save = np.array(records_list, dtype=new_dtypes)
                    np.save(file_path, data_to_save)

                    if 'r_ohm_corrected' not in original_field_names:
                         print(f"      Saved {os.path.basename(file_path)} with new 'r_ohm_corrected' column.")
                    else:
                         print(f"      Updated data and saved to {os.path.basename(file_path)} (replaced 'r_ohm_corrected').")

                except Exception as save_e:
                    print(f"      Error saving updated data to {file_path}: {save_e}")
                    # print(f"      Debug Info: data.shape={data.shape}, R_corrected_Ohm.shape={R_corrected_Ohm.shape}")
                # --- End saving ---


                # --- Plotting (on the dedicated axis 'ax' for this folder) ---
                # Use file name in label if multiple files per folder exist
                file_label_suffix = f" ({os.path.basename(file_path).split('.')[0]})" if len(file_paths) > 1 else ""

                ax.plot(T*1000, R_original_mOhm, '.', ms=2.5, alpha=0.6, label=f'Orig{file_label_suffix}')
                plot_has_data = True # Mark that we plotted something

                if shift_detected is not None and np.any(R_original_mOhm != R_collapsed_mOhm):
                     ax.plot(T*1000, R_collapsed_mOhm, 'x', ms=3.0, alpha=0.8, color='red', label=f'Coll. (Shift={shift_detected:.2f}){file_label_suffix}')
                elif shift_detected is not None:
                     print("      Shift detected, but no points met collapse criteria.")
                     # Optionally plot original again slightly differently if needed
                     # ax.plot(T*1000, R_original_mOhm, 'o', ms=2.0, alpha=0.7, label=f'Orig (No Change){file_label_suffix}')


            except Exception as e:
                print(f"    Error processing {file_path}: {e}")
                import traceback
                traceback.print_exc() # Print full traceback for debugging


        if plot_has_data:
            ax.set_xlabel("T (mK)")
            ax.set_ylabel(r"R (m$\Omega$)")
            # ax.set(xlim=(20, 160), ylim=(100,120)) # Adjust limits as needed
            ax.set_title(f"R vs T for {folder_name}")

            handles, labels = ax.get_legend_handles_labels()

            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys(), fontsize='small', loc='best')
            ax.grid(which='both', axis='both', alpha=0.5, ls ='--')

            # Define output filename and path
            plot_filename = f"{folder_name}RvsT_corrected.png"
            # Save in the main parent folder (one level up from the specific device folder)
            output_plot_path = os.path.join(parent_folder, plot_filename)

            try:
                fig.savefig(output_plot_path, dpi=300, bbox_inches='tight')
                print(f"  Plot saved to: {output_plot_path}")
            except Exception as plot_save_e:
                print(f"  Error saving plot {output_plot_path}: {plot_save_e}")
        else:
             print(f"  No data plotted for folder {folder_name}, skipping plot saving.")


        # --- Close the figure to free memory ---
        # plt.close(fig)

    print("\nProcessing complete.")



if __name__ == "__main__":

    # Try to resolve local 'Data' folder relative to project root or fallback to absolute path
    local_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Data"))
    if os.path.isdir(local_data):
        base_data_folder = local_data
    else:
        base_data_folder = "C:\\Users\\trxuser\\Desktop\\Data"
    experiment_folder = "RvsT_20250505"
    parent_folder = os.path.join(base_data_folder, experiment_folder)


    if not os.path.isdir(parent_folder):
        print(f"Error: Parent folder not found at '{parent_folder}'")
        print("Please update the 'base_data_folder' and 'experiment_folder' variables.")
    else:
        print(f"Analyzing data in: {parent_folder}")
        analyze_npy_files(parent_folder)
    plt.show()

