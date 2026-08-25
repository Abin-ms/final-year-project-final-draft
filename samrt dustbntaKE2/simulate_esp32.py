#!/usr/bin/env python3
"""
===========================================================================
  SmartBin ESP32 Hardware Simulator
===========================================================================
  Mimics the EXACT same WiFi HTTP POST behaviour as the real ESP32 firmware.
  Sends data to /api/bins/update every 2 seconds (same as firmware loop).
  Polls /api/hardware/poll/<bin_id> for web commands every second.

  ⚠️  DO NOT modify the production code.
  ✅  Once the real ESP32 is connected, this script is simply not run.
  ✅  No changes needed in app.py or any template when switching back.

  Usage:
      python simulate_esp32.py [--scenario SCENARIO] [--flask FLASK_URL]

  Scenarios:
      normal     — Gradual fill (default). Good for basic dashboard testing.
      ramp_full  — Quickly ramps wet waste to 97% to trigger notifications.
      cycle      — Fills → triggers alert → empties → repeats. Best for demos.
      manual     — Prompts you to type dry/wet values interactively.

  Examples:
      python simulate_esp32.py
      python simulate_esp32.py --scenario ramp_full
      python simulate_esp32.py --scenario cycle --flask https://10.96.135.88:5000
===========================================================================
"""

import requests
import time
import json
import math
import threading
import argparse
import sys
import random

# ─── CONFIG ────────────────────────────────────────────────────────────────
DEFAULT_FLASK  = "http://127.0.0.1:5000"    # change to your IP if needed
BIN_ID         = 1
POST_INTERVAL  = 2.0   # seconds (matches ESP32 firmware: every 2000ms)
POLL_INTERVAL  = 1.0   # seconds (matches ESP32 firmware: pollCommands every 1000ms)
VERIFY_SSL     = False  # Flask is using self-signed cert — skip SSL verify
# ───────────────────────────────────────────────────────────────────────────

# Suppress the InsecureRequestWarning for self-signed cert
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Shared mutable state (thread-safe via lock)
_lock = threading.Lock()
_state = {
    "dry_level":  0,
    "wet_level":  0,
    "human_dist": 999,
    "running":    True,
}


def post_bin_update(flask_url: str):
    """Send bin telemetry — identical JSON payload to the ESP32 firmware."""
    with _lock:
        payload = {
            "bin_id":    BIN_ID,
            "dry_level": int(_state["dry_level"]),
            "wet_level": int(_state["wet_level"]),
            "human_dist": int(_state["human_dist"]),
        }

    try:
        resp = requests.post(
            f"{flask_url}/api/bins/update",
            json=payload,
            verify=VERIFY_SSL,
            timeout=3,
        )
        status = resp.json().get("status", "?")
        print(
            f"[POST] dry={payload['dry_level']:3d}%  "
            f"wet={payload['wet_level']:3d}%  "
            f"human={payload['human_dist']:3d}cm  "
            f"→ {resp.status_code} ({status})"
        )
    except requests.exceptions.ConnectionError:
        print("[POST] ❌ Cannot reach Flask server. Is it running?")
    except Exception as e:
        print(f"[POST] Error: {e}")


def poll_hw_commands(flask_url: str):
    """Poll for pending hardware commands — mirrors ESP32 pollCommands()."""
    try:
        resp = requests.get(
            f"{flask_url}/api/hardware/poll/{BIN_ID}",
            verify=VERIFY_SSL,
            timeout=2,
        )
        if resp.status_code == 200:
            data = resp.json()
            cmds = data.get("commands", [])
            for cmd in cmds:
                print(f"[POLL] 📥 Command received: {cmd}")
                # Mirror what real ESP32 does with each command
                if cmd == "open_lid":
                    print("[SIM]  🔓 (Simulating) Lid OPEN — servo to 180°")
                elif cmd == "close_lid":
                    print("[SIM]  🔒 (Simulating) Lid CLOSE — servo to 100°")
                elif cmd == "reset_stepper":
                    print("[SIM]  ⚙️  (Simulating) Stepper RESET")
    except Exception:
        pass  # silent — same as ESP32 ignoring failed polls


# ─── SCENARIO: normal ──────────────────────────────────────────────────────
def scenario_normal(flask_url: str):
    """Gradual fill simulation — realistic sensor drift."""
    print("\n📦 Scenario: NORMAL — gradual fill over ~5 minutes")
    dry, wet = 0.0, 0.0
    while _state["running"]:
        dry = min(100, dry + random.uniform(0.3, 0.8))
        wet = min(100, wet + random.uniform(0.2, 0.6))
        human = random.choice([999, 999, 999, random.randint(10, 40)])
        with _lock:
            _state["dry_level"]  = dry
            _state["wet_level"]  = wet
            _state["human_dist"] = human
        time.sleep(POST_INTERVAL)


# ─── SCENARIO: ramp_full ───────────────────────────────────────────────────
def scenario_ramp_full(flask_url: str):
    """Quickly ramps wet level to 97% then holds — triggers all alerts fast."""
    print("\n🚀 Scenario: RAMP_FULL — ramping wet to 97% in ~10 seconds")
    wet = 0.0
    while _state["running"] and wet < 97:
        wet = min(97, wet + 10)
        with _lock:
            _state["wet_level"]  = wet
            _state["dry_level"]  = random.randint(10, 30)
            _state["human_dist"] = 999
        time.sleep(POST_INTERVAL)

    print("[SIM] ⛔ Bin is full — holding at 97% until you Ctrl+C")
    while _state["running"]:
        with _lock:
            _state["wet_level"]  = 97
        time.sleep(POST_INTERVAL)


# ─── SCENARIO: cycle ───────────────────────────────────────────────────────
def scenario_cycle(flask_url: str):
    """Fills to 95%, triggers notification, drops to 5%, repeats — best for demos."""
    print("\n🔄 Scenario: CYCLE — fill → alert → empty → repeat")
    while _state["running"]:
        print("\n[SIM] ⬆️  Filling bin...")
        level = 0.0
        while level < 95 and _state["running"]:
            level = min(95, level + 5)
            with _lock:
                _state["wet_level"]  = level
                _state["dry_level"]  = level * 0.6
                _state["human_dist"] = 999
            time.sleep(POST_INTERVAL)

        print("[SIM] 🚨 Bin full! Holding for 90 seconds (2 notification cycles)...")
        hold = 0
        while hold < 90 and _state["running"]:
            time.sleep(POST_INTERVAL)
            hold += POST_INTERVAL

        print("[SIM] ⬇️  Emptying bin (simulating collection)...")
        level = 95.0
        while level > 5 and _state["running"]:
            level = max(5, level - 8)
            with _lock:
                _state["wet_level"]  = level
                _state["dry_level"]  = level * 0.6
                _state["human_dist"] = 999
            time.sleep(POST_INTERVAL)

        print("[SIM] ✅ Bin emptied. Waiting 10s before next cycle...")
        time.sleep(10)


# ─── SCENARIO: manual ──────────────────────────────────────────────────────
def scenario_manual(flask_url: str):
    """Interactive mode — you type dry/wet values to test specific states."""
    print("\n🎛️  Scenario: MANUAL — type values to simulate specific sensor states")
    print("    Format: dry wet   (e.g. 95 20 → dry=95%, wet=20%)")
    print("    Type 'q' to quit\n")
    while _state["running"]:
        try:
            line = input("Enter dry wet values: ").strip()
            if line.lower() == 'q':
                _state["running"] = False
                break
            parts = line.split()
            if len(parts) == 2:
                with _lock:
                    _state["dry_level"] = max(0, min(100, float(parts[0])))
                    _state["wet_level"] = max(0, min(100, float(parts[1])))
                    _state["human_dist"] = 999
            else:
                print("  ⚠️  Please enter exactly two numbers")
        except EOFError:
            break
        except ValueError:
            print("  ⚠️  Invalid numbers")


# ─── HTTP WORKER THREAD ────────────────────────────────────────────────────
def http_worker(flask_url: str):
    """Background thread that handles all HTTP calls at correct intervals."""
    last_post = 0.0
    last_poll = 0.0
    while _state["running"]:
        now = time.time()
        if now - last_post >= POST_INTERVAL:
            post_bin_update(flask_url)
            last_post = now
        if now - last_poll >= POLL_INTERVAL:
            poll_hw_commands(flask_url)
            last_poll = now
        time.sleep(0.1)  # 100ms sleep to avoid busy-loop


# ─── MAIN ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="SmartBin ESP32 Hardware Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scenario", default="normal",
        choices=["normal", "ramp_full", "cycle", "manual"],
        help="Simulation scenario (default: normal)"
    )
    parser.add_argument(
        "--flask", default=DEFAULT_FLASK,
        help=f"Flask server URL (default: {DEFAULT_FLASK})"
    )
    args = parser.parse_args()

    flask_url = args.flask.rstrip("/")
    scenarios = {
        "normal":    scenario_normal,
        "ramp_full": scenario_ramp_full,
        "cycle":     scenario_cycle,
        "manual":    scenario_manual,
    }

    print("=" * 60)
    print("  SmartBin ESP32 Hardware Simulator")
    print("=" * 60)
    print(f"  Flask server : {flask_url}")
    print(f"  Bin ID       : {BIN_ID}")
    print(f"  Scenario     : {args.scenario}")
    print(f"  Post interval: {POST_INTERVAL}s  Poll interval: {POLL_INTERVAL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 60)

    # Start HTTP worker in background thread
    http_thread = threading.Thread(target=http_worker, args=(flask_url,), daemon=True)
    http_thread.start()

    try:
        scenarios[args.scenario](flask_url)
    except KeyboardInterrupt:
        print("\n\n[SIM] 🛑 Stopped by user")
    finally:
        _state["running"] = False
        print("[SIM] Simulation ended.")


if __name__ == "__main__":
    main()
