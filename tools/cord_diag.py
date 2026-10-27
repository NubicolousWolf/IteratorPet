"""Number dump for the umbilical cord: is it long enough, and are the nodes bunched?

Run: PYTHONPATH=src python tools/cord_diag.py [screen_w screen_h zoom]
"""
from __future__ import annotations

import sys
from math import hypot

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

SW, SH, ZOOM = 1920.0, 1080.0, 2.2
if len(sys.argv) >= 3:
    SW, SH = float(sys.argv[1]), float(sys.argv[2])
if len(sys.argv) >= 4:
    ZOOM = float(sys.argv[3])

bounds = (0.0, 0.0, SW / ZOOM, SH / ZOOM)


def pct(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def report(sim: SimulationState, label: str) -> None:
    cord = sim.cord
    n = cord.nodes
    knot = cord.stuck_index
    links = [C.dist(n[i], n[i + 1]) for i in range(len(n) - 1)]
    before = links[:knot - 1]
    after = links[knot + 1:]
    path_len = sum(links)
    print(f"\n=== {label} ===")
    print(f"bounds            {tuple(round(v, 1) for v in bounds)}")
    print(f"socket            ({cord.socket[0]:.1f}, {cord.socket[1]:.1f})")
    print(f"nodes             {len(n)}   rest {cord.rest:.1f}   rope {(len(n)-1)*cord.rest:.1f}")
    print(f"hook node #{knot}   ({n[knot][0]:.1f}, {n[knot][1]:.1f})")
    print(f"tail node #{len(n)-1}  ({n[-1][0]:.1f}, {n[-1][1]:.1f})")
    print(f"body0             ({sim.creature.chunk0[0]:.1f}, {sim.creature.chunk0[1]:.1f})")
    print(f"head              ({sim.creature.head[0]:.1f}, {sim.creature.head[1]:.1f})")
    print(f"socket->hook      {C.dist(cord.socket, n[knot]):.1f}")
    print(f"socket->body0     {C.dist(cord.socket, sim.creature.chunk0):.1f}")
    print(f"hook->body0       {C.dist(n[knot], sim.creature.chunk0):.1f}")
    print(f"tail->body0       {C.dist(n[-1], sim.creature.chunk0):.1f}")
    print(f"unrolled path     {path_len:.1f}  (straight-line socket->tail "
          f"{C.dist(n[0], n[-1]):.1f})  detour x{path_len / max(1e-6, C.dist(n[0], n[-1])):.2f}")
    print(f"link len  before hook: min {min(before):.2f} p10 {pct(before,0.1):.2f} "
          f"med {pct(before,0.5):.2f} p90 {pct(before,0.9):.2f} max {max(before):.2f}")
    print(f"link len  after  hook: min {min(after):.2f} p10 {pct(after,0.1):.2f} "
          f"med {pct(after,0.5):.2f} p90 {pct(after,0.9):.2f} max {max(after):.2f}")
    bunched = sum(1 for v in links if v < 4.0)
    collapsed = sum(1 for v in links if v < 1.0)
    print(f"bunched (<4 px)   {bunched}/{len(links)} links   collapsed (<1 px) {collapsed}")
    print(f"stretch all/free  {cord.stretch()[0]:.3f} / {cord.stretch()[1]:.3f}")
    # How much of the rope hangs below the hook, and does it fold over itself?
    tail_rope = (len(n) - 1 - (knot + 1)) * cord.rest
    print(f"rope after hook   {tail_rope:.1f} px in a {C.dist(n[knot], n[-1]):.1f} px gap "
          f"-> slack x{tail_rope / max(1e-6, C.dist(n[knot], n[-1])):.2f}")
    # self-overlap: count node pairs (non-adjacent) closer than 6 px
    close = 0
    for i in range(0, len(n), 2):
        for j in range(i + 12, len(n), 2):
            if C.dist(n[i], n[j]) < 6.0:
                close += 1
    print(f"overlapping pairs {close}  (stride 2, >6 apart in index)")


sim = SimulationState(bounds)
report(sim, "t=0 (after 120 warm-up ticks)")
for frame in range(1, 5):
    for _ in range(100):
        sim._advance()
    report(sim, f"t={frame * 100} ticks")
