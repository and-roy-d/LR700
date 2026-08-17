from __future__ import annotations

from pathlib import Path
import datetime
import os
import sys
import time
import numpy as np
from npy_append_array import NpyAppendArray

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bftc import BFTC
from prologix_lr700_test import (
    DEFAULT_AUTO_MODE as DEFAULT_LR700_AUTO,
    DEFAULT_GPIB_ADDRESS as DEFAULT_LR700_GPIB_ADDRESS,
    DEFAULT_PORT as DEFAULT_LR700_PORT,
    PrologixLR700,
)

DEFAULT_LOGGING_INTERVAL_S = 1.0
ENTRY_DTYPE = [
    ("r_ohm", "f8"),
    ("x_ohm", "f8"),
    ("t_K", "f8"),
    ("p_uW", "f8"),
    ("time_s", "f8"),
]


def log_data(
    temp_source,
    filename: str,
    logging_interval_s: float = DEFAULT_LOGGING_INTERVAL_S,
    stop_event=None,
    target_temp: float | None = None,
    direction: str | None = None,
    lr700_adapter: str = 'prologix',
    lr700_port: str = DEFAULT_LR700_PORT,
    lr700_gpib: int = DEFAULT_LR700_GPIB_ADDRESS,
    lr700_auto: int = DEFAULT_LR700_AUTO,
    bf_ip: str | None = None,
    **kwargs,
) -> None:
    print(
        f"Starting BFTC/LR700 logging every {logging_interval_s}s "
        f"(Channel {temp_source}, LR700 Adapter: {lr700_adapter})..."
    )

    try:
        npaa = NpyAppendArray(filename)
    except Exception as exc:
        print(f"Error initializing NpyAppendArray: {exc}")
        return

    import lr700 as pyvisa_lr700

    if lr700_adapter != 'prologix':
        try:
            pyvisa_lr700.init_gpib(lr700_gpib)
        except Exception as exc:
            print(f"Warning: Failed to initialize pyvisa_lr700: {exc}")

    try:
        bf = BFTC(bf_ip, timeout=5.0) if bf_ip else BFTC(timeout=5.0)
    except Exception as exc:
        print(f"Warning: Failed to initialize BFTC client: {exc}")
        bf = None

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                print("Stop event detected. Stopping data logging and closing file.")
                break

            r = np.nan
            x = np.nan
            t = np.nan
            power_uW = np.nan
            current_time = time.time()

            try:
                # 1. Read LR700 via short-lived connection
                if lr700_adapter == 'prologix':
                    try:
                        with PrologixLR700(
                            port=lr700_port,
                            gpib_address=lr700_gpib,
                            auto=lr700_auto,
                        ) as lr:
                            r = lr.read_r().value_ohms
                            x = lr.read_x().value_ohms
                    except Exception as exc:
                        print(f"Warning: Failed to read LR700: {exc}")
                else:
                    try:
                        r_val = pyvisa_lr700.read_ohm(lr700_gpib)
                        r = r_val if r_val is not None else np.nan
                        x = np.nan
                    except Exception as exc:
                        print(f"Warning: Failed to read LR700 via PyVISA: {exc}")

                # 2. Read temperature from BFTC channel
                try:
                    ch = int(temp_source)
                except (ValueError, TypeError):
                    ch = 6

                if bf is not None:
                    try:
                        t_val = bf.get_temperature(ch)
                        t = t_val if t_val is not None else np.nan
                    except Exception as exc:
                        print(f"Warning: Failed to read BFTC CH{ch} temperature: {exc}")

                    # Read heater power: prioritize active ramp setpoint if available
                    try:
                        from ramp_controller import controller
                        if controller.state in ("RAMPING", "PAUSED") and controller.instrument == "Myriad/Miniebit" and controller.current_power_or_setpoint is not None:
                            power_uW = float(controller.current_power_or_setpoint) * 1e6
                        else:
                            p_val = bf.get_latest_heater_power_uW(heater_nr=4, minutes=5.0)
                            power_uW = p_val if p_val is not None else 0.0
                    except Exception as exc:
                        print(f"Warning: Failed to read BFTC heater power: {exc}")
                        power_uW = 0.0

                entry = np.array(
                    [(r, x, t, power_uW, current_time)],
                    dtype=ENTRY_DTYPE,
                )

                print(
                    f"R: {r * 1000:.2f} mOhm, "
                    f"X: {x * 1000:.2f} mOhm, "
                    f"T: {t * 1000:.2f} mK, "
                    f"P: {power_uW:.2f} uW, "
                    f"Time: {current_time:.2f}s"
                )
                npaa.append(entry)

                if target_temp is not None and direction in ('up', 'down') and not np.isnan(t):
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
            except Exception as exc:
                print(f"An error occurred during logging: {exc}")
                time.sleep(logging_interval_s * 2)
    finally:
        npaa.close()


def main(
    save_dir,
    device_name,
    temp_source_choice,
    logging_interval_s=DEFAULT_LOGGING_INTERVAL_S,
    stop_event=None,
    target_temp=None,
    direction=None,
    lr700_adapter='prologix',
    lr700_port=DEFAULT_LR700_PORT,
    lr700_gpib=DEFAULT_LR700_GPIB_ADDRESS,
    bf_ip=None,
    **kwargs,
):
    if not save_dir:
        save_dir = ROOT_DIR / "Data" / datetime.datetime.now().strftime("%Y%m%d")
    else:
        save_dir = Path(save_dir)
        if not save_dir.is_absolute():
            save_dir = ROOT_DIR / save_dir

    if not os.path.exists(save_dir):
        print(f"Creating directory: {save_dir}")
        os.makedirs(save_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_filename = "lr700log"
    ch_str = f"CH{temp_source_choice}"
    if device_name:
        filename = save_dir / f"{base_filename}_{device_name}_{ch_str}_{timestamp}.npy"
    else:
        filename = save_dir / f"{base_filename}_{ch_str}_{timestamp}.npy"

    print(f"Data will be saved to: {filename}")

    try:
        log_data(
            temp_source=temp_source_choice,
            filename=str(filename),
            logging_interval_s=logging_interval_s,
            stop_event=stop_event,
            target_temp=target_temp,
            direction=direction,
            lr700_adapter=lr700_adapter,
            lr700_port=lr700_port,
            lr700_gpib=lr700_gpib,
            bf_ip=bf_ip,
            **kwargs,
        )
    except Exception as e:
        print(f"Data logger stopped due to error: {e}")

