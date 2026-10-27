"""Where does the puppet actually spend its time? (the "stuck bottom-right" report)

Run: PYTHONPATH=src python tools/roam_report.py [minutes]
"""
from __future__ import annotations

import sys
from collections import Counter
from math import hypot

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
SW, SH, ZOOM = 1707.0, 912.0, 2.2
bounds = (0.0, 0.0, SW / ZOOM, SH / ZOOM)
x0, y0, w, h = bounds

sim = SimulationState(bounds)
ticks = int(MINUTES * 60 * C.GAME_HZ)

xs, ys, stations = [], [], []
dests = []
moving = 0
for tick in range(ticks):
    before = sim.frame_pos
    sim._advance()
    if abs(sim.frame_pos - before) > 1e-9:
        moving += 1
    if tick % 4 == 0:
        cx, cy = sim.creature.chunk0
        xs.append(cx)
        ys.append(cy)
        stations.append(sim.frame_pos)
        dests.append(sim.wander.next)

n = len(xs)


def frac(pred) -> float:
    return sum(1 for v in zip(xs, ys) if pred(*v)) / n


print(f"bounds                ({x0:.0f},{y0:.0f}) {w:.0f}x{h:.0f}   span {hypot(w, h):.0f}")
print(f"simulated             {MINUTES:.0f} min = {ticks} ticks")
print(f"puppet x              mean {sum(xs)/n:.0f}   min {min(xs):.0f}   max {max(xs):.0f}")
print(f"puppet y              mean {sum(ys)/n:.0f}   min {min(ys):.0f}   max {max(ys):.0f}")
print(f"explored x range      {max(xs)-min(xs):.0f} px of {w:.0f}  ({100*(max(xs)-min(xs))/w:.0f} %)")
print(f"explored y range      {max(ys)-min(ys):.0f} px of {h:.0f}  ({100*(max(ys)-min(ys))/h:.0f} %)")

print("\ntime in each screen quadrant (x>mid, y>mid):")
grid = Counter()
for cx, cy in zip(xs, ys):
    grid[(cx > x0 + w / 2, cy > y0 + h / 2)] += 1
for left in (True, False):
    for bottom in (True, False):
        key = (left, bottom)
        tag = ("right" if left else "left ") + "/" + ("bottom" if bottom else "top   ")
        print(f"  {tag}  {100*grid[key]/n:5.1f} %  {grid[key]:5d}")

print(f"\nbase moved on          {100*moving/ticks:4.1f} % of ticks")
print(f"base frame_pos         min {min(stations):.2f}  max {max(stations):.2f}  "
      f"unique-ish {len(set(round(f, 2) for f in stations))}")
hist = Counter(int(f) for f in stations)
edges = ("top   ", "right ", "bottom", "left  ")
for q in range(4):
    print(f"  station on {edges[q]} edge  {100*hist.get(q,0)/n:5.1f} %")

# carriage vs puppet, and the reach budget
print()
print(f"carriage start / stop  {sim.start_threshold:.0f} / {sim.stop_threshold:.0f} px"
      f"   (ratios {C.BASE_MOVE_START_RATIO} / {C.BASE_MOVE_STOP_RATIO})")
print(f"carriage speed         {C.BASE_MOVE_SPEED_DEG} deg/tick   stable gate {C.BASE_STABLE_TICKS} ticks")
print(f"must-follow override   {sum(C.ARM_LENGTHS)*C.BASE_FOLLOW_ARM_RATIO:.0f} px from the carriage")
print(f"destination clamp      {C.ARM_LENGTHS_TOTAL*C.DESTINATION_REACH_RATIO:.0f} px from the carriage"
      f"   (arm {C.ARM_LENGTHS_TOTAL:.0f})")
print(f"dwell / hold cap       {C.WANDER_DWELL_TICKS} / {C.WANDER_HOLD_CAP} ticks")
print(f"destination spread     x {min(d[0] for d in dests):.0f}..{max(d[0] for d in dests):.0f}"
      f"  y {min(d[1] for d in dests):.0f}..{max(d[1] for d in dests):.0f}")
