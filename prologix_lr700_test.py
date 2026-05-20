from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports


import sys
from pathlib import Path
_parent = Path(__file__).resolve().parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))
import port_locker

if sys.platform.startswith("win"):
    DEFAULT_PORT = "COM11"
else:
    DEFAULT_PORT = "/dev/ttyUSB0"

DEFAULT_GPIB_ADDRESS = 17
DEFAULT_AUTO_MODE = 1


UNIT_MULTIPLIERS = {
    "UOHM": 1e-6,
    "MOHM": 1e-3,
    "OHM": 1.0,
    "KOHM": 1e3,
}


class LR700ReadError(RuntimeError):
    pass


@dataclass
class Measurement:
    kind: str
    value_ohms: float
    raw_value: float
    raw_unit: str
    raw_response: str


def parse_measurement(response: str, expected_kind: str) -> Measurement:
    parts = response.strip().split()
    if len(parts) < 3:
        raise LR700ReadError(f"Unexpected response format: {response!r}")

    kind_text = parts[-1]
    unit_text = parts[-2]
    value_text = "".join(parts[:-2])

    if kind_text != expected_kind:
        raise LR700ReadError(
            f"Expected {expected_kind!r} but received {kind_text!r}: {response!r}"
        )

    try:
        raw_value = float(value_text)
    except ValueError as exc:
        raise LR700ReadError(f"Invalid numeric value {value_text!r}") from exc

    try:
        multiplier = UNIT_MULTIPLIERS[unit_text]
    except KeyError as exc:
        raise LR700ReadError(f"Unknown unit {unit_text!r}") from exc

    return Measurement(
        kind=kind_text,
        value_ohms=raw_value * multiplier,
        raw_value=raw_value,
        raw_unit=unit_text,
        raw_response=response.strip(),
    )


class PrologixLR700:
    def __init__(
        self,
        port: str,
        gpib_address: int,
        baudrate: int = 9600,
        timeout: float = 1.0,
        eos: int = 2,
        auto: int = 0,
    ) -> None:
        self.port = port
        self.lock = port_locker.get_port_lock(port)
        self.lock.acquire()
        self.has_lock = True
        
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                write_timeout=timeout,
            )
            self.gpib_address = gpib_address
            self.timeout = timeout
            self.eos = eos
            self.auto = auto
        except Exception:
            self.lock.release()
            self.has_lock = False
            raise

    def close(self) -> None:
        try:
            if hasattr(self, "serial") and self.serial:
                self.serial.close()
        finally:
            if hasattr(self, "has_lock") and self.has_lock:
                self.lock.release()
                self.has_lock = False

    def __enter__(self) -> "PrologixLR700":
        self.configure()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _write_controller(self, command: str) -> None:
        self.serial.write(f"{command}\n".encode("ascii"))
        self.serial.flush()

    def _write_instrument(self, command: str) -> None:
        self.serial.write(f"{command}\n".encode("ascii"))
        self.serial.flush()

    def _read_response(self, idle_timeout: float = 0.2) -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + max(self.timeout, 0.1) + idle_timeout
        last_data_at: float | None = None

        while time.monotonic() < deadline:
            waiting = self.serial.in_waiting
            if waiting:
                chunks.append(self.serial.read(waiting))
                last_data_at = time.monotonic()
                continue

            chunk = self.serial.read(1)
            if chunk:
                chunks.append(chunk)
                last_data_at = time.monotonic()
                continue

            if chunks and last_data_at is not None:
                if time.monotonic() - last_data_at >= idle_timeout:
                    break

        response = b"".join(chunks).decode("ascii", errors="replace").strip()
        if not response:
            raise LR700ReadError("Timed out waiting for response from Prologix/LR700")
        return response

    def controller_query(self, command: str) -> str:
        self.serial.reset_input_buffer()
        self._write_controller(command)
        return self._read_response()

    def configure(self) -> None:
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()
        self._write_controller("++savecfg 0")
        self._write_controller("++mode 1")
        self._write_controller(f"++addr {self.gpib_address}")
        self._write_controller(f"++auto {self.auto}")
        self._write_controller("++eoi 1")
        self._write_controller(f"++eos {self.eos}")
        self._write_controller(f"++read_tmo_ms {min(max(int(self.timeout * 1000), 1), 3000)}")
        self._write_controller("++ifc")
        self._write_controller("++clr")
        time.sleep(0.1)

    def probe(self) -> dict[str, str]:
        return {
            "version": self.controller_query("++ver"),
            "mode": self.controller_query("++mode"),
            "addr": self.controller_query("++addr"),
            "auto": self.controller_query("++auto"),
            "eos": self.controller_query("++eos"),
        }

    def set_address(self, gpib_address: int) -> None:
        self.gpib_address = gpib_address
        self._write_controller(f"++addr {self.gpib_address}")
        time.sleep(0.05)

    def serial_poll(self, gpib_address: int | None = None) -> str:
        if gpib_address is not None:
            self.set_address(gpib_address)
        return self.controller_query("++spoll")

    def query_measurement(self, get_channel: int, expected_kind: str) -> Measurement:
        if self.auto == 1:
            last_error: Exception | None = None
            for attempt in range(3):
                self.serial.reset_input_buffer()
                self._write_instrument(f"GET {get_channel}")
                try:
                    return parse_measurement(self._read_response(), expected_kind)
                except LR700ReadError as exc:
                    last_error = exc
                    time.sleep(0.1)
            if last_error is not None:
                raise last_error
            raise LR700ReadError("No response received from LR700")

        last_error: Exception | None = None

        for read_command in ("++read eoi", "++read 10", "++read"):
            self.serial.reset_input_buffer()
            self._write_instrument(f"GET {get_channel}")
            time.sleep(0.1)
            self._write_controller(read_command)
            try:
                return parse_measurement(self._read_response(), expected_kind)
            except LR700ReadError as exc:
                last_error = exc
                time.sleep(0.05)

        if last_error is not None:
            raise last_error
        raise LR700ReadError("No response received from LR700")

    def read_r(self) -> Measurement:
        return self.query_measurement(get_channel=0, expected_kind="R")

    def read_x(self) -> Measurement:
        return self.query_measurement(get_channel=1, expected_kind="X")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read R and X from an LR700 through a Prologix GPIB-USB controller."
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit",
    )
    parser.add_argument(
        "--scan-prologix",
        action="store_true",
        help="Probe FTDI serial ports with ++ver and report which ones respond like a Prologix controller",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"Serial port for the Prologix adapter (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--gpib-address",
        type=int,
        default=DEFAULT_GPIB_ADDRESS,
        help=f"LR700 GPIB address (default: {DEFAULT_GPIB_ADDRESS})",
    )
    parser.add_argument("--baudrate", type=int, default=9600, help="Prologix serial baud rate")
    parser.add_argument("--timeout", type=float, default=1.0, help="Serial timeout in seconds")
    parser.add_argument(
        "--auto",
        type=int,
        choices=(0, 1),
        default=DEFAULT_AUTO_MODE,
        help=f"Prologix auto read-after-write mode (default: {DEFAULT_AUTO_MODE})",
    )
    parser.add_argument(
        "--eos",
        type=int,
        choices=(0, 1, 2, 3),
        default=2,
        help="Prologix EOS setting for instrument commands: 0=CRLF, 1=CR, 2=LF, 3=None",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Only query the Prologix controller and print its configuration",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print Prologix controller details before taking measurements",
    )
    parser.add_argument(
        "--raw-controller-query",
        help="Send one controller command such as ++ver and print the raw response",
    )
    parser.add_argument(
        "--scan-addresses",
        action="store_true",
        help="Scan GPIB addresses 1-30 using ++spoll and an LR700 GET 0 read",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of read cycles to perform. Use 0 for continuous reads.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Delay between read cycles in seconds",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    iteration = 0

    if args.list_ports:
        for port in list_ports.comports():
            details = ", ".join(
                value
                for value in (port.description, port.manufacturer, port.hwid)
                if value
            )
            print(f"{port.device}: {details}")
        return 0

    if args.scan_prologix:
        matches = 0
        for port in list_ports.comports():
            if "FTDI" not in (port.manufacturer or "") and "USB VID:PID=0403:6001" not in (port.hwid or ""):
                continue

            print(f"Probing {port.device}...")
            try:
                bridge = PrologixLR700(
                    port=port.device,
                    gpib_address=args.gpib_address,
                    baudrate=args.baudrate,
                    timeout=args.timeout,
                    eos=args.eos,
                    auto=args.auto,
                )
                try:
                    response = bridge.controller_query("++ver")
                finally:
                    bridge.close()
            except (serial.SerialException, LR700ReadError) as exc:
                print(f"  no response: {exc}")
                continue

            print(f"  response: {response}")
            matches += 1

        if matches == 0:
            print("No Prologix-style controller responded to ++ver.")
            return 1
        return 0

    try:
        if args.raw_controller_query:
            bridge = PrologixLR700(
                port=args.port,
                gpib_address=args.gpib_address,
                baudrate=args.baudrate,
                timeout=args.timeout,
                eos=args.eos,
                auto=args.auto,
            )
            try:
                print(bridge.controller_query(args.raw_controller_query))
                return 0
            finally:
                bridge.close()

        with PrologixLR700(
            port=args.port,
            gpib_address=args.gpib_address,
            baudrate=args.baudrate,
            timeout=args.timeout,
            eos=args.eos,
            auto=args.auto,
        ) as bridge:
            if args.scan_addresses:
                hits = 0
                for address in range(1, 31):
                    print(f"Address {address}:")
                    try:
                        print(f"  spoll: {bridge.serial_poll(address)}")
                    except LR700ReadError as exc:
                        print(f"  spoll: no response ({exc})")

                    try:
                        bridge.set_address(address)
                        measurement = bridge.read_r()
                        print(
                            f"  GET 0: {measurement.value_ohms:.9g} ohm "
                            f"(raw: {measurement.raw_value} {measurement.raw_unit})"
                        )
                        hits += 1
                    except LR700ReadError as exc:
                        print(f"  GET 0: no response ({exc})")

                if hits == 0:
                    print("No LR700-style response found on addresses 1-30.")
                    return 1
                return 0

            if args.verbose or args.probe_only:
                probe = bridge.probe()
                for key, value in probe.items():
                    print(f"{key}: {value}")
                print("---")

            if args.probe_only:
                return 0

            while args.count == 0 or iteration < args.count:
                r_value = bridge.read_r()
                x_value = bridge.read_x()
                print(
                    f"R = {r_value.value_ohms:.9g} ohm "
                    f"(raw: {r_value.raw_value} {r_value.raw_unit})"
                )
                print(
                    f"X = {x_value.value_ohms:.9g} ohm "
                    f"(raw: {x_value.raw_value} {x_value.raw_unit})"
                )
                print("---")
                iteration += 1
                if args.count == 0 or iteration < args.count:
                    time.sleep(args.interval)
    except (serial.SerialException, LR700ReadError) as exc:
        print(f"Read failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
