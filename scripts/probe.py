#!/usr/bin/python3
"""Read-only identity probe for Validity/Synaptics "Prometheus" fingerprint chips.

Sends five cleartext query commands (ROM info, state, firmware info, flash
layout, sensor identity) and decodes the answers. It never writes to the chip:
no reset, no pairing, no flash access beyond reading the partition table.

Usage:
    sudo systemctl stop python3-validity     # the driver must not own the device
    sudo /usr/bin/python3 scripts/probe.py   # add --json for machine output

Needs python3-usb (pyusb). Root is only required when your user cannot open
the USB device node.
"""

import argparse
import json
import struct
import sys
import time

import usb.core
import usb.util

# USB IDs known to carry this chip family on HP laptops.
CANDIDATES = {
    (0x138A, 0x00AB): 'HP EliteBook 830/840/850 G5, ZBook Studio G5',
    (0x06CB, 0x00B7): 'HP EliteBook 840 G6 family',
    (0x06CB, 0x00CB): 'HP Pavilion x360 14-dh',
}

# (major, version) -> (sensor type, name), from python-validity's hw_tables.py.
# Only the rows that matter for the decision this script helps with.
KNOWN_SENSORS = {
    (0x0190, 0x70): (0x0D51, '57K0 FM- 154-120'),
}

# The ROM build the upstream 0xd51 support was validated against.
VALIDATED_ROM = {'timestamp': 1415491824, 'build': 164, 'major': 6, 'minor': 7, 'product': 48}

PARTITION_NAMES = {1: 'certificate store', 2: 'firmware (xpfwext)', 3: 'unknown (type 2)',
                   4: 'template database', 5: 'unknown (type 5)', 6: 'calibration data'}


def find_device():
    found = [d for d in usb.core.find(find_all=True)
             if (d.idVendor, d.idProduct) in CANDIDATES]
    if not found:
        sys.exit('No known sensor found. Check `lsusb` for 138a:00ab / 06cb:00b7 / 06cb:00cb.')
    if len(found) > 1:
        sys.exit('More than one candidate sensor found; refusing to guess.')
    return found[0]


def status_of(rsp):
    return struct.unpack('<H', rsp[:2])[0] if len(rsp) >= 2 else None


def probe(dev):
    def cmd(request):
        dev.write(0x01, request)
        return bytes(dev.read(0x81, 100 * 1024, timeout=5000))

    out = {'usb': '%04x:%04x' % (dev.idVendor, dev.idProduct),
           'model_hint': CANDIDATES[(dev.idVendor, dev.idProduct)],
           'raw': {}}

    rsp = cmd(b'\x01')
    out['raw']['01'] = rsp.hex()
    if status_of(rsp) == 0 and len(rsp) >= 0x12:
        ts, build, major, minor, product, u1 = struct.unpack('<LLBBxBxxxB', rsp[2:0x12])
        out['rom'] = {'timestamp': ts, 'built': time.strftime('%Y-%m-%d', time.gmtime(ts)),
                      'build': build, 'major': major, 'minor': minor, 'product': product}

    rsp = cmd(b'\x19')
    out['raw']['19'] = rsp.hex()

    rsp = cmd(b'\x43\x02')
    out['raw']['4302'] = rsp.hex()
    if rsp[:2] == b'\xb0\x04':
        out['firmware'] = None
    elif status_of(rsp) == 0 and len(rsp) >= 12:
        major, minor, modules, buildtime = struct.unpack('<HHHL', rsp[2:12])
        out['firmware'] = {'major': major, 'minor': minor, 'modules': modules,
                           'built': time.strftime('%Y-%m-%d', time.gmtime(buildtime))}
    else:
        out['firmware'] = {'error': 'status %04x' % (status_of(rsp) or 0)}

    rsp = cmd(b'\x3e')
    out['raw']['3e'] = rsp.hex()
    if status_of(rsp) == 0 and len(rsp) >= 16:
        jid0, jid1, blocks, _, blocksize, _, count = struct.unpack('<HHHHHHH', rsp[2:16])
        parts = []
        for i in range(count):
            pid, ptype, access, offset, size = struct.unpack('<BBHLL', rsp[16 + i * 12:28 + i * 12])
            parts.append({'id': pid, 'type': ptype, 'access': access, 'offset': offset,
                          'size': size, 'role': PARTITION_NAMES.get(pid, '?')})
        out['flash'] = {'jedec': '%x:%x' % (jid0, jid1), 'bytes': blocks * blocksize,
                        'partitions': parts}

    rsp = cmd(b'\x75')
    out['raw']['75'] = rsp.hex()
    if status_of(rsp) == 0 and len(rsp) >= 10:
        _, version, major = struct.unpack('<LHH', rsp[2:10])
        stype, name = KNOWN_SENSORS.get((major, version), (None, None))
        out['sensor'] = {'major': major, 'version': version, 'type': stype, 'name': name}

    return out


def verdict(result):
    sensor = result.get('sensor') or {}
    rom = result.get('rom') or {}
    flash = result.get('flash') or {}
    lines = []

    same_rom = all(rom.get(k) == v for k, v in VALIDATED_ROM.items())
    if sensor.get('type') == 0x0D51 and same_rom:
        lines.append('MATCH   Sensor type 0xd51 (%s) on the validated ROM - this guide applies.'
                     % sensor['name'])
    elif sensor.get('type') == 0x0D51:
        lines.append('PARTIAL Sensor type 0xd51 but a different ROM build - likely fine while the '
                     'chip is provisioned, untested territory otherwise.')
    else:
        lines.append('UNKNOWN Sensor major=0x%04x version=0x%02x is not the chip this guide was '
                     'written for. The driver may still support it (0x969 variants exist under '
                     'the same USB ID); read the upstream PR before going further.'
                     % (sensor.get('major', 0), sensor.get('version', 0)))

    count = len(flash.get('partitions', []))
    if count:
        lines.append('OK      Flash is provisioned (%d partitions). Nothing needs formatting.' % count)
    else:
        lines.append('CAUTION Flash has no partitions (factory-empty or wiped). The driver will '
                     'format and pair the chip on first start; see "Clean-slate chips" in README.')

    if result.get('firmware') is None:
        lines.append('NOTE    No firmware extension on the chip. The driver uploads it from the '
                     'HP driver package on first start (needs internet once).')
    elif 'error' in result['firmware']:
        lines.append('CAUTION Firmware partition reports %s - probably an interrupted upload; the '
                     'driver repairs this case itself.' % result['firmware']['error'])
    else:
        fw = result['firmware']
        lines.append('OK      Firmware %d.%d present (%d modules, built %s). No upload needed.'
                     % (fw['major'], fw['minor'], fw['modules'], fw['built']))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--json', action='store_true', help='print raw + decoded data as JSON')
    args = parser.parse_args()

    dev = find_device()
    try:
        dev.get_active_configuration()
    except usb.core.USBError:
        dev.set_configuration()

    try:
        usb.util.claim_interface(dev, 0)
    except usb.core.USBError as error:
        sys.exit('Cannot claim the sensor (%s).\nStop the driver first: '
                 'sudo systemctl stop python3-validity\n'
                 'and run this script with sudo if your user cannot open the device.' % error)

    try:
        result = probe(dev)
    finally:
        usb.util.release_interface(dev, 0)
        usb.util.dispose_resources(dev)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print('Device   %s  (%s)' % (result['usb'], result['model_hint']))
    if 'rom' in result:
        print('ROM      %(major)d.%(minor)d build %(build)d, product %(product)d, built %(built)s'
              % result['rom'])
    for part in result.get('flash', {}).get('partitions', []):
        print('Flash    #%(id)d  offset 0x%(offset)06x  size 0x%(size)06x  %(role)s' % part)
    print()
    for line in verdict(result):
        print(line)


if __name__ == '__main__':
    main()
