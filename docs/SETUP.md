# Setup: from a blank SD card to a working station

Allow about an hour the first time, most of it waiting for downloads.

---

## 1. Flash the SD card

Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your
laptop, put an 8GB+ microSD card in, and choose:

- **Device:** Raspberry Pi Zero
- **Operating System:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (32-bit)**
- **Storage:** your card

> **It must be the 32-bit image.** The Pi Zero W is an ARMv6 chip. The 64-bit
> image will not boot on it, and you will get a black screen with no
> explanation.

Click the gear/**Edit Settings** button before writing and set:

- **Hostname:** `joybox`
- **Username and password:** pick your own and write them down
- **Configure wireless LAN:** your WiFi name and password
- **Services → Enable SSH** → use password authentication

WiFi is only for setup and troubleshooting. Once installed, the station prints
perfectly well with no network at all.

Write the card, then put it in the Pi.

## 2. First boot

Power the Pi (outer micro-USB port, marked `PWR`). Give it three or four
minutes on its first boot — it resizes the filesystem and reboots itself.

From your laptop:

```bash
ssh joybox@joybox.local
```

If `joybox.local` does not resolve, find the Pi's address in your router's
device list and use that instead.

If the login prints a row of `setlocale: LC_CTYPE: cannot change locale (UTF-8)`
warnings, nothing is wrong — that is your laptop's terminal, not the Pi, and the
Joybox does not care. [TROUBLESHOOTING.md](TROUBLESHOOTING.md) has the one-line
fix if the noise bothers you.

## 3. Install

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/swrhythm/heart-joybox.git
cd heart-joybox
sudo ./scripts/install.sh
```

The installer prints what it is doing and finishes with a checklist. Two lines
will fail at this point — the printer is not plugged in yet and the button is
not wired. That is expected.

If it tells you it added your user to a group, log out and back in
(`exit`, then `ssh` again) so that takes effect.

Re-running `install.sh` later is safe: it never overwrites your artwork or your
`config.toml`.

## 4. Wire the button

**Shut down first**, then unplug:

```bash
sudo shutdown -h now
```

Wire the button and LED following **[WIRING.md](WIRING.md)**.

## 5. Connect the printer

1. Printer into its own power brick. Not the Pi.
2. Micro-USB OTG adapter into the Pi's **middle** port, marked `USB`.
3. Printer's USB cable into the adapter.
4. Load a roll of 80mm thermal paper, shiny side facing the print head, and
   close the lid firmly.
5. Power the printer on, then the Pi.

Order does not actually matter — the Joybox reconnects whenever the printer
appears — but this order gets you a working station fastest.

## 6. Check and test

```bash
joybox doctor
```

Everything should read `ok`. If not, each line names its own fix, and
**[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** covers them in detail.

Then print the self-test page:

```bash
joybox test
```

Look at the long black bar. **It must touch both edges of the paper.** If its
right-hand end is missing, your printer is a 512-dot model: edit `config.toml`
on the SD card (see step 8) and set `width_dots = 512`.

## 7. Press the button

That is the whole product. You should get: header, a random verse, footer, and
a cut.

Press it ten times. You should see every body image once before any of them
repeats.

## 8. Put your own artwork on

Shut the Pi down, take the SD card out, and put it in your laptop. The card
shows up as a drive named **bootfs** (or `boot`). Inside it is a folder:

```
heart-joybox/
├── config.toml      <- settings, editable in any text editor
├── header.png       <- printed at the top of every receipt
├── footer.png       <- printed at the bottom of every receipt
└── body/
    ├── 1.png        <- one of these is chosen at random
    ├── 2.png
    └── 3.png
```

Replace the images with your own and add as many to `body/` as you like.
**[CONTENT.md](CONTENT.md)** has the export settings.

Put the card back, power up, press the button.

## 9. Optional hardening for an unattended station

Two things worth doing before you leave the station alone for a day.

**Cap the receipts per hour.** In `config.toml` on the card:

```toml
[button]
max_prints_per_hour = 120
```

**Make the SD card read-only**, so pulling the power can never corrupt it:

```bash
sudo raspi-config
```

→ *Performance Options* → *Overlay File System* → enable the overlay, and leave
the **boot partition writable** when it asks. Reboot.

After this the Pi forgets any change to its own filesystem on every reboot,
which is exactly what you want for a kiosk. Your artwork still updates
normally, because it lives on the boot partition.

To install updates later, turn the overlay off in `raspi-config`, reboot, do
the work, turn it back on, reboot.

## What you end up with

- Powering on the Pi is the entire startup procedure.
- If the software crashes, systemd restarts it within seconds.
- If the Pi itself locks up, the hardware watchdog reboots it within 15 seconds.
- If the power cuts, it comes back printing on its own.

Leave the card from **[STATION_CARD.md](STATION_CARD.md)** at the station for
whoever is nearby.
