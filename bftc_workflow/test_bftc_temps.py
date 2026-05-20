"""Quick test: read temperatures from the Bluefors controller.

Tests two code-paths:
  1. get_temp_history()  — historical endpoint (always returns requested channel)
  2. read_channel_temperature() — /channel/measurement/latest (polls until correct
     channel comes back; may take ~11 s when multiple channels are active)
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bftc_workflow"))

import bftc

CHANNELS = [1, 2, 5, 6, 7, 8]

print(f"Controller: {bftc.ip}")
print("=" * 60)

# --- Test 1: historical endpoint (reliable, no channel-wait needed) ---
print("\n[1] get_temp_history (historical data, 2 min window)")
print("-" * 60)
for ch in CHANNELS:
    try:
        data = bftc.get_temp_history(ch=ch, start_minutes_ago=2, stop_minutes_ago=0)
        temps = data.get("measurements", {}).get("temperature", [])
        if temps:
            latest = temps[-1]
            print(f"  CH {ch}:  {latest*1000:10.3f} mK  ({latest:.6f} K)  [{len(temps)} pts]")
        else:
            print(f"  CH {ch}:  (no data — channel may be off)")
    except Exception as e:
        print(f"  CH {ch}:  ERROR — {e}")

# --- Test 2: latest-measurement endpoint (polls for correct channel) ---
print(f"\n[2] read_channel_temperature (latest endpoint, polls up to 30 s)")
print("-" * 60)

# Find which channels are actually active (had historical data)
active_channels = []
for ch in CHANNELS:
    try:
        data = bftc.get_temp_history(ch=ch, start_minutes_ago=2, stop_minutes_ago=0)
        if data.get("measurements", {}).get("temperature", []):
            active_channels.append(ch)
    except:
        pass

if not active_channels:
    print("  No active channels found — skipping latest-measurement test.")
else:
    print(f"  Active channels: {active_channels}")
    for ch in active_channels:
        try:
            t0 = time.time()
            temp = bftc.read_channel_temperature(ch, timeout_s=30, poll_interval=1)
            elapsed = time.time() - t0
            print(f"  CH {ch}:  {temp*1000:10.3f} mK  (waited {elapsed:.1f} s)")
        except TimeoutError as e:
            print(f"  CH {ch}:  TIMEOUT — {e}")
        except Exception as e:
            print(f"  CH {ch}:  ERROR — {e}")

print("\n" + "=" * 60)
print("Done.")
