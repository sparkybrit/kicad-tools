"""Build sparky1_modules.kicad_sym from interfaces/*.json.

    uv run tools/build_lib.py

One symbol per module, one unit per connector, or one body with the connectors
down its two sides when modules.toml gives the module `sides`. Each pin's number
is the connector pin (CONN.N, such as J1.8, when the module has several
connectors) and its name is the module's own signal name, so the system
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


def geometry(ifaces):
    """Body half-width and half-height shared by every unit of one symbol."""
    longest = max(shown_len(p[1]) for c in ifaces['connectors'] for p in c['pins'])
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
                                          iface['source'], iface['commit'])
    sym = ['symbol', Str(name),
           ['pin_names', ['offset', '1.016']],
           ['exclude_from_sim', 'yes'], ['in_bom', 'no'], ['on_board', 'no'],
           ['in_pos_files', 'no'], ['duplicate_pin_numbers_are_jumpers', 'no'],
           prop('Reference', mod.get('reference', 'A'), -half_w, body_top + GRID, justify='left'),
           prop('Value', name, -half_w, body_bot - GRID, justify='left'),
           prop('Footprint', '', 0, 0, hide=True),
           prop('Datasheet', iface['repo'], 0, 0, hide=True),
           prop('Description', descr, 0, 0, hide=True),
           prop('Source_Commit', iface['commit'], 0, 0, hide=True),
           prop('ki_keywords', 'sparky1 module interface', 0, 0, hide=True),
           ]
    labels = mod.get('names', {})
    # KiCad takes pins with the same number on different units to be one pin, so
    # a module with several connectors numbers each pin CONN.N (J1.8).
    multi = len(iface['connectors']) > 1
    for unit, conn in enumerate(iface['connectors'], start=1):
        # Every unit shares the top edge; each ends a grid step below its own last pin.
        unit_bot = top - (len(conn['pins']) - 1) * GRID - GRID
        body = ['symbol', Str('%s_%d_1' % (name, unit)),
                ['rectangle',
                 ['start', fmt(-half_w), fmt(body_top)],
                 ['end', fmt(half_w), fmt(unit_bot)],
                 ['stroke', ['width', '0.254'], ['type', 'default']],
                 ['fill', ['type', 'background']]],
                ['text', Str(labels.get(conn['ref'], conn['ref'])),
                 ['at', '0', fmt(body_top - GRID), '0'],
                 ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]]]
        for i, (number, pname, etype) in enumerate(conn['pins']):
            if multi:
                number = '%s.%s' % (conn['ref'], number)
            y = top - i * GRID
            body.append(
                ['pin', etype, 'line',
                 ['at', fmt(-half_w - PIN_LEN), fmt(y), '0'],
                 ['length', fmt(PIN_LEN)],
                 ['name', Str(pname), ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]],
                 ['number', Str(number), ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]]])
        sym.append(body)
    sym.append(['embedded_fonts', 'no'])
    return sym


def pin_node(etype, x, y, angle, number, pname, length=PIN_LEN):
    font = ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]]]
    return ['pin', etype, 'line', ['at', fmt(x), fmt(y), str(angle)],
            ['length', fmt(length)],
            ['name', Str(pname), font], ['number', Str(number), font]]


def build_sided(name, mod, iface):
    """The module in one body, its pins down the two sides as on the board.

    Each run of pins follows a heading row, which names its connector the first
    time it appears, so pins that face each other on the board read straight
    across. With body_width the body is drawn as wide as the package and as long
    as its pins, their names outside it, their numbers just inside its edges and
    the module's name down the middle, with no heading rows."""
    conns = {c['ref']: c for c in iface['connectors']}
    multi = len(conns) > 1
    labels = mod.get('names', {})

    def run(entry):
        """(heading or None, connector, its pins) for one sides entry:
        [TEXT|]CONN, CONN:FIRST-LAST or CONN:P,Q,R."""
        head, _, entry = entry.rpartition('|')
        ref, _, span = entry.partition(':')
        if ref not in conns:
            sys.exit('%s: sides entry %r names no connector of %s' % (name, entry, list(conns)))
        pins = conns[ref]['pins']
        head = head or None
        if not span:
            return head, ref, pins
        by_num = {p[0]: p for p in pins}
        if ',' in span or '-' not in span:
            nums = span.split(',')
        else:
            a, _, b = span.partition('-')
            try:
                a, b = int(a), int(b)
            except ValueError:
                sys.exit('%s: sides entry %r: give CONN:FIRST-LAST' % (name, entry))
            step = 1 if b >= a else -1
            nums = [str(n) for n in range(a, b + step, step)]
        missing = [n for n in nums if n not in by_num]
        if missing:
            sys.exit('%s: sides entry %r: %s has no pin %s' % (name, entry, ref, missing))
        return head, ref, [by_num[n] for n in nums]

    sides = mod['sides']
    runs = {s: [run(e) for e in sides.get(s, [])] for s in ('left', 'right')}
    placed = [(ref, p[0]) for s in runs.values() for _, ref, ps in s for p in ps]
    every = [(c['ref'], p[0]) for c in iface['connectors'] for p in c['pins']]
    if sorted(placed) != sorted(every):
        sys.exit('%s: sides must place every pin once: %s placed, %d pins'
                 % (name, len(placed), len(every)))

    headed = set()
    package = mod.get('body_width')

    def rows(side):
        out = []          # heading text ('' after the connector's first run), or a pin
        for head, ref, pins in runs[side]:
            if not package:
                out.append(head if head is not None
                           else '' if ref in headed else labels.get(ref, ref))
            headed.add(ref)
            out += [('%s.%s' % (ref, n) if multi else n, pn, et) for n, pn, et in pins]
        return out

    left, right = rows('left'), rows('right')

    def widest(rs):
        return max([shown_len(r if isinstance(r, str) else r[1]) for r in rs] + [0])
    n = max(len(left), len(right))
    top = math.ceil((n - 1) / 2) * GRID
    pin_len = PIN_LEN
    if package:
        # As the package: half a pin pitch and a bit past the end pins (a 24-pin
        # DIP's 1.25 in), the names outside on pins long enough to hold them,
        # ending on the 2.54 mm grid.
        half_w = package / 2
        need = max(widest(left), widest(right)) * CHAR_W + 2.5
        pin_len = snap_up(half_w + need) - half_w
        body_top, body_bot = top + 0.75 * GRID, top - (n - 1) * GRID - 0.75 * GRID
    else:
        half_w = snap_up((widest(left) + widest(right)) * CHAR_W / 2 + 2 * GRID)
        body_top, body_bot = top + GRID, top - (n - 1) * GRID - GRID
    descr = '%s Source: %s, %s at %s.' % (mod['description'], iface['repo'],
                                          iface['source'], iface['commit'])
    sym = ['symbol', Str(name),
           ['pin_names', ['offset', '0' if package else '1.016']],
           ] + ([['pin_numbers', ['hide', 'yes']]] if package else []) + [
           ['exclude_from_sim', 'yes'], ['in_bom', 'no'], ['on_board', 'no'],
           ['in_pos_files', 'no'], ['duplicate_pin_numbers_are_jumpers', 'no'],
           prop('Reference', mod.get('reference', 'A'), -half_w, body_top + GRID, justify='left'),
           prop('Value', name, -half_w, body_bot - GRID, justify='left'),
           prop('Footprint', '', 0, 0, hide=True),
           prop('Datasheet', iface['repo'], 0, 0, hide=True),
           prop('Description', descr, 0, 0, hide=True),
           prop('Source_Commit', iface['commit'], 0, 0, hide=True),
           prop('ki_keywords', 'sparky1 module interface', 0, 0, hide=True),
           ]
    body = ['symbol', Str('%s_1_1' % name),
            ['rectangle', ['start', fmt(-half_w), fmt(body_top)],
             ['end', fmt(half_w), fmt(body_bot)],
             ['stroke', ['width', '0.254'], ['type', 'default']],
             ['fill', ['type', 'background']]]]
    if package:
        title = ' / '.join(labels.get(c, c) for c in conns)
        body.append(['text', Str(title), ['at', '0', fmt((body_top + body_bot) / 2), '900'],
                     ['effects', ['font', ['size', fmt(FONT), fmt(FONT)], ['bold', 'yes']]]])
    for side, rs in (('left', left), ('right', right)):
        sign = -1 if side == 'left' else 1
        for i, r in enumerate(rs):
            y = top - i * GRID
            if isinstance(r, str):
                if not r:
                    continue
                body.append(['text', Str(r),
                             ['at', fmt(sign * (half_w - GRID)), fmt(y), '0'],
                             ['effects', ['font', ['size', fmt(FONT), fmt(FONT)], ['bold', 'yes']],
                              ['justify', side]]])
            else:
                number, pname, etype = r
                if package:
                    # Its number just inside the package edge, as on a chip label:
                    # KiCad's own, below the pin, would sit on the next pin's name.
                    body.append(['text', Str(number.split('.')[-1]),
                                 ['at', fmt(sign * (half_w - 0.5)), fmt(y), '0'],
                                 ['effects', ['font', ['size', fmt(FONT), fmt(FONT)]],
                                  ['justify', side]]])
                body.append(pin_node(etype, sign * (half_w + pin_len), y,
                                     0 if side == 'left' else 180, number, pname, pin_len))
    sym.append(body)
    sym.append(['embedded_fonts', 'no'])
    return sym


def build_stock(name, mod, iface):
    """A stock KiCad symbol, flattened and renamed to the module's name."""
    from sexp import find_all
    _, node = extract.stock_symbol(name, mod)
    old = str(node[1])
    out = [node[0], Str(name)]
    for item in node[2:]:
        if isinstance(item, list) and item and item[0] == 'symbol':
            sub = list(item)
            sub[1] = Str(str(item[1]).replace(old, name, 1))
            out.append(sub)
        elif isinstance(item, list) and item and item[0] == 'property':
            key = str(item[1])
            prop_ = list(item)
            if key == 'Value' and 'value' in mod:
                prop_[2] = Str(mod['value'])
            elif key == 'Description':
                prop_[2] = Str('%s Stock symbol %s, from %s.'
                               % (mod['description'], mod['stock'], iface['commit']))
            out.append(prop_)
        else:
            out.append(item)
    return out


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
        if 'stock' in reg[name]:
            syms.append(build_stock(name, reg[name], iface))
        elif 'sides' in reg[name]:
            syms.append(build_sided(name, reg[name], iface))
        else:
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
