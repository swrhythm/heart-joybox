#!/usr/bin/env bash
#
# Install (or update) Heart Joybox on a Raspberry Pi.
#
#   sudo ./scripts/install.sh
#
# Safe to run again at any time: it never overwrites your artwork or your
# config.toml, and it leaves the service running.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/heart-joybox
BIN=/usr/local/bin/joybox
SERVICE_USER=joybox
UNIT=joybox.service

WITH_WATCHDOG=1
WITH_APT=1
for arg in "$@"; do
  case "$arg" in
    --no-watchdog) WITH_WATCHDOG=0 ;;
    --no-apt)      WITH_APT=0 ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    ! %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }

# ---------------------------------------------------------------- packages
if [ "$WITH_APT" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
  say "Installing system packages"
  # apt packages, not pip: Raspberry Pi OS refuses pip installs into the system
  # Python, and a Pi Zero would spend a long time building Pillow from source.
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-pil python3-gpiozero python3-lgpio fonts-dejavu-core
else
  say "Skipping apt (--no-apt or apt-get not found)"
fi

# ------------------------------------------------------------ service user
say "Setting up the ${SERVICE_USER} user"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  note "created system user ${SERVICE_USER}"
fi
for group in lp gpio; do
  if getent group "$group" >/dev/null 2>&1; then
    usermod -aG "$group" "$SERVICE_USER"
    note "${SERVICE_USER} is in group ${group}"
  else
    warn "group ${group} does not exist on this system"
  fi
done

# Put the person installing this in the same groups, so `joybox test` works
# from their own shell without sudo.
LOGIN_USER="${SUDO_USER:-}"
if [ -n "$LOGIN_USER" ] && [ "$LOGIN_USER" != "root" ]; then
  ADDED=""
  for group in lp gpio; do
    if getent group "$group" >/dev/null 2>&1 && ! id -nG "$LOGIN_USER" | tr ' ' '\n' | grep -qx "$group"; then
      usermod -aG "$group" "$LOGIN_USER"
      ADDED="${ADDED} ${group}"
    fi
  done
  if [ -n "$ADDED" ]; then
    note "added ${LOGIN_USER} to:${ADDED}"
    warn "log out and back in before running joybox commands yourself (or use sudo until then)"
  fi
fi

# -------------------------------------------------------------- the code
say "Installing the code to ${PREFIX}"
rm -rf "${PREFIX}/joybox"
install -d "$PREFIX"
cp -r "${REPO}/src/joybox" "${PREFIX}/joybox"
cp "${REPO}/scripts/make_samples.py" "${PREFIX}/make_samples.py"
find "$PREFIX" -type d -exec chmod 755 {} +
find "$PREFIX" -type f -exec chmod 644 {} +
chmod 755 "${PREFIX}/make_samples.py"

cat > "$BIN" <<'WRAPPER'
#!/bin/sh
# Heart Joybox command line.  Try `joybox doctor`.
exec /usr/bin/python3 -X faulthandler -m joybox "$@"
WRAPPER
chmod 755 "$BIN"
sed -i "1a PYTHONPATH=${PREFIX}\nexport PYTHONPATH" "$BIN"
note "installed ${BIN}"

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" /var/cache/heart-joybox /var/lib/heart-joybox
install -d /etc/heart-joybox

# ------------------------------------------------------------ card content
say "Setting up the content folder on the SD card"
CONTENT="$(PYTHONPATH="$PREFIX" python3 -c 'from joybox import paths; print(paths.content_dir())')"
# mkdir, not install -d: the card is FAT32 and cannot take a mode.
mkdir -p "$CONTENT" "${CONTENT}/body"
EXISTING="$(find "${CONTENT}/body" -maxdepth 1 -type f ! -name '._*' \
  \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.bmp' \) 2>/dev/null)"
if [ -z "$EXISTING" ]; then
  # cp -r, not cp -a: preserving ownership onto FAT32 fails.
  cp -r "${REPO}/content-template/." "$CONTENT/"
  note "copied the sample artwork into ${CONTENT}"
else
  note "${CONTENT} already has artwork; leaving it alone"
  [ -f "${CONTENT}/config.toml" ] || cp "${REPO}/content-template/config.toml" "${CONTENT}/config.toml"
fi
if ! sudo -u "$SERVICE_USER" test -r "${CONTENT}/config.toml" 2>/dev/null; then
  warn "${SERVICE_USER} cannot read ${CONTENT} - see docs/TROUBLESHOOTING.md ('cannot read the SD card')"
fi
note "put your images in ${CONTENT}"

# ------------------------------------------------------------------ udev
say "Installing the printer device rule"
install -d /etc/udev/rules.d
install -m 644 "${REPO}/udev/99-joybox-printer.rules" /etc/udev/rules.d/99-joybox-printer.rules
udevadm control --reload-rules >/dev/null 2>&1 || warn "could not reload udev rules"
udevadm trigger --subsystem-match=usbmisc >/dev/null 2>&1 || true
if [ -e /dev/joybox-printer ]; then
  note "printer found at /dev/joybox-printer"
elif [ -e /dev/usb/lp0 ]; then
  note "printer found at /dev/usb/lp0 (the symlink appears after a replug)"
else
  warn "no printer device yet - plug it in and power it on, then run: joybox doctor"
fi

# --------------------------------------------------------------- systemd
say "Installing the service"
install -d /etc/systemd/system /etc/systemd/journald.conf.d
install -m 644 "${REPO}/systemd/${UNIT}" "/etc/systemd/system/${UNIT}"
install -m 644 "${REPO}/systemd/journald.conf.d/10-joybox.conf" /etc/systemd/journald.conf.d/

# systemd refuses to start a unit that names a group the system does not have,
# so keep only the ones that exist here.
PRESENT=""
for group in lp gpio; do
  getent group "$group" >/dev/null 2>&1 && PRESENT="${PRESENT} ${group}"
done
sed -i "s/^SupplementaryGroups=.*/SupplementaryGroups=${PRESENT# }/" "/etc/systemd/system/${UNIT}"

if [ "$WITH_WATCHDOG" -eq 1 ]; then
  install -d /etc/systemd/system.conf.d
  install -m 644 "${REPO}/systemd/system.conf.d/10-joybox-watchdog.conf" /etc/systemd/system.conf.d/
  note "hardware watchdog enabled (takes effect after a reboot)"
else
  note "hardware watchdog skipped"
fi

if [ -d /run/systemd/system ]; then
  systemctl restart systemd-journald >/dev/null 2>&1 || true
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null
  systemctl restart "$UNIT"
  sleep 2
  note "${UNIT} is $(systemctl is-active "$UNIT")"
else
  warn "systemd is not running here, so the service was installed but not started"
fi

# ---------------------------------------------------------------- verify
say "Checking the installation"
set +e
sudo -u "$SERVICE_USER" PYTHONPATH="$PREFIX" python3 -m joybox doctor
DOCTOR=$?
set -e

echo
if [ "$DOCTOR" -eq 0 ]; then
  say "Done - press the button"
else
  say "Installed, but some checks failed"
  note "fix the FAIL lines above, then run:  sudo -u ${SERVICE_USER} joybox doctor"
  note "docs/TROUBLESHOOTING.md explains each one"
fi
note "print a test page:   joybox test"
note "watch the log:       journalctl -u ${UNIT} -f"
exit 0
