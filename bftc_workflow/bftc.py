"""
Bluefors Temperature Controller (BFTC) HTTP API wrapper.

Usage
-----
    from bftc_workflow.bftc import BFTC

    bf = BFTC("169.169.10.10:5001")
    t  = bf.get_temperature(channel=6)   # K
    bf.set_heater_power(power_W=1e-6)
    bf.solo_channel(6)                    # pause all others
    bf.enable_all_channels()
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

os.environ.setdefault("NO_PROXY", "169.169.10.10,132.163.157.220,localhost,127.0.0.1")

# Channels present on the Myriad/Miniebit scanner
ALL_CHANNELS = (1, 2, 5, 6)


class BFTC:
    """Thin wrapper around the Bluefors Temperature Controller REST API."""

    def __init__(self, ip: str = "169.169.10.10:5001", timeout: float = 5.0):
        self.ip = ip
        self.timeout = timeout
        self._server_utc_offset: Optional[timedelta] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_server_time(self, data: dict) -> None:
        """Extract server datetime from response payload and update UTC offset."""
        if isinstance(data, dict):
            dt_str = data.get("datetime")
            if dt_str and isinstance(dt_str, str):
                try:
                    clean_dt = dt_str.replace("Z", "+00:00")
                    server_dt = datetime.fromisoformat(clean_dt)
                    local_utc = datetime.now(timezone.utc)
                    self._server_utc_offset = server_dt - local_utc
                except Exception:
                    pass

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"http://{self.ip}/{endpoint}"
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        self._sync_server_time(data)
        return data

    def _get(self, endpoint: str) -> dict:
        url = f"http://{self.ip}/{endpoint}"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        self._sync_server_time(data)
        return data

    def _time_window(self, minutes_ago: float) -> tuple[str, str]:
        """Return (start_time_str, stop_time_str) synchronized to the BFTC server clock.

        Includes a forward buffer on stop_time so query windows never truncate
        incoming live data due to timezone differences or clock drift.
        """
        if self._server_utc_offset is not None:
            server_now = datetime.now(timezone.utc) + self._server_utc_offset
        else:
            try:
                self._get("channel/measurement/latest")
            except Exception:
                pass
            if self._server_utc_offset is not None:
                server_now = datetime.now(timezone.utc) + self._server_utc_offset
            else:
                server_now = datetime.now(timezone.utc)

        start = server_now - timedelta(minutes=minutes_ago)
        stop = server_now + timedelta(minutes=10)
        fmt = "%Y-%m-%dT%H:%M:%S"
        return start.strftime(fmt), stop.strftime(fmt)

    # ------------------------------------------------------------------
    # Channel (thermometer) control
    # ------------------------------------------------------------------

    def set_channel_active(self, channel: int, active: bool) -> dict:
        """Enable or disable a thermometer channel on the scanner."""
        return self._post("channel/update", {"active": active, "channel_nr": channel})

    def solo_channel(self, channel: int, all_channels: tuple = ALL_CHANNELS) -> None:
        """Disable every channel except *channel* to maximise scan rate."""
        for ch in all_channels:
            self.set_channel_active(ch, ch == channel)

    def enable_all_channels(self, all_channels: tuple = ALL_CHANNELS) -> None:
        """Re-enable all thermometer channels."""
        for ch in all_channels:
            self.set_channel_active(ch, True)

    # ------------------------------------------------------------------
    # Temperature reading
    # ------------------------------------------------------------------

    def get_temperature_history(
        self,
        channel: int,
        minutes: float = 2.0,
    ) -> list[float]:
        """Return a list of temperatures (K) logged in the last *minutes* minutes.

        Returns an empty list if the channel was disabled during that window.
        """
        start_str, stop_str = self._time_window(minutes)
        payload = {
            "channel_nr": channel,
            "fields": ["temperature"],
            "start_time": start_str,
            "stop_time": stop_str,
        }
        data = self._post("channel/historical-data", payload)
        return data.get("measurements", {}).get("temperature", [])

    def get_temperature(self, channel: int, minutes: float = 2.0) -> Optional[float]:
        """Return the most recent temperature (K) for *channel*, or None if unavailable."""
        # 1. Fast path: check if latest measurement matches the requested channel
        try:
            latest = self._get("channel/measurement/latest")
            if latest.get("channel_nr") == channel and latest.get("temperature") is not None:
                return float(latest["temperature"])
        except Exception:
            pass

        # 2. Historical query path
        history = self.get_temperature_history(channel, minutes)
        return history[-1] if history else None

    def read_latest_temperature(
        self,
        channel: int,
        timeout_s: float = 60.0,
        poll_interval: float = 2.0,
    ) -> float:
        """Poll /channel/measurement/latest until *channel* is the active one.

        With multiple channels enabled the scanner cycles ~11 s per channel.
        Raises TimeoutError if the channel does not appear within *timeout_s*.
        """
        deadline = time.time() + timeout_s
        while True:
            data = self._get("channel/measurement/latest")
            if data.get("channel_nr") == channel:
                return data["temperature"]
            if time.time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for channel {channel} "
                    f"(last saw channel {data.get('channel_nr')})"
                )
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Heater control
    # ------------------------------------------------------------------

    def set_heater_power(self, power_W: float, heater_nr: int = 4) -> dict:
        """Drive the heater in open-loop constant-power mode."""
        return self._post(
            "heater/update",
            {"active": True, "pid_mode": 0, "power": power_W, "heater_nr": heater_nr},
        )

    def set_heater_pid(
        self, setpoint_K: float, heater_nr: int = 4, channel: Optional[int] = None
    ) -> dict:
        """Engage the BFTC hardware PID to regulate at *setpoint_K*.

        Optionally solo *channel* first so the PID reads the right thermometer.
        """
        if channel is not None:
            self.solo_channel(channel)
        return self._post(
            "heater/update",
            {
                "active": True,
                "pid_mode": 1,
                "setpoint": setpoint_K,
                "heater_nr": heater_nr,
            },
        )

    def stop_heater(self, heater_nr: int = 4) -> None:
        """Zero the heater power and deactivate the channel."""
        self.set_heater_power(0.0, heater_nr)
        self._post("heater/update", {"active": False, "channel_nr": heater_nr})

    # ------------------------------------------------------------------
    # Heater power history
    # ------------------------------------------------------------------

    def get_heater_power_history(
        self,
        heater_nr: int = 4,
        minutes: float = 2.0,
    ) -> list[float]:
        """Return a list of heater power values (W) from the last *minutes* minutes.

        Returns an empty list when the heater is off (the API returns no data).
        """
        start_str, stop_str = self._time_window(minutes)
        payload = {
            "heater_nr": heater_nr,
            "fields": ["power"],
            "start_time": start_str,
            "stop_time": stop_str,
        }
        data = self._post("heater/historical-data", payload)
        return data.get("measurements", {}).get("power", [])

    def get_latest_heater_power_uW(self, heater_nr: int = 4, minutes: float = 2.0) -> Optional[float]:
        """Return the most recent heater power in µW, or None if unavailable."""
        history = self.get_heater_power_history(heater_nr, minutes)
        return history[-1] * 1e6 if history else None


# ---------------------------------------------------------------------------
# Convenience: default instance pointing at the lab instrument
# Callers can import this and override .ip if needed.
# ---------------------------------------------------------------------------
default = BFTC()
