import os
import requests
import numpy as np
import time
from datetime import datetime, timedelta, timezone

# Bypass system proxy for direct instrument connections
os.environ.setdefault("NO_PROXY", "169.169.10.10,132.163.157.220,localhost,127.0.0.1")

# this should clearly be a class that can take an IP as an argument, but it was faster this way

# ip = "132.163.157.220:5001"
ip = "169.169.10.10:5001"

def set_setpoint(temp_K, thermometer = 'scepter'):
    if thermometer == 'scepter':
        turn_off_all_but_scepter()
    elif thermometer == 'mxc':  # NOT IMPLEMENTED
        turn_off_all_but_mxc()
    response = requests.post(f"http://{ip}/heater/update",
                             timeout=10, json={"active":True,"pid_mode":1,"setpoint":temp_K,"heater_nr":4})
    response.raise_for_status()
    return response.json()

def stop_regulation():
    set_heaterpower(power_W=0)
    turn_off_heater(heater_ch=4)
    turn_on_all()

def turn_off_heater(heater_ch):
    response = requests.post(f"http://{ip}/heater/update",
                             timeout=10, json={"active":False,"channel_nr":heater_ch})
    response.raise_for_status()
    return response.json()


def set_heaterpower(power_W):
    response = requests.post(f"http://{ip}/heater/update",
                             timeout=10, json={"active":True,"pid_mode":0,"power":power_W,"heater_nr":4})
    response.raise_for_status()
    return response.json()

BFTC_TIME_OFFSET = timedelta(hours=7, minutes=2, seconds=-58.5)
def get_heaterpower(ch=4, start_minutes_ago=10, stop_minutes_ago=0):
    """
    when the heater is off, this won't return zeros, it will return no data
    Args:
        ch: heater channel number
        start_minutes_ago: minutes from now to go back in time for start data
        stop_minutes_ago: minutes from now to go back in time for stop data

    Returns:
        a dict, see example below
        {'status': 'OK', 'measurements': {'timestamp': [1741277222], 'power': [0.0]}, 'over_limit': False,
        'fields': ['timestamp', 'power'], 'start_time': '2025-03-06T16:07:02', 'heater_nr': 4,
        'datetime': '2025-03-06T16:17:03.006066Z', 'stop_time': '2025-03-06T16:17:02'}

    """
    global BFTC_TIME_OFFSET

    current_time = datetime.now()

    start_time = current_time - timedelta(minutes=start_minutes_ago) + BFTC_TIME_OFFSET
    end_time = current_time - timedelta(minutes=stop_minutes_ago) + BFTC_TIME_OFFSET

    # Format the times as YYYY-MM-DD HH:MM:SS
    start_time_str = start_time.strftime(r'%Y-%m-%dT%H:%M:%S')
    end_time_str = end_time.strftime(r'%Y-%m-%dT%H:%M:%S')
    payload = {"heater_nr":ch, "fields":["power"],
               "start_time":start_time_str, "stop_time":end_time_str}
    # print(f"{payload=}")
    response = requests.post(f"http://{ip}/heater/historical-data", timeout=10, json=payload)
    response.raise_for_status()
    response_json = response.json()
    # print(response_json)
    response_datetime = datetime.strptime(response_json["datetime"], '%Y-%m-%dT%H:%M:%S.%fZ')
    # print(f"{response_datetime=}")
    # print(f"{current_time=}")

    # Calculate the time difference from the current time
    time_difference_from_offset = current_time + BFTC_TIME_OFFSET - response_datetime
    # print(f"{time_difference_from_offset=}")
    if abs((time_difference_from_offset).total_seconds())>10:
        BFTC_TIME_OFFSET = response_datetime-current_time
        print(f"UPDATING BTFC_TIME_OFFSET to {BFTC_TIME_OFFSET}")

    return response_json


def get_temp_history(ch=4, start_minutes_ago=10, stop_minutes_ago=0):
    """
    if a thermometer is disabled, it returns no data for that time
    Args:
        ch: thermometer channel number
        start_minutes_ago: minutes from now to go back in time for start data
        stop_minutes_ago: minutes from now to go back in time for stop data

    Returns:
        a dict, see example below
        {'status': 'OK', 'over_limit': False, 'fields': ['timestamp', 'temperature'],
        'start_time': '2025-03-07T18:57:47',
        'measurements': {'timestamp': [1741373899.235115, 1741373945.37407, 1741373991.536156,
        1741374037.788178, 1741374084.061766, 1741374130.303498, 1741374176.575457,
        1741374222.775761, 1741374268.985624, 1741374315.177683, 1741374361.364557,
        1741374407.542292, 1741374453.749854], 'temperature': [294.267346, 294.265306, 294.269387,
        294.283673, 294.271428, 294.27551, 294.267346, 294.271428, 294.267346, 294.273469, 294.263265,
        294.27551, 294.27551]},
        'channel_nr': 1, 'datetime': '2025-03-07T19:07:47.429265Z', 'stop_time': '2025-03-07T19:07:47'}

    """
    global BFTC_TIME_OFFSET

    current_time = datetime.now()

    start_time = current_time - timedelta(minutes=start_minutes_ago) + BFTC_TIME_OFFSET
    end_time = current_time - timedelta(minutes=stop_minutes_ago) + BFTC_TIME_OFFSET

    # Format the times as YYYY-MM-DD HH:MM:SS
    start_time_str = start_time.strftime(r'%Y-%m-%dT%H:%M:%S')
    end_time_str = end_time.strftime(r'%Y-%m-%dT%H:%M:%S')
    payload = {"channel_nr": ch, "fields": ["temperature"],
               "start_time": start_time_str, "stop_time": end_time_str}
    # print(f"{payload=}")
    response = requests.post(f"http://{ip}/channel/historical-data", timeout=10, json=payload)
    response.raise_for_status()
    response_json = response.json()
    # print(response_json)
    response_datetime = datetime.strptime(response_json["datetime"], '%Y-%m-%dT%H:%M:%S.%fZ')
    # print(f"{response_datetime=}")
    # print(f"{current_time=}")

    # Calculate the time difference from the current time
    time_difference_from_offset = current_time + BFTC_TIME_OFFSET - response_datetime
    # print(f"{time_difference_from_offset=}")
    if abs((time_difference_from_offset).total_seconds()) > 10:
        BFTC_TIME_OFFSET = response_datetime - current_time
        print(f"UPDATINNG BTFC_TIME_OFFSET to {BFTC_TIME_OFFSET}")

    return response_json



def turn_off(ch):
    response = requests.post(f"http://{ip}/channel/update",
                             timeout=10, json={"active":False,"channel_nr":ch})
    response.raise_for_status()
    return response.json()

def turn_off_all_but_mxc():
    for ch in [1, 2, 5]:
        turn_off(ch)

def turn_off_all_but_scepter():
    for ch in [1, 2, 6]:
        turn_off(ch)

def turn_on(ch):
    response = requests.post(f"http://{ip}/channel/update",
                             timeout=10, json={"active":True,"channel_nr":ch})
    response.raise_for_status()
    return response.json()

def turn_on_all():
    for ch in [1, 2, 5, 6]:
        turn_on(ch)


def _read_latest_temperature(expected_channel, timeout_s=60, poll_interval=2):
    """Poll /channel/measurement/latest until the expected channel appears.

    When multiple channels are active the controller cycles through them
    (~11 s per full cycle).  This waits up to *timeout_s* seconds for the
    right channel to come back.
    """
    deadline = time.time() + timeout_s
    while True:
        response = requests.get(f"http://{ip}/channel/measurement/latest",
                                timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("channel_nr") == expected_channel:
            return data["temperature"]
        if time.time() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for channel {expected_channel} "
                f"(last saw channel {data.get('channel_nr')})"
            )
        time.sleep(poll_interval)


def read_channel_temperature(channel, **kwargs):
    """Read the latest temperature for an arbitrary channel number."""
    return _read_latest_temperature(channel, **kwargs)


def read_mxc_temperature(**kwargs):
    return _read_latest_temperature(6, **kwargs)


def read_scepter_temperature(**kwargs):
    return _read_latest_temperature(7, **kwargs)

def mypid(setpoint_mK, I=0.3, P=2, breakout_err_mK = 0.1,power_uW_start=0):
    t_mK = read_mxc_temperature()*1000
    err_mK = setpoint_mK-t_mK
    Ipower_uW = power_uW_start
    while np.abs(err_mK)>breakout_err_mK:
        err_mK = setpoint_mK-t_mK
        Ipower_uW+=I*err_mK
        Ipower_uW = max(0, Ipower_uW)
        Ppower_uW = P*err_mK
        power_uW = Ipower_uW+Ppower_uW
        power_uW = max(power_uW,0)
        print(f"{setpoint_mK=:.2f} {t_mK=:.2f} {err_mK=:.2f} {Ipower_uW=:.2f} {breakout_err_mK=:.2f}\n {Ppower_uW=:.2f} {power_uW=:.2f} {I=:.2f} {P=:.2f}\n")
        time.sleep(1)

        set_heaterpower(power_uW*1e-6)

if __name__ == "__main__":
    print(get_heaterpower())
    print(get_temp_history(ch=7))
