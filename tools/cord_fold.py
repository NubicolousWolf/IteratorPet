"""Where does the cord fold on itself? (the "nodes bunched" complaint)

Run: PYTHONPATH=src python tools/cord_fold.py
"""
from __future__ import annotations

from math import acos, degrees

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

bounds = (0.0, 0.0, 1707.0 / 2.2, 912.0 / 2.2)
sim = SimulationState(bounds)
for _ in range(1400):          # 35 s: past the leash settling
    sim._advance()

n = sim.cord.nodes
knot = sim.cord.stuck_index
print(f"nodes {len(n)}  hook #{knot}  rest {sim.cord.rest}")

turns = []
for i in range(1, len(n) - 1):
    a = C.dir_vec(n[i], n[i - 1])
    b = C.dir_vec(n[i], n[i + 1])
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))
    turns.append(degrees(acos(dot)))

print("\nturn angle per node (deg): index:angle")
print("  before hook:", " ".join(f"{i}:{t:.0f}" for i, t in enumerate(turns[:knot])))
print("  after  hook:", " ".join(f"{i}:{t:.0f}" for i, t in enumerate(turns) if i >= knot))

sharp = [i for i, t in enumerate(turns) if t > 100.0]
print(f"\nsharp folds (>100 deg): {sharp}")

# Visual overlap: two parts of the cable closer than the plate width.
pairs = [(i, j) for i in range(len(n)) for j in range(i + 4, len(n))
         if C.dist(n[i], n[j]) < 11.0]
print(f"node pairs < 11 px with >4 links between: {len(pairs)}")
if pairs:
    print("  first 12:", pairs[:12])

# How much rope sits in the cramped tail region?
print(f"\ncord tail {tuple(round(v, 1) for v in n[-1])}  body0 {tuple(round(v,1) for v in sim.creature.chunk0)}")
print(f"tail->body0 {C.dist(n[-1], sim.creature.chunk0):.1f}  head {tuple(round(v,1) for v in sim.creature.head)}")
print(f"gap tail->head {C.dist(n[-1], sim.creature.head):.1f}")
print(f"strand lengths {[round(v) for v in sim.cord.filament_len]}")
print(f"tail region: 19 links = 190 px of rope inside "
      f"{C.dist(n[knot], n[-1]):.1f} px (hook -> tail)")
