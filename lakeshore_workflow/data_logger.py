from __future__ import annotations

from pathlib import Path
import datetime
import os
import sys
import time

import numpy as np
from npy_append_array import NpyAppendArray

ROOT_DIR = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prologix_lr700_test import (  # noqa: E402
    DEFAULT_AUTO_MODE as DEFAULT_LR700_AUTO,
    DEFAULT_GPIB_ADDRESS as DEFAULT_LR700_GPIB_ADDRESS,
    DEFAULT_PORT as DEFAULT_LR700_PORT,
    PrologixLR700,
)
from lakeshore_370_temperature_test import (  # noqa: E402
    DEFAULT_BAUDRATE as DEFAULT_LS370_BAUDRATE,
    DEFAULT_CHANNEL as DEFAULT_LS370_CHANNEL,
    DEFAULT_PORT as DEFAULT_LS370_PORT,
    LakeShore370,
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
    filename: str,
    logging_interval_s: float = DEFAULT_LOGGING_INTERVAL_S,
    stop_event=None,
    ls370_port: str = DEFAULT_LS370_PORT,
    ls370_channel: int = DEFAULT_LS370_CHANNEL,
    ls370_baudrate: int = DEFAULT_LS370_BAUDRATE,
    ls370_gpib_address: int | None = None,
    lr700_adapter: str = 'prologix',
    lr700_port: str = DEFAULT_LR700_PORT,
    lr700_gpib_address: int = DEFAULT_LR700_GPIB_ADDRESS,
    lr700_auto: int = DEFAULT_LR700_AUTO,
    log_bf_power: bool = False,
    bf_ip: str | None = None,
    log_ls_open_loop_power: bool = False,
    heater_resistance: float = 120.0,
) -> None:
    print(
        "Starting Lake Shore/LR700 logging "
        f"(LS370 {ls370_port} ch{ls370_channel}, LR700 Adapter: {lr700_adapter})..."
    )

    try:
        npaa = NpyAppendArray(filename)
    except Exception as exc:
        print(f"Error initializing NpyAppendArray: {exc}")
        return

    import lr700 as pyvisa_lr700

    if lr700_adapter != 'prologix':
        try:
            pyvisa_lr700.init_gpib(lr700_gpib_address)
        except Exception as exc:
            print(f"Warning: Failed to initialize pyvisa_lr700: {exc}")

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
                # 1. Read LR700
                if lr700_adapter == 'prologix':
                    try:
                        with PrologixLR700(
                            port=lr700_port,
                            gpib_address=lr700_gpib_address,
                            auto=lr700_auto,
                        ) as lr:
                            r = lr.read_r().value_ohms
                            x = lr.read_x().value_ohms
                    except Exception as exc:
                        print(f"Warning: Failed to read LR700: {exc}")
                else:
                    try:
                        r_val = pyvisa_lr700.read_ohm(lr700_gpib_address)
                        r = r_val if r_val is not None else np.nan
                        x = np.nan
                    except Exception as exc:
                        print(f"Warning: Failed to read LR700 via PyVISA: {exc}")

                # 2. Read Lake Shore 370 and calculate power
                try:
                    with LakeShore370(
                        port=ls370_port,
                        baudrate=ls370_baudrate,
                        gpib_address=ls370_gpib_address,
                    ) as ls:
                        t = ls.temperature_kelvin(ls370_channel)

                        if log_ls_open_loop_power:
                            try:
                                hrng = ls.heater_range()
                                mout_str = ls.query("MOUT?")
                                mout = float(mout_str)
                                range_currents = {
                                    0: 0.0,
                                    1: 31.6e-6,
                                    2: 100e-6,
                                    3: 316e-6,
                                    4: 1e-3,
                                    5: 3.16e-3,
                                    6: 10e-3,
                                    7: 31.6e-3,
                                    8: 100e-3
                                }
                                i_full = range_currents.get(hrng, 0.0)
                                i_actual = i_full * (mout / 100.0)
                                power_uW = (i_actual ** 2) * heater_resistance * 1e6
                            except Exception as e:
                                print(f"Warning: Failed to calculate open loop Lakeshore heater power: {e}")
                except Exception as exc:
                    print(f"Warning: Failed to read LS370: {exc}")

                # 3. Read Bluefors power if requested
                if log_bf_power and bf_ip:
                    try:
                        import bftc
                        bftc.ip = bf_ip
                        import bftc_workflow.ramp_heater as bf_ramp_heater
                        p = bf_ramp_heater.get_latest_heater_power_uW()
                        if p is not None:
                            power_uW = p
                    except Exception as e:
                        print(f"Warning: Failed to get Bluefors heater power: {e}")

                entry = np.array(
                    [(r, x, t, power_uW, current_time)],
                    dtype=ENTRY_DTYPE,
                )

                print(
                    f"R: {r * 1000:.2f} mOhm, "
                    f"X: {x * 1000:.2f} mOhm, "
                    f"T: {t * 1000:.2f} mK, "
                    f"Time: {current_time:.2f}s"
                )
                npaa.append(entry)
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
    logging_interval_s=DEFAULT_LOGGING_INTERVAL_S,
    stop_event=None,
    ls370_port: str = DEFAULT_LS370_PORT,
    ls370_channel: int = DEFAULT_LS370_CHANNEL,
    ls370_baudrate: int = DEFAULT_LS370_BAUDRATE,
    ls370_gpib_address: int | None = None,
    lr700_adapter: str = 'prologix',
    lr700_port: str = DEFAULT_LR700_PORT,
    lr700_gpib_address: int = DEFAULT_LR700_GPIB_ADDRESS,
    lr700_auto: int = DEFAULT_LR700_AUTO,
    log_bf_power: bool = False,
    bf_ip: str | None = None,
    log_ls_open_loop_power: bool = False,
    heater_resistance: float = 120.0,
):
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
        filename = save_dir / f"{base_filename}_{device_name}_ls370_{timestamp}.npy"
    else:
        filename = save_dir / f"{base_filename}_ls370_{timestamp}.npy"

    print(f"Data will be saved to: {filename}")
    log_data(
        str(filename),
        logging_interval_s=logging_interval_s,
        stop_event=stop_event,
        ls370_port=ls370_port,
        ls370_channel=ls370_channel,
        ls370_baudrate=ls370_baudrate,
        ls370_gpib_address=ls370_gpib_address,
        lr700_adapter=lr700_adapter,
        lr700_port=lr700_port,
        lr700_gpib_address=lr700_gpib_address,
        lr700_auto=lr700_auto,
        log_bf_power=log_bf_power,
        bf_ip=bf_ip,
        log_ls_open_loop_power=log_ls_open_loop_power,
        heater_resistance=heater_resistance,
    )
