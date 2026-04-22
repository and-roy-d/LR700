import requests
import time
from datetime import datetime, timedelta


class BlueforsController:
    """A class to control the Bluefors Temperature Controller via its HTTP API."""

    def __init__(self, ip_address: str):
        self.ip = ip_address
        self.base_url = f"http://{self.ip}"
        self.time_offset = self._calculate_time_offset()
        print(f"Connected to Bluefors Controller at {self.ip}")
        print(f"Initial time offset to controller is {self.time_offset.total_seconds():.2f} seconds.")

    def _post_request(self, endpoint, payload, wait_for_response=True):
        """A robust POST request handler with detailed error logging."""
        try:
            if not wait_for_response:
                # Fire-and-forget: send command but don't wait for the slow reply.
                # Use a very short timeout that we expect to trigger.
                requests.post(f"{self.base_url}{endpoint}", timeout=0.5, json=payload)
                return None  # Return immediately
            else:
                # Normal operation: send command and wait for a response.
                response = requests.post(f"{self.base_url}{endpoint}", timeout=30, json=payload)
                response.raise_for_status()
                return response.json()
        except requests.exceptions.Timeout:
            # This is expected for fire-and-forget commands.
            if not wait_for_response:
                return None
            else:
                print("\n--- ERROR: Request timed out ---")
                raise
        except requests.exceptions.HTTPError as err:
            print("\n--- ERROR ---")
            print(f"Failed to send command to {err.request.url}")
            print(f"Payload sent: {err.request.body.decode()}")
            print(f"Error from controller: {err.response.text}")
            print("-------------")
            raise err

    def _calculate_time_offset(self) -> timedelta:
        try:
            response = requests.get(f"{self.base_url}/system", timeout=10)
            response.raise_for_status()
            response_datetime = datetime.strptime(response.json()["datetime"], '%Y-%m-%dT%H:%M:%S.%fZ')
            return response_datetime - datetime.now()
        except requests.RequestException:
            return timedelta(0)

    def set_temperature_pid(self, temp_K: float, heater_nr: int = 4, algorithm: int = 2):
        """Activates PID control and sets the temperature. This is a complete command."""
        payload = {
            "active": True,
            "pid_mode": 1,
            "control_algorithm": algorithm,
            "setpoint": temp_K,
            "heater_nr": heater_nr
        }
        print(f"Setting heater {heater_nr} to {temp_K * 1000:.2f} mK using PID algorithm {algorithm}")
        # Use fire-and-forget for this slow command
        self._post_request("/heater/update", payload, wait_for_response=False)

    def set_heater_power(self, power_W: float, heater_nr: int = 4):
        """Sets the heater to a fixed power output (manual mode)."""
        payload = {
            "active": True,
            "pid_mode": 0,
            "power": power_W,
            "heater_nr": heater_nr
        }
        self._post_request("/heater/update", payload)

    def stop_regulation(self, heater_nr: int = 4):
        """Stops all regulation by setting heater power to 0 and deactivating."""
        print("Setting heater to manual mode with 0 W power...")
        self.set_heater_power(power_W=0, heater_nr=heater_nr)
        time.sleep(1)
        print("Deactivating heater...")
        payload = {"active": False, "heater_nr": heater_nr}
        self._post_request("/heater/update", payload)
        print(f"Regulation stopped on heater {heater_nr}.")

    def get_latest_temperature(self, ch: int) -> float:
        """Gets the single latest temperature reading for a channel."""
        current_time = datetime.now()
        start_time = current_time - timedelta(minutes=1) + self.time_offset
        end_time = current_time + self.time_offset
        payload = {"channel_nr": ch, "fields": ["temperature"],
                   "start_time": start_time.strftime(r'%Y-%m-%dT%H:%M:%S'),
                   "stop_time": end_time.strftime(r'%Y-%m-%dT%H:%M:%S')}
        data = self._post_request("/channel/historical-data", payload)

        if data and data.get('measurements') and data['measurements'].get('temperature'):
            return data['measurements']['temperature'][-1]
        else:
            print(f"Warning: No recent temperature data found for channel {ch}.")
            return -1.0


# =============================================================================
# Example Usage
# =============================================================================
if __name__ == "__main__":
    bf = None
    try:
        bf = BlueforsController("132.163.157.220:5001")

        control_thermometer_channel = 7
        setpoints_mK = [25, 30, 35, 30, 25]

        print("\n--- Starting Temperature Stepping with Adaptive PID ---")

        for setpoint in setpoints_mK:
            temp_K = setpoint / 1000.0

            bf.set_temperature_pid(
                temp_K=temp_K,
                heater_nr=4,
                algorithm=2
            )

            print(f"Command sent. Waiting 90 seconds for temperature to settle...")
            time.sleep(90)

            current_temp = bf.get_latest_temperature(ch=control_thermometer_channel)
            print(f"Setpoint: {setpoint:.2f} mK -> Current Temp: {current_temp * 1000:.2f} mK\n")

    except requests.RequestException:
        print("Process aborted due to communication error.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
    finally:
        if bf:
            print("--- Stopping Regulation ---")
            bf.stop_regulation(heater_nr=4)