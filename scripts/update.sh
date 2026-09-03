#!/usr/bin/env bash
# Pull the latest code and reinstall.  Your artwork and config.toml are untouched.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
git pull --ff-only
exec sudo "${REPO}/scripts/install.sh" "$@"
