# Bit the Tamagotchi USB Drive

A Tamagotchi that lives on a Raspberry Pi Pico and reacts to files you give him.![[Main.00_00_02_07.Still001.jpg]]

## What You Need

**Hardware:**
- Raspberry Pi Pico
- ST7735 LCD screen (128x128)
- USB cable
- Jumper wires

**Software:**
- [Thonny IDE](https://thonny.org/) (for uploading code to Pico)
- Python 3 (for running the watcher)

## Setup Instructions

### Step 0: Wire Up the Hardware

Connect your ST7735 LCD screen to the Pico:

| LCD Pin | Pico Pin | Purpose |
|---------|----------|---------|
| VCC     | 3.3V     | Power   |
| GND     | GND      | Ground  |
| SCK     | GP18     | SPI Clock |
| MOSI    | GP19     | SPI Data |
| CS      | GP22     | Chip Select |
| RST     | GP21     | Reset |
| DC      | GP26     | Data/Command |

**Tip:** Double-check your connections before powering on. Wrong wiring can damage the screen.

![Wiring diagram][to-do]
![Completed setup][to-do]

### Step 1: Install MicroPython on your Pico

1. Download MicroPython from [micropython.org](https://micropython.org/download/RPI_PICO/)
2. Hold the BOOTSEL button on your Pico and plug it into your computer
3. Drag the downloaded `.uf2` file onto the Pico (it shows up as a USB drive)
4. The Pico will reboot automatically

### Step 2: Upload Code to the Pico

1. Open **Thonny**
2. Connect your Pico via USB
3. In Thonny, select your Pico: **Run → Select Interpreter → MicroPython (Raspberry Pi Pico)**
4. Upload these files to the Pico:
   - `main.py` (main program)
   - `art/` folder (all the animation files)

**How to upload:**
- Right-click each file → "Upload to /"
- For the art folder: create a folder called `art` on the Pico, then upload all `.bin` files into it

![Thonny upload process][to-do]

### Step 3: Set Up the Watcher on Your Computer

1. Copy `watcher.py` to your Desktop (or anywhere on your computer)
2. Open Terminal
3. Install the required package:
   ```bash
   pip3 install pyserial
   ```

### Step 4: Run the Watcher

1. Make sure your Pico is plugged in
2. In Terminal, navigate to where you saved `watcher.py`:
   ```bash
   cd ~/Desktop
   ```
3. Run the watcher:
   ```bash
   python3 watcher.py
   ```

**Note:** The watcher auto-detects your Pico's serial port. If it doesn't work, open `watcher.py` and change line 28 from:
```python
PICO_PORT = "auto"
```
to your specific port (e.g., `"/dev/cu.usbmodem11201"` on Mac or `"COM3"` on Windows)

## How It Works

- The Pico shows up as a USB drive called "hey im bit"
- When you add files to the drive, Bit gets fatter and happier
- When you delete files, Bit gets sad
- The watcher script monitors the drive and tells the Pico how to react via serial commands

### States:
- **0 files** = Hangry (sad loop)
- **1-9 files** = Happy (normal loop)
- **10+ files** = Chonk (fat and happy)

## Troubleshooting

**Pico not detected?**
- Check the USB cable (some are power-only)
- Try a different USB port
- Restart Thonny and reconnect

**Watcher not connecting?**
- Make sure the Pico is plugged in and `main.py` is running
- Check the serial port in `watcher.py` (set `PICO_PORT` to your specific port)
- On Mac: `ls /dev/cu.*` to find your port

**Animations not working?**
- Make sure all `.bin` files are in the `art/` folder on the Pico
- Check that the file names match exactly (case-sensitive)

## See Bit in Action

![Bit reacting to files][to-do]

## Credits

Built with love. Sometimes it feels like he loves me back.
