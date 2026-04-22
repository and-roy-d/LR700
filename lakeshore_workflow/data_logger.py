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
    lr700_port: str = DEFAULT_LR700_PORT,
    lr700_gpib_address: int = DEFAULT_LR700_GPIB_ADDRESS,
    lr700_auto: int = DEFAULT_LR700_AUTO,
) -> None:
    print(
        "Starting Lake Shore/LR700 logging "
        f"(LS370 {ls370_port} ch{ls370_channel}, LR700 {lr700_port} GPIB {lr700_gpib_address})..."
    )

    try:
        npaa = NpyAppendArray(filename)
    except Exception as exc:
        print(f"Error initializing NpyAppendArray: {exc}")
        return

    try:
        with LakeShore370(port=ls370_port, baudrate=ls370_baudrate) as ls370, PrologixLR700(
            port=lr700_port,
            gpib_address=lr700_gpib_address,
            auto=lr700_auto,
        ) as lr700:
            while True:
                if stop_event is not None and stop_event.is_set():
                    print("Stop event detected. Stopping data logging and closing file.")
                    break

                try:
                    r = lr700.read_r().value_ohms
                    x = lr700.read_x().value_ohms
                    t = ls370.temperature_kelvin(ls370_channel)
                    current_time = time.time()

                    entry = np.array(
                        [(r, x, t, np.nan, current_time)],
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
    lr700_port: str = DEFAULT_LR700_PORT,
    lr700_gpib_address: int = DEFAULT_LR700_GPIB_ADDRESS,
    lr700_auto: int = DEFAULT_LR700_AUTO,
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
        lr700_port=lr700_port,
        lr700_gpib_address=lr700_gpib_address,
        lr700_auto=lr700_auto,
    )
