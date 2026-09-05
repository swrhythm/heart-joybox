# Wiring

**Do all of this with the Pi unplugged.** Getting a wire wrong on a powered Pi
is how you kill a Pi.

## Parts

| Part | Notes |
|---|---|
| Raspberry Pi Zero WH | The WH already has the header soldered on |
| Illuminated arcade button (60mm) | Any momentary push button works |
| 2 × female-to-female jumper wires | Button |
| 2 more jumper wires | LED, if your button has one |
| 330 Ω resistor | Only for a bare 3.3V LED — see below |
| 5V 2.5A micro-USB supply | For the Pi |
| Micro-USB OTG adapter, or micro-USB-to-USB-A cable | Pi to printer |

The printer has **its own power brick**. Never try to power the printer from
the Pi — the Pi Zero cannot supply anything close to what a thermal head draws,
and you will brown out the Pi mid-print.

## Pin map

GPIO numbers below are **BCM** numbers (the ones in `config.toml`). The
physical pin numbers are what you count on the board.

| Signal | BCM GPIO | Physical pin |
|---|---|---|
| Button | GPIO 17 | pin 11 |
| Button ground | — | pin 9 |
| LED | GPIO 27 | pin 13 |
| LED ground | — | pin 14 |

They are four pins in a row on the same side of the header, which makes this
hard to get wrong:

```
        3V3  (1) (2)  5V
      GPIO2  (3) (4)  5V
      GPIO3  (5) (6)  GND
      GPIO4  (7) (8)  GPIO14
        GND  (9) (10) GPIO15      <- pin 9  : button ground
     GPIO17 (11) (12) GPIO18      <- pin 11 : button
     GPIO27 (13) (14) GND         <- pin 13 : LED   pin 14 : LED ground
     GPIO22 (15) (16) GPIO23
```

## The button

Two wires, no resistor, no polarity:

```
   pin 11 (GPIO17) ────────┐
                        [button]
   pin 9  (GND) ───────────┘
```

The Pi holds GPIO17 high internally and the button pulls it to ground. If your
button's terminals are labelled, use **NO** (normally open) and **C** (common),
not NC.

## The LED

Check the LED's voltage before wiring it. This is the one step where a mistake
damages hardware: **a GPIO pin is 3.3V and can supply about 16mA.** Many arcade
buttons ship with a 5V or 12V LED module.

### Bare 3mm/5mm LED, or a 3.3V module

Direct, with a resistor to limit current:

```
   pin 13 (GPIO27) ──[330 Ω]──▶|── pin 14 (GND)
                              LED
```

The flat side of the LED (short leg, cathode) goes to ground.

### 5V LED module — use a transistor

```
                    +5V (pin 2 or 4)
                        │
                     [LED module]
                        │
   pin 13 (GPIO27) ──[1 kΩ]── base
                          2N2222 / BC547
                    collector ── (to LED module)
                       emitter ── GND (pin 14 or 6)
```

### 12V LED module — use a logic-level MOSFET

Same shape as above, but a logic-level N-channel MOSFET (2N7000, IRLZ44N) with
the 12V supply feeding the LED and **the 12V supply's ground tied to a Pi GND
pin**. Never let 12V touch a GPIO pin.

### No LED at all

Fine — set `enabled = false` under `[led]` in `config.toml`. Everything works;
you just lose the at-a-glance status. Holding the button still prints a
diagnostics slip.

## Optional: a safe shutdown button

Pulling the power on a running Pi can corrupt the SD card. A second momentary
button between **pin 5 (GPIO3)** and **pin 6 (GND)** gives you a proper
shutdown. Add this line to `/boot/firmware/config.txt`:

```
dtoverlay=gpio-shutdown,gpio_pin=3
```

**Where the line goes matters.** `config.txt` is split into sections that only
apply to certain models — `[all]`, `[pi4]`, `[cm4]`, `[pi5]` — and a line under
`[pi5]` is silently ignored on a Zero. Everything after a heading belongs to it
until the next one, so put the line **under `[all]`**. Check the last heading in
the file before you append to the end:

```bash
grep -n '^\[' /boot/firmware/config.txt      # [all] should be the last one
```

Editing the card on a Mac or PC instead? It is `config.txt` in the root of the
small FAT partition — the only one those machines will mount. On the Pi itself
it is `/boot/firmware/config.txt`; older guides say `/boot/config.txt`, which
has been a leftover symlink since Bookworm.

Reboot, then confirm the overlay actually loaded:

```bash
grep -i gpio-shutdown /proc/bus/input/devices    # a match means it is live
```

Then a press shuts the Pi down cleanly, and a press while it is off wakes it
up. (If you enable the read-only filesystem in `SETUP.md` step 9, pulling the
power is already safe and you do not need this.)

## After wiring

1. Plug the printer into the Pi's **middle** micro-USB port (marked `USB`).
   The outer one, marked `PWR`, is power only and will not see the printer.
2. Power the printer from its own brick.
3. Power the Pi last.
4. `joybox doctor` should now pass `gpio access` and `printer device`.
