# Notes from the test machine

HP EliteBook 830 G5, KDE neon (Ubuntu 24.04 base), kernel 7.0, Plasma 6.8.
Previously ran Windows 11 with the fingerprint reader working.

## The sensor

```
Bus 001 Device 016: ID 138a:00ab Validity Sensors, Inc.
  bcdDevice 1.64, vendor-specific class, 5 endpoints:
  0x01 bulk OUT, 0x81 / 0x82 bulk IN, 0x83 / 0x84 interrupt IN
```

Raw answers to the read-only queries (`scripts/probe.py --json`):

| Command | Meaning | Result |
|---|---|---|
| `01` | ROM info | ROM 6.7, build 164, product 48, timestamp 1415491824 |
| `4302` | firmware partition header | firmware 1.2, 8 modules, built 2020-01-21 |
| `3e` | flash layout | 5 partitions (see below) |
| `75` | sensor identity | major `0x0190`, version `0x70` → type `0xd51`, `57K0 FM- 154-120` |

Flash layout as left by HP's Windows driver. It differs from the ThinkPad
layout python-validity hardcodes (larger firmware partition, an extra
partition 3, no partition 5):

| ID | Type | Offset | Size | Role |
|---|---|---|---|---|
| 1 | 4 | `0x001000` | `0x001000` | certificate store (TLS keys) |
| 2 | 1 | `0x002000` | `0x055000` | firmware extension |
| 6 | 6 | `0x057000` | `0x008000` | calibration data |
| 3 | 2 | `0x05f000` | `0x04f000` | unknown |
| 4 | 3 | `0x0ae000` | `0x052000` | template database |

## What the earlier attempt got wrong

The starting assumption was "the scanner needs firmware uploaded before it
works". For this machine that was not the problem: the firmware has been in
the chip's flash since Windows put it there.

An earlier attempt had hand-edited the installed `python3-validity 0.15`
files: added the USB ID, pointed it at the ThinkPad `0097` blobs, invented a
`0xd51` sensor profile from the `0xdb` one, and added a reboot on open. That
got as far as starting a capture and then hung waiting for data.

The hang has a specific cause, found by the author of python-validity#256:
during a capture this chip never sends the interrupt with first byte `2`
("finger detected"). It goes from the start acknowledgement (`0`) straight to
capture events (`3`). The stock capture loop waits for `2` forever. Two more
details matter: the `0x199` sensor profile is the one whose images the on-chip
matcher accepts (the `0xdb` profile is not), and the driver now ships init
blobs for this chip family instead of borrowing the `0097` ones.

`dpkg -V python3-validity` is a quick way to see whether installed driver
files were edited by hand; installing the package built by `install.sh`
replaces them.

## Result

With the package built by `install.sh` (python-validity `0.16~hp18` at commit
`128a9eb` plus the patch in `patches/`):

```
INFO:root:Flash has 5 partitions.
INFO:root:Detected firmware version 1.2 (Tue Jan 21 03:49:25 2020))
INFO:root:Sensor type 0xd51 — aliasing to 0x199 profile
INFO:root:Opening sensor: 57K0 FM- 154-120
INFO:root:Fingerprint device registered with open-fprintd
```

| Check | Outcome |
|---|---|
| Driver starts against the Windows-provisioned chip, no formatting or pairing | Works |
| `fprintd-enroll` (right index finger) | Completed after 8 accepted touches |
| `fprintd-verify` with the enrolled finger | `verify-match` |
| A finger that is not enrolled | Rejected (`Failed to match fingerprint` in `sudo`) |
| `sudo` through PAM (`pam-auth-update --enable fprintd`) | Works |
| KDE lock screen | Erratic until `scripts/kde-lockscreen-pam.sh` is applied; with it, unlocks by finger and by password (brief use so far) |
| Login screen | Not tested yet |
| Suspend / resume | Not tested yet |
| Clean-slate chip (format, pair, firmware upload) | Not tested; the chip was provisioned |

## Two problems found on Ubuntu 24.04

1. **`GLibUnix.signal_add` does not exist.** The driver picks `GLibUnix` when
   the namespace can be imported. With PyGObject 3.48.2 and GLib 2.80 it can,
   but it only offers `signal_add_full`, so the service died immediately after
   a successful sensor initialisation. Fixed by
   `patches/0001-dbus-service-glibunix-signal-add.patch`.
2. **The start limiter latches.** The service unit restarts on failure every
   two seconds, and the driver refuses more than ten starts a minute while
   recording the refused ones too. After a crash loop it therefore keeps
   refusing until `/run/python-validity/backoff` is deleted. `install.sh`
   deletes it before starting the service.

Both are worth reporting on the upstream pull request.

## Smaller observations

- **Unrecognised no-match reply.** When an unenrolled finger was presented
  the chip answered with interrupt `04 00 02 00 db`. The driver only treats
  `04 00 01 00 db` and `05 00 01 00 db` as a clean "no template matched", so
  this one goes down the generic error path: the attempt is refused, which is
  correct, but with a traceback in the log and without the driver's quiet
  retry.
- **Early "place your finger again".** While waiting for a finger the chip
  completes a capture about every two seconds. Each empty one surfaces as
  `Chip capture retry-scan #n` in the log, and the first is forwarded to the
  client as `verify-retry-scan`. Cosmetic.
  `patches/0002-dbus-service-no-idle-retry-prompt.patch` stops forwarding it
  on `0xd51` / `0x969` chips and logs the reason of each rejected capture.
  The trade-off: a genuinely bad touch no longer produces a "try again" hint
  either. Upstream already limited the hint to one per attempt, and that one
  was always used up by an empty capture, so nothing useful is lost. Once the
  logged reasons show which code means "empty", the hint can be brought back
  for the others.
- **Plasma lock screen.** `kscreenlocker` ships `/usr/lib/pam.d/kde` (includes
  `common-auth`) and `/usr/lib/pam.d/kde-fingerprint` (`pam_fprintd` only) and
  runs both at once. With `pam_fprintd` also in `common-auth`, the driver log
  shows interleaved verifications, one of them ending in `Fingerprint
  verification cancelled`.

## Why build from a pinned commit

- The PPA with prebuilt packages for this sensor
  (`ppa:devtochukwu/fingerprint`) carried `0.16~hp7` for Ubuntu 24.04 when
  this was written, while the pull request had moved on to `0.16~hp18` with
  fixes for cancellation races, retry floods and suspend/resume.
- The driver runs as root and talks to a security device. Pinning one commit
  means the code that was read is the code that gets installed.

## Things checked in the pinned source

- Formatting and pairing (`init_flash`) only run when the flash reports zero
  partitions, and for this USB ID only after the ROM build, sensor type and
  sensor name all match a chip the sequence was validated on.
- Firmware upload only runs when the chip reports no firmware. The one
  exception erases the firmware partition when it reports an interrupted
  upload (`044f`), a state in which there is nothing valid to keep.
- The firmware downloader fetches from `ftp.hp.com` and checks a SHA-512
  before extracting.
- The package no longer starts the service or triggers udev from inside
  `dpkg`; the service is started by the installer afterwards.
