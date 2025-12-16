
import pyvisa
import time

_rm = pyvisa.ResourceManager()
_inst = _rm.open_resource('GPIB0::16::INSTR')


class LR700Exception(Exception):
    pass


def read_value(channel=0, value_type='R'):
    """
    Reads resistance (R) or reactance (X) from LR700.

    Parameters:
        channel (int): 0 for R only, 1 for R and X
        value_type (str): 'R' or 'X'

    Returns:
        float: Value in Ohms
    """
    for _ in range(10000):
        try:
            return _read_value(channel, value_type)
        except LR700Exception:
            time.sleep(0.001)
            continue
    return None


def _read_value(channel, value_type):
    """
    Internal method to read and parse value from LR700.

    Parameters:
        channel (int): 0 for resistance (R), 1 for reactance (X)
        value_type (str): 'R' or 'X' — expected value type in response

    Returns:
        float: Parsed and scaled value
    """
    if channel not in (0, 1):
        raise ValueError("channel must be 0 or 1")
    if value_type not in ('R', 'X'):
        raise ValueError("value_type must be 'R' or 'X'")

    _inst.write(f"GET {channel}", termination="\n")
    result = _inst.read().strip()

    valuestrings = result.split()

    if len(valuestrings) < 3:
        raise LR700Exception(f"Response too short for GET {channel}")

    # For GET 0 (channel=0), expect: VALUE UNIT R
    if channel == 0:
        if valuestrings[2] != 'R':
            raise LR700Exception("Expected R unit in GET 0 response")
        value_str, unit_str = valuestrings[0], valuestrings[1]

    else:
        if valuestrings[2] != 'X':
            raise LR700Exception("Expected X unit in GET 1 response")
        value_str, unit_str = valuestrings[0], valuestrings[1]

    try:
        value = float(value_str)
    except ValueError:
        raise LR700Exception(f"Invalid numeric value: {value_str}")

    multiplier = {
        'UOHM': 1e-6,
        'MOHM': 1e-3,
        'OHM': 1,
        'KOHM': 1e3,
    }.get(unit_str)

    if multiplier is None:
        raise LR700Exception(f"Unknown unit: {unit_str}")

    return value * multiplier

if __name__ == "__main__":
    try:
        resistance = read_value(channel=0, value_type='R')
        reactance = read_value(channel=1, value_type='X')
        print(f"Resistance (R): {resistance} Ω")
        print(f"Reactance (X): {reactance} Ω")
    except LR700Exception as e:
        print(f"Error reading from LR700: {e}")