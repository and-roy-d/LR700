'''
LR700 
Created on Augest 15, 2009
@author: bennett
'''


'''
Created on Mar 3, 2009

@author: schimaf
@version: 1.0.0
'''

#import time

import pyvisa
import time

_rm = pyvisa.ResourceManager()
_inst = _rm.open_resource('GPIB0::16::INSTR')


class LR700Exception(Exception):
    pass


def read_ohm():
    for i in range(10000):
        try:
            return _read_ohm()
        except LR700Exception:
            time.sleep(0.001)
            continue
    
    return None

def _read_ohm():
    _inst.write("GET 0", termination="\n")
    result = _inst.read()
    valuestrings = result.split()
    # print(f"{result=} {valuestrings=}")
    if len(valuestrings) < 3:
        raise LR700Exception("too short valuestrings")
    if valuestrings[2] != 'R':
        raise LR700Exception("no R found")
    value = float(valuestrings[0])
    units = valuestrings[1]

    if units == 'KOHM':
        multiplier = 1e3
    elif units == 'OHM':
        multiplier = 1
    elif units == 'MOHM':
        multiplier = 1e-3
    elif units == 'UOHM':
        multiplier = 1e-6
    else:
        raise LR700Exception("bad multipler")


    resistance = value * multiplier
    
    return resistance

