import threading
import time
import sys
import numpy as np
from pathlib import Path
from bftc_workflow.bftc import BFTC

# Add workflows to path
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR / "bftc_workflow") not in sys.path:
    sys.path.insert(0, str(THIS_DIR / "bftc_workflow"))
if str(THIS_DIR / "lakeshore_workflow") not in sys.path:
    sys.path.insert(0, str(THIS_DIR / "lakeshore_workflow"))

import bftc
from lakeshore_370_temperature_test import LakeShore370
from prologix_lr700_test import PrologixLR700

DEFAULT_LR700_PORT = "COM14" if sys.platform.startswith("win") else "/dev/ttyUSB0"
DEFAULT_LS370_PORT = "COM6" if sys.platform.startswith("win") else "/dev/ttyUSB1"

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
        
        # Active settings readback
        self.current_control_mode = None
        self.current_heater_range = None
        self.current_pid = None

        # Logging state
        self.log_thread = None
        self.log_stop_event = threading.Event()
        self.log_state = "IDLE"
        self.log_message = "Logger Idle"

        # Bluefors/Myriad state
        self._bf_target_temp = None
        self._bf_step = None
        self._bf_sleep_time = None
        self._bf_max_power = None

        # Lakeshore active bridge reference
        self._ls_bridge = None

    def start_logging(self, instrument, save_dir, prefix, interval, **kwargs):
        if self.log_state == "LOGGING":
            return False, "Already logging"

        self.log_stop_event.clear()
        self.log_state = "LOGGING"
        self.log_message = "Starting logger..."

        def log_runner(target_func, func_kwargs):
            try:
                target_func(**func_kwargs)
            except Exception as e:
                self.log_message = f"Logger Error: {e}"
            finally:
                self.log_state = "IDLE"
                if "Stopped" not in self.log_message and "Error" not in self.log_message:
                    self.log_message = "Logger Stopped"

        if instrument == "Myriad/Miniebit":
            import bftc_workflow.data_logger as bf_logger

            ch = kwargs.get('bf_source', 6)
            try:
                ch = int(ch)
            except:
                pass
            source_choice = str(ch)

            self.log_thread = threading.Thread(
                target=log_runner,
                args=(bf_logger.main, {
                    "save_dir": save_dir, "device_name": prefix,
                    "temp_source_choice": source_choice,
                    "logging_interval_s": interval,
                    "stop_event": self.log_stop_event,
                    "lr700_adapter": kwargs.get('lr700_adapter', 'prologix'),
                    "lr700_port": kwargs.get('lr700_port', DEFAULT_LR700_PORT),
                    "lr700_gpib": kwargs.get('lr700_gpib', 17),
                    "bf_ip": kwargs.get('bf_ip'),
                }),
                daemon=True
            )
        else:
            import lakeshore_workflow.data_logger as ls_logger
            self.log_thread = threading.Thread(
                target=log_runner,
                args=(ls_logger.main, {
                    "save_dir": save_dir, "device_name": prefix,
                    "logging_interval_s": interval,
                    "stop_event": self.log_stop_event,
                    "ls370_port": kwargs.get('ls_port', DEFAULT_LS370_PORT),
                    "ls370_channel": kwargs.get('ls_channel', 5),
                    "ls370_baudrate": kwargs.get('ls_baudrate', 9600),
                    "ls370_gpib_address": kwargs.get('ls_gpib', None),
                    "lr700_adapter": kwargs.get('lr700_adapter', 'prologix'),
                    "lr700_port": kwargs.get('lr700_port', DEFAULT_LR700_PORT),
                    "lr700_gpib_address": kwargs.get('lr700_gpib', 17),
                    "log_bf_power": False,
                    "bf_ip": None,
                    "log_ls_open_loop_power": (instrument == "2120 OG"),
                    "heater_resistance": kwargs.get('heater_resistance', 120.0),
                }),
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
        lr700_port = kwargs.get('lr700_port', DEFAULT_LR700_PORT)
        lr700_gpib = kwargs.get('lr700_gpib', 17)

        # 1. Check LR700
        if self.log_state == "LOGGING":
            res_parts.append("LR700: Active (Logging)")
        else:
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
            except PermissionError:
                res_parts.append("LR700: Busy (Access Denied / Already in use)")
            except Exception as e:
                res_parts.append(f"LR700: Error ({e}).")

        # 2. Check Instrument
        if instrument == "Myriad/Miniebit":
            source = kwargs.get('bf_source', 6)
            bf_ip = kwargs.get('bf_ip')
            temp = self._get_bf_temp(source, bf_ip)
            if temp is not None:
                res_parts.append(f"BFTC: {temp*1000:.2f} mK.")
            else:
                res_parts.append("BFTC: Failed.")
        elif instrument == "KPAC":
            if self.log_state == "LOGGING":
                res_parts.append("LS370: Active (Logging)")
            elif self.state in ["RAMPING", "PAUSED"]:
                t_str = f"{self.current_temp*1000:.2f} mK" if self.current_temp is not None else "Unknown"
                res_parts.append(f"LS370: Active (Ramping) | Temp: {t_str} | {self.current_control_mode} | Htr: {self.current_heater_range} | PID: {self.current_pid}")
            else:
                ls_port = kwargs.get('ls_port', DEFAULT_LS370_PORT)
                baud = kwargs.get('ls_baudrate', 9600)
                ch = kwargs.get('ls_channel', 5)
                ls_gpib = kwargs.get('ls_gpib', None)
                try:
                    with LakeShore370(port=ls_port, baudrate=baud, gpib_address=ls_gpib) as ls:
                        temp = ls.temperature_kelvin(ch)
                        cmode = ls.control_mode()
                        hrng = ls.heater_range()
                        p_val, i_val, d_val = ls.pid_parameters()
                        
                        cmode_str = {1: "PID", 3: "Open Loop"}.get(cmode, f"Mode {cmode}")
                        hrng_str = {0: "Off", 1: "31.6uA", 2: "100uA", 3: "316uA", 4: "1mA", 5: "3.16mA", 6: "10mA", 7: "31.6mA", 8: "100mA"}.get(hrng, f"Range {hrng}")
                        
                        res_parts.append(f"LS370: {temp*1000:.2f} mK | {cmode_str} | Htr: {hrng_str} | PID: {p_val}/{i_val}/{d_val}")
                except PermissionError:
                    res_parts.append("LS370: Busy (Access Denied / Already in use)")
                except Exception as e:
                    res_parts.append(f"LS370: Error ({e}).")
        elif instrument == "2120 OG":
            if self.log_state == "LOGGING":
                res_parts.append("LS370: Active (Logging)")
            elif self.state in ["RAMPING", "PAUSED"]:
                t_str = f"{self.current_temp*1000:.2f} mK" if self.current_temp is not None else "Unknown"
                res_parts.append(f"LS370: Active (Ramping) | Temp: {t_str}")
            else:
                ls_port = kwargs.get('ls_port', DEFAULT_LS370_PORT)
                baud = kwargs.get('ls_baudrate', 9600)
                ch = kwargs.get('ls_channel', 5)
                ls_gpib = kwargs.get('ls_gpib', None)
                try:
                    with LakeShore370(port=ls_port, baudrate=baud, gpib_address=ls_gpib) as ls:
                        temp = ls.temperature_kelvin(ch)
                        hrng = ls.heater_range()
                        mout_str = ls.query("MOUT?")
                        hrng_str = {0: "Off", 1: "31.6uA", 2: "100uA", 3: "316uA", 4: "1mA", 5: "3.16mA", 6: "10mA", 7: "31.6mA", 8: "100mA"}.get(hrng, f"Range {hrng}")
                        res_parts.append(f"LS370: {temp*1000:.2f} mK | Htr: {hrng_str} at {mout_str}%")
                except PermissionError:
                    res_parts.append("LS370: Busy (Access Denied / Already in use)")
                except Exception as e:
                    res_parts.append(f"LS370: Error ({e}).")
                    
        return " | ".join(res_parts)

    def start_myriad(self, bf_ip, source, target_temp, init_power, step, sleep_time, timeout, max_power):
        if self.state in ["RAMPING", "PAUSED"]:
            return False, "Already running"

        self._bf_ip = bf_ip
        self.instrument = "Myriad/Miniebit"
        self.state = "RAMPING"
        self.stop_event.clear()
        self.pause_event.clear()
        self.message = "Starting Myriad/Miniebit ramp..."

        self._bf_target_temp = target_temp
        self._bf_step = step
        self._bf_sleep_time = sleep_time
        self._bf_max_power = max_power

        self.thread = threading.Thread(
            target=self._run_myriad,
            args=(source, init_power, timeout),
            daemon=True
        )
        self.thread.start()
        return True, "Started"

    def start_kpac(self, port, baudrate, channel, target_setpoint, ramp_rate, gpib_address=None,
                   control_mode=1, heater_range=6, p_val=None, i_val=None, d_val=None):
        if self.state in ["RAMPING", "PAUSED"]:
            return False, "Already running"

        self.instrument = "KPAC"
        self.state = "RAMPING"
        self.stop_event.clear()
        self.pause_event.clear()
        self.message = f"Connecting to KPAC (LakeShore) on {port}..."

        self._active_port = port
        self._active_baudrate = baudrate
        self._active_gpib = gpib_address

        self.thread = threading.Thread(
            target=self._run_kpac,
            args=(port, baudrate, channel, target_setpoint, ramp_rate, gpib_address,
                  control_mode, heater_range, p_val, i_val, d_val),
            daemon=True
        )
        self.thread.start()
        return True, "Started"

    def start_2120_og(self, port, baudrate, channel, gpib_address,
                      target_temp, heater_range, init_output, output_step, step_delay, max_output, resistance,
                      solo_channel=False, ramp_mode='constant_current',
                      ramp_rate_mk_per_min=2.0, kp=5.0, ki=0.1):
        if self.state in ["RAMPING", "PAUSED"]:
            return False, "Already running"

        self.instrument = "2120 OG"
        self.state = "RAMPING"
        self.stop_event.clear()
        self.pause_event.clear()
        self.message = "Starting 2120 OG open-loop Lakeshore ramp..."

        self._active_port = port
        self._active_baudrate = baudrate
        self._active_gpib = gpib_address

        self._og_target_temp = target_temp
        self._og_heater_range = heater_range
        self._og_init_output = init_output
        self._og_output_step = output_step
        self._og_step_delay = step_delay
        self._og_max_output = max_output
        self._og_resistance = resistance
        self._og_solo_channel = solo_channel
        self._og_ramp_mode = ramp_mode
        self._og_ramp_rate_mk_per_min = ramp_rate_mk_per_min
        self._og_kp = kp
        self._og_ki = ki

        self.thread = threading.Thread(
            target=self._run_2120_og,
            args=(port, baudrate, channel, gpib_address),
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

        if self.instrument == "KPAC":
            self.message = "Paused. Holding current setpoint"

        return True, "Paused"

    def resume(self):
        if self.state != "PAUSED":
            return False, "Not paused"

        self.state = "RAMPING"
        self.pause_event.clear()
        self.message = "Resumed"

        if self.instrument == "KPAC":
            self.message = "Resuming KPAC ramp."

        return True, "Resumed"

    def stop(self):
        self.state = "IDLE"
        self.stop_event.set()
        self.pause_event.clear()
        self.message = "Stopped"

        if self.instrument == "Myriad/Miniebit":
            try:
                BFTC(getattr(self, '_bf_ip', None) or '169.169.10.10:5001').set_heater_power(0.0)
            except:
                pass
        elif self.instrument in ["KPAC", "2120 OG"]:
            try:
                if hasattr(self, '_active_port') and self._active_port:
                    with LakeShore370(port=self._active_port, baudrate=self._active_baudrate, gpib_address=self._active_gpib) as ls:
                        ls.set_heater_range(0)
                        if self.instrument == "2120 OG":
                            try:
                                ls._write_command("MOUT 0")
                            except Exception:
                                pass
            except Exception as e:
                print(f"Error shutting off heater in stop(): {e}")
        
        return True, "Stopped"
    def _get_bf_temp(self, source, bf_ip=None):
        try:
            ch = int(source)
            bf = BFTC(bf_ip) if bf_ip else BFTC()
            return bf.get_temperature(ch)
        except Exception as e:
            print(f"Error getting temp for channel {source}: {e}")
            return None

    def _run_myriad(self, source, init_power, timeout):
        try:
            p_set = init_power
            start_time = time.time()
            
            bf = BFTC(self._bf_ip) if getattr(self, '_bf_ip', None) else BFTC()
            base_temp = self._get_bf_temp(source, getattr(self, '_bf_ip', None))
            self.current_temp = base_temp
            self.current_power_or_setpoint = p_set

            direction = "up" if base_temp < self._bf_target_temp else "down"

            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(1)
                    continue

                if time.time() - start_time > timeout:
                    self.state = "ERROR"
                    self.message = "Timeout reached"
                    bf.set_heater_power(0.0)
                    break

                base_temp = self._get_bf_temp(source, getattr(self, '_bf_ip', None))
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
                
                bf.set_heater_power(p_set)
                
                sleep_elapsed = 0
                while sleep_elapsed < self._bf_sleep_time and not self.stop_event.is_set() and not self.pause_event.is_set():
                    time.sleep(1)
                    sleep_elapsed += 1

        except Exception as e:
            self.state = "ERROR"
            self.message = f"Myriad error: {e}"
            try:
                bf.set_heater_power(0.0)
            except:
                pass



    def _run_kpac(self, port, baudrate, channel, target_setpoint, ramp_rate, gpib_address=None,
                  control_mode=1, heater_range=6, p_val=None, i_val=None, d_val=None):
        try:
            self._ls_channel = channel
            self._ls_target_setpoint = target_setpoint
            
            # Initial setup block using a short-lived connection
            with LakeShore370(port=port, baudrate=baudrate, gpib_address=gpib_address) as bridge:
                # Set PID parameters if provided
                if p_val is not None and i_val is not None and d_val is not None:
                    bridge.set_pid_parameters(p_val, i_val, d_val)
                else:
                    try:
                        p_val, i_val, d_val = bridge.pid_parameters()
                    except Exception:
                        pass
                
                # Set control mode (1: PID)
                bridge.set_control_mode(control_mode)
                
                # Set heater range
                bridge.set_heater_range(heater_range)
                
                # Enable ramp and set the rate
                bridge.set_ramp(enabled=True, rate_kelvin_per_minute=ramp_rate)
                
                # Set target setpoint
                bridge.set_setpoint_kelvin(target_setpoint)
                
                # Read starting values
                base_temp = bridge.temperature_kelvin(channel)
                curr_setpoint = bridge.setpoint_kelvin()
            
            self.current_temp = base_temp
            self.current_power_or_setpoint = curr_setpoint
            
            cmode_str = {1: "PID", 3: "Open Loop"}.get(control_mode, f"Mode {control_mode}")
            hrng_str = {0: "Off", 1: "31.6uA", 2: "100uA", 3: "316uA", 4: "1mA", 5: "3.16mA", 6: "10mA", 7: "31.6mA", 8: "100mA"}.get(heater_range, f"Range {heater_range}")
            pid_str = f"{p_val}/{i_val}/{d_val}" if (p_val is not None) else "N/A"
            
            self.current_control_mode = cmode_str
            self.current_heater_range = hrng_str
            self.current_pid = pid_str
            
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(1)
                    continue
                
                try:
                    with LakeShore370(port=port, baudrate=baudrate, gpib_address=gpib_address) as bridge:
                        temp = bridge.temperature_kelvin(channel)
                        setpoint = bridge.setpoint_kelvin()
                        
                        if temp is not None:
                            self.current_temp = temp
                        if setpoint is not None:
                            self.current_power_or_setpoint = setpoint
                except Exception as exc:
                    print(f"Warning: KPAC loop step failed: {exc}")
                
                if self.current_temp is not None and self.current_power_or_setpoint is not None:
                    self.message = f"Ramping... Temp: {self.current_temp*1000:.2f} mK, Setpoint: {self.current_power_or_setpoint*1000:.2f} mK"
                else:
                    self.message = f"Ramping... Temp: Unknown, Setpoint: Unknown"
                
                sleep_elapsed = 0
                while sleep_elapsed < 2 and not self.stop_event.is_set() and not self.pause_event.is_set():
                    time.sleep(1)
                    sleep_elapsed += 1
                    
        except Exception as e:
            self.state = "ERROR"
            self.message = f"KPAC error: {e}"

    def _run_2120_og(self, port, baudrate, channel, gpib_address):
        try:
            target_temp_k = self._og_target_temp * 1e-3  # mK -> K
            r_val = self._og_resistance
            ramp_mode = getattr(self, '_og_ramp_mode', 'constant_current')

            # Range full-scale currents (A)
            range_currents = {0: 0.0, 1: 31.6e-6, 2: 100e-6, 3: 316e-6,
                              4: 1e-3, 5: 3.16e-3, 6: 10e-3, 7: 31.6e-3, 8: 100e-3}
            i_full = range_currents.get(self._og_heater_range, 0.0)
            p_full = (i_full ** 2) * r_val  # full-scale power in W

            # Starting output % is always provided directly by the caller
            # (resolved in the Dash callback before ramp starts)
            curr_out = self._og_init_output

            # --- Initial setup block (short-lived connection) ---
            with LakeShore370(port=port, baudrate=baudrate, gpib_address=gpib_address) as bridge:
                # Solo channel if requested
                if getattr(self, '_og_solo_channel', False):
                    try:
                        bridge.solo_channel(channel)
                    except Exception as e:
                        print(f"Warning: Failed to set solo channel: {e}")

                # Force open-loop control mode
                bridge.set_control_mode(3)
                # Set heater range
                bridge.set_heater_range(self._og_heater_range)

                # Write the starting output
                bridge._write_command(f"MOUT {curr_out:.9g}")


                # Read starting temperature
                base_temp = bridge.temperature_kelvin(channel)

            self.current_temp = base_temp
            direction = "up" if base_temp < target_temp_k else "down"

            # Status card
            hrng_str = {0: "Off", 1: "31.6uA", 2: "100uA", 3: "316uA",
                        4: "1mA", 5: "3.16mA", 6: "10mA", 7: "31.6mA", 8: "100mA"}.get(self._og_heater_range, "Unknown")
            mode_labels = {
                'constant_current': 'Const. Current',
                'linear_power':     'Linear Power',
                'software_pi':      'Software PI',
            }
            self.current_control_mode = f"Open Loop ({mode_labels.get(ramp_mode, ramp_mode)})"
            self.current_heater_range = hrng_str
            self.current_pid = "N/A"

            # ---- Software PI: initialise state ----
            pi_integral = 0.0
            pi_p_current = (curr_out / 100.0) ** 2 * p_full  # starting power estimate
            pi_start_time = time.time()
            pi_start_temp = base_temp
            ramp_rate_k_per_s = getattr(self, '_og_ramp_rate_mk_per_min', 2.0) * 1e-3 / 60.0
            kp = getattr(self, '_og_kp', 5.0) * 1e-6   # uW/mK -> W/K
            ki = getattr(self, '_og_ki', 0.1) * 1e-6   # uW/(mK*s) -> W/(K*s)

            # ---- Linear Power: convert step % to power step ----
            # Output Step (%) is reinterpreted as % of full-scale *power*
            delta_p = p_full * (self._og_output_step / 100.0)
            # Track current power for linear_power mode
            lp_p_current = (curr_out / 100.0) ** 2 * p_full

            # --------------------------------------------------------
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(1)
                    continue

                try:
                    with LakeShore370(port=port, baudrate=baudrate, gpib_address=gpib_address) as bridge:
                        # 1. Read temperature
                        base_temp = bridge.temperature_kelvin(channel)
                        if base_temp is not None:
                            self.current_temp = base_temp

                        # 2. Check if target is reached
                        target_reached = False
                        if self.current_temp is not None:
                            if direction == "up" and self.current_temp >= target_temp_k:
                                target_reached = True
                            elif direction == "down" and self.current_temp <= target_temp_k:
                                target_reached = True

                        if target_reached:
                            self.state = "IDLE"
                            self.message = f"Target reached ({self.current_temp * 1000:.2f} mK) — heater holding"
                            break

                        # 3. Compute next MOUT based on selected ramp mode
                        if ramp_mode == 'constant_current':
                            # ---- Mode 1: Constant Current Ramp (legacy) ----
                            # Step MOUT directly; power steps quadratically.
                            if direction == "up":
                                curr_out += self._og_output_step
                            else:
                                curr_out -= self._og_output_step
                            curr_out = max(0.0, min(curr_out, self._og_max_output))

                        elif ramp_mode == 'linear_power':
                            # ---- Mode 2: Linear Power Steps (constant dP/dt) ----
                            if direction == "up":
                                lp_p_current += delta_p
                            else:
                                lp_p_current -= delta_p
                            p_max_allowed = (self._og_max_output / 100.0) ** 2 * p_full
                            lp_p_current = max(0.0, min(lp_p_current, p_max_allowed))
                            # Convert power back to MOUT %
                            if p_full > 0:
                                curr_out = 100.0 * np.sqrt(lp_p_current / p_full)
                            else:
                                curr_out = 0.0
                            curr_out = max(0.0, min(curr_out, self._og_max_output))

                        elif ramp_mode == 'software_pi':
                            # ---- Mode 3: Software PI Temperature Control ----
                            elapsed = time.time() - pi_start_time
                            # Moving temperature target (clamped to final target)
                            if direction == "up":
                                t_set = min(pi_start_temp + ramp_rate_k_per_s * elapsed, target_temp_k)
                            else:
                                t_set = max(pi_start_temp - ramp_rate_k_per_s * elapsed, target_temp_k)

                            error = t_set - (self.current_temp or pi_start_temp)
                            pi_integral += error * self._og_step_delay

                            p_max_allowed = (self._og_max_output / 100.0) ** 2 * p_full
                            pi_p_current += kp * error + ki * pi_integral
                            pi_p_current = max(0.0, min(pi_p_current, p_max_allowed))

                            if p_full > 0:
                                curr_out = 100.0 * np.sqrt(pi_p_current / p_full)
                            else:
                                curr_out = 0.0
                            curr_out = max(0.0, min(curr_out, self._og_max_output))

                        # 4. Write MOUT
                        bridge._write_command(f"MOUT {curr_out:.9g}")

                except Exception as exc:
                    print(f"Warning: 2120 OG loop step failed: {exc}")

                # Compute power for status readback
                i_actual = i_full * (curr_out / 100.0)
                p_set = (i_actual ** 2) * r_val
                self.current_power_or_setpoint = p_set

                if self.current_temp is not None:
                    self.message = (f"Ramping {direction} [{mode_labels.get(ramp_mode, ramp_mode)}]... "
                                    f"Temp: {self.current_temp * 1000:.2f} mK, "
                                    f"Output: {curr_out:.2f}%, Power: {p_set * 1e6:.2f} uW")
                else:
                    self.message = (f"Ramping {direction} [{mode_labels.get(ramp_mode, ramp_mode)}]... "
                                    f"Temp: Unknown, Output: {curr_out:.2f}%, Power: {p_set * 1e6:.2f} uW")

                sleep_elapsed = 0
                while sleep_elapsed < self._og_step_delay and not self.stop_event.is_set() and not self.pause_event.is_set():
                    time.sleep(1)
                    sleep_elapsed += 1

        except Exception as e:
            self.state = "ERROR"
            self.message = f"2120 OG error: {e}"

    def get_status(self):
        return {
            "state": self.state,
            "instrument": self.instrument,
            "message": self.message,
            "current_temp": self.current_temp,
            "current_power_or_setpoint": self.current_power_or_setpoint,
            "current_control_mode": self.current_control_mode,
            "current_heater_range": self.current_heater_range,
            "current_pid": self.current_pid
        }

# Global controller instance
controller = RampController()
