#!/usr/bin/env python3
"""
HID Keypad Listener Node
Reads and prints raw key events from a dedicated input device.
"""

import sys
import signal
import select
from evdev import InputDevice, categorize, ecodes, KeyEvent, list_devices

class KeypadReader:
    def __init__(self, device_path=None):
        self._running = True
        self._ctrl_held = False
        self.device = None
        self._grabbed = False
        
        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.device_path = device_path or self._auto_detect_device()
        if not self.device_path:
            print("[ERROR] Could not automatically find a keypad. Please check USB connection.")
            sys.exit(1)
            
        # Robot State Array (10 elements)
        # 0: Power (Toggle, key /)
        # 1: DS    (Toggle, key *)
        # 2: BS    (Toggle, key .)
        # 3: HMS   (Left/Right, key 0, False=Left)
        # 4: Fire  (Toggle, key ENTER)
        # 5: Light (Toggle, key 5)
        # 6: Up    (Continuous, key 8)
        # 7: Down  (Continuous, key 2)
        # 8: Left  (Continuous, key 4)
        # 9: Right (Continuous, key 6)
        self.state = [False] * 10
            
    def _signal_handler(self, signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"\n[INFO] Received {sig_name}. Stopping...")
        self._running = False

    def _auto_detect_device(self):
        print("[INFO] Auto-scanning for connected keypads...")
        devices = [InputDevice(path) for path in list_devices()]
        
        # Filter for devices that report key capabilities
        key_devices = [d for d in devices if ecodes.EV_KEY in d.capabilities()]
        
        # Prioritize highest event number (most recently plugged in USB device)
        key_devices.sort(key=lambda d: d.path, reverse=True)
        
        for dev in key_devices:
            name = dev.name.lower()
            # Identify external keypads by common names, excluding mice and system control interfaces
            if ("hid" in name or "keypad" in name or "usb keyboard" in name) and "mouse" not in name and "control" not in name:
                return dev.path

        # Fallback to the newest keyboard found if no explicit HID/Keypad name matched
        if key_devices:
            return key_devices[0].path
        return None

  
    def _print_state(self, message=""):
        # Format the 10 boolean states as a continuous binary string e.g., "1000101000"
        state_str = "".join(["1" if x else "0" for x in self.state])
        if message:
            print(f"{message:<25} | Data: {state_str}")
        else:
            print(f"Data: {state_str}")

    def run(self):
        try:
            self.device = InputDevice(self.device_path)
            print(f"[INFO] Connected to: {self.device.name} ({self.device_path})")
            print("[INFO] Listening for key presses... (Press Ctrl+C to exit)\n")
            
            # Grab acquires exclusive access so the keypad doesn't type raw characters into your terminal
            self.device.grab()
            self._grabbed = True

            # Use select with timeout to allow Ctrl+C signals to interrupt efficiently
            import time
            last_continuous_print = 0

            while self._running:
                r, w, x = select.select([self.device.fd], [], [], 0.1)  # Wake up every 100ms
                
                if r:
                    for event in self.device.read():
                        if event.type == ecodes.EV_KEY:
                            key_event = categorize(event)
                            
                            keycode = key_event.keycode
                            if isinstance(keycode, list):
                                keycode = keycode[0]
                                
                            # ── Track Ctrl key state (for Ctrl+C / Ctrl+Z detection) ──
                            if keycode in ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"):
                                if key_event.keystate in (KeyEvent.key_down, KeyEvent.key_hold):
                                    self._ctrl_held = True
                                elif key_event.keystate == KeyEvent.key_up:
                                    self._ctrl_held = False
                                continue

                            # ── Detect Ctrl+C or Ctrl+Z in the event stream itself ────
                            if key_event.keystate == KeyEvent.key_down and self._ctrl_held:
                                if keycode == "KEY_C":
                                    print("\n[INFO] Ctrl+C detected. Exiting...")
                                    self._running = False
                                    return
                                if keycode == "KEY_Z":
                                    print("\n[INFO] Ctrl+Z detected. Exiting...")
                                    self._running = False
                                    return

                            is_press = (key_event.keystate == KeyEvent.key_down)
                            is_release = (key_event.keystate == KeyEvent.key_up)
                            state_changed = False
                            msg = ""

                            # --- TOGGLE KEYS (Trigger ON/OFF only when initially pressed) ---
                            if is_press:
                                if keycode in ["KEY_KPSLASH"]:  # / -> Power
                                    self.state[0] = not self.state[0]
                                    msg = f"Power {'ON' if self.state[0] else 'OFF'}"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KPASTERISK"]:  # * -> DS
                                    self.state[1] = not self.state[1]
                                    msg = f"DS {'ON' if self.state[1] else 'OFF'}"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KPDOT", "KEY_DELETE"]:  # . -> BS
                                    self.state[2] = not self.state[2]
                                    msg = f"BS {'ON' if self.state[2] else 'OFF'}"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP5", "KEY_CLEAR"]:  # 5 -> Light
                                    self.state[5] = not self.state[5]
                                    msg = f"Light {'ON' if self.state[5] else 'OFF'}"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KPENTER", "KEY_ENTER"]:  # ENTER -> Fire
                                    self.state[4] = not self.state[4]
                                    msg = f"Fire {'ON' if self.state[4] else 'OFF'}"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP0", "KEY_INSERT"]:  # 0 -> HMS (Left/Right)
                                    if not self.state[4]: # Only toggle HMS if Fire is OFF
                                        self.state[3] = not self.state[3]
                                        msg = f"HMS {'Right' if self.state[3] else 'Left'}"
                                    else:
                                        msg = "HMS Blocked (Fire is ON!)"
                                    state_changed = True

                                # --- CONTINUOUS KEYS (Trigger ON when pressed) ---
                                elif keycode in ["KEY_KP2", "KEY_DOWN"]:
                                    self.state[7] = True
                                    msg = "Down move"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP8", "KEY_UP"]:
                                    self.state[6] = True
                                    msg = "Up move"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP4", "KEY_LEFT"]:
                                    self.state[8] = True
                                    msg = "Video left move"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP6", "KEY_RIGHT"]:
                                    self.state[9] = True
                                    msg = "Video right move"
                                    state_changed = True

                            # --- CONTINUOUS KEYS (Trigger OFF when released) ---
                            elif is_release:
                                if keycode in ["KEY_KP2", "KEY_DOWN"]:
                                    self.state[7] = False
                                    msg = "Down Stop"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP8", "KEY_UP"]:
                                    self.state[6] = False
                                    msg = "Up Stop"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP4", "KEY_LEFT"]:
                                    self.state[8] = False
                                    msg = "Video left stop"
                                    state_changed = True
                                    
                                elif keycode in ["KEY_KP6", "KEY_RIGHT"]:
                                    self.state[9] = False
                                    msg = "Video right stop"
                                    state_changed = True

                            # Generate String and Print data if any valid key changed state
                            if state_changed:
                                self._print_state(msg)
                                if any(self.state[6:10]):
                                    # Reset timer so we don't double-print instantly
                                    last_continuous_print = time.time()

                # --- CONTINUOUS PRINTING FOR HELD KEYS (10Hz) ---
                if any(self.state[6:10]):
                    current_time = time.time()
                    if current_time - last_continuous_print >= 0.1:  # Every 100ms
                        msg = ""
                        if self.state[6]: msg = "Up move"
                        elif self.state[7]: msg = "Down move"
                        elif self.state[8]: msg = "Video left move"
                        elif self.state[9]: msg = "Video right move"
                        
                        self._print_state(msg)
                        last_continuous_print = current_time

        except PermissionError:
            print(f"\n[ERROR] Permission denied!")
            print(f"        Linux blocks standard users from reading hardware inputs.")
            print(f"        You must be in the 'input' group to run without sudo.")
        except FileNotFoundError:
            print(f"\n[ERROR] Device {self.device_path} not found. Check if the keypad is plugged in.")
        except KeyboardInterrupt:
            pass
        except OSError as e:
            if self._running:
                print(f"\n[ERROR] Device error: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        if self.device is not None:
            try:
                if self._grabbed:
                    self.device.ungrab()
                    self._grabbed = False
            except OSError:
                pass
            try:
                self.device.close()
            except Exception:
                pass

if __name__ == '__main__':
    # Initialize without a path to trigger auto-detection
    reader = KeypadReader()
    reader.run()
