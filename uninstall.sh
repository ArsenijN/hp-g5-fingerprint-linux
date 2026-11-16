#!/usr/bin/env bash
# Removes what install.sh set up. Fingerprints stored on the sensor chip stay
# there (delete them first with `fprintd-delete $USER` if you want them gone).

set -euo pipefail
export PATH=/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user, not with sudo." >&2; exit 1; }

echo "==> Turning off fingerprint authentication in PAM"
sudo pam-auth-update --disable fprintd || true

echo "==> Removing the driver"
sudo systemctl disable --now python3-validity.service 2>/dev/null || true
sudo apt-get remove -y python3-validity

cat <<'EOF'

Left in place on purpose:
  - open-fprintd and fprintd-clients (remove with: sudo apt-get remove open-fprintd fprintd-clients)
  - /var/lib/python-validity (cached firmware and calibration; remove with sudo rm -r)
  - the build cache in ~/.cache/hp-validity-fingerprint
EOF
