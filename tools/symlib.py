"""Reading KiCad symbol libraries and locating symbol pins in schematic space.

The pin geometry here is what lets the generator draw wires that actually land on
pins. Library symbols are stored with Y pointing up; schematics use Y pointing
down, so every pin is mirrored in Y before the placement rotation is applied.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sexp import parse, find, find_all, Str


class Pin:
    def __init__(self, number, name, etype, style, x, y, angle, length, unit):
        self.number, self.name, self.etype, self.style = number, name, etype, style
        self.x, self.y, self.angle, self.length, self.unit = x, y, angle, length, unit

    def __repr__(self):
        return 'Pin(%s %s @%.2f,%.2f u%d)' % (self.number, self.name, self.x, self.y, self.unit)


class Symbol:
    def __init__(self, name, node, lib):
        self.name, self.node, self.lib = name, node, lib
        self.pins = []
        self.units = set()
        for sub in find_all(node, 'symbol'):
            parts = str(sub[1]).rsplit('_', 2)
            unit = int(parts[-2]) if len(parts) == 3 and parts[-2].isdigit() else 0
            for p in find_all(sub, 'pin'):
                at = find(p, 'at')
                self.pins.append(Pin(
                    str(find(p, 'number')[1]), str(find(p, 'name')[1]), p[1], p[2],
                    float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0,
                    float(find(p, 'length')[1]), unit))
                self.units.add(unit)
        self.props = {str(p[1]): str(p[2]) for p in find_all(node, 'property')}

    def unit_count(self):
        real = [u for u in self.units if u > 0]
        return max(real) if real else 1

    def pin(self, number, unit=None):
        for p in self.pins:
            if p.number == number and (unit is None or p.unit in (0, unit)):
                return p
        raise KeyError('%s: no pin %r (unit %r)' % (self.name, number, unit))


class Library:
    """A .kicad_sym file, with `extends` inheritance flattened on demand."""

    def __init__(self, path):
        self.path = path
        self.root = parse(open(path).read())[0]
        self.raw = {str(s[1]): s for s in find_all(self.root, 'symbol')}
        self._cache = {}

    def names(self):
        return sorted(self.raw)

    def get(self, name):
        if name in self._cache:
            return self._cache[name]
        if name not in self.raw:
            raise KeyError('%s: no symbol %r' % (self.path, name))
        node = self.raw[name]
        ext = find(node, 'extends')
        if ext:
            node = self.flatten(name)
        sym = Symbol(name, node, self)
        self._cache[name] = sym
        return sym

    def flatten(self, name):
        """Resolve `extends` into a standalone symbol definition.

        Graphics and pins come from the base symbol; properties from the child
        win, so a derived part keeps its own value, footprint and datasheet.
        """
        node = self.raw[name]
        ext = find(node, 'extends')
        if not ext:
            return node
        base = self.flatten(str(ext[1]))
        base_name = str(base[1])
        out = [node[0], Str(name)]
        child_props = {str(p[1]): p for p in find_all(node, 'property')}
        seen = set()
        for item in base[2:]:
            if not isinstance(item, list):
                continue
            if item[0] == 'property':
                key = str(item[1])
                seen.add(key)
                out.append(child_props.get(key, item))
            elif item[0] == 'symbol':
                sub = [c for c in item]
                sub[1] = Str(str(item[1]).replace(base_name, name, 1))
                out.append(sub)
            else:
                out.append(item)
        for key, prop in child_props.items():
            if key not in seen:
                out.append(prop)
        return out


def place_pin(pin, at_x, at_y, rot=0, mirror=None):
    """Absolute schematic position of `pin` for a symbol placed at (at_x, at_y).

    Library Y is up and schematic Y is down, hence the initial negation.
    """
    x, y = pin.x, -pin.y
    if mirror == 'x':
        y = -y
    elif mirror == 'y':
        x = -x
    a = math.radians(rot)
    ca, sa = round(math.cos(a)), round(math.sin(a))
    rx = x * ca + y * sa
    ry = -x * sa + y * ca
    return (round(at_x + rx, 4), round(at_y + ry, 4))
