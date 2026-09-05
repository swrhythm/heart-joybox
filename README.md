# ❤️ Heart Joybox

A button on a stand. Someone presses it, and a thermal printer hands them a
Bible verse with your welcome message, your Instagram handle and a QR code —
then cuts the paper.

Built for a Raspberry Pi Zero WH and an 80mm ESC/POS thermal printer, and
designed to be left running unattended.

```
   ┌─────────┐        ┌──────────┐        ┌─────────────────┐
   │ button  │───────▶│ Pi Zero  │───USB─▶│  80mm printer   │
   │ + light │        │          │        │  + auto-cutter  │
   └─────────┘        └──────────┘        └─────────────────┘
                            │
                    ┌───────┴────────┐
                    │  SD card       │   header.png
                    │  heart-joybox/ │   body/1.png, 2.png, 3.png …
                    └────────────────┘   footer.png
```

Every receipt is **header → one random body image → footer → cut**.

## Updating the content takes no technical knowledge

Everything printed is an image on the SD card's ordinary FAT32 boot partition.
Take the card out, put it in any Windows or Mac laptop, drag PNGs into the
`heart-joybox` folder, put it back. No SSH, no network, no Linux, no code.

Add fifty verses by dropping in fifty PNGs. Change the Instagram handle by
editing one image.

## Built to be left alone

| Situation | What happens |
|---|---|
| Power cut | Comes back printing by itself |
| Printer off at boot, or power-cycled later | Reconnects on its own |
| Out of paper | Light shows it; no queue builds up to dump later |
| One corrupt image | Skipped; the rest of the receipt still prints |
| A typo in `config.toml` | Ignored; defaults used; keeps printing |
| Button mashing | 5-second cooldown, plus an optional hourly cap |
| Button jammed down | Locked out until released |
| An image far too tall | Scaled down instead of eating the roll |
| Software crash or hang | Restarted by systemd within seconds |
| Kernel lockup | Rebooted by the hardware watchdog within 15 seconds |

And when something does need a human, the button's light says which of six
things it is — from across the room, with no laptop.

## The light

| Light | Meaning |
|---|---|
| Solid on | Ready |
| Fast flicker | Printing |
| 2 blinks, pause | Printer offline, or button jammed or not working |
| 3 blinks, pause | Out of paper, or lid open |
| Slow 1-second blink | No images found on the card |
| Fast blink at power-on | Starting up |

**Hold the button for five seconds** and it prints a diagnostics slip — network
address, image count, printer state, last error. That is how the station gets
diagnosed with nobody technical present.

## Install

```bash
git clone https://github.com/swrhythm/heart-joybox.git
cd heart-joybox
sudo ./scripts/install.sh
```

Full walkthrough from a blank SD card: **[docs/SETUP.md](docs/SETUP.md)**.

## Hardware

- Raspberry Pi Zero WH (headers pre-soldered)
- Iware X-Series XS-80UL or any 80mm ESC/POS printer with an auto-cutter
- An illuminated arcade button, two jumper wires (four with the light)
- Micro-USB OTG adapter, and a 5V 2.5A supply for the Pi

The printer runs off its own power brick — never off the Pi.

Wiring, including the transistor you need if your button's LED is 5V or 12V:
**[docs/WIRING.md](docs/WIRING.md)**.

## Commands

```bash
joybox doctor      # check everything a button press needs, and name the fix
joybox test        # self-test page (confirms 576 vs 512 dot width)
joybox print       # one receipt, exactly as the button does
joybox list        # what images it can see
joybox status      # what the printer says about itself
joybox render body/1.png --preview check.png    # preview on your laptop
journalctl -u joybox -f                          # watch it work
```

## Documentation

| | |
|---|---|
| **[SETUP.md](docs/SETUP.md)** | Blank SD card to first receipt |
| **[WIRING.md](docs/WIRING.md)** | Pin map, LED voltages, what not to fry |
| **[CONTENT.md](docs/CONTENT.md)** | Export settings, QR sizing, swapping artwork |
| **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | Light codes, restart procedure, every failure |
| **[STATION_CARD.md](docs/STATION_CARD.md)** | One page to tape inside the stand |

## How it works

`gpiozero` watches the button. Images are rendered once into ESC/POS raster
bytes and cached, so a press is a byte blit rather than a computation — the Pi
Zero is slow enough that rendering on the press would be felt as dead air. The
printer is reached as a character device through the kernel's `usblp` driver,
so unplugging it simply makes the next write fail and reopen.

Printer status is best effort by design: a positive "out of paper" blocks the
job, but an unreadable status never does. A printer whose status line we cannot
read must not become a printer that refuses to print.

Runtime dependencies are three apt packages — Pillow, gpiozero, lgpio. The
ESC/POS layer and the raster encoder are in this repo, which keeps a Pi Zero
install to seconds instead of a long compile.

## Development

```bash
pip install pillow pytest
pytest
```

95 tests, no Raspberry Pi required — GPIO is imported lazily so the whole suite
and every CLI command except `run` work on a laptop.

## Licence

MIT
