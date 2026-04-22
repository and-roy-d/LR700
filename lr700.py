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

_rm = None
_inst = None

class LR700Exception(Exception):
    pass

def init_gpib(gpib_address=17):
    global _rm, _inst
    if _inst is None:
        try:
            _rm = pyvisa.ResourceManager()
            _inst = _rm.open_resource(f'GPIB0::{gpib_address}::INSTR')
        except Exception as e:
            print(f"Failed to connect to LR700 via PyVISA: {e}")
            _inst = None

def read_ohm(gpib_address=17):
    if _inst is None:
        init_gpib(gpib_address)
    
    if _inst is None:
        return None

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

