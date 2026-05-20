import threading

_port_locks = {}
_port_locks_lock = threading.Lock()

def get_port_lock(port):
    """Get a threading.Lock instance specific to the given port name."""
    if not port:
        # Return a dummy lock if port is None or empty
        return threading.Lock()
        
    port_key = port.upper() if isinstance(port, str) else str(port)
    with _port_locks_lock:
        if port_key not in _port_locks:
            _port_locks[port_key] = threading.Lock()
        return _port_locks[port_key]
