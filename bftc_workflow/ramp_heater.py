from __future__ import annotations
from pathlib import Path
import sys
import time
import tqdm

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from bftc import BFTC


def ramp_up(bf: BFTC, channel: int, P_init, target_temp, step=30e-6, timeout=3600, sleep_time=60):
    P_set = P_init
    start_time = time.time()
    while True:
        base_temp = bf.get_temperature(channel)
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
        bf.set_heater_power(P_set)

    print("Ramp up complete.")
    return True


def ramp_down(bf: BFTC, channel: int, P_init, target_temp, step=30e-6, timeout=3600, sleep_time=60):
    P_set = P_init
    start_time = time.time()
    while True:
        base_temp = bf.get_temperature(channel)
        with tqdm.tqdm(total=sleep_time, desc="Waiting for next step", unit="s", dynamic_ncols=True, leave=False) as pbar:
            pbar.write(f'Heater power = {P_set * 1e6} uW, Base temperature = {round(base_temp * 1000, 2)} mK')
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
        bf.set_heater_power(P_set)

    print("Ramp down complete.")
    return True


def get_latest_heater_power_uW(bf: BFTC, heater_nr: int = 4) -> float | None:
    return bf.get_latest_heater_power_uW(heater_nr)


def main(bf: BFTC, channel: int, direction, p_init, target_temp, step, sleep_time, timeout):
    if direction == 'up':
        return ramp_up(bf, channel, p_init, target_temp, step, timeout, sleep_time)
    elif direction == 'down':
        return ramp_down(bf, channel, p_init, target_temp, step, timeout, sleep_time)
    else:
        print(f"Invalid ramp direction: {direction}")
        return False
