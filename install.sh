#!/usr/bin/env bash
# Installs fingerprint support for the Validity/Synaptics 138a:00ab sensor
# (HP EliteBook 830/840/850 G5 and relatives) on Debian/Ubuntu-based systems.
#
# What it does, in order:
#   1. checks that the sensor is present,
#   2. installs open-fprintd + fprintd-clients (uunicorn's PPA) if missing,
#   3. fetches python-validity at a pinned, reviewed commit and builds a .deb,
#   4. installs the .deb and starts the driver service.
#
# Run it as your normal user; it calls sudo where root is needed.
# Nothing is written to the sensor by this script itself. On a chip that is
# already provisioned (the normal case) the driver does not format or pair it.

set -euo pipefail

# The driver must be built with the distribution's Python. Homebrew, pyenv or
# conda earlier in PATH would silently produce a broken package.
export PATH=/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

# --- what gets installed -----------------------------------------------------
# python-validity with 0xd51 sensor support, from the branch behind
# https://github.com/uunicorn/python-validity/pull/256
SOURCE_REPO="https://github.com/SimpleX-T/python-validity.git"
SOURCE_COMMIT="128a9ebc9e7e21b12d606b636d839906473dee18"
OPEN_FPRINTD_PPA="ppa:uunicorn/open-fprintd"
# -----------------------------------------------------------------------------

# Built outside the repository on purpose: the checkout may live on a
# filesystem without POSIX permissions (NTFS, exFAT), which breaks packaging.
BUILD_DIR="${FINGERPRINT_BUILD_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/hp-validity-fingerprint}"
LOG="$BUILD_DIR/install.log"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || fail "Run this as your normal user, not with sudo."
command -v apt-get >/dev/null || fail "This installer needs apt (Debian/Ubuntu). See README.md for other distributions."

mkdir -p "$BUILD_DIR"
exec > >(tee -a "$LOG") 2>&1
echo "--- install started $(date -Is) ---"

say "Looking for the sensor"
if lsusb -d 138a:00ab >/dev/null 2>&1; then
    echo "Found 138a:00ab."
elif lsusb -d 06cb:00b7 >/dev/null 2>&1 || lsusb -d 06cb:00cb >/dev/null 2>&1; then
    echo "Found a related sensor (06cb:00b7 / 06cb:00cb). The driver supports it,"
    echo "but this guide was only tested on 138a:00ab. Continuing."
else
    fail "No 138a:00ab sensor in lsusb. This guide is not for your hardware; nothing was changed."
fi

say "Installing build and runtime dependencies (sudo)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    git ca-certificates dpkg-dev fakeroot debhelper dh-python python3-all python3-setuptools \
    python3-usb python3-cryptography python3-yaml python3-dbus python3-gi \
    innoextract cabextract usbutils

if ! dpkg -s open-fprintd >/dev/null 2>&1; then
    say "Installing open-fprintd from $OPEN_FPRINTD_PPA (sudo)"
    # open-fprintd replaces the stock fprintd daemon; the two cannot coexist.
    sudo apt-get install -y software-properties-common
    sudo add-apt-repository -y "$OPEN_FPRINTD_PPA"
    sudo apt-get update
    sudo apt-get remove -y fprintd || true
    sudo apt-get install -y open-fprintd fprintd-clients
fi

say "Fetching python-validity at $SOURCE_COMMIT"
SRC="$BUILD_DIR/python-validity"
rm -rf "$SRC" "$BUILD_DIR"/python3-validity_*.deb "$BUILD_DIR"/python-validity_*
git init -q "$SRC"
git -C "$SRC" remote add origin "$SOURCE_REPO"
git -C "$SRC" fetch -q --depth 1 origin "$SOURCE_COMMIT"
git -C "$SRC" checkout -q FETCH_HEAD
[ "$(git -C "$SRC" rev-parse HEAD)" = "$SOURCE_COMMIT" ] || fail "Fetched commit does not match the pinned one."

say "Applying local patches"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for patch in "$REPO_DIR"/patches/*.patch; do
    [ -e "$patch" ] || continue
    echo "  $(basename "$patch")"
    git -C "$SRC" apply "$patch" || fail "Patch $(basename "$patch") does not apply."
done

say "Building the package (this also runs the driver's unit tests)"
( cd "$SRC" && dpkg-buildpackage -us -uc -b )
DEB="$(ls "$BUILD_DIR"/python3-validity_*_all.deb 2>/dev/null | head -n1)"
[ -n "$DEB" ] || fail "Build finished without producing a .deb. See $LOG."
echo "Built $DEB"

say "Installing the driver (sudo)"
sudo systemctl stop python3-validity.service 2>/dev/null || true
# --no-install-recommends: the package recommends libpam-fprintd, which would
# pull the stock fprintd back in and fight with open-fprintd. The PAM module
# already ships in fprintd-clients.
# During this step the package may download HP's driver bundle once to cache
# the sensor firmware under /var/lib/python-validity/firmware.
sudo apt-get install -y --no-install-recommends --allow-downgrades "$DEB"

say "Starting services (sudo)"
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo systemctl enable python3-validity.service
sudo systemctl stop python3-validity.service 2>/dev/null || true
# The driver refuses to start more than 10 times a minute and counts refused
# starts too, so a crash loop never recovers by itself. Start from zero.
sudo rm -f /run/python-validity/backoff
sudo systemctl restart open-fprintd.service
sudo systemctl start python3-validity.service

echo "Waiting for the sensor to initialise and calibrate..."
registered=no
for _ in $(seq 1 30); do
    sleep 2
    if fprintd-list "$USER" >/dev/null 2>&1; then
        registered=yes
        break
    fi
done

say "Sensor registered with open-fprintd: $registered"
sudo journalctl -u python3-validity.service -b --no-pager -n 25 || true

if [ "$registered" = "yes" ]; then
    cat <<EOF

Done. The sensor is registered. Next:

  fprintd-enroll                      # enrol your right index finger
  fprintd-verify                      # check that it matches
  sudo pam-auth-update --enable fprintd   # use it for sudo / login

More in README.md ("Enrolling" and "Using it for login").
EOF
else
    cat <<EOF

The driver did not come up cleanly. Nothing destructive was attempted.
Full log: $LOG
See "Troubleshooting" in README.md.
EOF
    exit 1
fi
