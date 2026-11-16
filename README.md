# HP EliteBook G5 fingerprint reader on Linux (Validity `138a:00ab`)

Getting the built-in fingerprint reader of the HP EliteBook 830 G5 (and its
siblings) to work on Linux: enrolment, `sudo`, login and the lock screen.

The reader is a Synaptics/Validity **VFS7552 "PurePrint"** touch sensor, USB ID
`138a:00ab`, sensor type `0xd51`. libfprint has no driver for it. It does work
with [python-validity](https://github.com/uunicorn/python-validity) plus the
changes from [pull request #256](https://github.com/uunicorn/python-validity/pull/256),
and this repository is the tested recipe for putting those pieces together:
an installer, a read-only probe to check your chip first, and the notes on
what goes wrong.

> **Status:** working on an EliteBook 830 G5 with KDE neon (Ubuntu 24.04
> base): the installer ran, a finger was enrolled in eight touches and
> `fprintd-verify` reported `verify-match`. What was and was not tested is
> listed in [docs/NOTES.md](docs/NOTES.md).

## Is this your hardware?

```
$ lsusb | grep -i -e 138a -e 06cb
Bus 001 Device 016: ID 138a:00ab Validity Sensors, Inc.
```

| USB ID | Seen in | Covered here |
|---|---|---|
| `138a:00ab` | EliteBook 830 / 840 / 850 G5, ZBook Studio G5 | Yes, tested on an 830 G5 |
| `06cb:00b7` | EliteBook 840 G6 family | Same driver, not tested here |
| `06cb:00cb` | Pavilion x360 14-dh | Same driver, not tested here |

The same USB ID is used for more than one chip variant, so check the chip
itself before installing anything. The probe only asks the sensor five
read-only questions and changes nothing:

```
sudo apt install python3-usb
sudo /usr/bin/python3 scripts/probe.py
```

```
Device   138a:00ab  (HP EliteBook 830/840/850 G5, ZBook Studio G5)
ROM      6.7 build 164, product 48, built 2014-11-09
Flash    #1  offset 0x001000  size 0x001000  certificate store
Flash    #2  offset 0x002000  size 0x055000  firmware (xpfwext)
Flash    #6  offset 0x057000  size 0x008000  calibration data
Flash    #3  offset 0x05f000  size 0x04f000  unknown (type 2)
Flash    #4  offset 0x0ae000  size 0x052000  template database

MATCH   Sensor type 0xd51 (57K0 FM- 154-120) on the validated ROM - this guide applies.
OK      Flash is provisioned (5 partitions). Nothing needs formatting.
OK      Firmware 1.2 present (8 modules, built 2020-01-21). No upload needed.
```

Three `OK`/`MATCH` lines mean you are in the easy case: the chip was set up by
Windows at some point, it already carries its firmware, and the driver only
has to talk to it.

## Install

Debian, Ubuntu and derivatives (tested on KDE neon / Ubuntu 24.04):

```
git clone https://github.com/ArsenijN/hp-g5-fingerprint-linux.git
cd hp-g5-fingerprint-linux
./install.sh
```

Run it as your normal user; it asks for `sudo` when needed. It will

1. install `open-fprintd` and `fprintd-clients` from
   [`ppa:uunicorn/open-fprintd`](https://launchpad.net/~uunicorn/+archive/ubuntu/open-fprintd)
   (this replaces the stock `fprintd`, which cannot drive this sensor),
2. fetch python-validity at one pinned commit, apply the fixes from
   [patches/](patches/), build a `.deb` from it and run its unit tests,
3. install the package and start `python3-validity.service`.

The log is kept in `~/.cache/hp-validity-fingerprint/install.log`.

On other distributions, install `open-fprintd` and the python-validity branch
named at the top of [install.sh](install.sh) the way your distribution
prefers; the rest of this guide applies unchanged.

## Enrolling

```
fprintd-enroll                       # right index finger by default
fprintd-enroll -f left-index-finger  # any other finger
fprintd-verify
fprintd-list $USER
```

Touch the sensor, lift, touch again. Each accepted touch prints
`enroll-stage-passed`; after about eight of them it prints `enroll-completed`
and exits. Move the finger a little between touches. `enroll-retry-scan`
means that touch did not count.

```
Enroll result: enroll-stage-passed
...
Enroll result: enroll-completed
$ fprintd-verify
Verify result: verify-match (done)
```

Several fingers can be enrolled, one `fprintd-enroll -f <finger>` each
(`left-` or `right-` plus `thumb`, `index-finger`, `middle-finger`,
`ring-finger`, `little-finger`); any of them is accepted afterwards.
`fprintd-delete $USER` removes them all. The graphical route should work too:
System Settings → Users → Configure Fingerprint Authentication.

## Using it for login

```
sudo pam-auth-update --enable fprintd
```

After that `sudo`, the login screen and polkit prompts accept a finger. With
the default settings you get one try and ten seconds before the password
prompt appears.

KDE Plasma's lock screen has its own fingerprint support and picks the
enrolled finger up without extra configuration; press Enter with an empty
password field on older versions if it does not start scanning by itself.

## Firmware and vendor files

Nothing proprietary is stored in this repository, and `.gitignore` is set up
to keep it that way.

The chip runs a signed firmware extension (`6_07f_hp_cmit_mis_qm.xpfwext`)
that ships inside HP's Windows driver. **A sensor that has ever worked under
Windows already has it in its flash**, and the driver finds it there; that is
what the last `OK` line of the probe tells you.

Only a factory-empty or wiped chip needs the file. The driver package fetches
it by itself: it downloads HP's driver bundle
[`sp135736.exe`](https://ftp.hp.com/pub/softpaq/sp135501-136000/sp135736.exe)
straight from HP, checks its SHA-512, extracts the one firmware file with
`cabextract` and caches it in `/var/lib/python-validity/firmware/`. To do the
same by hand:

```
sudo validity-sensors-firmware
```

If HP ever removes that download, copy the `.xpfwext` out of the Synaptics
driver folder of a Windows installation
(`C:\Windows\System32\DriverStore\FileRepository\synawudfbiousb*`) into that
directory.

## Dual boot with Windows

- The Linux driver uses the pairing that Windows created. It does not re-pair
  the chip, so Windows Hello keeps working.
- Matching happens inside the chip, against every template stored on it. A
  finger enrolled in Windows Hello can win over the Linux enrolment of the
  same finger, and Linux then reports no match. Enrol different fingers in
  each system, or remove the Windows enrolment.

## Troubleshooting

Start with the driver's log:

```
systemctl status python3-validity
journalctl -u python3-validity -b
```

**`fprintd-enroll` or `fprintd-verify` waits forever.** This is the classic
symptom of an unpatched python-validity on this chip: the sensor never sends
the "finger detected" interrupt that the stock driver waits for. Check that
the patched build is the one installed: `apt-cache policy python3-validity`
should show `0.16~hp…`, not `0.15~ppa…`.

**`No devices available` / `list_devices failed`.** The driver service is not
running or has not registered yet. `sudo systemctl restart python3-validity`,
wait ten seconds, try again.

**`AttributeError: 'gi.repository.GLibUnix' object has no attribute
'signal_add'`.** The driver was built without
[patches/0001](patches/0001-dbus-service-glibunix-signal-add.patch). Ubuntu
24.04's PyGObject 3.48 only has `signal_add_full`. Re-run `install.sh`.

**`Refusing to start more than 10 times per minute`, over and over.** The
driver limits its own start rate and counts refused starts as well, so once
it is in a crash loop it stays there even after the cause is fixed:

```
sudo systemctl stop python3-validity
sudo rm -f /run/python-validity/backoff
sudo systemctl start python3-validity
```

**The build fails in `setup.py`.** Another Python is ahead of the system one
in your `PATH` (Homebrew, pyenv, conda). `install.sh` already forces the
system `PATH`; if you build by hand, do the same.

**Sensor stops responding after suspend.** `sudo systemctl restart
python3-validity`. The package also keeps USB autosuspend off for the sensor
for this reason.

**Do not run the generic factory reset** (`factory-reset.py` from older
python-validity guides) on this chip. It was written for ThinkPad sensors.

### Clean-slate chips

If the probe says the flash has no partitions, the driver will format the
flash, pair with the chip and upload the firmware on first start. Upstream
validated that path on exactly this chip (`0xd51`, `57K0 FM- 154-120`, ROM
6.7 build 164) and refuses to try it on anything else. It was not exercised
while writing this guide, because the test machine's sensor was already
provisioned.

## Uninstall

```
./uninstall.sh
```

## How it works

- **open-fprintd** provides the standard `net.reactivated.Fprint` D-Bus API,
  so `fprintd-enroll`, PAM and desktop settings work unmodified.
- **python-validity** is the actual driver, a userspace reimplementation of
  the protocol spoken by Synaptics' Windows driver: a TLS session with the
  chip, calibration, capture, and on-chip template storage and matching.
- The **0xd51 changes** teach it this chip: its USB ID, a capture loop that
  copes with the missing "finger detected" interrupt, and the HP-specific
  provisioning data.

More detail, including the state the test machine was found in, is in
[docs/NOTES.md](docs/NOTES.md).

## Credits

- [uunicorn](https://github.com/uunicorn) for python-validity and open-fprintd.
- [SimpleX-T](https://github.com/SimpleX-T) and the contributors to
  [python-validity#256](https://github.com/uunicorn/python-validity/pull/256)
  for the 0xd51 support, which is what actually makes this sensor work.
- [nmikhailov/Validity90](https://github.com/nmikhailov/Validity90) for the
  original protocol reverse engineering.

## Use of AI

This repository was put together with Claude (Anthropic's AI model), used
through Claude Code: the diagnosis of the sensor's state, the review of the
upstream driver changes, the scripts and this guide. The result was run and
checked by the author on the hardware described above.

## License

The scripts and documentation here are under the MIT license, see
[LICENSE.md](LICENSE.md). python-validity and open-fprintd are separate
projects with their own licenses; HP's driver and firmware remain HP's.
