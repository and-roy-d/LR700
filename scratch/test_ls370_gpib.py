import serial
import time

def scan_all_idn(port):
    print(f"Scanning all GPIB addresses on {port}...")
    try:
        ser = serial.Serial(
            port=port,
            baudrate=9600,
            timeout=0.3,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
    except Exception as e:
        print(f"Failed to open port {port}: {e}")
        return

    # Configure Prologix
    ser.write(b"++savecfg 0\n")
    ser.write(b"++mode 1\n")
    ser.write(b"++auto 1\n") # Auto read-after-write
    ser.write(b"++eoi 1\n")
    ser.write(b"++eos 2\n") # LF
    ser.write(b"++read_tmo_ms 250\n")
    ser.write(b"++ifc\n")
    time.sleep(0.1)
    
    for addr in range(1, 31):
        ser.write(f"++addr {addr}\n".encode())
        ser.write(b"*IDN?\n")
        # Read response
        response = ser.readline().decode('ascii', errors='replace').strip()
        if response:
            print(f"  Address {addr}: {response!r}")

    ser.close()

if __name__ == "__main__":
    scan_all_idn("COM11")
    scan_all_idn("COM14")
