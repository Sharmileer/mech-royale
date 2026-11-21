"""Static campus geometry and bounded, incremental ground navigation (stdlib only).

The map is public. No spawn, seed, loot or opponent state is inferred from files.
Prism ray tests are geometric; terrain ray tests are conservatively sampled.
"""
from __future__ import annotations

from array import array
from collections import defaultdict
import heapq
import json
import math
from pathlib import Path
import sys


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def inside_ring(x, y, ring):
    inside = False
    ax, ay = ring[-2], ring[-1]
    for k in range(0, len(ring), 2):
        bx, by = ring[k], ring[k + 1]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
        ax, ay = bx, by
    return inside


def segment_dist2(x, y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = clamp(((x - ax) * dx + (y - ay) * dy) / max(dx * dx + dy * dy, 1e-12), 0, 1)
    return (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2


def segments_dist2(ax, ay, bx, by, cx, cy, dx, dy):
    ux, uy, vx, vy = bx - ax, by - ay, dx - cx, dy - cy
    den = ux * vy - uy * vx
    if abs(den) > 1e-10:
        rx, ry = cx - ax, cy - ay
        t, s = (rx * vy - ry * vx) / den, (rx * uy - ry * ux) / den
        if 0 <= t <= 1 and 0 <= s <= 1:
            return 0.0
    return min(segment_dist2(ax, ay, cx, cy, dx, dy), segment_dist2(bx, by, cx, cy, dx, dy),
               segment_dist2(cx, cy, ax, ay, bx, by), segment_dist2(dx, dy, ax, ay, bx, by))


class Prism:
    def __init__(self, raw):
        self.base, self.top = raw['base'], raw['top']
        self.ring, self.holes = raw['ring'], raw.get('holes', [])
        self.x0, self.x1 = min(self.ring[::2]), max(self.ring[::2])
        self.y0, self.y1 = min(self.ring[1::2]), max(self.ring[1::2])
        self.edges = []
        for ring in [self.ring] + self.holes:
            ax, ay = ring[-2:]
            for k in range(0, len(ring), 2):
                bx, by = ring[k:k + 2]
                self.edges.append((ax, ay, bx, by))
                ax, ay = bx, by

    def inside(self, x, y):
        return (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1
                and inside_ring(x, y, self.ring)
                and not any(inside_ring(x, y, h) for h in self.holes))

    def distance(self, x, y, limit):
        if x < self.x0 - limit or x > self.x1 + limit or y < self.y0 - limit or y > self.y1 + limit:
            return limit
        if self.inside(x, y):
            return 0.0
        best = limit * limit
        for a, b, c, d in self.edges:
            if max(a, c) < x - limit or min(a, c) > x + limit or max(b, d) < y - limit or min(b, d) > y + limit:
                continue
            best = min(best, segment_dist2(x, y, a, b, c, d))
        return math.sqrt(best)

    def sweep_distance(self, a, b, limit):
        if (max(a[0], b[0]) < self.x0 - limit or min(a[0], b[0]) > self.x1 + limit
                or max(a[1], b[1]) < self.y0 - limit or min(a[1], b[1]) > self.y1 + limit):
            return limit
        if self.inside(*a[:2]) or self.inside(*b[:2]):
            return 0.0
        best = limit * limit
        x0, x1 = min(a[0], b[0]) - limit, max(a[0], b[0]) + limit
        y0, y1 = min(a[1], b[1]) - limit, max(a[1], b[1]) + limit
        for ax, ay, bx, by in self.edges:
            if max(ax, bx) < x0 or min(ax, bx) > x1 or max(ay, by) < y0 or min(ay, by) > y1:
                continue
            best = min(best, segments_dist2(a[0], a[1], b[0], b[1], ax, ay, bx, by))
        return math.sqrt(best)

    def intersects_ray(self, a, b):
        if (max(a[0], b[0]) < self.x0 or min(a[0], b[0]) > self.x1
                or max(a[1], b[1]) < self.y0 or min(a[1], b[1]) > self.y1):
            return False
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        lo, hi = 0.0, 1.0
        if abs(dz) < 1e-10:
            if not self.base - 1e-6 <= a[2] <= self.top + 1e-6:
                return False
        else:
            t0, t1 = (self.base - a[2]) / dz, (self.top - a[2]) / dz
            lo, hi = max(lo, min(t0, t1)), min(hi, max(t0, t1))
        if lo > hi:
            return False
        # Splitting at footprint crossings handles concave rings and courtyards.
        cuts = [lo, hi]
        for ax, ay, bx, by in self.edges:
            ex, ey = bx - ax, by - ay
            den = dx * ey - dy * ex
            if abs(den) < 1e-10:
                continue
            rx, ry = ax - a[0], ay - a[1]
            t, u = (rx * ey - ry * ex) / den, (rx * dy - ry * dx) / den
            if lo <= t <= hi and -1e-7 <= u <= 1 + 1e-7:
                cuts.append(t)
        cuts.sort()
        for left, right in zip(cuts, cuts[1:]):
            t = (left + right) * 0.5
            if self.inside(a[0] + dx * t, a[1] + dy * t):
                return True
        return self.inside(a[0] + dx * lo, a[1] + dy * lo)


class ArenaMap:
    CELL = 4.0
    BUCKET = 24.0

    def __init__(self, map_dir, config):
        root = Path(map_dir)
        world_path = root / 'v2' / 'world.json'
        data = json.loads(world_path.read_text())
        self.bounds = data['bounds']
        self.terrain = data['terrain']
        self.raw = array('h')
        terrain_path = world_path.parent / self.terrain['file']
        with terrain_path.open('rb') as f:
            self.raw.fromfile(f, self.terrain['nx'] * self.terrain['ny'])
        if sys.byteorder != 'little':
            self.raw.byteswap()
        self.prisms = [Prism(p) for p in data['prisms']]
        self.buckets = defaultdict(list)
        for i, p in enumerate(self.prisms):
            for bx in range(math.floor(p.x0 / self.BUCKET), math.floor(p.x1 / self.BUCKET) + 1):
                for by in range(math.floor(p.y0 / self.BUCKET), math.floor(p.y1 / self.BUCKET) + 1):
                    self.buckets[bx, by].append(i)
        self.step_up = config['mech']['step_up']
        self.slope = math.tan(math.radians(config['mech']['max_slope_deg']))
        self.x0, self.y0 = self.bounds['minX'], self.bounds['minY']
        self.nx = math.ceil((self.bounds['maxX'] - self.x0) / self.CELL)
        self.ny = math.ceil((self.bounds['maxY'] - self.y0) / self.CELL)
        self.clearance = array('f', [0.0]) * (self.nx * self.ny)
        self.ground = array('f', [0.0]) * (self.nx * self.ny)
        # Clearance is deliberately conservative for tall, upgraded mechs.
        nav_height = config['mech']['height'] * 1.7
        for j in range(self.ny):
            y = self.y0 + (j + 0.5) * self.CELL
            for i in range(self.nx):
                x = self.x0 + (i + 0.5) * self.CELL
                k = j * self.nx + i
                z = self.height(x, y)
                self.ground[k] = z
                c = min(14.0, x - self.x0, self.bounds['maxX'] - x,
                        y - self.y0, self.bounds['maxY'] - y)
                for p in self.near_prisms(x, y, 14):
                    if p.top > z + self.step_up and p.base < z + nav_height:
                        c = p.distance(x, y, c)
                        if c <= 0:
                            break
                self.clearance[k] = c
        self._components(config['mech']['radius'] + .6)

    def _components(self, clearance):
        """Avoid selecting ground goals inside disconnected building courtyards."""
        self.component = array('i', [-1]) * len(self.clearance)
        label = 0
        for seed in range(len(self.clearance)):
            if self.component[seed] >= 0 or self.clearance[seed] < clearance:
                continue
            self.component[seed] = label
            pending = [seed]
            while pending:
                u = pending.pop()
                ui, uj = u % self.nx, u // self.nx
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    i, j = ui + di, uj + dj
                    if not (0 <= i < self.nx and 0 <= j < self.ny):
                        continue
                    v = j * self.nx + i
                    if self.component[v] >= 0 or self.clearance[v] < clearance:
                        continue
                    if abs(self.ground[v] - self.ground[u]) > self.CELL * self.slope:
                        continue
                    self.component[v] = label
                    pending.append(v)
            label += 1

    def height(self, x, y):
        t = self.terrain
        fx = clamp((x - t['minX']) / t['step'], 0, t['nx'] - 1.00001)
        fy = clamp((y - t['minY']) / t['step'], 0, t['ny'] - 1.00001)
        i, j = int(fx), int(fy)
        u, v = fx - i, fy - j
        k, n = j * t['nx'] + i, t['nx']
        return ((1 - v) * ((1 - u) * self.raw[k] + u * self.raw[k + 1])
                + v * ((1 - u) * self.raw[k + n] + u * self.raw[k + n + 1])) * t.get('scale', .01)

    def near_prisms(self, x, y, radius=0):
        seen = set()
        for i in range(math.floor((x - radius) / self.BUCKET), math.floor((x + radius) / self.BUCKET) + 1):
            for j in range(math.floor((y - radius) / self.BUCKET), math.floor((y + radius) / self.BUCKET) + 1):
                for k in self.buckets.get((i, j), ()):
                    if k not in seen:
                        seen.add(k)
                        yield self.prisms[k]

    def cell(self, x, y):
        i = int(clamp((x - self.x0) / self.CELL, 0, self.nx - 1))
        j = int(clamp((y - self.y0) / self.CELL, 0, self.ny - 1))
        return j * self.nx + i

    def xy(self, k):
        return (self.x0 + (k % self.nx + .5) * self.CELL,
                self.y0 + (k // self.nx + .5) * self.CELL)

    def nearest_free(self, x, y, radius, max_cells=16, component=None):
        base = self.cell(x, y)
        if self.clearance[base] >= radius + .6 and (component is None or self.component[base] == component):
            return base
        bi, bj = base % self.nx, base // self.nx
        for r in range(1, max_cells + 1):
            candidates = []
            for j in range(max(0, bj - r), min(self.ny, bj + r + 1)):
                for i in range(max(0, bi - r), min(self.nx, bi + r + 1)):
                    if abs(i - bi) != r and abs(j - bj) != r:
                        continue
                    k = j * self.nx + i
                    if self.clearance[k] >= radius + .6 and (component is None or self.component[k] == component):
                        qx, qy = self.xy(k)
                        candidates.append(((qx - x) ** 2 + (qy - y) ** 2, k))
            if candidates:
                return min(candidates)[1]
        return None

    def free_at(self, x, y, z, radius, height):
        b = self.bounds
        if not b['minX'] + radius < x < b['maxX'] - radius or not b['minY'] + radius < y < b['maxY'] - radius:
            return False
        for p in self.near_prisms(x, y, radius + .15):
            if p.top > z + self.step_up and p.base < z + height - .1:
                if p.distance(x, y, radius + .15) < radius + .1:
                    return False
        return True

    def can_move(self, pos, end, radius, height):
        distance = math.hypot(end[0] - pos[0], end[1] - pos[1])
        n = max(1, math.ceil(distance / .75))
        ground_start = self.height(pos[0], pos[1])
        airborne = pos[2] > ground_start + self.step_up + .8
        previous = ground_start
        min_feet, max_feet = (pos[2], pos[2]) if airborne else (ground_start, ground_start)
        for k in range(1, n + 1):
            f = k / n
            x = pos[0] + (end[0] - pos[0]) * f
            y = pos[1] + (end[1] - pos[1]) * f
            z = self.height(x, y)
            if not airborne and z - previous > max(self.step_up, distance / n * self.slope):
                return False
            feet = max(z, pos[2]) if airborne else z
            min_feet, max_feet = min(min_feet, feet), max(max_feet, feet)
            previous = z
        b = self.bounds
        if not b['minX'] + radius < end[0] < b['maxX'] - radius or not b['minY'] + radius < end[1] < b['maxY'] - radius:
            return False
        # Exact swept circle prevents long-step samples from missing a corner.
        for prism in self.near_prisms((pos[0] + end[0]) * .5, (pos[1] + end[1]) * .5, distance * .5 + radius + .15):
            if prism.top <= min_feet + self.step_up or prism.base >= max_feet + height - .1:
                continue
            if prism.sweep_distance(pos, end, radius + .15) < radius + .1:
                start_clear = prism.distance(pos[0], pos[1], radius + .15)
                end_clear = prism.distance(end[0], end[1], radius + .15)
                # Growth or physics can leave us touching geometry: allow escape.
                if not (start_clear < radius + .1 and end_clear > start_clear + .01):
                    return False
        return True

    def line_clear(self, a, b, terrain=True):
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        distance = math.hypot(dx, dy)
        seen = set()
        n = max(1, math.ceil(distance / (self.BUCKET * .45)))
        for k in range(n + 1):
            f = k / n
            x, y = a[0] + dx * f, a[1] + dy * f
            # Including adjacent buckets also catches a short corner crossing.
            for p in self.near_prisms(x, y, self.BUCKET * .55):
                ident = id(p)
                if ident not in seen:
                    seen.add(ident)
                    if p.intersects_ray(a, b):
                        return False
        if terrain:
            n = max(1, math.ceil(distance / 3))
            for k in range(1, n):
                f = k / n
                if a[2] + dz * f < self.height(a[0] + dx * f, a[1] + dy * f) + .08:
                    return False
        return True


class Navigator:
    """Budget path work across observations; never search unboundedly in act()."""
    def __init__(self, world):
        self.world = world
        self.path = []
        self.goal = None
        self.search = None
        self.radius = 0
        self.done_at = -100

    def invalidate(self):
        self.search = None
        self.goal = None
        self.path = []

    def waypoint(self, pos, goal, radius, height, now, deadline):
        w = self.world
        if math.dist(pos[:2], goal[:2]) < 42 and w.can_move(pos, goal, radius, height):
            self.search = None
            return goal[:2]
        start = w.nearest_free(pos[0], pos[1], radius)
        component = w.component[start] if start is not None else None
        end = w.nearest_free(goal[0], goal[1], radius, component=component)
        if start is None or end is None:
            return goal[:2]
        if self.goal != end or abs(self.radius - radius) > .2 or (not self.path and not self.search and now - self.done_at > 1):
            self.goal, self.radius = end, radius
            self.path = []
            gx, gy = w.xy(end)
            sx, sy = w.xy(start)
            self.search = {'open': [(math.hypot(gx - sx, gy - sy), start)],
                           'g': {start: 0.0}, 'parent': {}, 'closed': set(),
                           'start': start, 'best': start, 'best_h': math.hypot(gx - sx, gy - sy)}
        if self.search:
            self._advance(deadline)
            if self.search is None:
                self.done_at = now
        if self.path:
            nearest = min(range(min(len(self.path), 20)), key=lambda i: math.dist(pos[:2], w.xy(self.path[i])))
            if nearest:
                self.path = self.path[nearest:]
            chosen = None
            for k in self.path[:7]:
                pt = w.xy(k)
                if w.can_move(pos, pt, radius, height):
                    chosen = pt
                else:
                    break
            if chosen:
                return chosen
        # Move toward the projected start while a long route is being computed.
        projected = w.xy(start)
        if math.dist(pos[:2], projected) > 2:
            return projected
        return goal[:2]

    def _advance(self, deadline):
        import time
        s, w = self.search, self.world
        gx, gy = w.xy(self.goal)
        count = 0
        while s['open'] and count < 700:
            if count % 32 == 0 and time.perf_counter() >= deadline:
                break
            _, u = heapq.heappop(s['open'])
            if u in s['closed']:
                continue
            s['closed'].add(u)
            if u == self.goal:
                path = [u]
                while u in s['parent']:
                    u = s['parent'][u]
                    path.append(u)
                self.path = list(reversed(path))
                self.search = None
                return
            count += 1
            ui, uj = u % w.nx, u // w.nx
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                i, j = ui + di, uj + dj
                if not (0 <= i < w.nx and 0 <= j < w.ny):
                    continue
                v = j * w.nx + i
                if v in s['closed'] or w.clearance[v] < self.radius + .6:
                    continue
                diagonal = bool(di and dj)
                if diagonal and (w.clearance[uj * w.nx + i] < self.radius + .6 or w.clearance[j * w.nx + ui] < self.radius + .6):
                    continue
                length = w.CELL * (1.41421356 if diagonal else 1)
                if w.ground[v] - w.ground[u] > length * w.slope:
                    continue
                # A little clearance preference keeps large mechs off corners.
                g = s['g'][u] + length * (1 + .7 / max(1, w.clearance[v] - self.radius))
                if g >= s['g'].get(v, float('inf')):
                    continue
                s['g'][v], s['parent'][v] = g, u
                x, y = w.xy(v)
                h = math.hypot(x - gx, y - gy)
                heapq.heappush(s['open'], (g + h * 1.12, v))
        if not s['open']:
            self.search = None
