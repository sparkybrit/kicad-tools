"""Check that a project's library still matches its modules' sources.

    uv run tools/check.py [-C DIR]                  # library, and every module with a path
    uv run tools/check.py [-C DIR] MODULE REPO [...] # and each MODULE against a checkout

The library must be exactly what build_lib.py generates from interfaces/, and the
pins read back out of it must match those files. Each module with a `path` (and
each MODULE given with a checkout) has its pins read afresh from its source, which
must match its interface file pin for pin. Exits 1 on any difference, naming each
pin.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import parse, find, find_all
import extract
import build_lib


def library_pins(path):
    """{symbol: [(connector or None, [(number, name, type), ...])]} read from a .kicad_sym.

    Pins numbered CONN.N are grouped by connector, whatever unit they are drawn
    in; otherwise each unit is one connector, its name left to the caller."""
    root = parse(open(path).read())[0]
    out = {}
    for sym in find_all(root, 'symbol'):
        name = str(sym[1])
        units, conns = {}, {}
        for sub in find_all(sym, 'symbol'):
            unit = int(str(sub[1]).rsplit('_', 2)[-2])
            for p in find_all(sub, 'pin'):
                number = str(find(p, 'number')[1])
                pin = (number.split('.', 1)[-1], str(find(p, 'name')[1]), str(p[1]))
                if '.' in number:
                    conns.setdefault(number.split('.', 1)[0], []).append(pin)
                else:
                    units.setdefault(unit, []).append(pin)
        key = lambda p: extract.pin_key(p[0])
        if conns:
            out[name] = [(c, sorted(ps, key=key)) for c, ps in conns.items()]
            continue
        # Unit 0 holds pins common to every unit: fold them into each real unit.
        common = units.pop(0, [])
        if not units:
            units[1] = []
        out[name] = [(None, sorted(common + units[u], key=key)) for u in sorted(units)]
    return out


def compare(what, want, got):
    """Pin-by-pin differences between two [(ref, [(number, name, type)])] lists."""
    errs = []
    want_refs = [r for r, _ in want]
    got_refs = [r for r, _ in got]
    if want_refs != got_refs:
        errs.append('%s: connectors %s, expected %s' % (what, got_refs, want_refs))
    for (ref, wp), (_, gp) in zip(want, got):
        w = {p[0]: p[1:] for p in wp}
        g = {p[0]: p[1:] for p in gp}
        show = lambda v: '%s (%s)' % v if v else None
        for num in sorted(set(w) | set(g), key=extract.pin_key):
            if w.get(num) != g.get(num):
                errs.append('%s: %s pin %s is %s, expected %s'
                            % (what, ref, num, show(g.get(num)) or '(missing)',
                               show(w.get(num)) or '(absent)'))
    return errs


def as_pairs(iface):
    return [(c['ref'], [tuple(p) for p in c['pins']]) for c in iface['connectors']]


def check_library(project):
    errs = []
    lib = project.lib_path
    if not os.path.exists(lib):
        return ['%s does not exist; run make' % os.path.basename(lib)]
    if open(lib).read() != build_lib.build(project):
        errs.append('%s is not what build_lib.py generates: edited by hand, or not rebuilt '
                    'after an interface changed; run make' % os.path.basename(lib))
    pins = library_pins(lib)
    reg = project.modules
    for name in sorted(set(reg) | set(pins)):
        if name not in pins:
            errs.append('library has no symbol %s' % name)
            continue
        if name not in reg:
            errs.append('library has symbol %s, which modules.toml does not list' % name)
            continue
        iface = project.load(name)
        refs = [c['ref'] for c in iface['connectors']]
        got = pins[name]
        if got[0][0] is None:
            if len(got) != len(refs):
                errs.append('%s: library has %d units, interface has %d connectors'
                            % (name, len(got), len(refs)))
                continue
            got = [(r, ps) for r, (_, ps) in zip(refs, got)]
        else:
            got.sort(key=lambda g: refs.index(g[0]) if g[0] in refs else len(refs))
        errs += compare('library %s' % name, as_pairs(iface), got)
    return errs


def check_module(project, name, repo=None):
    iface = project.load(name)
    fresh = extract.extract(project, name, repo)
    errs = compare('%s (%s at %s)' % (name, fresh['source'], fresh['commit']),
                   as_pairs(iface), as_pairs(fresh))
    note = None
    if not errs and fresh['commit'] != iface['commit']:
        note = ('%s: schematic now at %s, interface taken at %s; pins unchanged'
                % (name, fresh['commit'], iface['commit']))
    return errs, note


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-C', dest='dir', default='.', help='the directory holding modules.toml')
    ap.add_argument('pairs', nargs='*', metavar='MODULE REPO')
    args = ap.parse_args()
    if len(args.pairs) % 2:
        ap.error('give MODULE REPO in pairs')
    project = extract.Project(args.dir)
    errs = check_library(project)
    checked = []
    todo = [(n, None) for n, m in project.modules.items() if 'path' in m or 'stock' in m]
    given = list(zip(args.pairs[::2], args.pairs[1::2]))
    todo = [t for t in todo if t[0] not in dict(given)] + given
    for name, repo in todo:
        e, note = check_module(project, name, repo)
        errs += e
        checked.append(name)
        if note:
            print(note)
    for e in errs:
        print('FAIL ' + e)
    if errs:
        print('%d difference%s' % (len(errs), '' if len(errs) == 1 else 's'))
        return 1
    print('ok: library matches interfaces/' +
          (''.join('; %s matches its source' % n for n in checked)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
