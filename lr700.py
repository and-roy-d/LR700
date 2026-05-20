"""
LR700 PyVISA Driver
Created on March 3, 2009
@author: bennett, schimaf
Consolidated and cleaned up for cross-platform compatibility in 2026.
"""

import pyvisa
import time

_rm = None
_inst = None

class LR700Exception(Exception):
    pass

def init_gpib(gpib_address=17):
    """
    Lazily initialize PyVISA ResourceManager and connect to the LR700 instrument.
    """
    global _rm, _inst
    if _inst is None:
        try:
            _rm = pyvisa.ResourceManager()
            _inst = _rm.open_resource(f'GPIB0::{gpib_address}::INSTR')
        except Exception as e:
            print(f"Failed to connect to LR700 via PyVISA: {e}")
            _inst = None

def read_ohm(gpib_address=17):
    """
    Legacy method to read resistance from LR700.
    Forwards to read_value for robust dual-channel logic.
    """
    return read_value(channel=0, value_type='R', gpib_address=gpib_address)

def read_value(channel=0, value_type='R', gpib_address=17):
    """
    Reads resistance (R) or reactance (X) from LR700.

    Parameters:
        channel (int): 0 for R, 1 for X
        value_type (str): 'R' or 'X'
        gpib_address (int): GPIB address of the instrument

    Returns:
        float: Value in Ohms, or None if reading failed
    """
    global _inst
    if _inst is None:
        init_gpib(gpib_address)
    
    if _inst is None:
        return None

    for _ in range(10000):
        try:
            return _read_value(channel, value_type)
        except LR700Exception:
            time.sleep(0.001)
            continue
    return None

def _read_value(channel, value_type):
    """
    Internal method to read and parse values from the LR700 instrument.
    """
    global _inst
    if _inst is None:
        raise LR700Exception("Instrument not initialized")

    if channel not in (0, 1):
        raise ValueError("channel must be 0 or 1")
    if value_type not in ('R', 'X'):
        raise ValueError("value_type must be 'R' or 'X'")

    try:
        _inst.write(f"GET {channel}", termination="\n")
        result = _inst.read().strip()
    except Exception as e:
        raise LR700Exception(f"PyVISA communication failed: {e}")

    valuestrings = result.split()

    if len(valuestrings) < 3:
        raise LR700Exception(f"Response too short for GET {channel}: {result!r}")

    # Validate returned kind match (expect R for channel 0, X for channel 1)
    expected_label = 'R' if channel == 0 else 'X'
    if valuestrings[2] != expected_label:
        raise LR700Exception(f"Expected {expected_label} unit in response, got: {result!r}")

    value_str, unit_str = valuestrings[0], valuestrings[1]

    try:
        value = float(value_str)
    except ValueError:
        raise LR700Exception(f"Invalid numeric value: {value_str}")

    multiplier = {
        'UOHM': 1e-6,
        'MOHM': 1e-3,
        'OHM': 1.0,
        'KOHM': 1e3,
    }.get(unit_str)

    if multiplier is None:
        raise LR700Exception(f"Unknown unit: {unit_str}")

    return value * multiplier

if __name__ == "__main__":
    # Test read if executed directly
    print("Attempting local test read from LR700...")
    try:
        resistance = read_value(channel=0, value_type='R')
        reactance = read_value(channel=1, value_type='X')
        print(f"Resistance (R): {resistance} Ω")
        print(f"Reactance (X): {reactance} Ω")
    except Exception as e:
        print(f"Could not read from LR700 (expected if not connected): {e}")
