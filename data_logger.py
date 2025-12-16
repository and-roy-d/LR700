import bftc
import lr700
import datetime
import os
import time
import numpy as np
from npy_append_array import NpyAppendArray
import ramp_heater

def log_data(temp_source, filename, logging_interval_s=1, stop_event=None,
             target_temp=None, direction=None):
    print(f"Starting data logging every {logging_interval_s} second(s) from {temp_source}... Press Ctrl+C to stop.")
    try:
        npaa = NpyAppendArray(filename)
    except Exception as e:
        print(f"Error initializing NpyAppendArray: {e}")
        return

    while True:
        if stop_event is not None and stop_event.is_set():
            print("Stop event detected. Stopping data logging and closing file.")
            npaa.close()
            break

        try:
            r = lr700.read_ohm()

            if temp_source == 'scepter':
                bftc.turn_off_all_but_scepter()
                t = bftc.read_scepter_temperature()
            elif temp_source == 'mxc':
                bftc.turn_off_all_but_mxc()
                t = bftc.read_mxc_temperature()
            else:
                print(f"Invalid temperature source '{temp_source}'. Skipping cycle.")
                time.sleep(logging_interval_s)
                continue

            current_time = time.time()
            power_uW = ramp_heater.get_latest_heater_power_uW()

            if r is None:
                print("Warning: Failed LR700 read. Skipping this data point.")
                time.sleep(logging_interval_s)
                continue

            if t is None:
                print(f"Warning: Failed BFTC {temp_source} temperature read. Skipping this data point.")
                time.sleep(logging_interval_s)
                continue

            entry = np.array([(r, t, power_uW, current_time)],
                             dtype=[('r_ohm', 'f8'), ('t_K', 'f8'), ('p_uW', 'f8'), ('time_s', 'f8')])

            print(f"R: {r*1000:.2f} mΩ, T: {t*1000:.2f} mK, P: {power_uW:.2f} µW, Time: {current_time:.2f}s")
            npaa.append(entry)

            # Check for temperature target reached
            if target_temp is not None and direction in ('up', 'down'):
                if direction == 'up' and t >= target_temp:
                    print(f"Target temperature {target_temp:.3f} K reached. Stopping logging.")
                    if stop_event:
                        stop_event.set()
                    npaa.close()
                    break
                elif direction == 'down' and t <= target_temp:
                    print(f"Target temperature {target_temp:.3f} K reached. Stopping logging.")
                    if stop_event:
                        stop_event.set()
                    npaa.close()
                    break

            time.sleep(logging_interval_s)

        except KeyboardInterrupt:
            print("\nCtrl+C detected. Stopping data logging and closing file.")
            npaa.close()
            break

        except Exception as e:
            print(f"An error occurred during logging: {e}")
            time.sleep(logging_interval_s * 2)


def main(save_dir, device_name, temp_source_choice, logging_interval_s=1,
         stop_event=None, target_temp=None, direction=None):

    if not save_dir:
        save_dir = os.path.join("Data", datetime.datetime.now().strftime("%Y%m%d"))
    if not os.path.exists(save_dir):
        print(f"Creating directory: {save_dir}")
        os.makedirs(save_dir)

    if temp_source_choice not in ['scepter', 'mxc']:
        temp_source_choice = 'scepter'

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_filename = "lr700log"
    if device_name:
        filename = os.path.join(save_dir, f"{base_filename}_{device_name}_{temp_source_choice}_{timestamp}.npy")
    else:
        filename = os.path.join(save_dir, f"{base_filename}_{temp_source_choice}_{timestamp}.npy")

    print(f"Data will be saved to: {filename}")

    try:
        log_data(temp_source_choice, filename, logging_interval_s,
                 stop_event, target_temp, direction)
    except Exception as e:
        print(f"Data logger stopped due to error: {e}")
