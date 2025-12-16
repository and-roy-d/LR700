import threading
import time
import bftc
import ramp_heater
import data_logger
import dash_app

def get_user_params():
    save_dir = input(f"Enter directory to save data (or press Enter for default): ").strip()
    if not save_dir:
        save_dir = None  # Let data_logger set default

    device_name = input("Enter an optional device name (e.g., A1) or press Enter for none: ").strip()

    temp_source = input("Choose temperature source ('scepter' or 'mxc', default 'scepter'): ").lower()
    if temp_source not in ['scepter', 'mxc']:
        temp_source = 'scepter'

    # Display current temperature and heater power
    if temp_source == 'scepter':

        temp = bftc.read_scepter_temperature()

    else:
        temp = bftc.read_mxc_temperature()


    print(f"Current {temp_source} temperature: {temp*1000:.2f} mK")

    try:
        power_uW = ramp_heater.get_latest_heater_power_uW()
        if power_uW is not None:
            print(f"Current heater power: {power_uW:.2f} µW")
        else:
            print("Could not retrieve current heater power.")
    except Exception:
        print("Error retrieving heater power.")

    direction = None
    while direction not in ('up', 'down'):
        direction = input("Enter ramp direction ('up' or 'down'): ").lower()

    p_init_str = input("Press Enter to use current heater power, or enter heater power (µW): ").strip()
    if p_init_str == "" and power_uW is not None:
        p_init = power_uW * 1e-6  # convert µW to W
        print(f"Using current heater power: {power_uW:.2f} µW")
    else:
        p_init = float(p_init_str) * 1e-6

    target_temp = float(input("Enter target base temperature (in mK, e.g., 130): ")) * 1e-3  # to K

    step_str = input("Enter power step (in µW, or press Enter for default 10 µW): ").strip()
    step = float(step_str)*1e-6 if step_str else 10e-6
    assert step>0

    sleep_str = input("Enter sleep time between power steps (seconds, or Enter for default 10): ").strip()
    sleep_time = int(sleep_str) if sleep_str else 10
    assert sleep_time>0

    timeout_str = input("Enter timeout in seconds (or Enter for default 3600): ").strip()
    timeout = int(timeout_str) if timeout_str else 3600

    return save_dir, device_name, temp_source, direction, p_init, target_temp, step, sleep_time, timeout

def main():
    stop_event = threading.Event()

    save_dir, device_name, temp_source, direction, p_init, target_temp, step, sleep_time, timeout = get_user_params()

    # Start data logger thread
    data_thread = threading.Thread(
        target=data_logger.main,
        args=(save_dir, device_name, temp_source, 1, stop_event, target_temp, direction),
        daemon=True
    )

    data_thread.start()

    # Start Dash app thread (daemon)
    dash_thread = threading.Thread(target=dash_app.run_dash_app, daemon=True)
    dash_thread.start()

    # Run heater ramp in main thread (blocking)
    success = ramp_heater.main(direction, p_init, target_temp, step, sleep_time, timeout)

    if success:
        print("Ramp heater finished successfully.")
    else:
        print("Ramp heater finished with timeout or failure.")

    print("Type 'exit' to stop data logging (Dash app will keep running).")

    while True:
        cmd = input().strip().lower()
        if cmd == 'exit':
            print("Stopping data logger...")
            stop_event.set()
            break
        else:
            print("Unknown command. Type 'exit' to stop logger.")

    data_thread.join(timeout=5)
    print("Data logger stopped. Dash app will continue running until manually terminated.")
    print("Program finished.")

if __name__ == '__main__':
    main()
