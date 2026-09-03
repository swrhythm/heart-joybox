#!/usr/bin/env bash
# Remove Heart Joybox.  Leaves your artwork on the SD card alone.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }

systemctl disable --now joybox.service 2>/dev/null || true
rm -f /etc/systemd/system/joybox.service
rm -f /etc/systemd/system.conf.d/10-joybox-watchdog.conf
rm -f /etc/systemd/journald.conf.d/10-joybox.conf
rm -f /etc/udev/rules.d/99-joybox-printer.rules
rm -f /usr/local/bin/joybox
rm -rf /opt/heart-joybox /var/cache/heart-joybox /var/lib/heart-joybox
systemctl daemon-reload
udevadm control --reload-rules 2>/dev/null || true

echo "Removed.  Your images are still on the SD card."
echo "To remove the service user too:  sudo userdel joybox"
