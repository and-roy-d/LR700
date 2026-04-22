from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports


DEFAULT_PORT = "COM6"
DEFAULT_CHANNEL = 4
DEFAULT_BAUDRATE = 9600


STATUS_FLAGS = {
    1: "CS OVL",
    2: "VCM OVL",
    4: "VMIX OVL",
    8: "VDIF OVL",
    16: "R OVER",
    32: "R UNDER",
    64: "T OVER",
    128: "T UNDER",
}


class LS370ReadError(RuntimeError):
    pass


@dataclass
class TemperatureReading:
    channel: int
    temperature_kelvin: float
    resistance_ohms: float | None
    status_code: int | None
    status_flags: list[str]


@dataclass
class RampState:
    enabled: bool
    rate_kelvin_per_minute: float
    actively_ramping: bool | None = None


class LakeShore370:
    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = 1.5,
        post_response_delay: float = 0.05,
    ) -> None:
        self.timeout = timeout
        self.post_response_delay = post_response_delay
        self.serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            write_timeout=timeout,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_ODD,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def close(self) -> None:
        self.serial.close()

    def __enter__(self) -> "LakeShore370":
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def query(self, command: str) -> str:
        self.serial.reset_input_buffer()
        self.serial.write(f"{command}\r\n".encode("ascii"))
        self.serial.flush()
        response = self.serial.readline().decode("ascii", errors="replace").strip()
        time.sleep(self.post_response_delay)
        if not response:
            raise LS370ReadError(f"Timed out waiting for response to {command!r}")
        return response

    def identify(self) -> str:
        return self.query("*IDN?")

    def scan_state(self) -> tuple[int, int]:
        response = self.query("SCAN?")
        parts = [part.strip() for part in response.split(",")]
        if len(parts) != 2:
            raise LS370ReadError(f"Unexpected SCAN? response: {response!r}")
        try:
            return int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise LS370ReadError(f"Invalid SCAN? response: {response!r}") from exc

    def temperature_kelvin(self, channel: int) -> float:
        return self._parse_float(self.query(f"RDGK? {channel}"), f"RDGK? {channel}")

    def resistance_ohms(self, channel: int) -> float:
        return self._parse_float(self.query(f"RDGR? {channel}"), f"RDGR? {channel}")

    def status_code(self, channel: int) -> int:
        response = self.query(f"RDGST? {channel}")
        try:
            return int(response)
        except ValueError as exc:
            raise LS370ReadError(f"Invalid RDGST? response: {response!r}") from exc

    def read_temperature(
        self,
        channel: int,
        include_resistance: bool = False,
        include_status: bool = False,
    ) -> TemperatureReading:
        temperature_kelvin = self.temperature_kelvin(channel)
        resistance_ohms = self.resistance_ohms(channel) if include_resistance else None
        status_code = self.status_code(channel) if include_status else None
        status_flags = decode_status_flags(status_code) if status_code is not None else []
        return TemperatureReading(
            channel=channel,
            temperature_kelvin=temperature_kelvin,
            resistance_ohms=resistance_ohms,
            status_code=status_code,
            status_flags=status_flags,
        )

    def setpoint_kelvin(self) -> float:
        return self._parse_float(self.query("SETP?"), "SETP?")

    def set_setpoint_kelvin(self, value_kelvin: float) -> None:
        self._write_command(f"SETP {value_kelvin:.9g}")

    def ramp_state(self) -> RampState:
        response = self.query("RAMP?")
        parts = [part.strip() for part in response.split(",")]
        if len(parts) != 2:
            raise LS370ReadError(f"Unexpected RAMP? response: {response!r}")
        try:
            enabled = bool(int(parts[0]))
            rate = float(parts[1])
        except ValueError as exc:
            raise LS370ReadError(f"Invalid RAMP? response: {response!r}") from exc

        actively_ramping = None
        try:
            actively_ramping = bool(int(self.query("RAMPST?")))
        except LS370ReadError:
            actively_ramping = None

        return RampState(
            enabled=enabled,
            rate_kelvin_per_minute=rate,
            actively_ramping=actively_ramping,
        )

    def set_ramp(self, enabled: bool, rate_kelvin_per_minute: float) -> None:
        self._write_command(f"RAMP {1 if enabled else 0},{rate_kelvin_per_minute:.9g}")

    @staticmethod
    def _parse_float(response: str, command: str) -> float:
        try:
            return float(response)
        except ValueError as exc:
            raise LS370ReadError(f"Invalid response to {command!r}: {response!r}") from exc

    def _write_command(self, command: str) -> None:
        self.serial.reset_input_buffer()
        self.serial.write(f"{command}\r\n".encode("ascii"))
        self.serial.flush()
        time.sleep(self.post_response_delay)


def decode_status_flags(status_code: int | None) -> list[str]:
    if status_code is None:
        return []
    if status_code == 0:
        return []
    return [label for bit, label in STATUS_FLAGS.items() if status_code & bit]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read temperature from a Lake Shore Model 370 over RS-232."
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"Serial port for the Lake Shore 370 (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        choices=(300, 1200, 9600),
        help="LS370 baud rate",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=DEFAULT_CHANNEL,
        help="Measurement channel to query (1-16). If no scanner is installed, use 1.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.5,
        help="Serial timeout in seconds",
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
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Only query *IDN? and exit",
    )
    parser.add_argument(
        "--show-scan",
        action="store_true",
        help="Query and print SCAN? before taking measurements",
    )
    parser.add_argument(
        "--show-resistance",
        action="store_true",
        help="Also query RDGR? for the selected channel",
    )
    parser.add_argument(
        "--show-status",
        action="store_true",
        help="Also query RDGST? and decode any status flags",
    )
    parser.add_argument(
        "--raw-query",
        help="Send one raw query string such as *IDN? or RDGK? 1 and print the response",
    )
    parser.add_argument(
        "--show-control",
        action="store_true",
        help="Print setpoint and ramp state before taking measurements",
    )
    parser.add_argument(
        "--setpoint-k",
        type=float,
        help="Set the control setpoint in Kelvin before reading temperatures",
    )
    parser.add_argument(
        "--ramp-enabled",
        type=int,
        choices=(0, 1),
        help="Set ramp mode off/on before reading temperatures",
    )
    parser.add_argument(
        "--ramp-rate-k-per-min",
        type=float,
        help="Set ramp rate in Kelvin per minute before reading temperatures",
    )
    return parser


def print_reading(reading: TemperatureReading) -> None:
    print(f"Channel {reading.channel}: {reading.temperature_kelvin:.9g} K")
    if reading.resistance_ohms is not None:
        print(f"Resistance: {reading.resistance_ohms:.9g} ohm")
    if reading.status_code is not None:
        if reading.status_flags:
            print(f"Status: {reading.status_code} ({', '.join(reading.status_flags)})")
        else:
            print(f"Status: {reading.status_code} (valid reading)")
    print("---")


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

    if not 1 <= args.channel <= 16:
        print("Read failed: --channel must be in the range 1..16")
        return 1

    try:
        with LakeShore370(
            port=args.port,
            baudrate=args.baudrate,
            timeout=args.timeout,
        ) as bridge:
            if args.raw_query:
                print(bridge.query(args.raw_query))
                return 0

            ident = bridge.identify()
            print(f"IDN: {ident}")

            if args.setpoint_k is not None:
                bridge.set_setpoint_kelvin(args.setpoint_k)
            if args.ramp_enabled is not None or args.ramp_rate_k_per_min is not None:
                current_ramp = bridge.ramp_state()
                bridge.set_ramp(
                    enabled=current_ramp.enabled if args.ramp_enabled is None else bool(args.ramp_enabled),
                    rate_kelvin_per_minute=(
                        current_ramp.rate_kelvin_per_minute
                        if args.ramp_rate_k_per_min is None
                        else args.ramp_rate_k_per_min
                    ),
                )

            if args.show_control:
                setpoint_kelvin = bridge.setpoint_kelvin()
                ramp = bridge.ramp_state()
                print(f"SETP: {setpoint_kelvin:.9g} K")
                print(
                    f"RAMP: enabled={int(ramp.enabled)}, "
                    f"rate={ramp.rate_kelvin_per_minute:.9g} K/min, "
                    f"active={ramp.actively_ramping}"
                )
                print("---")

            if args.probe_only:
                return 0

            if args.show_scan:
                scan_channel, autoscan = bridge.scan_state()
                print(f"SCAN: channel={scan_channel}, autoscan={autoscan}")
                print("---")

            while args.count == 0 or iteration < args.count:
                reading = bridge.read_temperature(
                    channel=args.channel,
                    include_resistance=args.show_resistance,
                    include_status=args.show_status,
                )
                print_reading(reading)
                iteration += 1
                if args.count == 0 or iteration < args.count:
                    time.sleep(args.interval)
    except (serial.SerialException, LS370ReadError) as exc:
        print(f"Read failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
