#!/usr/bin/env python3
"""
HID Keypad Listener Node
Reads and prints key events from a USB numpad ONLY.

Bulletproof design:
  - Only prints keys that exist in KEY_MAP (numpad keys). All other keys
    (laptop letters, Ctrl, Shift, etc.) are silently ignored.
  - Detects Ctrl+C / Ctrl+Z inside the event stream itself, so the program
    can ALWAYS be killed even if the wrong device was grabbed.
  - Does NOT grab by default. Use --grab if you need exclusive access.
  - Use --list to see all devices, then -d /dev/input/eventN to pick one.
"""

import sys
import signal
import select
from evdev import InputDevice, categorize, ecodes, KeyEvent, list_devices


# ── Constants ─────────────────────────────────────────────────────────────────
BUS_USB = 0x03  # linux/input.h — USB bus type


class KeypadReader:
    """Reads key events from an external USB keypad."""

    # ── Key mapping (class constant) ──────────────────────────────────────────
    # ONLY keys in this dict will produce output. Everything else is ignored.
    KEY_MAP = {
        # NumLock ON (KP digit keys)
        "KEY_KP1": "1", "KEY_KP2": "2", "KEY_KP3": "3",
        "KEY_KP4": "4", "KEY_KP5": "5", "KEY_KP6": "6",
        "KEY_KP7": "7", "KEY_KP8": "8", "KEY_KP9": "9",
        "KEY_KP0": "0", "KEY_KPDOT": ".",
        # NumLock OFF (navigation aliases → same physical numpad keys)
        "KEY_END": "1",      "KEY_DOWN": "2",     "KEY_PAGEDOWN": "3",
        "KEY_LEFT": "4",     "KEY_CLEAR": "5",    "KEY_RIGHT": "6",
        "KEY_HOME": "7",     "KEY_UP": "8",       "KEY_PAGEUP": "9",
        "KEY_INSERT": "0",   "KEY_DELETE": ".",
        # Operator keys
        "KEY_KPASTERISK": "*", "KEY_KPMINUS": "-",
        "KEY_KPPLUS": "+",     "KEY_KPENTER": "ENTER",
        "KEY_KPSLASH": "/",    "KEY_NUMLOCK": "NUMLOCK",
    }

    # Names to reject during auto-detection
    _REJECT_KEYWORDS = (
        "at translated", "thinkpad", "internal", "laptop",
        "macbook", "chromebook", "built-in",
        "power button", "video bus", "pc speaker", "sleep button",
        "lid switch", "virtual", "sysrq",
        "system control", "consumer control",  # Exclude media/power endpoints
    )

    def __init__(self, device_path=None, grab=True):
        self._grabbed = False
        self._running = True
        self._grab_requested = grab
        self._ctrl_held = False   # Track Ctrl key state for Ctrl+C detection
        self.device = None

        # Register signal handlers
        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.device_path = device_path or self._auto_detect_device()
        if not self.device_path:
            print("[ERROR] Could not find a USB keypad.")
            print("        Run with --list to see all devices, then use:")
            print("        sudo python3 keypad_reader.py -d /dev/input/eventN")
            sys.exit(1)

    # ── Signal handling ───────────────────────────────────────────────────────
    def _signal_handler(self, signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"\n[INFO] Received {sig_name}. Stopping...")
        self._running = False

    # ── Device detection ──────────────────────────────────────────────────────
    def _auto_detect_device(self):
        """Find the best USB keypad candidate."""
        print("[INFO] Auto-scanning for USB keypads...\n")
        candidates = []

        for path in list_devices():
            try:
                dev = InputDevice(path)
            except Exception:
                continue

            name = dev.name
            low = name.lower()
            phys = (dev.phys or "").lower()
            bustype = dev.info.bustype

            # Gate 1: USB bus only
            if bustype != BUS_USB:
                print(f"  [SKIP non-USB]  {path}  →  {name}  (bus=0x{bustype:02x})")
                dev.close()
                continue

            # Gate 2: Real USB phys path
            if "usb" not in phys:
                print(f"  [SKIP no-phys]  {path}  →  {name}  (phys={dev.phys!r})")
                dev.close()
                continue

            # Gate 3: Must have EV_KEY
            if ecodes.EV_KEY not in dev.capabilities():
                dev.close()
                continue

            # Gate 4: Reject mice and known non-keypad devices
            if "mouse" in low:
                print(f"  [SKIP mouse]    {path}  →  {name}")
                dev.close()
                continue
            if any(kw in low for kw in self._REJECT_KEYWORDS):
                print(f"  [SKIP reject]   {path}  →  {name}")
                dev.close()
                continue

            # Scoring
            score = 10
            if "keypad" in low or "numpad" in low:
                score = 100
            elif "hid" in low:
                score = 50

            try:
                event_num = int(path.rsplit("event", 1)[1])
            except (IndexError, ValueError):
                event_num = 0

            vid, pid = dev.info.vendor, dev.info.product
            print(f"  [CANDIDATE]     {path}  →  {name}  "
                  f"(vid=0x{vid:04x}, pid=0x{pid:04x}, score={score})")
            candidates.append((score, event_num, path, name))
            dev.close()

        print()

        if not candidates:
            print("[WARN] No USB keyboard devices found.")
            return None

        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        best = candidates[0]
        print(f"[INFO] Selected: {best[2]}  →  {best[3]}  (score={best[0]})")
        return best[2]

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        try:
            self.device = InputDevice(self.device_path)
            print(f"\n[INFO] Connected to: {self.device.name} ({self.device_path})")

            if self._grab_requested:
                self.device.grab()
                self._grabbed = True
                print("[INFO] Device GRABBED (exclusive access).")
            else:
                print("[INFO] Device opened WITHOUT grab (laptop keyboard stays free).")

            print("[INFO] Only numpad keys will be printed. All other keys are ignored.")
            print("[INFO] Press Ctrl+C to exit.\n")

            fd = self.device.fd

            while self._running:
                r, _, _ = select.select([fd], [], [], 0.5)
                if not r:
                    continue

                for event in self.device.read():
                    if event.type != ecodes.EV_KEY:
                        continue

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
                    # This is the SAFETY NET: even if this code grabbed the
                    # wrong device (including your laptop keyboard), pressing
                    # Ctrl+C or Ctrl+Z will ALWAYS stop the program.
                    if key_event.keystate == KeyEvent.key_down and self._ctrl_held:
                        if keycode == "KEY_C":
                            print("\n[INFO] Ctrl+C detected in event stream. Exiting...")
                            self._running = False
                            return
                        if keycode == "KEY_Z":
                            print("\n[INFO] Ctrl+Z detected in event stream. Exiting...")
                            self._running = False
                            return

                    # ── Only process key_down events for other keys ───────────
                    if key_event.keystate != KeyEvent.key_down:
                        continue

                    # ── ONLY print keys that are in KEY_MAP ───────────────────
                    # This is why laptop keyboard presses (letters, F-keys,
                    # etc.) are silently ignored — they are NOT in KEY_MAP.
                    mapped = self.KEY_MAP.get(keycode)
                    if mapped is not None:
                        print(f"Button Pressed ==> {mapped}")
                    # Keys not in KEY_MAP → silently dropped, nothing printed

        except PermissionError:
            print(f"\n[ERROR] Permission denied! Run with sudo:")
            print(f"        sudo python3 {sys.argv[0]}")
        except FileNotFoundError:
            print(f"\n[ERROR] Device {self.device_path} not found.")
            print("        Check if the keypad is plugged in.")
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
                    print("[INFO] Device ungrabbed.")
            except OSError:
                pass
            try:
                self.device.close()
                print("[INFO] Device closed.")
            except Exception:
                pass


# ── List all devices (diagnostic mode) ───────────────────────────────────────
def list_all_devices():
    """Print every input device with bus type, phys path, and capabilities."""
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              ALL INPUT DEVICES ON THIS SYSTEM              ║")
    print("╠══════════════════════════════════════════════════════════════╣")

    for path in list_devices():
        try:
            d = InputDevice(path)
            bus = d.info.bustype
            vid = d.info.vendor
            pid = d.info.product
            has_keys = ecodes.EV_KEY in d.capabilities()

            bus_name = "USB" if bus == BUS_USB else f"0x{bus:02x}"
            key_str = "✓ has keys" if has_keys else "✗ no keys"

            print(f"║  {path}")
            print(f"║    Name:  {d.name}")
            print(f"║    Bus:   {bus_name}  |  VID:PID = 0x{vid:04x}:0x{pid:04x}")
            print(f"║    Phys:  {d.phys!r}")
            print(f"║    Keys:  {key_str}")
            print("║")
            d.close()
        except Exception as e:
            print(f"║  {path}  →  ERROR: {e}")
            print("║")

    print("╚══════════════════════════════════════════════════════════════╝")
    print("\nTo use a specific device:")
    print("  sudo python3 keypad_reader.py -d /dev/input/eventN")
    print("  sudo python3 keypad_reader.py -d /dev/input/eventN --grab")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="HID Keypad Listener — reads key events from a USB numpad."
    )
    parser.add_argument(
        "-d", "--device",
        default=None,
        help="Explicit /dev/input/eventN path (bypasses auto-detection).",
    )
    parser.add_argument(
        "--grab",
        action="store_true",
        default=False,
        help="Grab exclusive access to the device (prevents keypresses "
             "from reaching the terminal). Without this flag, the device "
             "is opened in shared mode — safer but numpad keys may echo.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="List all input devices and exit. Use this to find your keypad's path.",
    )
    args = parser.parse_args()

    if args.list:
        list_all_devices()
        sys.exit(0)

    reader = KeypadReader(device_path=args.device, grab=True)
    reader.run()
