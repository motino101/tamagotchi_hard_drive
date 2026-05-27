"""
TAMAGOTCHI WATCHER

Drives the Pico via single-byte serial commands (see main.py for .bin names):
  e = eating_anim (file added)
  s = crying_anim -> get-up_anim (file removed, delete streak 1-4)
  a = hangry_anim outburst (file removed, delete streak 5+)
  g = grow_anim then chonk (first cross into chonk, 10+ files)
  i / h / c = base: character / hangry (empty) / chonk (10+ files)

Base state from file count (CHONK_THRESHOLD=10, 10+ files = chonk):
  0 files   -> h (hangry_anim loop)
  1..9      -> i (character_anim)
  10+       -> c (chonk_anim)
"""
import os
import time
import random
import serial
from serial.tools import list_ports
from datetime import datetime

# --- CONFIGURATION ---
WATCH_DIR  = "/Volumes/hey im bit/"
# "auto" = scan USB for the MicroPython Pico (VID 0x2E8A) on every reconnect
# attempt, so we pick up whichever /dev/cu.usbmodemXXXX macOS hands out today.
# Override with a literal device path (e.g. "/dev/cu.usbmodem11201") to pin one.
PICO_PORT  = "auto"
BAUD_RATE  = 115200
CHONK_THRESHOLD = 10  # >=10 files = chonk

# Raspberry Pi Foundation USB IDs (RP2040 / Pico). PID 0x0005 is the
# MicroPython "Board in FS mode" CDC interface; we prefer it when present.
_RPI_VID = 0x2E8A
_MICROPYTHON_PID = 0x0005


def find_pico_port():
    """Return the device path of a connected MicroPython Pico, or None."""
    candidates = []
    for p in list_ports.comports():
        if p.vid == _RPI_VID:
            candidates.append(p)
            continue
        mfr = (p.manufacturer or "").lower()
        desc = (p.description or "").lower()
        product = (p.product or "").lower()
        if (
            "micropython" in mfr
            or "pico" in desc
            or "pico" in product
            or "board in fs mode" in desc
            or "board in fs mode" in product
        ):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: 0 if p.pid == _MICROPYTHON_PID else 1)
    return candidates[0].device

# --- ANSI COLORS (so the terminal looks alive) ---
class C:
    RESET   = "\033[0m"
    DIM     = "\033[2m"
    BOLD    = "\033[1m"
    PINK    = "\033[95m"
    CYAN    = "\033[96m"
    YELLOW  = "\033[93m"
    GREEN   = "\033[92m"
    RED     = "\033[91m"
    BLUE    = "\033[94m"
    GREY    = "\033[90m"

# --- PERSONALITY: faces + flavor phrases ---
FACES = {
    'idle':   "(•_•)",
    'hangry': "(>_<)",
    'chonk':  "(✿◠‿◠)",
    'eat':    "(◕‿◕)~🍎",
    'sad':    "(╥﹏╥)",
    'grow':   "✧･ﾟ(☉益☉)ﾟ･✧",
    'rage':   "(눈_눈)",
}

EAT_PHRASES = [
    "OM NOM NOM!",
    "*chomp chomp*",
    "DELICIOUS!!",
    "yum yum yum",
    "more pls more",
    "*munching intensifies*",
    "tasty bytes!",
]

SAD_PHRASES = [
    "*sniffle*",
    "where did it gooo",
    "i miss my snack",
    "noooooo",
    "*single pixel tear*",
    "why u do dis",
]

HANGRY_RAGE_PHRASES = [
    "TOO MANY CHANGES!!",
    "*hangry screech*",
    "STOP. DELETING. THINGS.",
    "i have HAD it—",
    "this is the LAST straw",
]

GROW_PHRASES = [
    "GROWTH SPURT!!",
    "BEHOLD, THE CHONK",
    "*absolute unit incoming*",
    "i am become big",
    "EVOLVING...",
    "thicc protocol engaged",
]

# --- STREAKS ---
# Streak resets if no matching event for this many seconds, OR opposite event occurs.
STREAK_WINDOW = 300  # 5 minutes

# (threshold, fire-icon, phrase) — first match >= n wins
EAT_STREAK_TIERS = [
    (20, "🔥👑", "LEGENDARY {n}× STREAK!!"),
    (15, "🔥💥", "UNSTOPPABLE — {n} in a row!"),
    (10, "🔥🔥🔥", "{n}× COMBO!! pet is BLESSED"),
    (7,  "🔥🔥",   "ON FIRE — {n} snacks back to back!"),
    (5,  "🔥",     "{n} STREAK!! pet is thriving"),
    (3,  "✨",     "{n} in a row!"),
]

SAD_STREAK_TIERS = [
    (7, "😭⚠️", "pet emotional damage: {n}"),
    (5, "😭💔", "{n} losses... i give up"),
    (3, "😭",   "{n} in a row... stop bullying meee"),
]

BASE_LABELS = {'i': 'idle', 'h': 'hangry', 'c': 'chonk'}

# --- helpers ---
def stamp():
    return f"{C.GREY}[{datetime.now().strftime('%H:%M:%S')}]{C.RESET}"

def say(icon, color, msg, face=""):
    tail = f"  {color}{face}{C.RESET}" if face else ""
    print(f"{stamp()} {icon} {color}{msg}{C.RESET}{tail}")

def pico_say(line):
    # gently rewrite "Loaded: foo.bin frames: N" so it reads like a whisper
    pretty = line
    if line.startswith("Loaded:"):
        try:
            _, rest = line.split("Loaded:", 1)
            name_part, frames_part = rest.split("frames:")
            name = name_part.strip().replace(".bin", "")
            n = frames_part.strip()
            pretty = f"loaded animation '{name}' ({n} frames)"
        except Exception:
            pass
    print(f"{stamp()} {C.DIM}🤖 pico whispers: {pretty}{C.RESET}")

def count_files(directory):
    total = 0
    try:
        for root, dirs, files in os.walk(directory):
            files = [f for f in files if not f.startswith('.')]
            total += len(files)
    except Exception:
        return 0
    return total

def get_base_state(count):
    """Return the base-state command character for a given file count."""
    if count <= 0:
        return 'h'                 # hangry
    if count >= CHONK_THRESHOLD:
        return 'c'                 # chonk
    return 'i'                     # idle

def streak_message(n, tiers):
    """Return (icon, phrase) for the highest tier this streak qualifies for, or None."""
    for threshold, icon, phrase in tiers:
        if n >= threshold:
            return icon, phrase.format(n=n)
    return None

def show_streak(n, tiers, color):
    """Print a milestone line if the streak hits a tier (only on tier boundaries)."""
    hit = streak_message(n, tiers)
    if not hit:
        return
    # Only celebrate when we just crossed into this tier
    prev = streak_message(n - 1, tiers)
    if prev and prev[0] == hit[0]:
        return
    icon, phrase = hit
    print(f"{stamp()}    {color}└─ {icon} {phrase}{C.RESET}")

def banner():
    print(f"{C.PINK}╔══════════════════════════════════════════╗")
    print(f"║  {C.BOLD}🐣  TAMAGOTCHI  WATCHER  🐣{C.RESET}{C.PINK}             ║")
    print(f"╚══════════════════════════════════════════╝{C.RESET}")

def open_pico(announce_waiting=True):
    """Block until a Pico is plugged in and we can open it. Returns (ser, port)."""
    waiting_announced = False
    error_announced = False
    while True:
        port = PICO_PORT if PICO_PORT not in ("", "auto", None) else find_pico_port()
        if not port:
            if announce_waiting and not waiting_announced:
                say("🔍", C.CYAN, "waiting for tamagotchi… plug the Pico in via USB")
                waiting_announced = True
            time.sleep(1.0)
            continue
        try:
            # timeout=0.1 makes the read non-blocking so it doesn't freeze the script
            ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
            say("🔌", C.GREEN, f"connected to {port}")
            return ser, port
        except serial.SerialException as e:
            if not error_announced:
                say("💔", C.RED, f"found pico at {port} but couldn't open it ({e})")
                say("  ", C.DIM, "is Thonny / mpremote / another script holding it?")
                error_announced = True
            time.sleep(2.0)


def main():
    banner()
    say("👀", C.CYAN, f"watching: {WATCH_DIR}")

    ser, active_port = open_pico()

    last_count = count_files(WATCH_DIR)
    last_base  = get_base_state(last_count)

    # Tell the Pico what base state to start in
    ser.write(last_base.encode())
    label = BASE_LABELS[last_base]
    say("📁", C.YELLOW, f"starting count: {last_count} files → {label}",
        face=FACES.get(label, ""))
    say("👂", C.DIM, "listening for pico whispers...")

    # streak state
    eat_streak = 0
    sad_streak = 0
    last_event_ts = 0.0

    while True:
        try:
            # --- 1. READ LOGS FROM PICO ---
            try:
                waiting = ser.in_waiting
            except (serial.SerialException, OSError) as e:
                say("🔌💤", C.YELLOW, f"lost {active_port} ({e}) — reconnecting…")
                try:
                    ser.close()
                except Exception:
                    pass
                ser, active_port = open_pico()
                ser.write(last_base.encode())
                continue
            if waiting > 0:
                try:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        pico_say(line)
                except (serial.SerialException, OSError) as e:
                    say("🔌💤", C.YELLOW, f"lost {active_port} ({e}) — reconnecting…")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser, active_port = open_pico()
                    ser.write(last_base.encode())
                    continue
                except Exception as e:
                    say("⚠️", C.RED, f"serial read error: {e}")

            # --- 2. WATCH FILES ---
            current_count = count_files(WATCH_DIR)

            if current_count != last_count:
                current_base = get_base_state(current_count)
                now = time.time()

                # streak window: stale streaks fizzle out
                if now - last_event_ts > STREAK_WINDOW:
                    eat_streak = 0
                    sad_streak = 0

                if current_count > last_count:
                    # File added → eat (or grow). Bump eat streak, reset sad streak.
                    sad_streak = 0
                    eat_streak += 1

                    if last_base != 'c' and current_base == 'c':
                        # Crossing threshold INTO chonk: grow then chonk
                        ser.write(b'g')
                        say("💪✨", C.PINK,
                            f"{random.choice(GROW_PHRASES)}  ({last_count} → {current_count})",
                            face=FACES['grow'])
                    else:
                        ser.write(b'e')
                        say("🍎", C.GREEN,
                            f"{random.choice(EAT_PHRASES)}  ({last_count} → {current_count})",
                            face=FACES['eat'])
                        if current_base != last_base:
                            # Eating fades into a different base (e.g. 0->1: hangry -> idle)
                            ser.write(current_base.encode())
                            new_label = BASE_LABELS[current_base]
                            print(f"{stamp()}    {C.DIM}└─ mood: "
                                  f"{BASE_LABELS[last_base]} → {new_label}  "
                                  f"{FACES.get(new_label, '')}{C.RESET}")

                    show_streak(eat_streak, EAT_STREAK_TIERS, C.YELLOW)
                else:
                    # File removed → sad + get-up, or hangry outburst on long delete streak
                    eat_streak = 0
                    sad_streak += 1

                    if sad_streak >= 5:
                        ser.write(b'a')
                        say("🌶️", C.RED,
                            f"{random.choice(HANGRY_RAGE_PHRASES)}  "
                            f"({last_count} → {current_count})",
                            face=FACES['rage'])
                    else:
                        ser.write(b's')
                        say("😢", C.BLUE,
                            f"{random.choice(SAD_PHRASES)}  ({last_count} → {current_count})",
                            face=FACES['sad'])
                    if current_base != last_base:
                        ser.write(current_base.encode())
                        new_label = BASE_LABELS[current_base]
                        print(f"{stamp()}    {C.DIM}└─ mood: "
                              f"{BASE_LABELS[last_base]} → {new_label}  "
                              f"{FACES.get(new_label, '')}{C.RESET}")

                    show_streak(sad_streak, SAD_STREAK_TIERS, C.BLUE)

                last_event_ts = now
                last_count = current_count
                last_base  = current_base

            time.sleep(0.1)

        except KeyboardInterrupt:
            print()
            say("👋", C.PINK, "bye bye! pet is taking a nap...", face="(­ ˘ ω ˘ )zzZ")
            ser.close()
            break
        except Exception as e:
            say("⚠️", C.RED, f"error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
