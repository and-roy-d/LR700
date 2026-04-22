from __future__ import annotations

import subprocess
import threading
from pathlib import Path
import sys

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prologix_lr700_test import DEFAULT_GPIB_ADDRESS as DEFAULT_LR700_GPIB_ADDRESS
from prologix_lr700_test import DEFAULT_PORT as DEFAULT_LR700_PORT
from lakeshore_370_temperature_test import DEFAULT_BAUDRATE as DEFAULT_LS370_BAUDRATE
from lakeshore_370_temperature_test import DEFAULT_CHANNEL as DEFAULT_LS370_CHANNEL
from lakeshore_370_temperature_test import DEFAULT_PORT as DEFAULT_LS370_PORT
from lakeshore_370_temperature_test import LakeShore370
import data_logger


def get_user_params():
    save_dir = input("Enter directory to save data (or press Enter for default): ").strip() or None
    device_name = input("Enter an optional device name (e.g. A1) or press Enter for none: ").strip()

    ls370_port = input(f"Enter LS370 serial port (default {DEFAULT_LS370_PORT}): ").strip() or DEFAULT_LS370_PORT
    ls370_channel_str = input(f"Enter LS370 channel (default {DEFAULT_LS370_CHANNEL}): ").strip()
    ls370_channel = int(ls370_channel_str) if ls370_channel_str else DEFAULT_LS370_CHANNEL
    ls370_baudrate_str = input(f"Enter LS370 baudrate (default {DEFAULT_LS370_BAUDRATE}): ").strip()
    ls370_baudrate = int(ls370_baudrate_str) if ls370_baudrate_str else DEFAULT_LS370_BAUDRATE

    lr700_port = input(f"Enter LR700 Prologix port (default {DEFAULT_LR700_PORT}): ").strip() or DEFAULT_LR700_PORT
    lr700_gpib_address_str = input(f"Enter LR700 GPIB address (default {DEFAULT_LR700_GPIB_ADDRESS}): ").strip()
    lr700_gpib_address = int(lr700_gpib_address_str) if lr700_gpib_address_str else DEFAULT_LR700_GPIB_ADDRESS

    interval_str = input("Enter logging interval in seconds (default 1): ").strip()
    logging_interval_s = float(interval_str) if interval_str else 1.0

    with LakeShore370(port=ls370_port, baudrate=ls370_baudrate) as bridge:
        temperature_kelvin = bridge.temperature_kelvin(ls370_channel)
        setpoint_kelvin = bridge.setpoint_kelvin()
        ramp = bridge.ramp_state()

        print(f"Current LS370 channel {ls370_channel} temperature: {temperature_kelvin * 1000:.2f} mK")
        print(
            f"Current setpoint: {setpoint_kelvin * 1000:.2f} mK "
            f"({setpoint_kelvin:.6f} K)"
        )
        print(
            f"Current ramp: enabled={int(ramp.enabled)}, "
            f"rate={ramp.rate_kelvin_per_minute * 1000:.3f} mK/min "
            f"({ramp.rate_kelvin_per_minute:.6f} K/min), "
            f"active={ramp.actively_ramping}"
        )

        ramp_rate_str = input(
            "Enter new ramp rate in mK/min, or press Enter to keep the current rate: "
        ).strip()
        if ramp_rate_str:
            new_ramp_rate = float(ramp_rate_str) * 1e-3
            bridge.set_ramp(ramp.enabled, new_ramp_rate)
            ramp = bridge.ramp_state()
            print(
                f"Updated ramp: enabled={int(ramp.enabled)}, "
                f"rate={ramp.rate_kelvin_per_minute * 1000:.3f} mK/min "
                f"({ramp.rate_kelvin_per_minute:.6f} K/min), "
                f"active={ramp.actively_ramping}"
            )

        setpoint_str = input(
            "Enter new setpoint in mK, or press Enter to keep the current setpoint. "
            "This is applied after the ramp rate above: "
        ).strip()
        if setpoint_str:
            new_setpoint_kelvin = float(setpoint_str) * 1e-3
            bridge.set_setpoint_kelvin(new_setpoint_kelvin)
            setpoint_kelvin = bridge.setpoint_kelvin()
            print(
                f"Updated setpoint: {setpoint_kelvin * 1000:.2f} mK "
                f"({setpoint_kelvin:.6f} K)"
            )

    return (
        save_dir,
        device_name,
        logging_interval_s,
        ls370_port,
        ls370_channel,
        ls370_baudrate,
        lr700_port,
        lr700_gpib_address,
    )


def main():
    stop_event = threading.Event()
    (
        save_dir,
        device_name,
        logging_interval_s,
        ls370_port,
        ls370_channel,
        ls370_baudrate,
        lr700_port,
        lr700_gpib_address,
    ) = get_user_params()

    data_thread = threading.Thread(
        target=data_logger.main,
        kwargs={
            "save_dir": save_dir,
            "device_name": device_name,
            "logging_interval_s": logging_interval_s,
            "stop_event": stop_event,
            "ls370_port": ls370_port,
            "ls370_channel": ls370_channel,
            "ls370_baudrate": ls370_baudrate,
            "lr700_port": lr700_port,
            "lr700_gpib_address": lr700_gpib_address,
        },
        daemon=True,
    )
    data_thread.start()

    dash_process = subprocess.Popen(
        [sys.executable, str(THIS_DIR / "dash_app.py")],
        cwd=str(ROOT_DIR),
    )
    print(f"Lake Shore plotter started on http://127.0.0.1:8051/ (PID {dash_process.pid})")

    print("Type 'exit' to stop data logging. The live plotter will continue until you close it.")
    while True:
        cmd = input().strip().lower()
        if cmd == "exit":
            print("Stopping data logger...")
            stop_event.set()
            break
        print("Unknown command. Type 'exit' to stop logger.")

    data_thread.join(timeout=5)
    print("Lake Shore workflow stopped.")


if __name__ == "__main__":
    main()
