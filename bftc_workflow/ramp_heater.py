import bftc
import time
import tqdm

def ramp_up(P_init, target_temp, step=30e-6, timeout=3600, sleep_time=60):
    P_set = P_init
    start_time = time.time()
    while True:
        base_temp = bftc.read_scepter_temperature()
        print(f'Heater power = {round(P_set * 1e6, 2)} uW, Base temperature = {round(base_temp * 1000, 2)} mK')
        with tqdm.tqdm(total=sleep_time, desc="Waiting for next step", unit="s", dynamic_ncols=True) as pbar:
            for _ in range(int(sleep_time)):
                time.sleep(1)
                pbar.update(1)

        if base_temp > target_temp:
            break
        if time.time() - start_time > timeout:
            print("Ramp up timed out!")
            return False

        P_set = max(P_set + step, 0)
        P_set = round(P_set, 9)
        bftc.set_heaterpower(P_set)

    print("Ramp up complete.")
    return True


def ramp_down(P_init, target_temp, step=30e-6, timeout=3600, sleep_time=60):
    P_set = P_init
    start_time = time.time()
    while True:
        base_temp = bftc.read_scepter_temperature()
        with tqdm.tqdm(total=sleep_time, desc="Waiting for next step", unit="s", dynamic_ncols=True, leave=False) as pbar:
            pbar.write(f'Heater power = {P_set * 1e6} uW, Base temperature = {round(base_temp * 1000,2)} mK')
            for _ in range(int(sleep_time)):
                time.sleep(1)
                pbar.update(1)

        if base_temp < target_temp:
            break
        if time.time() - start_time > timeout:
            print("Ramp down timed out!")
            return False

        if P_set < 1e-9:
            print("Heater power reached minimum, stopping ramp down.")
            return True

        P_set = max(P_set - step, 0)
        P_set = round(P_set, 9)
        bftc.set_heaterpower(P_set)

    print("Ramp down complete.")
    return True


def get_latest_heater_power_uW(ch=4):
    try:
        data = bftc.get_heaterpower(ch=ch, start_minutes_ago=2, stop_minutes_ago=0)
        power_vals = data.get("measurements", {}).get("power", [])
        if power_vals:
            return power_vals[-1] * 1e6
        else:
            print("No recent heater power data found.")
            return None
    except Exception as e:
        print(f"Failed to retrieve heater power: {e}")
        return None


def main(direction, p_init, target_temp, step, sleep_time, timeout):
    if direction == 'up':
        return ramp_up(p_init, target_temp, step, timeout, sleep_time)
    elif direction == 'down':
        return ramp_down(p_init, target_temp, step, timeout, sleep_time)
    else:
        print(f"Invalid ramp direction: {direction}")
        return False
