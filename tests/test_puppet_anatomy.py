"""Locks down the numbers that the puppet's legs and robe are built from.

Each assertion cites the decompiled line it protects. The suite used to have no
coverage here at all, which is how a 20 px tether borrowed from Looks to the Moon
and a 5 px knee from the same branch survived two rounds of "looks fine to me".
"""

from math import atan2, degrees, hypot

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.ornament import Gown
from iterator_pet.simulation.state import SimulationState


def _lateral(point, origin, perp):
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    return dx * perp[0] + dy * perp[1]


def test_foot_sits_exactly_on_the_ten_pixel_tether():
    """og.cs:1643  ConnectToPoint(bodyChunks[1].pos, IsMoon ? 20f : 10f, ...).

    Five Pebbles is not Moon, so 10. The foot is pulled out along the net force
    (0.447, -0.894), which puts it 4.47 px outboard and 8.94 px down.

    ``push`` is false, so ``BodyPart.ConnectToPoint`` only guards against the
    leash *stretching*: it corrects when ``|pos - anchor| > rad`` and never
    pushes back out. A fast body move can therefore compress the leash for a
    frame, which is why the assertion is "never longer than 10" plus "on the
    leash nearly all the time" instead of an exact equality.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    lateral = []          # every tick: the leash invariants hold everywhere
    lateral_parked = []   # stationary ticks only: the 4.47 px decomposition
    distances = []
    previous = sim.creature.chunk0
    for _ in range(600):
        sim.step(0.025)
        creature = sim.creature
        speed = C.dist(previous, creature.chunk0)
        previous = creature.chunk0
        down = C.dir_vec(creature.chunk0, creature.chunk1)
        perp = (down[1], -down[0])
        for index, foot in enumerate(creature.feet):
            sign = -1.0 if index == 0 else 1.0
            distance = hypot(foot[0] - creature.chunk1[0], foot[1] - creature.chunk1[1])
            assert distance <= C.FOOT_TETHER + 1e-9, "the leash stretched past 10 px"
            distances.append(distance)
            offset = _lateral(foot, creature.chunk1, perp) * sign
            lateral.append(offset)
            if speed < 1.0:
                lateral_parked.append(offset)

    on_leash = sum(1 for value in distances if value >= C.FOOT_TETHER - 0.01)
    assert on_leash > 0.95 * len(distances), f"only {on_leash}/{len(distances)} ticks at the leash"

    # The foot must hang on its own side of the body axis. It may briefly swing
    # across while the trunk rotates, but it must not sit on the mirror side.
    wrong_side = sum(1 for value in lateral if value < 0.0)
    assert wrong_side < 0.10 * len(lateral), f"{wrong_side}/{len(lateral)} ticks crossed"
    # The 4.47 px figure is the net force of a body at rest (0.447, -0.894).
    # A travelling body's net force points along its motion, so pooling every
    # tick drags this mean toward zero; judge the decomposition on parked ticks.
    assert len(lateral_parked) > 50, f"only {len(lateral_parked)} parked ticks"
    mean = sum(lateral_parked) / len(lateral_parked)
    assert 3.0 < mean < 5.5, f"parked mean lateral offset {mean:.2f}, expected ~4.47"


def test_body_axis_hangs_down_instead_of_laying_flat():
    """oracle.cs:294 pulls ``bodyChunks[1]`` toward ``getTo - GetToDir * 9``.

    ``SSOracleBehavior.GetToDir`` (ss.cs:3137-3152) returns a rotating
    ``DegToVec(investigateAngle)`` only while Idle/Investigating; every other
    state returns ``new Vector2(0f, 1f)``, straight up in the game's +y-up
    frame. This port uses that default branch, so the 9 px connection must stay
    vertical (down in the +y-down sim frame) instead of following the cursor or
    spinning with the roam heading.
    """
    assert C.BODY_HANG_DIR == (0.0, -1.0), "the game's (0, 1) up-frame vector mirrored"
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    parked, drifting = [], []
    previous = sim.creature.chunk0
    for _ in range(1200):
        sim.step(0.025)
        creature = sim.creature
        down = C.dir_vec(creature.chunk0, creature.chunk1)
        tilt = abs(degrees(atan2(down[0], down[1])))
        speed = C.dist(previous, creature.chunk0)
        previous = creature.chunk0
        # Split the sample: the invariant is "hangs straight down when it is not
        # travelling". ``oracle.cs:291-294`` gives chunk 0 a 6.2 impulse and
        # chunk 1 only 3.2, so a body that is being driven ALWAYS leans into its
        # motion -- that is the game's look, not a defect. Measuring one pooled
        # median conflated the two and made a roaming pet look broken.
        (parked if speed < 1.0 else drifting).append(tilt)

    def median(values):
        values = sorted(values)
        return values[len(values) // 2]

    assert len(parked) > 100, f"only {len(parked)} parked ticks to judge"
    # 15 deg, not 10: the trunk is 9 px long, so 15 deg is 2.3 px of lateral
    # offset -- visually upright. Below that the body is still breathing and
    # micro-correcting, and a 10 deg bound turns the assertion into a coin flip.
    assert median(parked) < 15.0, f"parked body tilt {median(parked):.1f} deg off vertical"
    # While travelling the trunk TRAILS its motion: chunk 1's hang term is 9 px
    # against a goal hundreds of px away, so the 100 px clamp swallows it and the
    # axis follows the velocity (measured 70-78 deg). That is what the source's
    # own expression does (oracle.cs:291-294) and it is why the dwell matters:
    # the pet must spend most of its life parked, upright, looking around.
    assert median(drifting) < 85.0, f"drifting body tilt {median(drifting):.1f} deg"
    assert len(parked) > len(drifting), (
        f"only {100 * len(parked) / (len(parked) + len(drifting)):.0f} % of ticks parked;"
        " a trailing trunk must not be the dominant pose")
    # and it must never *stay* flat, which was the original regression (median 49)
    assert median(parked + drifting) < 30.0


def test_head_sits_on_top_of_the_body_and_leans_toward_the_gaze():
    """og.cs:1621-1632, in the source's order.

    ``head.Update()`` (pos += vel; vel *= 0.995) -> ``ConnectToPoint(anchor, 8,
    push: true, ..., 0.5, 0.01)`` -> *then* ``vel += up*breathFac`` and
    ``vel += lookDir*0.5*breathFac``. The tether only decides the DISTANCE; the
    guidance impulse added after it decides WHERE on the sphere the head sits.
    Adding the guidance before the tether (and multiplying the whole velocity by
    0.5) is what left the head free to point anywhere, including backwards.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    angles = []
    radii = []
    for _ in range(900):
        sim.step(0.025)
        c = sim.creature
        up = C.dir_vec(c.chunk1, c.chunk0)
        anchor = (c.chunk0[0] + up[0] * C.HEAD_ANCHOR,
                  c.chunk0[1] + up[1] * C.HEAD_ANCHOR)
        hx, hy = c.head[0] - anchor[0], c.head[1] - anchor[1]
        radii.append(hypot(hx, hy))
        off = abs((degrees(atan2(hy, hx)) - degrees(atan2(up[1], up[0])) + 180.0) % 360.0 - 180.0)
        angles.append(off)

    assert max(abs(r - C.HEAD_TETHER) for r in radii) < 1e-9, "the weld must be exact"
    ordered = sorted(angles)
    median = ordered[len(ordered) // 2]
    near = sum(1 for a in angles if a < 45.0) / len(angles)
    assert median < 10.0, f"head sits {median:.1f} deg off the body axis"
    assert near > 0.95, f"only {near:.0%} of ticks within 45 deg of 'on top'"

    # Guidance steady state is v = 2*(up + 0.5*look), so with the gaze parked
    # perpendicular to the body the head must lean ~26 deg toward it and stay
    # there. Without the fix the angle was unbounded (up to 180).
    #
    # Measured over PARKED ticks only: a roaming puppet turns its whole body
    # toward the cursor, so on the final frame the body axis already points at
    # the gaze and the *relative* lean collapses to a few degrees. The lean is a
    # property of the head's steady state, not of a travelling body.
    leans = []
    previous = sim.creature.chunk0
    for _ in range(600):
        c = sim.creature
        sim.cursor = (c.chunk0[0] + 400.0, c.chunk0[1])
        sim.step(0.025)
        c = sim.creature
        speed = C.dist(previous, c.chunk0)
        previous = c.chunk0
        if speed >= 1.0:
            continue
        up = C.dir_vec(c.chunk1, c.chunk0)
        anchor = (c.chunk0[0] + up[0] * C.HEAD_ANCHOR, c.chunk0[1] + up[1] * C.HEAD_ANCHOR)
        leans.append(abs((degrees(atan2(c.head[1] - anchor[1], c.head[0] - anchor[0]))
                          - degrees(atan2(up[1], up[0])) + 180.0) % 360.0 - 180.0))
    assert len(leans) > 50, f"only {len(leans)} parked ticks to judge"
    leans.sort()
    lean = leans[len(leans) // 2]
    assert 10.0 < lean < 45.0, f"head leans {lean:.1f} deg toward a perpendicular gaze"


def test_face_look_matches_the_source_for_the_four_cardinals():
    """``RelativeLookDir`` = og.cs:1486, transcribed through the real helpers.

    ``Custom.VecToDeg`` is ``Atan2(v.x, v.y)`` -- a COMPASS angle, because the IL
    loads x first while Unity's ``Atan2`` takes ``(y, x)``. Reading it as
    ``atan2(y, x)`` turned this port's whole frame inside out; the tell is that a
    hanging body would then rotate the look by a quarter turn, and the eyes would
    stop moving sideways. These numbers are what the source produces for a
    hanging body with the eyes open, so they pin the convention down.
    """
    from iterator_pet.rendering.renderer import eye_offset, face_look

    axis = (0.0, 1.0)                       # head -> body, pointing down on screen
    perp = (axis[1], -axis[0])
    assert perp == (1.0, -0.0), "sideways must be +x for a hanging body"

    def eyes(look):
        rel = face_look(look, axis)
        return rel, [eye_offset(rel, perp, axis, i)[0] for i in (0, 1)]

    # cursor right -> pair shifts right (the +-5 clamp makes the centroid 2.75)
    rel, offs = eyes((1.0, 0.0))
    assert abs(rel[0] - 1.0) < 1e-9 and abs(rel[1]) < 1e-9
    assert min(o[0] for o in offs) > 0.0, "an eye moved away from the cursor"
    assert abs(sum(o[0] for o in offs) / 2.0 - 2.75) < 1e-9
    assert all(abs(o[1] - 1.0) < 1e-9 for o in offs)

    # cursor left -> mirrored
    rel, offs = eyes((-1.0, 0.0))
    assert abs(rel[0] + 1.0) < 1e-9
    assert max(o[0] for o in offs) < 0.0

    # cursor UP -> the along-body term must move the eyes UP by 2 px, not down.
    # forward = 1 - 3*1 = -2 along vector6 (= head -> body = down on screen).
    rel, offs = eyes((0.0, -1.0))
    assert abs(rel[1] - 1.0) < 1e-9, f"along-body sign broken: rel={rel}"
    assert all(abs(o[1] + 2.0) < 1e-9 for o in offs), "eyes went the wrong way looking up"

    # cursor DOWN -> eyes 4 px down (the clamp is asymmetric: 1 - 3*(-1) = 4)
    rel, offs = eyes((0.0, 1.0))
    assert all(abs(o[1] - 4.0) < 1e-9 for o in offs)

    # body lying on its side: the same cursor must now move the eyes vertically
    axis = (-1.0, 0.0)
    perp = (axis[1], -axis[0])
    rel = face_look((0.0, 1.0), axis)
    assert abs(rel[0] - 1.0) < 1e-9, f"lateral broken on a rotated body: {rel}"


def test_gaze_has_no_distance_cutoff():
    """``SSOracleBehavior.lookPoint`` is not gated by distance.

    The port used to ignore the cursor beyond 380 px, which meant a mouse parked
    on the right half of a wide screen was never looked at.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    far = (sim.creature.chunk0[0] + 4000.0, sim.creature.chunk0[1] + 1200.0)
    sim.cursor = far
    sim.step(0.025)
    assert sim.wander.look_point == far, "a distant cursor must still be the gaze target"


def test_eyes_are_white_with_the_pebbles_geometry():
    """og.cs:1943-1949 + ``FSprite::.ctor`` IL_0001..0006.

    The eye sprite is a bare ``new FSprite("pixel")``. The near-black
    ``Color(0.02f, 0f, 0f)`` is assigned only for IsMoon / IsPastMoon /
    IsStraw, ``ApplyPalette`` never touches the eye sprites, and the FSprite
    constructor stores ``Futile::white`` into ``_color``. So Five Pebbles' eyes
    are pure white -- drawing them dark was the Moon branch.

    Geometry comes from og.cs:2168-2169: eyes open means ``scaleY = 2`` (the 3f
    is Moon) and ``scaleX = 1 + InverseLerp(...)``, i.e. 1..2 px on a 1x1 pixel
    sprite.
    """
    assert C.EYE_COLOUR == (255, 255, 255), "Pebbles' eye must stay on the white default"

    def luminance(rgb):
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

    # Visible against the pink face without being the Moon's near-black.
    assert luminance(C.EYE_COLOUR) > luminance(C.SKIN_COLOUR) + 100.0
    assert C.EYE_SCALE_Y == 2.0
    assert (C.EYE_WIDTH_MIN, C.EYE_WIDTH_MAX) == (1.0, 2.0)


def test_knee_bows_to_the_same_side_as_its_foot():
    """og.cs:1770  (the `!IsMoon` branch, i.e. Five Pebbles)

    knee = (feet[j].pos + bodyChunks[1].pos) / 2
           + PerpendicularVector(DirVec(firstChunk, bodyChunks[1])) * (4f * sign)

    Same base vector and the same sign as the foot's steering term at og.cs:1649,
    so the knee must land outboard of its own foot. The 5f at og.cs:1788 and the
    0.4 smoothing / 15 px clamp that follow it belong to Looks to the Moon.
    """
    assert C.KNEE_BOW == 4.0
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    for _ in range(300):
        sim.step(0.025)
    creature = sim.creature
    down = C.dir_vec(creature.chunk0, creature.chunk1)
    perp = (down[1], -down[0])
    for index, (foot, knee) in enumerate(zip(creature.feet, creature.knees)):
        sign = -1.0 if index == 0 else 1.0
        foot_lateral = _lateral(foot, creature.chunk1, perp) * sign
        knee_lateral = _lateral(knee, creature.chunk1, perp) * sign
        assert foot_lateral * knee_lateral > 0.0, "knee and foot are on opposite sides"
        # the knee is the chord's midpoint pushed out by four pixels
        mid = ((foot[0] + creature.chunk1[0]) * 0.5,
               (foot[1] + creature.chunk1[1]) * 0.5)
        assert abs(hypot(knee[0] - mid[0], knee[1] - mid[1]) - C.KNEE_BOW) < 1e-6


def test_head_is_welded_eight_pixels_from_its_anchor():
    """og.cs:1622  ConnectToPoint(chunk0 + DirVec(chunk1, chunk0) * 6f, 8f, push: true).

    ``push: true`` skips the distance test and forces |pos - pnt| == connectionRad
    every tick, so this is a weld, not a leash.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    for _ in range(300):
        sim.step(0.025)
        creature = sim.creature
        up = C.dir_vec(creature.chunk1, creature.chunk0)
        anchor = (creature.chunk0[0] + up[0] * C.HEAD_ANCHOR,
                  creature.chunk0[1] + up[1] * C.HEAD_ANCHOR)
        distance = hypot(creature.head[0] - anchor[0], creature.head[1] - anchor[1])
        assert abs(distance - C.HEAD_TETHER) < 1e-6
    # The head body part's radius is 5, which is what the head sprite scales from.
    assert C.HEAD_ANCHOR == 6.0 and C.HEAD_TETHER == 8.0


def test_robe_matches_ideal_pos_for_point():
    """og.cs:332-338  Gown.IdealPosForPoint, a = 5f, num2 = 11f.

    A fresh gown is seeded on the ideal grid, so this compares the port against
    the formula written out longhand rather than against itself.
    """
    body = (0.0, 0.0)
    direction = (0.0, -1.0)          # DirVec(chunk1, chunk0) with +y down
    divs = C.GOWN_DIVS
    gown = Gown(body, direction)
    perp = (-direction[1], direction[0])
    for x in (0, 5, 10):
        for y in (0, 5, 10):
            num = x / (divs - 1)
            t = y / (divs - 1)
            lateral = (-1.0 + 2.0 * num) * (5.0 + (11.0 - 5.0) * t)
            drop = (5.0 + (-18.0 - 5.0) * t) * (
                1.0 + __import__("math").sin(__import__("math").pi * num)
                * 0.35 * (-1.0 + 2.0 * t))
            want = (body[0] + perp[0] * lateral + direction[0] * drop,
                    body[1] + perp[1] * lateral + direction[1] * drop)
            got = gown.pos[x][y]
            assert abs(got[0] - want[0]) < 1e-6
            assert abs(got[1] - want[1]) < 1e-6
    # hem half width 11 -> 22 px, and the middle hangs 6.3 px lower than the seams
    hem_seam = gown.pos[0][divs - 1]
    hem_mid = gown.pos[divs // 2][divs - 1]
    assert abs(hem_mid[1] - hem_seam[1] - 6.3) < 0.05


def test_foot_is_inside_the_robe_at_rest():
    """The arithmetic behind tools/leg_visibility.py, pinned so it cannot drift.

    With the 10 px tether the foot lands 17.94 px below the upper chunk while the
    robe's hem at that x sits 23.10 px below it, so the leg is covered until the
    cloth spends part of its Lerp(0, 9, t) tolerance going *up*.
    """
    divs = C.GOWN_DIVS
    gown = Gown((0.0, 0.0), (0.0, -1.0))
    outline = [gown.pos[i][0] for i in range(divs)]
    outline += [gown.pos[divs - 1][j] for j in range(1, divs)]
    outline += [gown.pos[i][divs - 1] for i in range(divs - 2, -1, -1)]
    outline += [gown.pos[0][j] for j in range(divs - 2, 0, -1)]

    def inside(poly, point):
        x, y = point
        hit = False
        for i in range(len(poly)):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % len(poly)]
            if (y0 > y) != (y1 > y):
                if x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                    hit = not hit
        return hit

    # The sim runs with +y DOWN: chunk1 sits 9 px below chunk0 and the foot hangs
    # 8.94 px further down, i.e. y = +17.94.
    for sign in (-1.0, 1.0):
        foot = (sign * 0.447 * C.FOOT_TETHER,
                C.BODY_CONNECTION + 0.894 * C.FOOT_TETHER)
        assert inside(outline, foot), "foot escaped the robe; the tether or robe is wrong"
    # a 5.15 px lift is enough to clear it, well inside the 9 px tolerance
    lifted = [(x, y - 5.2) for x, y in outline]
    foot = (0.447 * C.FOOT_TETHER, C.BODY_CONNECTION + 0.894 * C.FOOT_TETHER)
    assert not inside(lifted, foot)
    assert C.FOOT_TETHER == 10.0 and C.HAND_TETHER == 15.0


def test_cord_is_long_enough_and_keeps_its_ten_pixel_pitch():
    """og.cs:787/838  ``coord = new Vector2[80, 3]`` and every sweep solves for a
    fixed 10f. On a variable desktop the node COUNT has to follow the span while
    the pitch stays 10 -- at 80 nodes the cord only reached 790 px of a 1335 px
    diagonal, and shrinking the pitch to 6..10 px made it physically shorter than
    the distance it had to cross.
    """
    bounds = (0.0, 0.0, 1163.0, 654.0)     # a 2560x1440 screen at scale 2.2
    sim = SimulationState(bounds)
    cord = sim.cord
    reach = (len(cord.nodes) - 1) * cord.rest
    far = max(hypot(cx - cord.socket[0], cy - cord.socket[1])
              for cx in (bounds[0], bounds[0] + bounds[2])
              for cy in (bounds[1], bounds[1] + bounds[3]))
    assert reach >= far, f"cord reaches {reach:.0f} px but the corner is {far:.0f} px away"
    assert cord.rest == C.CORD_REST, "the pitch must stay at 10 px"

    links = []
    for _ in range(400):
        sim.step(0.025)
        c = sim.cord
        for i in range(len(c.nodes) - 1):
            if i in (0, c.stuck_index - 1, c.stuck_index, c.stuck_index + 1):
                continue
            links.append(abs(C.dist(c.nodes[i], c.nodes[i + 1]) - C.CORD_REST) / C.CORD_REST)
    links.sort()
    median = links[len(links) // 2]
    p99 = links[int(len(links) * 0.99)]
    assert median < 0.05, f"median link stretch {median:.3f}"
    assert p99 < 0.12, f"p99 link stretch {p99:.3f}"
    # The PINNED links are where the solver fights hardest and must not be hidden:
    # stretch() reports (all, free) precisely so a healthy-looking free number
    # cannot mask the socket link. ~15 % is this parameter set's floor.
    all_links, free_links = sim.cord.stretch()
    assert all_links < 0.22, f"worst link (incl. pinned) {all_links:.3f}"
    assert free_links < 0.14, f"worst free link {free_links:.3f}"


def test_filament_strands_are_the_source_node_simulation():
    """``og.cs:873-913``: 14 strands x 20 SIMULATED nodes, both ends welded.

    This used to be an analytic sine bow spanning the tail -> head gap. That is
    not what the source does: each strand owns 50..200 px of free rope (split
    over 20 links, ``num6 = smallCordsLengths[n] / 20``), it is integrated with
    inertia, and only then swept back onto the two anchors. A slack strand --
    free length greater than the gap -- therefore has to BOW, and its drawn path
    is as long as its rope, not as long as the gap. That difference is the whole
    complaint: the bow drew the gap (~90 px) where the source gives 200 px.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    for _ in range(600):
        sim.step(0.025)
    strands = sim._filaments()
    assert len(strands) == C.SMALL_CORDS
    tail, head = sim.cord.nodes[-1], sim.creature.head
    gap = C.dist(tail, head)
    assert gap < C.CORD_TAIL_LIMIT + C.HEAD_TETHER + 20.0, f"gap {gap:.1f} px"
    for index, strand in enumerate(strands):
        assert len(strand) == C.SMALL_CORD_NODES
        assert C.dist(strand[0], tail) < 1e-9, "strand root left the cord tail"
        assert C.dist(strand[-1], head) < 1e-9, "strand tip left the head"
        drawn = sum(C.dist(strand[k], strand[k + 1]) for k in range(len(strand) - 1))
        free = sim.cord.filament_len[index]
        # The welded ends over-constrain a strand shorter than the gap (the
        # source's two sweeps stretch it too, it has the same 1+1 passes), so the
        # middle link carries the residual and the strand wiggles while stretched
        # -- hence the wide UPPER band. The LOWER bound stays strict: a strand can
        # never draw less rope than it owns, which is exactly what the old
        # analytic bow did (it drew the gap, not the rope).
        assert drawn >= free * 0.95, f"strand {index} drew {drawn:.1f} < rope {free:.1f}"
        # Upper bound is a DIVERGENCE guard, not a physics claim: with both ends
        # welded and the 5/3/1.5 tail kick firing every tick, a strand legitimately
        # wiggles well past its own rope length. What must never happen is the
        # solver blowing up. (The old 1.25/1.5 figures were invented and flaky --
        # measured 1.6x on a 91 px strand next to a 93 px gap.)
        assert drawn < free * 4.0, f"strand {index} diverged: drew {drawn:.1f}"
    # at least one strand is slack enough that the rope is 1.5x the gap
    assert max(sim.cord.filament_len) > gap * 1.5, f"no slack strand (gap {gap:.1f})"


def test_filament_nodes_are_not_aliased_into_the_render_state():
    """Two consecutive snapshots must not share the strand lists.

    ``_compose`` keeps one RenderState as the previous tick and one as the
    current tick. Returning the live list made both snapshots the same object, so
    ``RenderState.lerp`` mixed the strands with themselves and they juddered at
    any render rate above 40 Hz while the spine stayed smooth.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    for _ in range(200):
        sim.step(0.025)
    strands = sim._filaments()
    first = strands[0][5]
    sim.cord.small[0][5] = (first[0] + 100.0, first[1] + 100.0)
    assert strands[0][5] == first, "filaments() handed out the live node list"
    sim.cord.small[0][5] = first
    mid = sim.interpolate(0.5)
    a, b = sim._prev.filaments[0][5], sim._cur.filaments[0][5]
    assert C.dist(mid.filaments[0][5], (a[0] + (b[0] - a[0]) * 0.5,
                                        a[1] + (b[1] - a[1]) * 0.5)) < 1e-9


def test_cord_plates_use_the_source_scales_and_do_not_overlap():
    """``og.cs:947-950`` + ``og.cs:979``: fixed 0.5/0.3 and 0.4/0.15, no clamp.

    The atlas sprite ``CentipedeSegment`` is 18x15 and drawn with its long axis
    horizontal -- which is why the source adds ``+ 90f`` to the rotation and why
    ``scaleX`` runs ALONG the cable. That gives a 9 px long, 4.5 px wide rib on a
    10 px pitch: a 1 px gap. The old renderer multiplied by invented factors
    (0.62 / 0.42 -> 11.16 px long) so every rib overlapped its neighbour on a
    10.4 px pitch, which is what "the nodes are bunched up" looked like.
    """
    assert C.CORD_PLATE_SCALE == (0.5, 0.3)
    assert C.CORD_PLATE2_SCALE == (0.4, 0.15)
    sprite_w, sprite_h = 18.0, 15.0
    along = sprite_w * C.CORD_PLATE_SCALE[0]
    across = sprite_h * C.CORD_PLATE_SCALE[1]
    assert abs(along - 9.0) < 1e-9 and abs(across - 4.5) < 1e-9
    assert along < C.CORD_REST, f"plate {along:.2f} px would overlap a {C.CORD_REST} px pitch"
    assert across > C.CORD_PLATE2_SCALE[1] * sprite_h, "part 1 must sit inside part 0"
    # and the ribbon itself is 2*num with num = 1.2f (og.cs:961)
    assert across > 2.0 * 1.2



def test_filament_colours_are_derived_from_metal_and_not_shifted():
    """``og.cs:1012-1026``: ``Lerp(red/blue, metalColor, 0.5f)``, index 0 = metal.

    Two shipped bugs are pinned here. The colours were hard-coded as
    (128,26,30) / (36,40,86) / (22,20,24): a 50/50 mix with a near-black metal
    colour can only ever give G = metal.g/2 and B = metal.b/2, so the red strand
    cannot have G = B = 26, and the blue has to be ≈(0,0,128), not a grey-blue.
    And the index order was shifted by one, so index 0 painted red where the
    source paints metalColor.
    """
    from iterator_pet.rendering.renderer import filament_palette

    metal = C.metal_colour()
    palette = filament_palette()
    assert palette["black"] == metal, "index 0 must be metalColor"
    assert palette["blue"][2] > 100, f"blue strand is washed out: {palette['blue']}"
    for name in ("red", "blue"):
        colour = palette[name]
        for channel in range(3):
            limit = metal[channel] / 2.0 + 1.5
            if channel in ({0} if name == "red" else {2}):
                assert colour[channel] > 100, f"{name} {channel} too weak: {colour}"
            else:
                assert colour[channel] <= limit, f"{name} {channel} impossible: {colour}"


def test_cord_hooks_onto_joint_ones_elbow_not_joint_two():
    """``og.cs:930-931`` + ``oracle.cs:45-53``.

    ``SetStuckSegments`` builds the hook from
    ``Lerp(myJoint.pos, myJoint.ElbowPos(1f, next.pos), 0.4f + 0.07f*i)``.
    ``ElbowPos`` is ``InverseKinematic(Tip=next.pos, vc=this.pos,
    totalLength/3, totalLength*2/3, ...)``, so the elbow is **100 px** from joint 1
    and **50 px** from joint 2 -- not joint 2 itself. Hooking onto the
    joint1 -> joint2 chord put the cord ~20 px further down the arm.
    """
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    for _ in range(200):
        sim.step(0.025)
    arm = sim.arm
    elbow = arm.elbow_pos(1)
    joint1, joint2 = arm.joints[1].pos, arm.joints[2].pos
    assert abs(C.dist(elbow, joint1) - joint1_len(arm) * 2.0 / 3.0) < 0.5, "elbow not 100 px from joint 1"
    assert abs(C.dist(elbow, joint2) - joint1_len(arm) / 3.0) < 0.5, "elbow not 50 px from joint 2"
    # the three stuck nodes sit at 0.40 / 0.47 / 0.54 of joint1 -> elbow
    hook = sim.cord.nodes[sim.cord.stuck_index]
    along = C.dist(hook, joint1)
    assert 35.0 < along < 60.0, f"hook sits {along:.1f} px from joint1 (chord math gave ~60-70)"


def joint1_len(arm) -> float:
    return arm.joints[1].total_length

def test_cord_tail_is_only_leashed_at_eighty_pixels():
    """og.cs:868  ``if (!DistLess(coord[79], firstChunk.pos, 80f))``.

    The cord's tail is NOT plugged into the body: it is leashed at 80 px and the
    filaments are what touch the head. The old 12 px limit glued the spine to the
    chunk centre, so it could be dragged outside the silhouette.
    """
    assert C.CORD_TAIL_LIMIT == 80.0
    sim = SimulationState((0.0, 0.0, 900.0, 600.0))
    distances = []
    for _ in range(500):
        sim.step(0.025)
        distances.append(C.dist(sim.cord.nodes[-1], sim.creature.chunk0))
    # The 80 px pull is a soft correction (0.25 of the excess per tick), so the
    # tail settles a little past 80 rather than exactly on it. What it must NOT
    # do is sit glued to the chunk centre the way the old 12 px limit forced.
    assert max(distances) > 40.0, f"tail never left the body (max {max(distances):.1f} px)"
    assert max(distances) < C.CORD_TAIL_LIMIT * 1.3, f"tail ran to {max(distances):.1f} px"
