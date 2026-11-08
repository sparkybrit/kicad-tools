"""Build sparky1_modules.kicad_sym from interfaces/*.json.

    uv run tools/build_lib.py

One symbol per module, one unit per connector. Each pin's number is the
connector pin and its name is the module's own signal name, so the system
schematic wires modules together exactly as the breadboard does. The library
is generated: edit modules.toml or re-extract an interface, never the
.kicad_sym by hand.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import dumps, Str
import extract

ROOT = extract.ROOT
OUT = os.path.join(ROOT, 'sparky1_modules.kicad_sym')

# KiCad 10's symbol library format, as sparky1-backplane uses.
LIB_VERSION = 20251024

GRID = 2.54
PIN_LEN = 5.08
FONT = 1.27
CHAR_W = 1.0  # rough width of one character at 1.27 mm, for sizing the body


def fmt(v):
    s = '%.4f' % float(v)
    s = s.rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def snap_up(v):
    return math.ceil(v / GRID - 1e-9) * GRID


def prop(name, value, x=0.0, y=0.0, hide=False, justify=None):
    effects = ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]
    if justify:
        effects.append(['justify', justify])
    node = ['property', Str(name), Str(value), ['at', fmt(x), fmt(y), '0'],
            ['show_name', 'no'], ['do_not_autoplace', 'no']]
    if hide:
        node.append(['hide', 'yes'])
    node.append(effects)
    return node


def shown_len(name):
    """Characters as drawn: overbar markup takes no width."""
    return len(name.replace('~{', '').replace('}', ''))


def pin_type(name):
    if name in extract.POWER:
        return 'power_in'
    if name == extract.NC:
        return 'no_connect'
    return 'passive'


def geometry(ifaces):
    """Body half-width and half-height shared by every unit of one symbol."""
    longest = max(shown_len(n) for c in ifaces['connectors'] for _, n in c['pins'])
    most = max(len(c['pins']) for c in ifaces['connectors'])
    half_w = snap_up(longest * CHAR_W + 2 * GRID)
    top = math.ceil((most - 1) / 2) * GRID
    return half_w, top


def build_symbol(name, mod, iface):
    half_w, top = geometry(iface)
    body_top = top + 2 * GRID          # room for the connector label
    bottom = top - (max(len(c['pins']) for c in iface['connectors']) - 1) * GRID
    body_bot = bottom - GRID
    descr = '%s Source: %s, %s at %s.' % (mod['description'], iface['repo'],
                                          iface['schematic'], iface['commit'])
    sym = ['symbol', Str(name),
           ['pin_names', ['offset', '1.016']],
           ['exclude_from_sim', 'yes'], ['in_bom', 'no'], ['on_board', 'no'],
           ['in_pos_files', 'no'], ['duplicate_pin_numbers_are_jumpers', 'no'],
           prop('Reference', 'A', -half_w, body_top + GRID, justify='left'),
           prop('Value', name, -half_w, body_bot - GRID, justify='left'),
           prop('Footprint', '', 0, 0, hide=True),
           prop('Datasheet', iface['repo'], 0, 0, hide=True),
           prop('Description', descr, 0, 0, hide=True),
           prop('Source_Commit', iface['commit'], 0, 0, hide=True),
           prop('ki_keywords', 'sparky1 module interface', 0, 0, hide=True),
           ]
    labels = mod.get('names', {})
    for unit, conn in enumerate(iface['connectors'], start=1):
        body = ['symbol', Str('%s_%d_1' % (name, unit)),
                ['rectangle',
                 ['start', fmt(-half_w), fmt(body_top)],
                 ['end', fmt(half_w), fmt(body_bot)],
                 ['stroke', ['width', '0.254'], ['type', 'default']],
                 ['fill', ['type', 'background']]],
                ['text', Str(labels.get(conn['ref'], conn['ref'])),
                 ['at', '0', fmt(body_top - GRID), '0'],
                 ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]]]
        for i, (number, pname) in enumerate(conn['pins']):
            y = top - i * GRID
            body.append(
                ['pin', pin_type(pname), 'line',
                 ['at', fmt(-half_w - PIN_LEN), fmt(y), '0'],
                 ['length', fmt(PIN_LEN)],
                 ['name', Str(pname), ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]],
                 ['number', Str(number), ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]]])
        sym.append(body)
    sym.append(['embedded_fonts', 'no'])
    return sym


def build():
    reg = extract.registry()
    syms = []
    for name in sorted(reg):
        path = extract.iface_path(name)
        if not os.path.exists(path):
            sys.exit('%s: no interface yet; run tools/extract.py %s <repo>' % (name, name))
        iface = extract.load(name)
        refs = [c['ref'] for c in iface['connectors']]
        if refs != reg[name]['connectors']:
            sys.exit('%s: interface has connectors %s, modules.toml says %s; re-extract'
                     % (name, refs, reg[name]['connectors']))
        syms.append(build_symbol(name, reg[name], iface))
    root = ['kicad_symbol_lib',
            ['version', str(LIB_VERSION)],
            ['generator', Str('sparky1-modules/tools/build_lib.py')],
            ['generator_version', Str('10.0')]] + syms
    return dumps(root) + '\n'


def main():
    text = build()
    with open(OUT, 'w') as fh:
        fh.write(text)
    print('wrote %s' % os.path.relpath(OUT, ROOT))


if __name__ == '__main__':
    main()
