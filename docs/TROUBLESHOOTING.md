# Troubleshooting

Start here: **what is the light doing?**

## The light

| Light | Meaning | What to do |
|---|---|---|
| **Solid on** | Ready | Press the button |
| **Flickering fast** | Printing | Wait |
| **Fast blink at power-on** | Starting up | Wait ~30 seconds |
| **Blink · blink · pause** | Printer offline, or the button is jammed or not working | [Printer offline](#the-light-blinks-twice-printer-offline) |
| **Blink · blink · blink · pause** | Out of paper, or the paper lid is open | Load paper, close the lid firmly |
| **Slow blink, one second on, one off** | No images found on the SD card | [No images](#the-light-blinks-slowly-no-images) |
| **Off entirely** | No power, or the service is not running | [Nothing at all](#nothing-happens-at-all) |

## The restart procedure

Nine times out of ten this fixes it, and anyone can do it:

1. **Unplug the Pi** (the small board). Leave the printer alone.
2. **Count to ten.**
3. **Plug the Pi back in.**
4. Wait about 30 seconds. The light goes from fast-blinking to solid.
5. Press the button.

If that does not do it, do the same thing to both:

1. Unplug the Pi **and** the printer.
2. Count to ten.
3. Plug the **printer** in first, wait for its own light to settle.
4. Plug the **Pi** in.
5. Wait 30 seconds for a solid light.

You cannot break anything by doing this. The Joybox is built to survive being
unplugged mid-print.

## The status slip

**Hold the button down for five seconds.** The Joybox prints a slip with its
version, network address, how many images it can see, what the printer is
saying, and the last error it hit. That answers most questions without a
laptop, and it is the first thing to do before calling anyone.

---

## Symptoms

### Nothing happens at all

The light is off and pressing does nothing.

1. Is the Pi's power light on? Check the plug and the socket.
2. Is the cable in the Pi's **outer** micro-USB port, marked `PWR`? The middle
   one is for the printer.
3. Do the [restart procedure](#the-restart-procedure).
4. If you have SSH: `sudo systemctl status joybox` will say what happened.

### The light blinks twice: printer offline

The Joybox cannot reach the printer.

1. Is the printer powered on, with its own light on?
2. Is the USB cable in the Pi's **middle** micro-USB port, marked `USB`? The
   outer one is power only and will never see the printer.
3. Is the OTG adapter fully seated? It is the most common culprit.
4. Unplug the printer's USB from the Pi, count to five, plug it back in. The
   Joybox reconnects on its own; you do not need to restart anything.
5. Still nothing → [restart procedure](#the-restart-procedure).

This code also means **the button is jammed** — held down for more than 30
seconds. The Joybox deliberately ignores it so a stuck button cannot print the
whole roll. Free the button and the light goes back to solid on its own.

It also means **the Joybox could not claim the button pin at all**, which is a
software fault rather than a wiring one. The service keeps running so you can
still print by hand and still ask it what is wrong; `joybox doctor` names the
cause, and `systemctl status joybox` says `button not watched`.

### The light blinks three times: out of paper

1. Open the printer lid and check the roll.
2. Thermal paper only prints on one side. Feed it **shiny side up**, towards
   the print head.
3. Close the lid until it clicks. A lid that is not fully latched reads as an
   error.
4. The light goes solid within 15 seconds. No restart needed.

### The light blinks slowly: no images

The Joybox cannot find any images to print.

1. Power down, take the SD card out, and put it in a laptop.
2. Open the drive named **bootfs**, then the `heart-joybox` folder.
3. There must be a `body` folder with at least one `.png` in it.
4. Check the images really are PNGs and not `.png.txt` or zero bytes.
5. Card back in, power up.

### It prints, but the right edge is cut off

Your printer is a 512-dot model, not 576.

Edit `config.toml` on the SD card:

```toml
[print]
width_dots = 512
```

Run `joybox test` to confirm the black bar now reaches both edges.

### It prints blank paper

- The paper is in upside down. Thermal paper only marks on one side — turn the
  roll over.
- Or your images are white-on-black. The printer marks black pixels; a white
  design on a black background comes out as a solid black slab or nothing.
  Design black on white.

### The print is faint or streaky

- The roll is nearly done — the last few metres are often poor.
- The print head needs cleaning. Power off, wipe the thin grey strip inside the
  lid with isopropyl alcohol on a cotton bud, let it dry fully.
- Cheap thermal paper fades. Try a different roll.

### The images look muddy or dotty

You have `dither = true` in `config.toml`. That is meant for photographs. For
text and line art set `dither = false` (the default).

If text looks *thin* rather than muddy, lower the threshold to make more of it
black:

```toml
[print]
dither = false
threshold = 100     # default is 128; lower = more black
```

### Stray characters at the top of each receipt

The printer is printing the status request instead of answering it. Turn the
request off:

```toml
[printer]
status_check = false
```

Everything else keeps working; you lose the out-of-paper light.

### It cuts in the wrong place

Too little paper before the cut, so the last line is chopped:

```toml
[print]
feed_lines_before_cut = 6     # default is 4
```

Too much blank paper after each receipt: lower it.

### The paper jams in the cutter

1. Power the printer off.
2. Open the lid and gently pull the jammed paper out **in the direction it was
   feeding**. Never pull it backwards through the cutter.
3. If the blade is stuck out, most X-Series printers have a small manual release
   wheel behind the front cover.
4. Reload, close the lid, power on.

### Someone is mashing the button

That is already handled: presses inside the 5-second cooldown are ignored. For
an unattended station you can also cap the hour:

```toml
[button]
max_prints_per_hour = 120
```

### The button does nothing but the light is solid

The light being solid means the software is fine, so it is the wiring.

1. Power down and unplug.
2. Check both jumper wires: **pin 11** and **pin 9**, counting carefully.
   [WIRING.md](WIRING.md) has the pin map.
3. If your button has NO/NC terminals, you want **NO** and **C**.
4. With SSH you can test the software side without the button:
   `joybox print` prints a receipt. If that works, it is definitely wiring.

**Testing with a bare jumper wire** before the real button arrives works, but a
wire has no debounce and behaves in three ways that look like faults and are
not: a single touch may register once (the 50 ms debounce absorbs the rest of
the chatter); holding it on for more than five seconds prints the diagnostics
slip instead of a receipt; and leaving it shorted for thirty seconds trips the
jammed-button lockout and the two-blink code until you part the wires.

### It worked yesterday and now the light never goes solid

Almost always the SD card. Reflash it following [SETUP.md](SETUP.md) — and turn
on the read-only overlay in step 9 this time, which prevents it.

---

## With SSH

```bash
ssh joybox@joybox.local
```

### The one command that explains everything

```bash
joybox doctor
```

Every line either says `ok` or names its own fix.

`doctor` checks the account you run it from. The service runs as the `joybox`
user, so if your own shell passes but the station still does not work, check
what the service sees:

```bash
sudo -u joybox joybox doctor
```

### Watch what it is doing, live

```bash
journalctl -u joybox -f
```

Press the button and watch. Ctrl-C to stop.

### What went wrong earlier

```bash
journalctl -u joybox -b --no-pager | tail -50   # this boot
journalctl -u joybox -p err --no-pager          # errors only, all boots
```

### Restart, stop, start

```bash
sudo systemctl restart joybox
sudo systemctl stop joybox      # to work on it by hand
sudo systemctl start joybox
systemctl status joybox
```

### Print without the button

```bash
joybox print          # one receipt, exactly as the button does
joybox test           # the self-test page
joybox diagnostics    # the status slip
joybox list           # what images it can see
joybox status         # what the printer says
joybox feed 5         # feed 5 lines
joybox cut            # cut now
```

### Common fixes

**`cannot open /dev/joybox-printer: Permission denied`**

```bash
sudo usermod -aG lp joybox && sudo systemctl restart joybox
```

**`joybox cannot use /dev/gpiochip0`**

```bash
sudo usermod -aG gpio joybox && sudo systemctl restart joybox
```

**Cannot read the SD card** — the boot partition is mounted so the service user
cannot read it. Check with `ls -l /boot/firmware/heart-joybox`. If the files are
not world-readable, add `,umask=0022` to the `/boot/firmware` line in
`/etc/fstab` and reboot.

**No printer device at all**

```bash
lsusb                       # is the printer listed?
ls -l /dev/usb/ /dev/joybox-printer
dmesg | grep -i usblp       # did the kernel bind the printer driver?
```

If `lsusb` shows nothing, it is the cable, the OTG adapter, or the wrong
micro-USB port. If `lsusb` shows the printer but there is no `lp0`, the
`usblp` driver did not attach — `sudo modprobe usblp` and replug.

**The service restarts every few seconds, and the log mentions `.lgd-nfy`**

```
xCreatePipe: Can't set permissions (436) for //.lgd-nfy0, No such file or directory
PinFactoryFallback: Falling back from lgpio: ... '.lgd-nfy-3'
```

lgpio makes a small pipe in its working directory when it is imported, and a
service started in a directory it cannot write to never gets one. The traceback
that follows blames `/sys/class/gpio`, which is a red herring — that is the
*last* driver gpiozero tried, not the one that failed first. Fixed by the
`WorkingDirectory=` line in the shipped unit, so the fix is to update:

```bash
cd ~/heart-joybox && sudo ./scripts/update.sh
systemctl show joybox -p WorkingDirectory     # expect /run/joybox
ls -la /run/joybox/                           # expect a .lgd-nfy* pipe
```

**`gpio driver` says gpiozero fell back to NativeFactory** — that driver can
light the LED but cannot see a button press, so the station looks ready and
never prints. Install the real driver and restart:

```bash
sudo apt install python3-lgpio && sudo systemctl restart joybox
```

The unit names `lgpio` outright rather than letting gpiozero pick, so a problem
is one loud error instead of a silent fallback. On a board that genuinely needs
a different driver, `sudo systemctl edit joybox` and add an empty
`Environment=GPIOZERO_PIN_FACTORY=` to restore auto-detection.

**`setlocale: LC_CTYPE: cannot change locale (UTF-8)` when you log in** — comes
from your laptop, not the Pi, and nothing is broken. macOS sends `LC_CTYPE=UTF-8`,
which is not a locale name Linux knows. Stop sending it, on your Mac in
`~/.ssh/config`:

```
Host joybox joybox.local
    SetEnv LC_CTYPE=C.UTF-8
```

(Terminal → Settings → Profiles → Advanced → *Set locale environment variables
on startup* is where it comes from.) The service itself is unaffected: it runs
under `C.UTF-8`, set in the unit.

**A bad `config.toml`** cannot stop the Joybox — it logs the problem, uses the
defaults, and keeps printing. See exactly what it made of your file with:

```bash
joybox doctor | grep config
```

### Start completely fresh

```bash
cd ~/heart-joybox
git pull
sudo ./scripts/install.sh
```

Safe to run any time. Your artwork and `config.toml` are never touched.

To remove it entirely: `sudo ./scripts/uninstall.sh`.

---

## What is already handled for you

You do not need to intervene for any of these:

| Situation | What happens |
|---|---|
| Power cut | Comes back printing by itself |
| Printer off at boot, plugged in later | Picked up automatically |
| Printer power-cycled while running | Reconnects on the next press |
| Software crash | systemd restarts it within 3 seconds |
| Software hangs | Watchdog restarts it within 2 minutes |
| Kernel lockup | Hardware watchdog reboots the Pi within 15 seconds |
| One corrupt image | Skipped; the rest of the receipt still prints |
| A typo in `config.toml` | Ignored; defaults used; keeps printing |
| Button mashing | Cooldown drops the extra presses |
| Button jammed down | Locked out until released |
| An image far too tall | Scaled down instead of eating the roll |
| Mac/Windows junk files on the card | Ignored |
| Log files growing | Capped at 50 MB |
