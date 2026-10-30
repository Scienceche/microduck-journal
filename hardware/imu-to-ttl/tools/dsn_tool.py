#!/usr/bin/env python3
"""DSN preprocessor for Freerouting (v3.8 strategy).

Two community-standard fixes for KiCad's Specctra export gaps:

  strip_net(text, name)  - remove a net (e.g. GND) from the DSN so the router
                           never spends channels on it; the copper pours carry
                           it instead (filled AFTER routing by pours.py).
  inset_boundary(text, mm) - KiCad's DSN export contains no board-edge
                           clearance rule, so routed copper can hug the edge /
                           the bite walls. Insetting the boundary polygon by
                           the edge clearance makes the router respect it.

Both operate on the DSN text between pcbnew.ExportSpecctraDSN and the
Freerouting run; unit scale is derived from the boundary extents vs the
board's Edge.Cuts bounding box passed in mm.
"""
import re


def _blocks(text, start_index):
    """Yield (start, end) of paren-balanced blocks beginning at start_index."""
    depth = 0
    i = start_index
    while i < len(text):
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                yield start_index, i + 1
                return
        i += 1


def strip_net(text, name):
    """Delete '(net NAME ...)' block and the NAME token from class lines."""
    m = re.search(r'\n\s*\(net %s\b' % re.escape(name), text)
    if not m:
        return text, False
    s, e = next(_blocks(text, m.start() + 1))
    # m.start()+1 -> the '(' of (net
    text = text[:m.start()] + '\n' + text[e:]
    text = re.sub(r'(\(class [^\n]*?)\b%s\b' % re.escape(name), r'\1', text, count=1)
    # collapse the double space left in the class line
    text = re.sub(r'\(class ([^\n]*)\)', lambda mm: '(class ' + re.sub(r'  +', ' ', mm.group(1)) + ')',
                  text, count=0)
    return text, True


def _path_points(path_body):
    pts = []
    for tok in re.findall(r'(-?[\d.]+)\s+(-?[\d.]+)', path_body):
        pts.append((float(tok[0]), float(tok[1])))
    return pts


def _inset_polygon(pts, d):
    """Miter inset of a closed polygon by distance d (inward).

    Works for mostly-convex outlines with fine arc discretization; at sharp
    concave corners the miter can overshoot, which for a router keepout is
    harmless (it only removes a little extra routing area).
    """
    n = len(pts)
    # signed area -> orientation
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    cw = area < 0

    # offset line for each edge
    lines = []
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-9:
            lines.append(None)
            continue
        # inward normal (for CW polygon in standard axes, inward = left of dir)
        if cw:
            nx, ny = dy / L, -dx / L
        else:
            nx, ny = -dy / L, dx / L
        lines.append((x1 + nx * d, y1 + ny * d, x2 + nx * d, y2 + ny * d))

    out = []
    for i in range(n):
        a = lines[i]
        b = lines[(i + 1) % n]
        if a is None or b is None:
            out.append(pts[i])
            continue
        # intersection of line a (p1,p2) and line b (p3,p4)
        x1, y1, x2, y2 = a
        x3, y3, x4, y4 = b
        d1x, d1y = x2 - x1, y2 - y1
        d2x, d2y = x4 - x3, y4 - y3
        den = d1x * d2y - d1y * d2x
        if abs(den) < 1e-12:
            out.append(((x2 + x3) / 2.0, (y2 + y3) / 2.0))
            continue
        t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / den
        out.append((x1 + t * d1x, y1 + t * d1y))
    return out


def _rdp(pts, eps):
    """Ramker-Douglas-Peucker simplification (closed polygon: anchor at 0)."""
    if len(pts) < 8:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[len(pts) // 2] = keep[-1] = True
    stack = [(0, len(pts) - 1), (len(pts) // 2, len(pts) - 1), (0, len(pts) // 2)]
    stack = [(0, len(pts) // 2), (len(pts) // 2, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5 or 1e-12
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dx * (pts[i][1] - ay) - dy * (pts[i][0] - ax)) / L
            if d > best:
                best, bi = d, i
        if best > eps:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def inset_boundary(text, board_w_mm, board_h_mm):
    """Inset the (boundary (path pcb ...)) polygon.

    DSN unit scale is derived from the path extent vs the board size in mm.
    """
    m = re.search(r'\(boundary\s*\(path pcb [^\s]*\s*(.*?)\)\s*\)', text, re.S)
    if not m:
        return text, False
    pts = _path_points(m.group(1))
    if len(pts) < 8:
        return text, False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    scale = span / max(board_w_mm, board_h_mm)      # DSN units per mm
    d = 0.25 * scale
    inset = _inset_polygon(pts, d)
    inset = _rdp(inset, 0.06 * scale)  # dense arc points hang FR1.9's outline pass
    body = ' '.join('%.1f %.1f' % p for p in inset)
    new_block = '(path pcb 0 %s)' % body
    text = text[:m.start()] + new_block + text[m.end():]
    return text, True


if __name__ == '__main__':
    # smoke test on the exported DSN
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else 'analysis/imu_to_ttl.dsn'
    t = open(src, encoding='utf-8').read()
    t, ok = strip_net(t, 'GND')
    print('strip GND:', ok)
    t, ok = inset_boundary(t, 36.0, 30.0)
    print('inset boundary:', ok)
    open(src + '.test', 'w', encoding='utf-8').write(t)
    print('wrote', src + '.test')
