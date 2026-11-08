"""Minimal S-expression reader/writer for KiCad files."""
import re

TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse(text):
    """Parse S-expression text into nested lists. Strings become ('str', value)."""
    pos, stack, cur = 0, [], []
    n = len(text)
    while pos < n:
        m = TOKEN.match(text, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done = cur
            if not stack:
                raise ValueError("unbalanced )")
            cur = stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Str(unescape(m.group(3))))
        else:
            cur.append(m.group(4))
    if stack:
        raise ValueError("unbalanced (")
    return cur


class Str(str):
    """Marks an atom that was quoted in the source, so it re-serialises quoted."""
    __slots__ = ()


def unescape(s):
    return s.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')


def escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def dumps(node, indent=0):
    pad = '\t' * indent
    if isinstance(node, Str):
        return '"%s"' % escape(node)
    if isinstance(node, str):
        return node
    head = node[0] if node else None
    simple = all(not isinstance(c, list) for c in node)
    if simple:
        return '(' + ' '.join(dumps(c) for c in node) + ')'
    if head == 'pts' and all(isinstance(c, list) and c[0] == 'xy' for c in node[1:]):
        # KiCad keeps a point list on one line, so match it byte for byte.
        return ('(pts\n' + pad + '\t' + ' '.join(dumps(c) for c in node[1:])
                + '\n' + pad + ')')
    out = ['(']
    for i, c in enumerate(node):
        if isinstance(c, list):
            out.append('\n' + pad + '\t' + dumps(c, indent + 1))
        else:
            out.append(('' if i == 0 else ' ') + dumps(c))
    out.append('\n' + pad + ')')
    return ''.join(out)


def find_all(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def find(node, tag):
    r = find_all(node, tag)
    return r[0] if r else None
