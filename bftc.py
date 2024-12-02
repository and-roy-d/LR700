import requests
import numpy as np
import time

def set_setpoint(temp_K):
    response = requests.post("http://169.254.98.14:5001/heater/update",
                             timeout=10, json={"active":True,"pid_mode":1,"setpoint":temp_K,"heater_nr":4})
    response.raise_for_status()
    return response.json()

def set_heaterpower(power_W):
    response = requests.post("http://169.254.98.14:5001/heater/update",
                             timeout=10, json={"active":True,"pid_mode":0,"power":power_W,"heater_nr":4})
    response.raise_for_status()
    return response.json()    

def turn_off(ch):
    response = requests.post("http://169.254.98.14:5001/channel/update",
                             timeout=10, json={"active":False,"channel_nr":ch})
    response.raise_for_status()
    return response.json()

def turn_off_all_but_mxc():
    for ch in [1,2,5]:
        turn_off(ch)

def turn_on(ch):
    response = requests.post("http://169.254.98.14:5001/channel/update",
                             timeout=10, json={"active":True,"channel_nr":ch})
    response.raise_for_status()
    return response.json()   

def turn_on_all():
    for ch in [1,2,5,6]:
        turn_on(ch)


def read_mxc_temperature():
    response = requests.get("http://169.254.98.14:5001/channel/measurement/latest",
                            timeout=10)
    data = response.json()
    assert data["channel_nr"]==6
    return data["temperature"]

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