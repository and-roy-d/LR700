import time
import csv
from datetime import datetime
import tqdm
import bftc
from lr700_new import read_value
from ramp_heater import get_latest_heater_power_uW

# --- Ramp Down Function ---
def ramp_down_and_log(
    P_init,
    target_temp,
    step=30e-6,
    timeout=3600,
    sleep_time=60,
    log_filename="ramp_down_log.csv"
):
    P_set = P_init
    start_time = time.time()

    # Prepare CSV logging
    with open(log_filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Time", "Elapsed_s", "HeaterPower_uW", "Temp_mK", "R_Ohm", "X_Ohm"])

        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = round(time.time() - start_time, 1)

            base_temp = bftc.read_scepter_temperature()
            try:
                r = read_value(channel=0, value_type='R')
                x = read_value(channel=1, value_type='X')

            except Exception as e:
                print(f"LR700 read error: {e}")
                r, x = None, None

            # Log current values
            writer.writerow([
                now,
                elapsed,
                round(P_set * 1e6, 6),
                round(base_temp * 1000, 4),  # Convert K → mK
                round(r, 6) if r is not None else '',
                round(x, 6) if x is not None else ''
            ])
            file.flush()  # Ensure data is written even if script is interrupted

            with tqdm.tqdm(total=sleep_time, desc="Waiting", unit="s", dynamic_ncols=True, leave=False) as pbar:
                pbar.write(f"{now} | Power = {round(P_set * 1e6, 2)} uW, Temp = {round(base_temp * 1000, 2)} mK, R = {r:.2f} Ω, X = {x:.2f} Ω")
                for _ in range(sleep_time):
                    time.sleep(1)
                    pbar.update(1)

            if base_temp < target_temp:
                print("Target temperature reached.")
                break
            # if time.time() - start_time > timeout:
            #     print("Ramp down timed out!")
            #     return False

            # if P_set < 1e-9:
            #     print("Heater power reached minimum, stopping ramp down.")
            #     return True

            # Decrease heater power
            P_set = max(P_set - step, 0)
            P_set = round(P_set, 9)
            bftc.set_heaterpower(P_set)

    print("Ramp down complete.")
    return True


if __name__ == "__main__":
    current_p_uW = get_latest_heater_power_uW()
    print(f"{current_p_uW} uW")
    power_step = 10e-6
    sleep_time = 10
    start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
    if current_p_uW is None:
        print("Could not get initial heater power.")
    else:
        P_init = current_p_uW * 1e-6  # Convert uW → W
        target_temp = 0.02
        ramp_down_and_log(P_init, target_temp, step=power_step, sleep_time=sleep_time, log_filename=f"Data//test_{start_time}.csv")
