import threading
import time
import sys
from pathlib import Path

# Add workflows to path
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR / "bftc_workflow") not in sys.path:
    sys.path.insert(0, str(THIS_DIR / "bftc_workflow"))
if str(THIS_DIR / "lakeshore_workflow") not in sys.path:
    sys.path.insert(0, str(THIS_DIR / "lakeshore_workflow"))

import bftc
from lakeshore_370_temperature_test import LakeShore370
from prologix_lr700_test import PrologixLR700

class RampController:
    def __init__(self):
        self.state = "IDLE"  # IDLE, RAMPING, PAUSED, ERROR
        self.instrument = None
        self.thread = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_temp = None
        self.current_power_or_setpoint = None
        self.message = "Ready"

        # Logging state
        self.log_thread = None
        self.log_stop_event = threading.Event()
        self.log_state = "IDLE"
        self.log_message = "Logger Idle"

        # Bluefors state
        self._bf_target_temp = None
        self._bf_step = None
        self._bf_sleep_time = None
        self._bf_max_power = None
        self._bf_direction = None

        # Lakeshore state
        self._ls_bridge = None

    def start_logging(self, instrument, save_dir, prefix, interval, **kwargs):
        if self.log_state == "LOGGING":
            return False, "Already logging"

        self.log_stop_event.clear()
        self.log_state = "LOGGING"
        self.log_message = "Starting logger..."

        if instrument == "Bluefors":
            import bftc_workflow.data_logger as bf_logger
            
            if 'bf_ip' in kwargs:
                bftc.ip = kwargs['bf_ip']
            
            # Pass the channel number directly — the logger handles int channels
            # via get_temp_history(ch=...)
            ch = kwargs.get('bf_source', 6)
            try:
                ch = int(ch)
            except:
                pass
            source_choice = str(ch)

            self.log_thread = threading.Thread(
                target=bf_logger.main,
                kwargs={
                    "save_dir": save_dir, "device_name": prefix,
                    "temp_source_choice": source_choice,
                    "logging_interval_s": interval,
                    "stop_event": self.log_stop_event,
                    "lr700_adapter": kwargs.get('lr700_adapter', 'prologix'),
                    "lr700_port": kwargs.get('lr700_port', 'COM14'),
                    "lr700_gpib": kwargs.get('lr700_gpib', 17),
                },
                daemon=True
            )
        else:
            import lakeshore_workflow.data_logger as ls_logger
            self.log_thread = threading.Thread(
                target=ls_logger.main,
                kwargs={
                    "save_dir": save_dir, "device_name": prefix,
                    "logging_interval_s": interval,
                    "stop_event": self.log_stop_event,
                    "ls370_port": kwargs.get('ls_port', 'COM6'),
                    "ls370_channel": kwargs.get('ls_channel', 4),
                    "ls370_baudrate": kwargs.get('ls_baudrate', 9600)
                },
                daemon=True
            )
        
        self.log_thread.start()
        self.log_message = "Logging active"
        return True, "Started"

    def stop_logging(self):
        if self.log_state != "LOGGING":
            return False, "Not logging"
        self.log_stop_event.set()
        self.log_state = "IDLE"
        self.log_message = "Logger Stopped"
        return True, "Stopped"

    def check_connection(self, instrument, **kwargs):
        res_parts = []
        lr700_adapter = kwargs.get('lr700_adapter', 'prologix')
        lr700_port = kwargs.get('lr700_port', 'COM14')
        lr700_gpib = kwargs.get('lr700_gpib', 17)

        # 1. Check LR700
        try:
            if lr700_adapter == 'prologix':
                with PrologixLR700(port=lr700_port, gpib_address=lr700_gpib) as lr:
                    r = lr.read_r().value_ohms
                    res_parts.append(f"LR700: {r*1000:.2f} mOhm.")
            else:
                import lr700 as pyvisa_lr700
                r = pyvisa_lr700.read_ohm(lr700_gpib)
                if r is not None:
                    res_parts.append(f"LR700: {r*1000:.2f} mOhm.")
                else:
                    res_parts.append("LR700: Failed.")
        except Exception as e:
            res_parts.append(f"LR700: Error ({e}).")

        # 2. Check Instrument
        if instrument == "Bluefors":
            source = kwargs.get('bf_source', 6)
            if 'bf_ip' in kwargs:
                bftc.ip = kwargs['bf_ip']
            temp = self._get_bf_temp(source)
            if temp is not None:
                res_parts.append(f"BFTC: {temp*1000:.2f} mK.")
            else:
                res_parts.append("BFTC: Failed.")
        else:
            ls_port = kwargs.get('ls_port', 'COM6')
            baud = kwargs.get('ls_baudrate', 9600)
            ch = kwargs.get('ls_channel', 4)
            try:
                with LakeShore370(port=ls_port, baudrate=baud) as ls:
                    temp = ls.temperature_kelvin(ch)
                    res_parts.append(f"LS370: {temp*1000:.2f} mK.")
            except Exception as e:
                res_parts.append(f"LS370: Error ({e}).")
                
        return " | ".join(res_parts)

    def start_bluefors(self, bf_ip, source, target_temp, init_power, step, sleep_time, timeout, max_power):
        if self.state == "RAMPING":
            return False, "Already ramping — pause first to change parameters"
        
        # If paused, stop the old ramp thread gracefully (don't zero heater)
        if self.state == "PAUSED":
            self.stop_event.set()
            self.pause_event.clear()
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3)
            self.message = "Restarting with new parameters..."

        bftc.ip = bf_ip
        self.instrument = "Bluefors"
        self.state = "RAMPING"
        self.stop_event.clear()
        self.pause_event.clear()
        self.message = "Starting Bluefors ramp..."

        self._bf_target_temp = target_temp
        self._bf_step = step
        self._bf_sleep_time = sleep_time
        self._bf_max_power = max_power

        self.thread = threading.Thread(
            target=self._run_bluefors,
            args=(source, init_power, timeout),
            daemon=True
        )
        self.thread.start()
        return True, "Started"

    def start_lakeshore(self, port, baudrate, channel, target_setpoint, ramp_rate):
        if self.state in ["RAMPING", "PAUSED"]:
            return False, "Already running"

        self.instrument = "LakeShore"
        self.state = "RAMPING"
        self.stop_event.clear()
        self.pause_event.clear()
        self.message = f"Connecting to LakeShore on {port}..."

        self.thread = threading.Thread(
            target=self._run_lakeshore,
            args=(port, baudrate, channel, target_setpoint, ramp_rate),
            daemon=True
        )
        self.thread.start()
        return True, "Started"

    def pause(self):
        if self.state != "RAMPING":
            return False, "Not ramping"
        
        self.state = "PAUSED"
        self.pause_event.set()
        self.message = "Paused"

        if self.instrument == "LakeShore" and self._ls_bridge:
            try:
                # Set setpoint to current temperature to hold it
                temp = self._ls_bridge.temperature_kelvin(self._ls_channel)
                self._ls_bridge.set_setpoint_kelvin(temp)
                self.message = f"Paused. Holding at {temp*1000:.2f} mK"
            except Exception as e:
                self.message = f"Error pausing LakeShore: {e}"

        return True, "Paused"

    def resume(self):
        if self.state != "PAUSED":
            return False, "Not paused"

        self.state = "RAMPING"
        self.pause_event.clear()
        self.message = "Resumed"

        if self.instrument == "LakeShore" and self._ls_bridge:
            try:
                # Resume original target setpoint
                self._ls_bridge.set_setpoint_kelvin(self._ls_target_setpoint)
                self.message = "Resumed LakeShore ramp."
            except Exception as e:
                self.message = f"Error resuming LakeShore: {e}"

        return True, "Resumed"

    def stop(self):
        self.state = "IDLE"
        self.stop_event.set()
        self.pause_event.clear()
        self.message = "Stopped"

        if self.instrument == "Bluefors":
            try:
                bftc.set_heaterpower(0.0)
            except:
                pass
        
        return True, "Stopped"

    def _get_bf_temp(self, source):
        try:
            ch = int(source)
            history = bftc.get_temp_history(ch=ch, start_minutes_ago=2, stop_minutes_ago=0)
            temps = history.get("measurements", {}).get("temperature", [])
            if temps:
                return temps[-1]
            return None
        except Exception as e:
            print(f"Error getting temp for channel {source}: {e}")
            return None

    def _run_bluefors(self, source, init_power, timeout):
        try:
            p_set = init_power
            start_time = time.time()
            
            base_temp = self._get_bf_temp(source)
            self.current_temp = base_temp
            self.current_power_or_setpoint = p_set
            
            if base_temp < self._bf_target_temp:
                direction = "up"
            else:
                direction = "down"

            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(1)
                    continue

                if time.time() - start_time > timeout:
                    self.state = "ERROR"
                    self.message = "Timeout reached"
                    bftc.set_heaterpower(0.0)
                    break

                base_temp = self._get_bf_temp(source)
                self.current_temp = base_temp
                self.current_power_or_setpoint = p_set
                self.message = f"Ramping {direction}... Temp: {base_temp*1000:.2f} mK, Power: {p_set*1e6:.2f} uW"

                if direction == "up" and base_temp >= self._bf_target_temp:
                    self.state = "IDLE"
                    self.message = "Target reached"
                    break
                elif direction == "down" and base_temp <= self._bf_target_temp:
                    self.state = "IDLE"
                    self.message = "Target reached"
                    break

                if direction == "up":
                    p_set += self._bf_step
                else:
                    p_set -= self._bf_step

                p_set = max(0, min(p_set, self._bf_max_power))
                p_set = round(p_set, 9)
                
                bftc.set_heaterpower(p_set)
                
                # Sleep in small chunks to remain responsive to stop/pause
                sleep_elapsed = 0
                while sleep_elapsed < self._bf_sleep_time and not self.stop_event.is_set() and not self.pause_event.is_set():
                    time.sleep(1)
                    sleep_elapsed += 1

        except Exception as e:
            self.state = "ERROR"
            self.message = f"Bluefors error: {e}"
            try:
                bftc.set_heaterpower(0.0)
            except:
                pass

    def _run_lakeshore(self, port, baudrate, channel, target_setpoint, ramp_rate):
        try:
            self._ls_channel = channel
            self._ls_target_setpoint = target_setpoint
            
            with LakeShore370(port=port, baudrate=baudrate) as bridge:
                self._ls_bridge = bridge
                
                bridge.set_ramp(enabled=True, rate_kelvin_per_minute=ramp_rate)
                bridge.set_setpoint_kelvin(target_setpoint)
                
                while not self.stop_event.is_set():
                    temp = bridge.temperature_kelvin(channel)
                    setpoint = bridge.setpoint_kelvin()
                    self.current_temp = temp
                    self.current_power_or_setpoint = setpoint
                    
                    if not self.pause_event.is_set():
                        self.message = f"Ramping... Temp: {temp*1000:.2f} mK, Setpoint: {setpoint*1000:.2f} mK"
                    
                    time.sleep(2)
                    
        except Exception as e:
            self.state = "ERROR"
            self.message = f"LakeShore error: {e}"
        finally:
            self._ls_bridge = None

    def get_current_heater_power(self):
        """Read the current heater power from BFTC, returns value in Watts or None."""
        try:
            pwr = bftc.get_heaterpower(ch=4, start_minutes_ago=2, stop_minutes_ago=0)
            powers = pwr.get("measurements", {}).get("power", [])
            if powers:
                return powers[-1]  # latest power in Watts
            return None
        except Exception as e:
            print(f"Error reading heater power: {e}")
            return None

    def get_status(self):
        return {
            "state": self.state,
            "instrument": self.instrument,
            "message": self.message,
            "current_temp": self.current_temp,
            "current_power_or_setpoint": self.current_power_or_setpoint
        }

# Global controller instance
controller = RampController()
