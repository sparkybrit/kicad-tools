"""Emit KiCad 10 schematic (.kicad_sch) files.

The output matches what KiCad 10 itself writes - the same tokens, the same item
order, the same formatting - so KiCad opens the files without offering to
upgrade them, and saving a sheet from KiCad changes nothing but the generator
line.

Coordinates are millimetres with Y increasing downwards, matching KiCad. Pin
positions come from symlib.place_pin, so wires are drawn to where KiCad will
actually put the pin rather than to a guessed offset.

Every point is snapped to KiCad's 1.27 mm (50 mil) connection grid on the way
in, so the generator can lay out in round millimetres and hand edits in KiCad
still snap onto pins. Library pin offsets are all multiples of 1.27 mm, so
snapping preserves every alignment: a wire drawn to a pin still meets it.

UUIDs are derived deterministically from the project name, the sheet file and a
caller-supplied key, so regenerating the project produces a byte-identical file
and git diffs stay meaningful.

Copied from sparky1-backplane/tools/kisch.py and generalised: a sheet takes
symbols from several libraries, named by lib_id ('nickname:symbol'), and takes
each symbol's in_bom and on_board from its library.
"""
import os
import sys
import uuid as _uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import dumps, find, find_all, Str
from symlib import place_pin

NS = _uuid.UUID('2f1d4b8a-6c3e-5a7f-9b0d-1e2c3a4b5c6d')
SCH_VERSION = '20260306'        # KiCad 10 schematic format
GENERATOR_VERSION = '10.0'

# KiCad 10 writes a sheet's items grouped by type in this order, each group
# sorted by UUID. Matching it keeps a save from KiCad from reshuffling the file.
ITEM_ORDER = ['text', 'junction', 'no_connect', 'wire', 'label', 'global_label',
              'symbol', 'sheet']
FONT = ['effects', ['font', ['size', '1.27', '1.27']]]


def fmt(v):
    s = '%.4f' % float(v)
    s = s.rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


GRID = 1.27


def snap(v):
    return round(round(float(v) / GRID) * GRID, 4)


def snap_pt(p):
    return (snap(p[0]), snap(p[1]))


def at(x, y, rot=0):
    return ['at', fmt(x), fmt(y), fmt(rot)]


def pos(x, y):
    # Sheets, junctions and no-connects take a bare X Y; KiCad rejects a
    # rotation field on them.
    return ['at', fmt(x), fmt(y)]


def effects(size=1.27, justify=None):
    node = ['effects', ['font', ['size', fmt(size), fmt(size)]]]
    if justify:
        node.append(['justify'] + list(justify))
    return node


def field(name, value, x, y, rot=0, size=1.27, justify=None, hide=False):
    """A property on a placed item. KiCad 10 keeps `hide` on the property itself."""
    node = ['property', Str(name), Str(value), at(x, y, rot)]
    if hide:
        node.append(['hide', 'yes'])
    node += [['show_name', 'no'], ['do_not_autoplace', 'no'], effects(size, justify)]
    return node


class Placement:
    """A symbol instance, able to report where each of its pins landed."""

    def __init__(self, sym, unit, x, y, rot, mirror, ref):
        self.sym, self.unit, self.x, self.y = sym, unit, x, y
        self.rot, self.mirror, self.ref = rot, mirror, ref

    def pin(self, number):
        return snap_pt(place_pin(self.sym.pin(number, self.unit),
                                 self.x, self.y, self.rot, self.mirror))

    def pins(self):
        return {p.number: self.pin(p.number)
                for p in self.sym.pins if p.unit in (0, self.unit)}


class Sheet:
    def __init__(self, project, filename, title, libs, paper='A3',
                 rev='A', company='', comments=(), generator='sparky1-modules'):
        """libs: {nickname: symlib.Library}."""
        self.project, self.filename, self.title = project, filename, title
        self.libs, self.paper, self.rev = libs, paper, rev
        self.generator = generator
        self.company, self.comments = company, comments
        self.uuid = self.uid('sheet-self')
        self.items = []
        self.used = set()
        self.path = None          # set by the generator before render()
        self._pwr_seq = 0
        self._auto = 0
        self._instance_paths = []   # path nodes, filled in at render time
        self._segments = []     # ((x1,y1),(x2,y2)) for junction analysis
        self._pin_points = []   # absolute pin positions, ditto
        self._label_points = []

    # -- identity ---------------------------------------------------------
    def uid(self, key):
        return str(_uuid.uuid5(NS, '%s|%s|%s' % (self.project, self.filename, key)))

    def _key(self, key):
        if key is None:
            self._auto += 1
            return 'auto-%d' % self._auto
        return key

    # -- primitives -------------------------------------------------------
    def wire(self, a, b, key=None):
        a, b = snap_pt(a), snap_pt(b)
        if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
            raise ValueError('diagonal wire %s -> %s on %s' % (a, b, self.filename))
        if a == b:
            return
        self._segments.append((a, b))
        self.items.append(['wire',
                           ['pts', ['xy', fmt(a[0]), fmt(a[1])], ['xy', fmt(b[0]), fmt(b[1])]],
                           ['stroke', ['width', '0'], ['type', 'default']],
                           ['uuid', Str(self.uid('wire-%s-%s-%s' % (self._key(key), a, b)))]])

    def route(self, *points, **kw):
        """Wire through a list of points, which must be orthogonal step by step."""
        key = kw.get('key')
        for a, b in zip(points, points[1:]):
            self.wire(a, b, key=key)

    def elbow(self, a, b, horizontal_first=True, key=None):
        mid = (b[0], a[1]) if horizontal_first else (a[0], b[1])
        self.route(a, mid, b, key=key)

    def junction(self, p, key=None):
        p = snap_pt(p)
        self.items.append(['junction', pos(p[0], p[1]), ['diameter', '0'],
                           ['color', '0', '0', '0', '0'],
                           ['uuid', Str(self.uid('junc-%s-%s' % (self._key(key), p)))]])

    def no_connect(self, p, key=None):
        p = snap_pt(p)
        self.items.append(['no_connect', pos(p[0], p[1]),
                           ['uuid', Str(self.uid('nc-%s-%s' % (self._key(key), p)))]])

    def text(self, body, p, size=1.27, key=None, snap_to_grid=True):
        p = snap_pt(p) if snap_to_grid else (round(p[0], 4), round(p[1], 4))
        self.items.append(['text', Str(body), ['exclude_from_sim', 'no'],
                           at(p[0], p[1], 0), effects(size, ('left', 'bottom')),
                           ['uuid', Str(self.uid('text-%s-%s' % (self._key(key), p)))]])

    def global_label(self, name, p, rot=0, shape='bidirectional', key=None):
        p = snap_pt(p)
        self._label_points.append(p)
        self.items.append(['global_label', Str(name), ['shape', shape],
                           at(p[0], p[1], rot), ['fields_autoplaced', 'yes'],
                           effects(1.27, ('left',) if rot in (0, 90) else ('right',)),
                           ['uuid', Str(self.uid('gl-%s-%s-%s' % (name, self._key(key), p)))],
                           field('Intersheetrefs', '${INTERSHEET_REFS}', p[0], p[1], hide=True)])

    def label(self, name, p, rot=0, key=None):
        p = snap_pt(p)
        self._label_points.append(p)
        just = ('left', 'bottom') if rot in (0, 90) else ('right', 'bottom')
        self.items.append(['label', Str(name), at(p[0], p[1], rot),
                           ['fields_autoplaced', 'yes'], effects(1.27, just),
                           ['uuid', Str(self.uid('lab-%s-%s-%s' % (name, self._key(key), p)))]])

    # -- symbols ----------------------------------------------------------
    def symbol(self, lib_id, ref, value, p, rot=0, mirror=None, unit=1,
               footprint='', datasheet='', description='', fields=(),
               show_value=True, key=None, lib_fields=False):
        """Place a symbol. lib_fields puts Reference and Value where the library
        draws them (unrotated symbols only), rather than beside the origin."""
        nick, name = lib_id.split(':', 1)
        sym = self.libs[nick].get(name)
        for pin in sym.pins:
            if snap_pt((pin.x, pin.y)) != (pin.x, pin.y):
                raise ValueError('%s pin %s at (%s, %s) is off the %s mm grid'
                                 % (name, pin.number, pin.x, pin.y, GRID))
        self.used.add((nick, name))
        flag = lambda key_, default: (str(find(sym.node, key_)[1])
                                      if find(sym.node, key_) else default)
        x, y = snap_pt(p)
        uid = self.uid('sym-%s-%s' % (ref, self._key(key)))
        node = ['symbol', ['lib_id', Str(lib_id)], at(x, y, rot)]
        if mirror:
            node.append(['mirror', mirror])
        node += [['unit', str(unit)], ['body_style', '1'],
                 ['exclude_from_sim', flag('exclude_from_sim', 'no')],
                 ['in_bom', flag('in_bom', 'yes')],
                 ['on_board', flag('on_board', 'yes')],
                 ['in_pos_files', flag('in_pos_files', 'yes')], ['dnp', 'no'],
                 ['fields_autoplaced', 'yes'],
                 ['uuid', Str(uid)]]
        is_power = ref.startswith('#')
        spots = {'Reference': (x + 2.54, y - 1.27, ('left',)),
                 'Value': (x + 2.54, y + 1.27, ('left',))}
        if lib_fields and not rot and not mirror:
            for prop in find_all(sym.node, 'property'):
                if str(prop[1]) in spots:
                    pat = find(prop, 'at')
                    just = find(find(prop, 'effects') or [], 'justify')
                    spots[str(prop[1])] = (x + float(pat[1]), y - float(pat[2]),
                                           tuple(str(j) for j in just[1:]) if just else ())
        rx, ry, rj = spots['Reference']
        vx, vy, vj = spots['Value']
        node.append(field('Reference', ref, rx, ry, justify=rj, hide=is_power))
        node.append(field('Value', value, vx, vy, justify=vj, hide=not show_value))
        for fname, fvalue in [('Footprint', footprint), ('Datasheet', datasheet),
                              ('Description', description)] + list(fields):
            node.append(field(fname, fvalue, x, y, hide=True))
        # KiCad 10 lists every pin of the part on each unit's instance, not just
        # that unit's own; writing them all here keeps their UUIDs deterministic
        # rather than letting KiCad invent random ones on the next save.
        for pin in sym.pins:
            node.append(['pin', Str(pin.number),
                         ['uuid', Str(self.uid('pin-%s-%s-%s' % (ref, unit, pin.number)))]])
        path_node = ['path', Str(''), ['reference', Str(ref)], ['unit', str(unit)]]
        self._instance_paths.append(path_node)
        node.append(['instances', ['project', Str(self.project), path_node]])
        self.items.append(node)
        placement = Placement(sym, unit, x, y, rot, mirror, ref)
        self._pin_points.extend(placement.pins().values())
        return placement

    def subsheet(self, name, filename, p, size, page, key=None):
        uid = self.uid('subsheet-%s' % (key or name))
        p, (w, h) = snap_pt(p), snap_pt(size)
        path_node = ['path', Str(''), ['page', Str(str(page))]]
        self._instance_paths.append(path_node)
        self.items.append(
            ['sheet', pos(p[0], p[1]), ['size', fmt(w), fmt(h)],
             ['exclude_from_sim', 'no'], ['in_bom', 'yes'], ['on_board', 'yes'],
             ['dnp', 'no'],
             ['stroke', ['width', '0.1524'], ['type', 'solid']],
             ['fill', ['color', '0', '0', '0', '0']],
             ['uuid', Str(uid)],
             field('Sheetname', name, p[0], p[1] - 0.7239, size=1.524,
                   justify=('left', 'bottom')),
             field('Sheetfile', filename, p[0], p[1] + h + 1.0, size=1.524,
                   justify=('left', 'top')),
             ['instances',
              ['project', Str(self.project), path_node]]])
        return uid

    # -- output -----------------------------------------------------------

    def render(self, root_uuid=None):
        if not self.path:
            raise ValueError('sheet path not set for %s' % self.filename)
        for node in self._instance_paths:
            node[1] = Str(self.path)
        # KiCad omits empty title-block fields.
        title = ['title_block', ['title', Str(self.title)], ['rev', Str(self.rev)]]
        if self.company:
            title.append(['company', Str(self.company)])
        for i, c in enumerate(self.comments, start=1):
            if c:
                title.append(['comment', str(i), Str(c)])
        lib_syms = ['lib_symbols']
        for nick, name in sorted(self.used):
            node = self.libs[nick].flatten(name)
            # Only the outer name carries the library prefix; KiCad rejects
            # unit sub-symbols ("R_0_1") that have one.
            lib_syms.append([node[0], Str('%s:%s' % (nick, name))] + list(node[2:]))
        items = sorted(self.items, key=lambda n: (ITEM_ORDER.index(n[0]),
                                                  str(find(n, 'uuid')[1])))
        root = ['kicad_sch',
                ['version', SCH_VERSION],
                ['generator', Str(self.generator)],
                ['generator_version', Str(GENERATOR_VERSION)],
                ['uuid', Str(self.uuid)],
                ['paper', Str(self.paper)],
                title,
                lib_syms] + items
        if root_uuid is not None:
            root.append(['sheet_instances', ['path', Str('/'), ['page', Str('1')]]])
        return dumps(root) + '\n'


def _on_segment(p, a, b):
    """True if p lies strictly between a and b on an axis-aligned segment."""
    if abs(a[0] - b[0]) < 1e-6:                       # vertical
        return (abs(p[0] - a[0]) < 1e-6
                and min(a[1], b[1]) + 1e-6 < p[1] < max(a[1], b[1]) - 1e-6)
    if abs(a[1] - b[1]) < 1e-6:                       # horizontal
        return (abs(p[1] - a[1]) < 1e-6
                and min(a[0], b[0]) + 1e-6 < p[0] < max(a[0], b[0]) - 1e-6)
    return False


def analyse_junctions(sheet):
    """Work out where KiCad needs a junction dot, and flag suspicious crossings.

    A point needs a junction once three or more things meet there. Wire ends and
    pins each count once; a wire passing straight through counts twice, since it
    continues in both directions.

    Returns (junction_points, crossings) where crossings are points at which two
    wires cross with no wire end or pin involved - almost always a layout
    mistake rather than an intended connection.
    """
    pts = set()
    for a, b in sheet._segments:
        pts.add(a)
        pts.add(b)
    for p in sheet._pin_points:
        pts.add(tuple(p))

    junctions, crossings = [], []
    for p in sorted(pts):
        ends = sum(1 for a, b in sheet._segments if a == p or b == p)
        thru = sum(1 for a, b in sheet._segments if _on_segment(p, a, b))
        pins = sum(1 for q in sheet._pin_points if tuple(q) == p)
        degree = ends + 2 * thru + pins
        if degree >= 3:
            junctions.append(p)
            if ends == 0 and pins == 0:
                crossings.append(p)
    return junctions, crossings
