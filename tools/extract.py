"""Read a module's interface - its connector pins - from its own source.

A board module's comes from its KiCad schematic; a PLD's from the PIN lines of
its .pld; others from a label definition, a hand-written pin list or a stock
KiCad symbol.

    uv run tools/extract.py [-C DIR] MODULE [SOURCE_CHECKOUT]
    uv run tools/extract.py [-C DIR] --all

DIR (default .) holds the modules.toml; the result is DIR/interfaces/MODULE.json.
The source checkout defaults to the module's `path` in modules.toml. --all
extracts every module that has one. The netlist comes from `kicad-cli sch export
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
sys.path.insert(0, HERE)

POWER = {'GND', '+5V'}
NC = 'NC'


class Project:
    """A directory of module interfaces: DIR/modules.toml, DIR/interfaces/, and
    the library build_lib.py makes from them, DIR/<library>.kicad_sym.

    modules.toml's top-level keys are the library's `library` (its name, and so
    its file's stem) and optional `keywords`; every table is a module.
    """

    def __init__(self, root='.'):
        self.root = os.path.abspath(root)
        self.toml = os.path.join(self.root, 'modules.toml')
        if not os.path.exists(self.toml):
            sys.exit('no modules.toml in %s' % self.root)
        with open(self.toml, 'rb') as fh:
            data = tomllib.load(fh)
        if 'library' not in data:
            sys.exit('%s: no top-level library = "<name>"' % self.toml)
        self.library = data['library']
        self.keywords = data.get('keywords', 'module interface')
        self.modules = {k: v for k, v in data.items() if isinstance(v, dict)}
        self.iface_dir = os.path.join(self.root, 'interfaces')
        self.lib_path = os.path.join(self.root, self.library + '.kicad_sym')

    def module(self, name):
        if name not in self.modules:
            sys.exit('%s: no module %r (have: %s)' % (self.toml, name, ', '.join(self.modules)))
        return self.modules[name]

    def source_root(self, name, given=None):
        """Where module `name`'s source is checked out: as given, or its `path`."""
        if given:
            return given
        mod = self.module(name)
        if 'path' not in mod:
            if 'stock' in mod:
                return self.root
            sys.exit('%s: no path in modules.toml; give its checkout' % name)
        return os.path.normpath(os.path.join(self.root, mod['path']))

    def iface_path(self, name):
        return os.path.join(self.iface_dir, name + '.json')

    def load(self, name):
        with open(self.iface_path(name)) as fh:
            return json.load(fh)

    def rel(self, path):
        return os.path.relpath(path, os.getcwd())


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


def kicad_version():
    r = subprocess.run(['kicad-cli', 'version'], capture_output=True, text=True)
    return r.stdout.strip() or 'unknown'


def git(repo, *args):
    return subprocess.run(['git', '-C', repo] + list(args),
                          capture_output=True, text=True).stdout.strip()


def git_commit(repo, path):
    """The last commit to touch `path`, and whether the file has uncommitted changes."""
    commit = git(repo, 'log', '-1', '--format=%h', '--', path) or 'untracked'
    dirty = bool(git(repo, 'status', '--porcelain', '--', path))
    return commit, dirty and commit != 'untracked'


def same_repo(a, b):
    top = lambda d: git(d, 'rev-parse', '--show-toplevel')
    ta = top(a)
    return bool(ta) and ta == top(b)


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
PLD_REVISION = re.compile(r'^\s*Revision\s+([^;\s]+)\s*;', re.I | re.M)


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
    rev = PLD_REVISION.search(text)
    return mod['pld'], [{'ref': mod['connectors'][0], 'pins': pins}], rev and rev.group(1)


TYPES = {'input', 'output', 'bidirectional', 'tri_state', 'passive', 'free', 'unspecified',
         'power_in', 'power_out', 'open_collector', 'open_emitter', 'no_connect'}


def connectors_from_pins(name, mod, repo):
    """A hand-transcribed pin list, for a module with no KiCad project or .pld.

    One pin per line, 'number name type'; '#' starts a comment, and the file's
    header must say which datasheet or document it was transcribed from.
    A line '[REF]' starts the next connector; without one, the file is one
    connector named by modules.toml.
    """
    path = os.path.join(repo, mod['pins'])
    if not os.path.exists(path):
        sys.exit('%s: no pin file %s' % (name, path))
    conns, cur = [], None
    for lineno, line in enumerate(open(path), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('['):
            cur = {'ref': line.strip('[]'), 'pins': []}
            conns.append(cur)
            continue
        f = line.split()
        if len(f) != 3 or f[2] not in TYPES:
            sys.exit('%s:%d: want "number name type", type one of %s'
                     % (path, lineno, ', '.join(sorted(TYPES))))
        if cur is None:
            cur = {'ref': mod['connectors'][0], 'pins': []}
            conns.append(cur)
        cur['pins'].append(f)
    refs = [c['ref'] for c in conns]
    if refs != mod['connectors']:
        sys.exit('%s: %s has connectors %s, modules.toml says %s'
                 % (name, path, refs, mod['connectors']))
    for c in conns:
        nums = [p[0] for p in c['pins']]
        if len(set(nums)) != len(nums):
            sys.exit('%s: %s has a pin number twice in %s' % (name, path, c['ref']))
    return mod['pins'], conns


STOCK_DIR = os.environ.get('KICAD10_SYMBOL_DIR', '/usr/share/kicad/symbols')


def stock_symbol(name, mod):
    """The flattened stock KiCad symbol a 'stock' module is copied from."""
    from symlib import Library
    lib, sym = mod['stock'].split(':', 1)
    path = os.path.join(STOCK_DIR, lib + '.kicad_sym')
    if not os.path.exists(path):
        sys.exit('%s: no stock library %s (set KICAD10_SYMBOL_DIR)' % (name, path))
    library = Library(path)
    if sym not in library.raw:
        sys.exit('%s: %s has no symbol %s' % (name, path, sym))
    return library, library.flatten(sym)


def connectors_from_stock(name, mod, repo):
    """A part drawn with a stock KiCad symbol: its pins as KiCad has them."""
    from symlib import Symbol
    library, node = stock_symbol(name, mod)
    sym = Symbol(mod['stock'], node, library)
    pins = sorted(([p.number, p.name, p.etype] for p in sym.pins), key=lambda p: pin_key(p[0]))
    return 'KiCad stock ' + mod['stock'], [{'ref': mod['connectors'][0], 'pins': pins}]


def label_name(text):
    """A label's '/X' (active low) as KiCad's '~{X}'; 'R/W' is left alone."""
    return '~{%s}' % text[1:] if text.startswith('/') else text


def connectors_from_label(name, mod, repo):
    """A part wired by its ic-label-generator label: the label's pins are the truth.

    Pin numbers are the label's pin_numbers, or its convention: labels 1..N are
    the left rail top to bottom (pins 1..N), N+1..2N the right rail top to
    bottom (pins 2N..N+1). Types from the label's inputs and outputs lists;
    anything else is power (GND, VCC), NC, bidirectional (a data line D<n>), or
    passive (what the label greys out).
    """
    path = os.path.join(repo, mod['label'])
    if not os.path.exists(path):
        sys.exit('%s: no label definition %s' % (name, path))
    with open(path) as fh:
        d = json.load(fh)
    labels, n = d['pin_labels'], d['pins_per_side']
    if len(labels) != 2 * n:
        sys.exit('%s: %s has %d labels for %d pins' % (name, path, len(labels), 2 * n))
    numbers = d.get('pin_numbers') or (list(range(1, n + 1)) + list(range(2 * n, n, -1)))
    ins, outs = set(d.get('inputs', [])), set(d.get('outputs', []))
    pins = []
    for num, text in zip(numbers, labels):
        if text in ('GND', 'VCC'):
            t = 'power_in'
        elif text == 'NC':
            t = 'no_connect'
        elif text in ins:
            t = 'input'
        elif text in outs:
            t = 'output'
        elif re.fullmatch(r'D\d+', text):
            t = 'bidirectional'
        else:
            t = 'passive'
        pins.append([str(num), label_name(text), t])
    pins.sort(key=lambda p: pin_key(p[0]))
    return mod['label'], [{'ref': mod['connectors'][0], 'pins': pins}]


def extract(project, name, repo=None):
    """The interface of module `name`, read from the checkout at `repo`.

    The source's commit is recorded when it lives in another repo; a source in
    the project's own repo is versioned with it, so its commit is not."""
    mod = project.module(name)
    repo = project.source_root(name, repo)
    revision = None
    if 'label' in mod:
        source, connectors = connectors_from_label(name, mod, repo)
    elif 'stock' in mod:
        source, connectors = connectors_from_stock(name, mod, repo)
        return {'module': name, 'repo': mod.get('repo', ''), 'source': source,
                'commit': 'KiCad ' + kicad_version(), 'connectors': connectors}
    elif 'pins' in mod:
        source, connectors = connectors_from_pins(name, mod, repo)
    elif 'pld' in mod:
        source, connectors, revision = connectors_from_pld(name, mod, repo)
    else:
        source, connectors = connectors_from_schematic(name, mod, repo)
    if same_repo(project.root, repo):
        commit = 'this repo'
    else:
        commit, dirty = git_commit(repo, source)
        commit += '-dirty' if dirty else ''
    iface = {
        'module': name,
        'repo': mod.get('repo', ''),
        'source': source,
        'commit': commit,
        'connectors': connectors,
    }
    if revision:
        iface['revision'] = revision    # a PLD's Revision line: which part is on the board
    return iface


def dump(iface):
    """JSON with one pin per line, so a diff names the pin that changed."""
    lines = ['{']
    for key in ('module', 'repo', 'source', 'commit', 'revision'):
        if key in iface:
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


def write(project, name, repo=None):
    iface = extract(project, name, repo)
    if iface['commit'].endswith('-dirty'):
        print('warning: %s has uncommitted changes' % iface['source'], file=sys.stderr)
    os.makedirs(project.iface_dir, exist_ok=True)
    path = project.iface_path(name)
    with open(path, 'w') as fh:
        fh.write(dump(iface))
    n = sum(len(c['pins']) for c in iface['connectors'])
    print('wrote %s: %d connectors, %d pins, from %s'
          % (project.rel(path), len(iface['connectors']), n, iface['commit']))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-C', dest='dir', default='.', help='the directory holding modules.toml')
    ap.add_argument('--all', action='store_true', help='every module with a path')
    ap.add_argument('module', nargs='?')
    ap.add_argument('repo', nargs='?', help="the module's source checkout, if not its path")
    args = ap.parse_args()
    project = Project(args.dir)
    if args.all == bool(args.module):
        ap.error('give a MODULE or --all')
    names = [n for n, m in project.modules.items() if 'path' in m or 'stock' in m] \
        if args.all else [args.module]
    for name in names:
        write(project, name, args.repo)


if __name__ == '__main__':
    main()
