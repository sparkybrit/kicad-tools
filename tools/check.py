"""Check that the library still matches the modules.

    uv run tools/check.py                         # library against interfaces/, every module
    uv run tools/check.py MODULE REPO [...]       # and each MODULE against its own schematic

The library must be exactly what build_lib.py generates from interfaces/, and the
pins read back out of it must match those files. Given a module's repo, its
connector nets are read afresh from its schematic and must match its interface
file pin for pin. Exits 1 on any difference, naming each pin.
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
    """{symbol: [[(number, name, type), ...] per unit]} read back from a .kicad_sym."""
    root = parse(open(path).read())[0]
    out = {}
    for sym in find_all(root, 'symbol'):
        name = str(sym[1])
        units = {}
        for sub in find_all(sym, 'symbol'):
            unit = int(str(sub[1]).rsplit('_', 2)[-2])
            for p in find_all(sub, 'pin'):
                units.setdefault(unit, []).append(
                    (str(find(p, 'number')[1]), str(find(p, 'name')[1]), str(p[1])))
        # Unit 0 holds pins common to every unit: fold them into each real unit.
        common = units.pop(0, [])
        if not units:
            units[1] = []
        out[name] = [sorted(common + units[u], key=lambda p: extract.pin_key(p[0]))
                     for u in sorted(units)]
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


def check_library():
    errs = []
    lib = build_lib.OUT
    if not os.path.exists(lib):
        return ['%s does not exist; run make' % os.path.basename(lib)]
    if open(lib).read() != build_lib.build():
        errs.append('%s is not what build_lib.py generates: edited by hand, or not rebuilt '
                    'after an interface changed; run make' % os.path.basename(lib))
    pins = library_pins(lib)
    reg = extract.registry()
    for name in sorted(set(reg) | set(pins)):
        if name not in pins:
            errs.append('library has no symbol %s' % name)
            continue
        if name not in reg:
            errs.append('library has symbol %s, which modules.toml does not list' % name)
            continue
        iface = extract.load(name)
        refs = [c['ref'] for c in iface['connectors']]
        if len(pins[name]) != len(refs):
            errs.append('%s: library has %d units, interface has %d connectors'
                        % (name, len(pins[name]), len(refs)))
            continue
        errs += compare('library %s' % name, as_pairs(iface), list(zip(refs, pins[name])))
    return errs


def check_module(name, repo):
    iface = extract.load(name)
    fresh = extract.extract(name, repo)
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
    ap.add_argument('pairs', nargs='*', metavar='MODULE REPO')
    args = ap.parse_args()
    if len(args.pairs) % 2:
        ap.error('give MODULE REPO in pairs')
    errs = check_library()
    checked = []
    for name, repo in zip(args.pairs[::2], args.pairs[1::2]):
        e, note = check_module(name, repo)
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
