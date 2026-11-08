"""Read a module's interface - its connector pins - from its own source.

A board module's comes from its KiCad schematic; a PLD's from the PIN lines of
its .pld.

    uv run tools/extract.py MODULE PATH_TO_MODULE_REPO

writes interfaces/MODULE.json. The netlist comes from `kicad-cli sch export
netlist`, so what is recorded is what the module's schematic actually connects,
not what anyone remembers. check.py uses the same reader to compare.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTRY = os.path.join(ROOT, 'modules.toml')
IFACE_DIR = os.path.join(ROOT, 'interfaces')

POWER = {'GND', '+5V'}
NC = 'NC'


def registry():
    with open(REGISTRY, 'rb') as fh:
        return tomllib.load(fh)


def module(name):
    reg = registry()
    if name not in reg:
        sys.exit('%s: no module %r (have: %s)' % (REGISTRY, name, ', '.join(reg)))
    return reg[name]


def normalise(net, sheets=('/',)):
    """A KiCad net name as a pin name.

    Drops the sheet path ('/~{CEA0}' -> '~{CEA0}'), closes an overbar left open
    in the source ('~{BR' -> '~{BR}'), and calls an unconnected pin NC. Only a
    real sheet path is dropped, longest first, so a '/' inside a signal's own
    name survives: '/R/W' is R/W, not W.
    """
    if net.startswith('unconnected-'):
        return NC
    for path in sorted(sheets, key=len, reverse=True):
        if net.startswith(path):
            net = net[len(path):]
            break
    if net.count('{') > net.count('}'):
        net += '}' * (net.count('{') - net.count('}'))
    return net


def pin_key(number):
    m = re.match(r'(\d+)(.*)', number)
    return (int(m.group(1)), m.group(2)) if m else (1 << 30, number)


def read_netlist(schematic):
    """{ref: {pin number: raw net name}} for every component, and the sheet paths."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'net.xml')
        r = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                            '-o', out, schematic], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(out):
            sys.exit('kicad-cli failed on %s:\n%s%s' % (schematic, r.stdout, r.stderr))
        tree = ET.parse(out)
    sheets = [s.get('name') for s in tree.iter('sheet')] or ['/']
    pins = {}
    for net in tree.iter('net'):
        for node in net.iter('node'):
            pins.setdefault(node.get('ref'), {})[node.get('pin')] = net.get('name')
    return pins, sheets


def git_commit(repo, path):
    """The last commit to touch `path`, and whether the file has uncommitted changes."""
    def git(*args):
        return subprocess.run(['git', '-C', repo] + list(args),
                              capture_output=True, text=True).stdout.strip()
    commit = git('log', '-1', '--format=%h', '--', path) or 'untracked'
    dirty = bool(git('status', '--porcelain', '--', path))
    return commit, dirty and commit != 'untracked'


def default_type(name):
    """Electrical type for a pin read from a netlist: only power and NC are known."""
    if name in POWER:
        return 'power_in'
    if name == NC:
        return 'no_connect'
    return 'passive'


def connectors_from_schematic(name, mod, repo):
    sch = os.path.join(repo, mod['schematic'])
    if not os.path.exists(sch):
        sys.exit('%s: no schematic %s' % (name, sch))
    nets, sheets = read_netlist(sch)
    connectors = []
    for ref in mod['connectors']:
        if ref not in nets:
            sys.exit('%s: %s has no component %s' % (name, sch, ref))
        pins = []
        for num, net in sorted(nets[ref].items(), key=lambda kv: pin_key(kv[0])):
            pname = normalise(net, sheets)
            pins.append([num, pname, default_type(pname)])
        connectors.append({'ref': ref, 'pins': pins})
    return mod['schematic'], connectors


# GAL packages: pin count, power pins, and which pins are input-only (the rest
# are macrocell I/O). A 16V8 in registered mode has pin 1 as CLK and 11 as /OE.
GAL = {
    'g22v10': (24, {12: 'GND', 24: '+5V'}, set(range(1, 12)) | {13}),
    'g16v8': (20, {10: 'GND', 20: '+5V'}, set(range(1, 10)) | {11}),
}
PLD_PIN = re.compile(r'^\s*PIN\s+(\d+)\s*=\s*(!?)\s*([A-Za-z_][A-Za-z0-9_]*)\s*;', re.I | re.M)
PLD_DEVICE = re.compile(r'^\s*Device\s+(\w+)\s*;', re.I | re.M)


def strip_comments(text):
    return re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)


def pld_outputs(body, names):
    """Names the design drives: those on the left of an equation ('X =', 'X.d =')."""
    out = set()
    for n in names:
        if re.search(r'^\s*!?\s*%s(\.\w+)?\s*=' % re.escape(n), body, re.M):
            out.add(n)
    return out


def connectors_from_pld(name, mod, repo):
    """One 'connector', the chip's own pins, from the .pld's PIN declarations.

    Undeclared pins follow the chip-diagram convention: an unused input-only
    pin is IN<n> (tied low on the board), an unused macrocell pin NC.
    """
    path = os.path.join(repo, mod['pld'])
    if not os.path.exists(path):
        sys.exit('%s: no PLD source %s' % (name, path))
    text = strip_comments(open(path, errors='replace').read())
    dev = PLD_DEVICE.search(text)
    if not dev:
        sys.exit('%s: no Device line in %s' % (name, path))
    family = next((f for f in GAL if dev.group(1).lower().startswith(f)), None)
    if not family:
        sys.exit('%s: device %s is not a GAL this tool knows' % (name, dev.group(1)))
    count, power, inputs_only = GAL[family]
    declared = {}
    for m in PLD_PIN.finditer(text):
        num = int(m.group(1))
        if num in declared:
            sys.exit('%s: pin %d declared twice in %s' % (name, num, path))
        if num in power or not 1 <= num <= count:
            sys.exit('%s: pin %d of a %d-pin %s cannot be a signal' % (name, num, count, family))
        declared[num] = (m.group(3), m.group(2) == '!')
    body = PLD_PIN.sub('', text)
    driven = pld_outputs(body, [n for n, _ in declared.values()])
    pins = []
    for num in range(1, count + 1):
        if num in power:
            pins.append([str(num), power[num], 'power_in'])
        elif num in declared:
            sig, low = declared[num]
            pname = '~{%s}' % sig if low else sig
            pins.append([str(num), pname, 'output' if sig in driven else 'input'])
        elif num in inputs_only:
            pins.append([str(num), 'IN%d' % num, 'input'])
        else:
            pins.append([str(num), NC, 'no_connect'])
    return mod['pld'], [{'ref': mod['connectors'][0], 'pins': pins}]


def extract(name, repo):
    """The interface of module `name`, read from the checkout at `repo`."""
    mod = module(name)
    if 'pld' in mod:
        source, connectors = connectors_from_pld(name, mod, repo)
    else:
        source, connectors = connectors_from_schematic(name, mod, repo)
    commit, dirty = git_commit(repo, source)
    return {
        'module': name,
        'repo': mod['repo'],
        'source': source,
        'commit': commit + ('-dirty' if dirty else ''),
        'connectors': connectors,
    }


def iface_path(name):
    return os.path.join(IFACE_DIR, name + '.json')


def dump(iface):
    """JSON with one pin per line, so a diff names the pin that changed."""
    lines = ['{']
    for key in ('module', 'repo', 'source', 'commit'):
        lines.append('  %s: %s,' % (json.dumps(key), json.dumps(iface[key])))
    lines.append('  "connectors": [')
    for ci, c in enumerate(iface['connectors']):
        lines.append('    {"ref": %s, "pins": [' % json.dumps(c['ref']))
        for pi, pin in enumerate(c['pins']):
            sep = ',' if pi < len(c['pins']) - 1 else ''
            lines.append('      [%s]%s' % (', '.join(json.dumps(v) for v in pin), sep))
        lines.append('    ]}' + (',' if ci < len(iface['connectors']) - 1 else ''))
    lines.append('  ]')
    lines.append('}')
    return '\n'.join(lines) + '\n'


def load(name):
    with open(iface_path(name)) as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('module')
    ap.add_argument('repo', help="path to the module's own repo checkout")
    args = ap.parse_args()
    iface = extract(args.module, args.repo)
    if iface['commit'].endswith('-dirty'):
        print('warning: %s has uncommitted changes' % iface['source'], file=sys.stderr)
    os.makedirs(IFACE_DIR, exist_ok=True)
    with open(iface_path(args.module), 'w') as fh:
        fh.write(dump(iface))
    n = sum(len(c['pins']) for c in iface['connectors'])
    print('wrote %s: %d connectors, %d pins, from %s'
          % (os.path.relpath(iface_path(args.module), ROOT), len(iface['connectors']), n,
             iface['commit']))


if __name__ == '__main__':
    main()
