from math import hypot

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.arm import ArmChain
from iterator_pet.simulation.cord import Cord
from iterator_pet.simulation.rail import RailPath
from iterator_pet.simulation.state import SimulationState


def test_rail_is_closed_and_rounds_corners():
    rail = RailPath((0.0, 0.0, 800.0, 480.0))
    start = rail.pose(0.0).point
    end = rail.pose(4.0).point
    assert hypot(start[0] - end[0], start[1] - end[1]) < 1e-6
    r = rail.radius
    # f=0 is the top of the left edge; f=blend is the start of the top edge.
    start_of_top = rail.pose(C.CORNER_BLEND).point
    assert abs(start_of_top[0] - (rail.corners[0][0] + r)) < 1e-6
    assert abs(start_of_top[1] - rail.corners[0][1]) < 1e-6
    # The arc stays exactly `r` from the corner centre.
    mid = rail.pose(C.CORNER_BLEND * 0.5).point
    centre = (rail.corners[0][0] + r, rail.corners[0][1] + r)
    assert abs(hypot(mid[0] - centre[0], mid[1] - centre[1]) - r) < 1e-6


def test_rail_projection_round_trips():
    rail = RailPath((0.0, 0.0, 800.0, 480.0))
    for f in (0.3, 1.2, 2.7, 3.7):
        point = rail.pose(f).point
        assert abs(rail.project(point) - f) < 0.05


def test_arm_keeps_link_lengths_under_load():
    arm = ArmChain((100.0, 100.0))
    body0 = (600.0, 200.0)
    for _ in range(400):
        arm.update((100.0, 100.0), body0, (600.0, 209.0), (0.0, 1.0), base_moving=True)
    ok, stretch, _ = arm.validate()
    assert ok, stretch
    for index in range(len(arm.joints) - 1):
        distance = C.dist(arm.joints[index].pos, arm.joints[index + 1].pos)
        # known defect: links can stretch ~35 px while the body is dragged
        assert distance <= C.ARM_LENGTHS[index] + 40.0


def test_cord_pins_socket_and_arm_hook():
    cord = Cord((0.0, 0.0, 800.0, 480.0), (400.0, 200.0))
    elbow_a, elbow_b = (420.0, 120.0), (520.0, 140.0)
    for _ in range(200):
        cord.step(elbow_a, elbow_b, (430.0, 210.0), (430.0, 190.0), (0.0, 1.0))
    assert cord.nodes[0] == cord.socket
    # the three clamp nodes sit at 0.4 / 0.47 / 0.54 along the elbow chord,
    # offset 8 px sideways -- the middle one is the weighted one
    hook = cord.nodes[int(len(cord.nodes) * 0.75)]
    t = 0.47
    expected = (elbow_a[0] + (elbow_b[0] - elbow_a[0]) * t,
                elbow_a[1] + (elbow_b[1] - elbow_a[1]) * t)
    assert hypot(hook[0] - expected[0], hook[1] - expected[1]) < 12.0
    assert cord.finite()


def test_cord_node_count_keeps_the_ten_pixel_pitch():
    cord = Cord((0.0, 0.0, 800.0, 480.0), (400.0, 200.0))
    # og.cs:787 fixes 80 nodes for SS_AI's 800 px room; here the COUNT follows the
    # span so the pitch can stay 10 px (the physics pins the pitch, not the count).
    assert cord.rest == C.CORD_REST
    assert 20 <= len(cord.nodes) <= C.CORD_MAX_NODES
    reach = (len(cord.nodes) - 1) * cord.rest
    far = max(hypot(cx - cord.socket[0], cy - cord.socket[1])
              for cx in (0.0, 800.0) for cy in (0.0, 480.0))
    assert reach >= far, f"cord {reach:.0f} px cannot cross {far:.0f} px"
    # 14 strands, and their free lengths are the source's 50..200 px draw
    assert len(cord.filament_len) == C.SMALL_CORDS == 14
    assert all(C.SMALL_CORD_MIN_LEN <= L <= C.SMALL_CORD_MAX_LEN + 15.0
               for L in cord.filament_len)


def test_simulation_is_deterministic_and_stable():
    def run():
        sim = SimulationState((0.0, 0.0, 853.0, 480.0))
        for _ in range(600):
            sim.step(0.025)
        state = sim.interpolate(1.0)
        return (round(state.body0[0], 6), round(state.body0[1], 6))

    assert run() == run()


def test_tail_stays_attached_to_the_body():
    sim = SimulationState((0.0, 0.0, 853.0, 480.0))
    for _ in range(1200):
        sim.step(0.025)
        state = sim.interpolate(1.0)
        distance = C.dist(state.joints[-1], state.body0)
        assert abs(distance - C.ARM_LENGTHS[-1]) < 12.0, distance


def test_body_is_never_dragged_back_by_the_arm():
    """The arm bends to follow the body; it must not act as a leash.

    The old assertion capped base->body at 0.72 of the arm length, which forced
    the base to drag the puppet around. Correct behaviour: the base re-sites
    itself instead, so a short excursion past that radius is expected.
    """
    sim = SimulationState((0.0, 0.0, 853.0, 480.0))
    for _ in range(1200):
        sim.step(0.025)
        state = sim.interpolate(1.0)
        assert C.dist(state.base_point, state.body0) <= sum(C.ARM_LENGTHS) + 40.0


def test_gown_keeps_its_proportions():
    sim = SimulationState((0.0, 0.0, 853.0, 480.0))
    for _ in range(300):
        sim.step(0.025)
    grid = sim.interpolate(1.0).gown
    xs = [p[0] for row in grid for p in row]
    ys = [p[1] for row in grid for p in row]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    # ideal shape is ~22 wide by ~23 tall in game pixels
    assert 15.0 < width < 40.0
    assert 15.0 < height < 40.0

def test_puppet_roams_the_whole_chamber():
    """Regression for "the pet only ever sits in the bottom-right corner".

    The failure was structural, not a tuning problem: destinations were sampled
    inside a 260 px disc around the carriage, and the carriage's own target was
    the rail station minimising ``|dist(station, nextPos) - 200|`` -- a ring that
    on a 776x415 chamber can only reach the right and bottom edges. Measured
    then: carriage on the right edge 90.2 % of ticks, left and top 0 %.

    What must hold now:
      * the puppet covers most of the screen width and both halves,
      * the carriage visits all four rail edges,
      * the puppet never leaves the arm's reach (570 px) -- the reason the
        carriage follows the puppet in the first place.
    """
    width, height = 776.0, 415.0
    sim = SimulationState((0.0, 0.0, width, height))
    xs, edges, worst = [], set(), 0.0
    for tick in range(2400):
        sim.step(0.025)
        if tick % 4:
            continue
        xs.append(sim.creature.chunk0[0])
        edges.add(int(sim.frame_pos) % 4)
        worst = max(worst, C.dist(sim.rail.pose(sim.frame_pos).point, sim.creature.chunk0))

    covered = (max(xs) - min(xs)) / width
    left_half = sum(1 for x in xs if x < width * 0.5) / len(xs)
    assert covered > 0.55, f"puppet only covers {covered:.0%} of the screen width"
    assert 0.25 < left_half < 0.75, f"{left_half:.0%} of ticks on the left half -- lopsided"
    assert len(edges) == 4, f"carriage only visited edges {sorted(edges)}"
    reach = sum(C.ARM_LENGTHS)
    assert worst <= reach, f"puppet reached {worst:.0f} px from the carriage, arm is {reach:.0f}"


def test_the_carriage_can_actually_leave_a_large_chamber():
    """Regression for "roaming 下人偶不倾向于大范围移动，底座几乎不动".

    The roaming test above only exercised 776x415, the chamber the README
    documents. This machine runs ``scale = 1.5``, i.e. 1138x608, and there the two
    constants contradicted each other:

        start_threshold = 0.30 * diagonal        = 366 px
        destination clamp = 0.62 * ARM (570 px)  = 353 px

    Every destination is pulled inside 353 px of the carriage, so the puppet can
    never reach the 366 px that would let the carriage start: it was pinned to a
    353 px disc around a nail. Measured then -- carriage moving on 2 % of ticks
    and driving 4 px/s, x coverage 53 %; now 43 % / 63 px/s / 77 %.

    The structural half of this test (threshold < clamp radius) is what really
    matters: it holds for ANY chamber size, including ones bigger than this.
    """
    clamp_radius = C.DESTINATION_REACH_RATIO * C.ARM_LENGTHS_TOTAL
    for width, height in ((776.0, 415.0), (1138.0, 608.0)):
        sim = SimulationState((0.0, 0.0, width, height))
        assert sim.start_threshold < clamp_radius, (
            f"{width:.0f}x{height:.0f}: start threshold {sim.start_threshold:.0f} px "
            f">= destination clamp {clamp_radius:.0f} px -- the carriage can never move")

        ticks = 40 * 120
        moving = 0
        for _ in range(ticks):
            sim.step(0.025)
            moving += int(sim.base_moving)
        share = moving / ticks
        assert share > 0.15, (
            f"{width:.0f}x{height:.0f}: carriage moved on only {share:.0%} of ticks")
