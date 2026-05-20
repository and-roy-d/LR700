from pathlib import Path
import sys

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_LR700_PORT = "COM14" if sys.platform.startswith("win") else "/dev/ttyUSB0"

import bftc
import csv
import datetime
import os
import time
import numpy as np
from npy_append_array import NpyAppendArray
import ramp_heater

def log_data(temp_source, filename, logging_interval_s=1, stop_event=None,
             target_temp=None, direction=None, lr700_adapter='prologix', lr700_port=DEFAULT_LR700_PORT, lr700_gpib=17):
    print(f"Starting data logging every {logging_interval_s} second(s) from {temp_source}... Press Ctrl+C to stop.")

    try:
        npaa = NpyAppendArray(filename)
    except Exception as e:
        print(f"Error initializing NpyAppendArray: {e}")
        return

    # Open a parallel CSV file with the same stem
    csv_path = Path(filename).with_suffix(".csv")
    csv_file = open(csv_path, "a", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    if csv_path.stat().st_size == 0:
        csv_writer.writerow(["time_s", "t_K", "r_ohm", "p_uW"])

    from contextlib import ExitStack
    import lr700 as pyvisa_lr700

    try:
        with ExitStack() as stack:
            prologix_bridge = None
            if lr700_adapter == 'prologix':
                import sys
                from pathlib import Path
                lakeshore_path = str(Path(__file__).resolve().parents[1] / "lakeshore_workflow")
                if lakeshore_path not in sys.path:
                    sys.path.insert(0, lakeshore_path)
                from prologix_lr700_test import PrologixLR700
                prologix_bridge = stack.enter_context(PrologixLR700(port=lr700_port, gpib_address=lr700_gpib))
            else:
                pyvisa_lr700.init_gpib(lr700_gpib)

            while True:
                if stop_event is not None and stop_event.is_set():
                    print("Stop event detected. Stopping data logging and closing file.")
                    break

                try:
                    if lr700_adapter == 'prologix':
                        r = prologix_bridge.read_r().value_ohms
                    else:
                        r = pyvisa_lr700.read_ohm(lr700_gpib)

                    if temp_source == 'scepter':
                        bftc.turn_off_all_but_scepter()
                        t = bftc.read_scepter_temperature()
                    elif temp_source == 'mxc':
                        bftc.turn_off_all_but_mxc()
                        t = bftc.read_mxc_temperature()
                    else:
                        try:
                            ch = int(temp_source)
                            history = bftc.get_temp_history(ch=ch, start_minutes_ago=2)
                            t_list = history.get("measurements", {}).get("temperature", [])
                            t = t_list[-1] if t_list else None
                        except:
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

                    print(f"R: {r*1000:.2f} mOhm, T: {t*1000:.2f} mK, P: {power_uW:.2f} uW, Time: {current_time:.2f}s")
                    npaa.append(entry)
                    csv_writer.writerow([current_time, t, r, power_uW])
                    csv_file.flush()

                    if target_temp is not None and direction in ('up', 'down'):
                        if direction == 'up' and t >= target_temp:
                            print(f"Target temperature {target_temp:.3f} K reached. Stopping logging.")
                            if stop_event:
                                stop_event.set()
                            break
                        elif direction == 'down' and t <= target_temp:
                            print(f"Target temperature {target_temp:.3f} K reached. Stopping logging.")
                            if stop_event:
                                stop_event.set()
                            break

                    time.sleep(logging_interval_s)

                except KeyboardInterrupt:
                    print("\nCtrl+C detected. Stopping data logging and closing file.")
                    break
                except Exception as e:
                    print(f"An error occurred during logging: {e}")
                    time.sleep(logging_interval_s * 2)
    finally:
        npaa.close()
        csv_file.close()


def main(save_dir, device_name, temp_source_choice, logging_interval_s=1,
         stop_event=None, target_temp=None, direction=None,
         lr700_adapter='prologix', lr700_port=DEFAULT_LR700_PORT, lr700_gpib=17):

    if not save_dir:
        save_dir = ROOT_DIR / "Data" / datetime.datetime.now().strftime("%Y%m%d")
    else:
        save_dir = Path(save_dir)
    if not os.path.exists(save_dir):
        print(f"Creating directory: {save_dir}")
        os.makedirs(save_dir)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_filename = "lr700log"
    if device_name:
        filename = save_dir / f"{base_filename}_{device_name}_{temp_source_choice}_{timestamp}.npy"
    else:
        filename = save_dir / f"{base_filename}_{temp_source_choice}_{timestamp}.npy"

    print(f"Data will be saved to: {filename}")

    try:
        log_data(temp_source_choice, str(filename), logging_interval_s,
                 stop_event, target_temp, direction,
                 lr700_adapter, lr700_port, lr700_gpib)
    except Exception as e:
        print(f"Data logger stopped due to error: {e}")
