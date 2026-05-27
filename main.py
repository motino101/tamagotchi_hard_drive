"""
TAMAGOTCHI PLAYER (V8 - Multi-state)

Animations:
  character_anim.bin -> default loop (1..9 files on USB)
  eating_anim.bin      -> one-shot when a file is added
  crying_anim.bin      -> one-shot, then get-up, when a file is removed
  get-up_anim.bin      -> one-shot after crying (chained)
  hangry_anim.bin      -> loop when 0 files on USB, or one-shot on 5+ delete streak (see a)
  grow_anim.bin        -> one-shot when first crossing to 10+ files, then -> chonk
  chonk_anim.bin       -> loop when 10+ files on USB

Protocol (single chars over USB serial from watcher.py):
  e = eat (transient)     i = base idle
  s = sad then get-up     h = base hangry (0 files)
  a = hangry one-shot     c = base chonk
      (5+ delete streak)  g = grow then chonk
OFFSET CONFIG:
  BG_Y_OFFSET   negative = animation up,  positive = animation down
  BAR_Y_OFFSET  negative = bar up,        positive = bar down
"""

from machine import Pin, SPI
import time
import uos
import sys
import uselect
import micropython


# --- CONFIGURATION ---
WIDTH      = 128
HEIGHT     = 128
BYTES_PP   = 2
ROW_BYTES  = WIDTH * BYTES_PP
FRAME_SIZE = WIDTH * HEIGHT * BYTES_PP   # 32768 bytes

BAR_HEIGHT = 24
BAR_SIZE   = WIDTH * BAR_HEIGHT * BYTES_PP  # 6144 bytes

# -------------------------------------------------------
# OFFSETS
BG_Y_OFFSET  = -10   # negative = move animation up, positive = move down
BAR_Y_OFFSET = 2   # negative = move bar up, positive = move bar down
# -------------------------------------------------------

# -------------------------------------------------------
# RUNTIME COLOR REMAP
# Tweak these and reset the Pico - no .bin re-encoding needed.
# Multiple effects can be combined; they're applied in this order:
#   1. invert  2. swap_rb  3. tint
COLOR_INVERT      = False             # True = photo-negative
COLOR_TINT        = None
COLOR_TINT_AMOUNT = 0.30
COLOR_SWAP_RB     = False
# Examples:
#   COLOR_TINT = (255, 102, 170);  COLOR_TINT_AMOUNT = 0.35   # pink wash
#   COLOR_TINT = (80,  180, 255);  COLOR_TINT_AMOUNT = 0.25   # cool/blue
#   COLOR_TINT = (255, 200, 80);   COLOR_TINT_AMOUNT = 0.30   # warm/sunset
# -------------------------------------------------------

TARGET_FPS = 8
FRAME_MS   = int(1000 / TARGET_FPS)

# SPI Setup
spi = SPI(0, baudrate=50000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19))
dc  = Pin(26, Pin.OUT)
cs  = Pin(22, Pin.OUT)
rst = Pin(21, Pin.OUT)

# --- HELPER FUNCTIONS ---
def cmd(c):
    dc.value(0)
    cs.value(0)
    spi.write(bytes([c]))
    cs.value(1)

def data(buf):
    dc.value(1)
    cs.value(0)
    spi.write(buf)
    cs.value(1)

def set_window():
    cs.value(0)
    dc.value(0)
    spi.write(b'\x15')                  # SETCOLUMN
    dc.value(1)
    spi.write(bytes([0, WIDTH - 1]))

    dc.value(0)
    spi.write(b'\x75')                  # SETROW
    dc.value(1)
    spi.write(bytes([0, HEIGHT - 1]))

    dc.value(0)
    spi.write(b'\x5C')                  # WRITERAM
    dc.value(1)

def hard_reset():
    cs.value(1)
    rst.value(1)
    time.sleep(0.1)
    rst.value(0)
    time.sleep(0.2)
    rst.value(1)
    time.sleep(0.2)

def init_display():
    hard_reset()
    cmd(0xFD); data(b'\x12')  # Unlock
    cmd(0xFD); data(b'\xB1')  # Unlock
    cmd(0xAE)                 # Display Off

    # Try 0x74 first for normal orientation.
    # If colors are wrong, try 0x72.
    cmd(0xA0); data(b'\x74')  # Remap
    cmd(0xA1); data(b'\x00')
    cmd(0xA2); data(b'\x00')
    
    # --- ADD THESE ---
    cmd(0xB3); data(b'\xF1')              # Clock div / osc freq (max speed)
    cmd(0xCA); data(b'\x7F')              # Mux ratio (128 rows)
    cmd(0xC1); data(bytes([0xFF, 0xB0, 0xFF]))  # Contrast: R=200, G=128, B=200
    cmd(0xC7); data(b'\x0F')              # Master contrast: max (0x0F)
    cmd(0xB1); data(b'\x32')              # Reset / pre-charge phase
    cmd(0xBB); data(b'\x17')              # Pre-charge voltage
    cmd(0xBE); data(b'\x05')              # VCOMH voltage
    cmd(0xB6); data(b'\x01')              # Second pre-charge period
    # -----------------

    cmd(0xAF)                 # Display On
    time.sleep(0.05)

def clear_framebuf():
    framebuf[:] = BLACK_FRAME

def composite_bg_with_offset():
    """
    Copy anim_buf into framebuf with vertical offset.
    Negative BG_Y_OFFSET = move animation up
    Positive BG_Y_OFFSET = move animation down
    """
    src_row_start = max(0, -BG_Y_OFFSET)
    dst_row_start = max(0,  BG_Y_OFFSET)

    rows = min(HEIGHT - src_row_start, HEIGHT - dst_row_start)
    if rows <= 0:
        return

    for r in range(rows):
        src = (src_row_start + r) * ROW_BYTES
        dst = (dst_row_start + r) * ROW_BYTES
        mv_frame[dst:dst + ROW_BYTES] = mv_anim[src:src + ROW_BYTES]

def composite_bar():
    """Overlay bar onto framebuf."""
    if bar_data and rows_visible > 0:
        mv_bar_region[:] = bar_src


# --- RUNTIME COLOR TRANSFORMS (viper for speed) ---
# All operate IN PLACE on a buffer of big-endian RGB565 bytes.
# Pixel layout in memory: byte0 = RRRRRGGG, byte1 = GGGBBBBB.

@micropython.viper
def _invert_buf(buf: ptr8, nbytes: int):
    i = 0
    while i < nbytes:
        buf[i] = 0xFF ^ int(buf[i])
        i += 1

@micropython.viper
def _swap_rb_buf(buf: ptr8, nbytes: int):
    i = 0
    while i < nbytes:
        b0 = int(buf[i])
        b1 = int(buf[i + 1])
        r5 = (b0 >> 3) & 0x1F
        b5 = b1 & 0x1F
        buf[i]     = (b5 << 3) | (b0 & 0x07)
        buf[i + 1] = (b1 & 0xE0) | r5
        i += 2

@micropython.viper
def _tint_buf(buf: ptr8, nbytes: int, tr: int, tg: int, tb: int, a: int):
    """Blend each pixel toward (tr,tg,tb). a is fixed-point 0..256."""
    inv = 256 - a
    i = 0
    while i < nbytes:
        b0 = int(buf[i])
        b1 = int(buf[i + 1])
        r5 = (b0 >> 3) & 0x1F
        g6 = ((b0 & 0x07) << 3) | ((b1 >> 5) & 0x07)
        b5 = b1 & 0x1F
        # Expand 5/6-bit -> 8-bit
        r8 = (r5 << 3) | (r5 >> 2)
        g8 = (g6 << 2) | (g6 >> 4)
        b8 = (b5 << 3) | (b5 >> 2)
        # Linear blend
        nr = (r8 * inv + tr * a) >> 8
        ng = (g8 * inv + tg * a) >> 8
        nb = (b8 * inv + tb * a) >> 8
        if nr > 255: nr = 255
        if ng > 255: ng = 255
        if nb > 255: nb = 255
        # Re-pack as RGB565 big-endian
        rr = (nr >> 3) & 0x1F
        gg = (ng >> 2) & 0x3F
        bb = (nb >> 3) & 0x1F
        buf[i]     = (rr << 3) | ((gg >> 3) & 0x07)
        buf[i + 1] = ((gg & 0x07) << 5) | bb
        i += 2

# Precompute config so the per-frame check is one bool test
_TINT_R = 0
_TINT_G = 0
_TINT_B = 0
_TINT_A = 0
if COLOR_TINT is not None:
    _TINT_R, _TINT_G, _TINT_B = COLOR_TINT
    _TINT_A = max(0, min(256, int(COLOR_TINT_AMOUNT * 256)))

_RECOLOR_ACTIVE = (COLOR_INVERT
                   or COLOR_SWAP_RB
                   or (COLOR_TINT is not None and _TINT_A > 0))

def apply_color_transform(buf, nbytes):
    """Apply enabled runtime color effects in place on `buf`."""
    if COLOR_INVERT:
        _invert_buf(buf, nbytes)
    if COLOR_SWAP_RB:
        _swap_rb_buf(buf, nbytes)
    if COLOR_TINT is not None and _TINT_A > 0:
        _tint_buf(buf, nbytes, _TINT_R, _TINT_G, _TINT_B, _TINT_A)


# --- SETUP ---
print("A: Booting...")
init_display()
print("B: Display initialized")

# --- PRECOMPUTE BAR POSITION ---
bar_row_start = (HEIGHT - BAR_HEIGHT) + BAR_Y_OFFSET
bar_row_start = max(0, min(HEIGHT - 1, bar_row_start))

rows_visible  = min(BAR_HEIGHT, HEIGHT - bar_row_start)
bytes_visible = rows_visible * ROW_BYTES
bar_byte_start = bar_row_start * ROW_BYTES

print("Bar position:",
      "row", bar_row_start,
      "to", bar_row_start + rows_visible - 1,
      "(" + str(rows_visible) + " rows visible)")

# --- LOAD BAR INTO RAM ONCE ---
bar_data = None
try:
    with open("bar.bin", "rb") as bf:
        raw = bf.read(BAR_SIZE)

    if len(raw) == BAR_SIZE:
        bar_data = bytearray(raw)
        # Apply runtime recolor once so the bar matches the animations
        if _RECOLOR_ACTIVE:
            apply_color_transform(bar_data, BAR_SIZE)
        print("C: Bar loaded ({} bytes)".format(BAR_SIZE))
    else:
        print("Warning: bar.bin short read, bar disabled")
except OSError:
    print("Warning: bar.bin not found, bar disabled")

# --- INPUT POLLING ---
poll_obj = uselect.poll()
poll_obj.register(sys.stdin, uselect.POLLIN)

# --- BUFFERS ---
framebuf = bytearray(FRAME_SIZE)   # final composited frame sent to display
anim_buf = bytearray(FRAME_SIZE)   # raw animation frame from file

mv_frame = memoryview(framebuf)
mv_anim  = memoryview(anim_buf)

BLACK_FRAME = b'\x00' * FRAME_SIZE

# Prepare bar memoryviews once
if bar_data and rows_visible > 0:
    mv_bar_region = mv_frame[bar_byte_start : bar_byte_start + bytes_visible]
    bar_src = memoryview(bar_data)[:bytes_visible]
else:
    mv_bar_region = None
    bar_src = None

# --- ANIMATION FILES ---
# Rename here if your .bin files use different names.
BASE_FILES = {
    'idle':   "character_anim.bin",
    'hangry': "hangry_anim.bin",
    'chonk':  "chonk_anim.bin",
}
TRANSIENT_FILES = {
    'eating':      "eating_anim.bin",
    'sad':         "crying_anim.bin",
    'getup':       "get-up_anim.bin",
    'grow':        "grow_anim.bin",
    'hangry_once': "hangry_anim.bin",
}

# --- ANIMATION ENGINE ---
anim_file         = None
anim_total_frames = 0
anim_current_frame = 0

def close_anim():
    global anim_file
    if anim_file:
        try:
            anim_file.close()
        except Exception:
            pass
        anim_file = None

def load_anim(filename):
    """Open `filename` for streaming frames. Falls back to no-draw on miss."""
    global anim_file, anim_total_frames, anim_current_frame
    close_anim()
    anim_current_frame = 0
    try:
        anim_file = open(filename, "rb")
        anim_total_frames = uos.stat(filename)[6] // FRAME_SIZE
        print("Loaded:", filename, "frames:", anim_total_frames)
    except OSError:
        anim_file = None
        anim_total_frames = 0
        print("Animation missing:", filename)

# --- STATE MACHINE ---
# base_state: looping animation (idle / hangry / chonk)
# transient_state: one-shot (eating / sad / getup / grow / hangry_once)
# pending_base: base to switch to once the transient chain finishes
base_state      = 'idle'
transient_state = None
pending_base    = None

def start_transient(name, after_base=None):
    global transient_state, pending_base
    if name not in TRANSIENT_FILES:
        return
    transient_state = name
    if after_base is not None:
        pending_base = after_base
    load_anim(TRANSIENT_FILES[name])

def end_transient():
    """Called when a one-shot finishes. Resume base (or pending base)."""
    global transient_state, pending_base, base_state
    transient_state = None
    if pending_base is not None and pending_base != base_state:
        base_state = pending_base
    pending_base = None
    load_anim(BASE_FILES[base_state])

def set_base(new_base):
    """Switch base state. If a transient is playing, queue it for after."""
    global base_state, pending_base
    if new_base not in BASE_FILES:
        return
    if transient_state is not None:
        pending_base = new_base
    elif new_base != base_state:
        base_state = new_base
        load_anim(BASE_FILES[base_state])

def _chain_sad_to_getup():
    """After cryingAnim ends, play get-up before returning to base."""
    global transient_state
    if 'getup' not in TRANSIENT_FILES:
        end_transient()
        return
    transient_state = 'getup'
    load_anim(TRANSIENT_FILES['getup'])

def _finish_transient_or_chain():
    """End one-shot, or go sad -> get-up when applicable."""
    if transient_state == 'sad' and 'getup' in TRANSIENT_FILES:
        _chain_sad_to_getup()
    else:
        end_transient()

def handle_command(ch):
    if   ch == 'e': start_transient('eating')
    elif ch == 's': start_transient('sad')
    elif ch in ('a', 'A'): start_transient('hangry_once')
    elif ch == 'g': start_transient('grow', after_base='chonk')
    elif ch == 'i': set_base('idle')
    elif ch == 'h': set_base('hangry')
    elif ch == 'c': set_base('chonk')

# Boot in idle by default; watcher will correct us on its first tick.
load_anim(BASE_FILES[base_state])

# --- MAIN LOOP ---
try:
    next_t = time.ticks_ms()

    while True:
        # Drain any pending commands from the watcher
        while poll_obj.poll(0):
            ch = sys.stdin.read(1)
            if ch:
                handle_command(ch)

        if anim_file is None or anim_total_frames == 0:
            # Unplayable animation. If it was a transient, skip it and
            # fall through to the pending/current base so we don't freeze.
            if transient_state is not None:
                print("Transient unplayable, skipping:", transient_state)
                end_transient()
                continue
            # Base is missing too - nothing we can draw, just idle
            time.sleep_ms(FRAME_MS)
            continue

        # 1) Load raw animation frame into temp buffer
        anim_file.seek(anim_current_frame * FRAME_SIZE)
        n = anim_file.readinto(mv_anim)

        if n != FRAME_SIZE:
            # Truncated/wrong file; never spin without sleep (keeps USB responsive)
            anim_current_frame = 0
            time.sleep_ms(10)
            continue
        
        # Temporarily add after step 1b:
        if anim_current_frame == 0:
            print("RECOLOR_ACTIVE:", _RECOLOR_ACTIVE, 
                  "TINT_A:", _TINT_A,
                  "first bytes:", list(anim_buf[:6]))
        

        # 1b) Runtime color transform (in place on anim_buf)
        if _RECOLOR_ACTIVE:
            apply_color_transform(anim_buf, FRAME_SIZE)

        # 2) Clear final framebuffer
        clear_framebuf()

        # 3) Composite shifted background
        composite_bg_with_offset()

        # 4) Composite bar on top
        composite_bar()

        # 5) Draw full frame in one SPI transaction
        set_window()
        spi.write(mv_frame)
        cs.value(1)

        # 6) Advance frame
        anim_current_frame += 1
        if anim_current_frame >= anim_total_frames:
            if transient_state is not None:
                # One-shot finished: sad -> get-up chain, or resume base
                _finish_transient_or_chain()
            else:
                # Base animation loops forever
                anim_current_frame = 0

        # 7) Stable frame pacing
        next_t = time.ticks_add(next_t, FRAME_MS)
        sleep_ms = time.ticks_diff(next_t, time.ticks_ms())

        if sleep_ms > 0:
            time.sleep_ms(sleep_ms)
        else:
            next_t = time.ticks_ms()

except KeyboardInterrupt:
    print("\nStopped by user")
except Exception as e:
    print("\nError:", e)

finally:
    close_anim()
    cs.value(1)
    print("Cleaned up and closed.")

