# Making and swapping the artwork

Everything printed is an image you supply. Nothing in the code knows what a
Bible verse is, so changing the content never means changing code.

## The three parts

Every receipt is printed as: **header → one random body image → footer → cut.**

```
heart-joybox/            <- on the SD card, visible from any laptop
├── config.toml
├── header.png           <- every receipt starts with this
├── footer.png           <- every receipt ends with this
└── body/
    ├── 1.png            <- exactly one of these is chosen per press
    ├── 2.png
    ├── 3.png
    └── ...              <- add as many as you like
```

Put your logo and welcome in `header.png`, your verses in `body/`, and your
Instagram handle and QR code in `footer.png`.

`header.png` and `footer.png` are optional. Delete either and receipts print
without it.

## Export settings

| Setting | Value |
|---|---|
| Width | **576 pixels** exactly |
| Height | Whatever you like; under 2000 px is a sensible receipt |
| Format | PNG (JPG, BMP and WEBP also work) |
| Colours | Pure black on pure white |
| Colour mode | RGB or greyscale, either is fine |

The printer has no greys. Every pixel comes out either black or white, split at
50% by default. Design in black and white and what you see is what you get.

Anything that is not 576 px wide is scaled to fit, so a 1152 px export works
fine — it is just scaled down. Very small images get scaled *up* and look soft,
so do not export at 300 px and expect crisp text.

### How much paper

576 px wide is 72 mm across. Vertically, **203 pixels is 25 mm of paper**:

| Body image height | Paper used |
|---|---|
| 400 px | 5 cm |
| 800 px | 10 cm |
| 1600 px | 20 cm |

A header of 250 px plus a body of 800 px plus a footer of 450 px is about
19 cm of paper per receipt — roughly 400 receipts from an 80 mm × 80 m roll.

Any single image taller than 3000 px is automatically scaled down rather than
printed, so one wrong export cannot eat the roll. Change that limit with
`max_image_height` in `config.toml`.

## QR codes

Thermal printing is low contrast and slightly fuzzy, so QR codes need to be
generous:

- **At least 200 px square** in your artwork (that is 25 mm on paper). Smaller
  than that and phones struggle.
- **Error correction M or Q.** Not L.
- Keep the white quiet zone around it — do not crop tight to the edge.
- Pure black on pure white. No gradients, no colour, no logo in the middle.

Generate the QR wherever you like (Canva, a QR site, your phone) and place it
in `footer.png`. Test it by scanning the actual printed receipt, not the
screen.

## Making the images

Any tool that exports PNG works. In Canva, make a custom size of 576 × 800 px,
design in black and white, and export as PNG.

The sample artwork that ships with the Joybox was made by
`scripts/make_samples.py`. Regenerate it at any width if you want a starting
point:

```bash
python3 scripts/make_samples.py --width 576 --out ./my-content
```

## Checking before you print

Preview exactly what the paper will look like, on your laptop, before the card
goes anywhere near the Pi:

```bash
joybox render body/1.png --preview check.png
```

It prints the size in dots and how many millimetres of paper the image uses,
and writes `check.png` showing the image after black-and-white conversion. If
text has gone patchy or a QR has gone muddy, you will see it there.

## Swapping the artwork

1. Shut down: `sudo shutdown -h now`, or hold the optional shutdown button.
2. Wait for the green light on the Pi to stop flickering, then unplug it.
3. Take the SD card out and put it in your laptop.
4. Open the drive named **bootfs** (Windows/Mac may call it `boot`).
5. Change what you like inside the `heart-joybox` folder.
6. Eject the card properly, put it back in the Pi, power up.

The Joybox also notices new images **without a restart** if you add them over
the network — handy while you are designing.

### Files that get ignored

Copying to a card from a Mac or Windows leaves invisible sidecar files behind.
These are all ignored, so they never print as a blank receipt:

`._1.png` · `.DS_Store` · `Thumbs.db` · `desktop.ini` · `__MACOSX/` ·
anything starting with a dot · anything that is not an image · empty files

### Naming

Body images are sorted the way a human numbers them — `1, 2, 3, 10`, not
`1, 10, 2` — so `1.png`, `2.png`, `3.png` is all you need. The name has no
effect on the order they *print* in, which is random.

## How random is it?

Not purely random. Every body image is printed once before any of them repeats,
and a new round never opens with the image that just printed. With ten images,
ten consecutive presses give ten different verses. Pure random choice would
repeat itself often enough to look broken.

## Checking what is loaded

```bash
joybox list
```

lists the header, footer, and every body image the Joybox can see, plus
anything it decided to skip and why.
