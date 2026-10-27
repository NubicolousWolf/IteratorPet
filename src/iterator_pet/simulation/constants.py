"""Every number here is copied from Rain World's own code.

Sources (decompiled locally, numbers transcribed by hand):

* ``Oracle.cs``          - body chunks, arm joints, rail frame positions
* ``OracleGraphics.cs``  - gown grid, halo rings, umbilical cord, colours
* ``SSOracleBehavior.cs``- roaming targets, base scoring
* ``palettes/palette26.png`` - the SS_AI room palette

Everything is expressed in *game pixels per 40 Hz tick*, which is why the loop
now runs at exactly 40 TPS: the constants can be used verbatim.
"""

from __future__ import annotations

import colorsys
from math import hypot

GAME_HZ = 40.0

# ---------------------------------------------------------------- body -----
BODY_RADIUS = 6.0          # BodyChunk(this, i, pos, 6f, 0.5f)
BODY_CONNECTION = 9.0      # BodyChunkConnection(..., 9f, ...)
BODY_VEL_DAMPING = 0.4     # oracle.bodyChunks[i].vel *= 0.4f
BODY_IMPULSE_UPPER = 6.2   # ... * 6.2f
BODY_IMPULSE_LOWER = 3.2   # ... * 3.2f
BODY_IMPULSE_CLAMP = 100.0
# ``SSOracleBehavior.GetToDir`` (ss.cs:3137-3152) is NOT "the direction of
# travel". It is:
#     Idle        -> Custom.DegToVec(investigateAngle)
#     Investigate -> -Custom.DegToVec(investigateAngle)
#     otherwise   -> new Vector2(0f, 1f)          // the game's +y-UP frame
# and the posture spring aims bodyChunks[1] at ``getTo - GetToDir * 9``
# (oracle.cs:294). Only the default branch is portable to a desktop pet: the
# other two exist so the SS_AI behaviour can rotate Pebbles towards an
# investigate target, and they let the body axis sit at ANY angle -- including
# flat horizontal -- which is what made the puppet hang sideways here. The
# default branch is straight up in the game frame, which mirrors to "straight
# down" in this sim's +y-DOWN frame:
BODY_HANG_DIR = (0.0, -1.0)
# ``SetNewDestination`` (ss.cs:4290) is only called on a base-move event, so the
# source's body target sits still between events and ``currentGetTo`` stays on
# ``nextPos``. The port re-scored a destination every time one was reached, so
# the trunk was always being dragged. Hold the destination until the body axis
# is back upright, with this cap so the pet can never park forever.
# Minimum rest at a destination before the next one is picked (3 s at 40 TPS),
# then HOLD_CAP is the hard ceiling while the pose has not settled.
WANDER_DWELL_TICKS = 120
WANDER_HOLD_CAP = 160      # 4 s at 40 TPS
# "Come to rest" gate for picking the next destination. It used to be an axis
# angle, which a travelling body can never satisfy (see state._advance), so the
# pet drifted in 160-tick bursts forever. Pixels per tick now.
WANDER_SETTLED_SPEED = 1.0
WANDER_SETTLED_DEG = 6.0   # kept for reference: the old, unusable criterion
# The body HANGS below the rail, so it can never occupy the part of the screen
# above its own arm. Destinations sample the full WIDTH (that is what makes the
# carriage walk the whole perimeter) but only the lower this-fraction of the
# height, so the body is never asked to stand above its arm and press into the
# top wall.
AIR_FRICTION = 0.99        # base.airFriction
GRAVITY = 0.0              # SS rooms set room.gravity = 0

# --------------------------------------------------------------- limbs -----
# og.cs:1643  feet[j].ConnectToPoint(bodyChunks[1].pos, IsMoon ? 20f : 10f,
#                                   push: false, 0f, bodyChunks[1].vel, 0.3f, 0.01f)
# og.cs:1651  hands[j].ConnectToPoint(firstChunk.pos, 15f, push: false, 0f,
#                                    firstChunk.vel, 0.3f, 0.01f)
#
# Five Pebbles is not Moon, so the feet use the 10 px arm of that ternary. The
# 20 px value belongs to Looks to the Moon and must not be borrowed: it is not a
# tunable, it is a different character. `tools/leg_visibility.py` prints what the
# 10 px tether actually produces against the robe.
FOOT_TETHER = 10.0
HAND_TETHER = 15.0
# og.cs:1567/1572  hands/feet = new GenericBodyPart(this, 2f, 0.5f, 0.98f, firstChunk)
LIMB_AIR_FRICTION = 0.98
# og.cs:1643/1651  the last two arguments of ConnectToPoint are adaptVel 0.3 and
# exaggerateVel 0.01; the method ends with `vel -= hostVel; vel *= 1 - adaptVel`.
LIMB_ADAPT_VEL = 0.3
LIMB_HOST_EXAGGERATE = 0.01
# og.cs:1613/1622  head: anchor = firstChunk + DirVec(chunk1, firstChunk) * 6f,
# ConnectToPoint(anchor, 8f, push: true) -- push:true is a WELD, not a leash.
HEAD_ANCHOR = 6.0
HEAD_TETHER = 8.0
# og.cs:1546  head = new GenericBodyPart(this, 5f, 0.5f, 0.995f, oracle.firstChunk)
# so the head's own air friction is 0.995 -- NOT the 0.5 I had been multiplying the
# whole velocity by. The 0.5 in the call is ConnectToPoint's adaptVel.
HEAD_AIR_FRICTION = 0.995
HEAD_ADAPT_VEL = 0.5       # og.cs:1622  ConnectToPoint(..., 0.5f, 0.01f)
HEAD_EXAGGERATE_VEL = 0.01
# og.cs:1596-1598  breathe += 1/Lerp(10, 60, health); breathFac = Lerp(wave, 1, health^2)
HEALTH = 1.0
BREATHE_RATE = 1.0 / 60.0  # health 1 -> Lerp(10,60,1) = 60
KNEE_BOW = 4.0             # og.cs:1770  SS branch: perp * (4f * sign); 5f at 1788 is Moon
LEG_HALF_WIDTH = 2.0       # og.cs:2224  num4 = IsPebbles ? 2f : ...
ARM_HALF_WIDTH = 4.0       # og.cs:2197  num4 = IsPebbles ? 4f : 2f
FOOT_GLYPH = 6.0           # haloGlyph-1's 6x6 plus

# ----------------------------------------------------------------- arm -----
ARM_LENGTHS = (300.0, 150.0, 90.0, 30.0)
ARM_VEL_DAMPING = 0.8      # joint.vel *= 0.8f  (Downpour uses 0.7)
ARM_ELBOW_BIAS = 1.8       # vel += ClampMagnitude(ikElbow - pos, 100) / 100 * 1.8f
ARM_ELBOW_CLAMP = 100.0
ARM_TAIL_BIAS = 1.2        # last joint steering toward the body
ARM_TAIL_CLAMP = 50.0
ARM_MIN_LEN_FOLDED = 1.0   # LerpMap(dot, -1, 1, 1f, 0.2f): folded -> 1.0
ARM_MIN_LEN_STRAIGHT = 0.2  #                                    straight -> 0.2
# oracle.cs:327-331  the carriage, verbatim:
#     baseMoving = Distance(BasePos, baseIdeal) > (baseMoving ? 50f : 350f)
#                  && consistentBasePosCounter > 30;
#     if (baseMoving) framePos = MoveTowardsAngle(framePos*90, num3*90, 1f)/90f;
# 350 / 50 px are for a 1400 px wide room, so here they are ratios of the chamber
# DIAGONAL: 0.30 / 0.036 -> 243 / 29 px on a 776x415 chamber (the INSET rect is
# 724x363, diagonal 810; the outer diagonal 882 would give 264/32, which is what
# an earlier version of this comment claimed while the code used the inset).
BASE_MOVE_START_RATIO = 0.30   # puppet must stray this far before the base re-sites
BASE_MOVE_STOP_RATIO = 0.036   # 50 / 1400
# DESIGN ADDITION, not in the source. Hard ceiling on
# ``BASE_MOVE_START_RATIO * diagonal``, as a fraction of ``DESTINATION_REACH_RATIO
# * ARM_LENGTHS_TOTAL`` (the destination clamp radius, 353 px). A start threshold
# well above that radius is crossed only by luck: every destination is pulled
# inside it, so the carriage is asked to move on a few percent of ticks. Measured
# before the cap on this machine's chamber: threshold 366 px vs clamp 353 px,
# carriage moving on 2 % of ticks and driving 4 px/s. The 0.85 margin itself is
# ours -- the cap is structural, 0.85 is a tune.
BASE_MOVE_START_REACH_RATIO = 0.85
# CARRIAGE SPEED -- mechanism is the source's, magnitude is ours.
# oracle.cs:327-331 moves the SCALED angle by one degree per tick
#     framePos = Mathf.MoveTowardsAngle(framePos * 90f, num3 * 90f, 1f) / 90f
# i.e. 1/90 of a rail quadrant: 6.2 px/tick = 249 px/s on the game's 560 px rail,
# which reads as a ~2 s slide across one chamber edge. Copied literally onto this
# port's 1138 px rail it measured 135 px/s while moving and crossed one edge in
# 8.4 s -- the carriage visibly flies along the rail while the puppet ambles.
# Before the roaming rewrite (6d97b57) this port ran the same two numbers --
# 0.12 / 0.45, plus a third "still" tier (灵枢 mem_1792983112584_f6afa0 records the
# old design as fast when the puppet was beyond 0.34 x the chamber diagonal, slow
# from 0.26 x, still below). Here the "still" tier is the hysteresis above
# (start_threshold), so only the two speeds come back: fast when the carriage
# still has most of a quadrant to drive, slow otherwise. Same MoveTowardsAngle,
# same per-tick call, slower magnitude -- 135 px/s measured before, 51 px/s now,
# one edge in 22 s instead of 8.4 s.
BASE_SPEED_SLOW = 0.12         # degrees of the scaled angle per tick
BASE_SPEED_FAST = 0.45
BASE_STABLE_TICKS = 30         # consistentBasePosCounter > 30
# Design addition (not in the source): the arm is 570 px, so the carriage must
# follow once the puppet is further than this fraction of it, ignoring the
# hysteresis and the stability gate. The source never needs it -- its room is
# about one arm long; a 776x415 desktop chamber is wider.
BASE_FOLLOW_ARM_RATIO = 0.90
# How far from the carriage the puppet may aim: 0.95 of the 570 px arm. The old
# 0.62 (353 px) was tuned while the carriage was stuck and could not follow, so
# every full-screen destination pressed against it.
ARM_REACH_RATIO = 0.95
# How far a DESTINATION may be from the carriage when it is picked: 0.62 of the
# arm's 570 px = 353 px. Tighter than the arm on purpose -- the trunk's hang term
# is only 9 px (BODY_CONNECTION), so a goal much further away saturates chunk 1's
# impulse and the body loses its upright reference while travelling.
DESTINATION_REACH_RATIO = 0.62
ARM_LENGTHS_TOTAL = 570.0
CORNER_BLEND = 0.1         # num2 = 0.1f ; corner arc radius = 0.1 * edge

# ---------------------------------------------------------------- cord -----
# og.cs:787  coord = new Vector2[80, 3] and every sweep solves for a fixed
# distance of 10f, so SS_AI's cord is exactly 79 * 10 = 790 px. 80 is the room's
# number, not a law: on a variable desktop the NODE COUNT has to follow the span
# while the PITCH stays 10 (below ~6 px the chitin plates turn into a smear).
CORD_NODES = 80
CORD_MAX_NODES = 240
CORD_SLACK = 1.25          # reach = far corner distance * slack, so it can still bow
CORD_REST = 10.0           # (10f - num) in both sweeps
CORD_STUCK_INDEX = 60      # Length - 20, clamped onto the arm's first elbow
# og.cs:868  the last node is only leashed once it is more than 80 px from
# firstChunk.pos: pos -= dir*((80-dist)*0.25); vel -= dir*((80-dist)*0.5).
# It is NOT plugged into the body -- the 14 filaments are what touch the head.
CORD_TAIL_LIMIT = 80.0
CORD_VEL_DAMPING = 0.995
CORD_DAMPING = 0.995       # og.cs:821  coord[i, 2] *= 0.995f
CORD_TAIL_POS_PULL = 0.25  # og.cs:872
CORD_TAIL_VEL_PULL = 0.5   # og.cs:873
CORD_SAG = 0.0             # game rooms have zero gravity; the cord is meant to coil
CORD_BUOYANCY = 1.0        # og.cs:825  vel.y += InverseLerp(0.2f, 0f, i/(n-1))
CORD_BEND_STIFFNESS = 0.5  # num3 = 0.5f, then *= 0.75f per level
CORD_BEND_LEVELS = (2, 3)
CORD_SWEEP_LINKS = 8         # links resolved per constraint pass (solver convergence)
# og.cs:947-950  the chitin plates are `CentipedeSegment` sprites scaled by fixed
# factors -- there is NO pitch-dependent clamp in the source. Literal values:
#     part 0: scaleX 0.5, scaleY 0.3      part 1: scaleX 0.4, scaleY 0.15
# The extracted sprite is 18x15 and drawn with its LONG axis HORIZONTAL (its
# top-centre and bottom-centre pixels are notched), which is exactly why
# og.cs:979 adds +90 degrees to the rotation: that turns the sprite's WIDTH onto
# the cable. So scaleX is the length along the cord and scaleY the width across:
#     18*0.5 = 9 px long, 15*0.3 = 4.5 px across   (part 0)
#     18*0.4 = 7.2 px long, 15*0.15 = 2.25 px across (part 1)
# on a 10 px pitch, i.e. a 1 px gap between ribs -- a continuous ribbed cable.
# The old code multiplied by 0.62 / 0.42 (invented) to get 11.16 x 5.72, which
# made every plate overlap its neighbour on a 10.4 px pitch.
CORD_PLATE_SCALE = (0.5, 0.3)     # og.cs:947-948  (scaleX, scaleY)
CORD_PLATE2_SCALE = (0.4, 0.15)   # og.cs:949-950  (scaleX, scaleY)

# ------------------------------------------------------------ behaviour ----
# DESIGN ADDITION -- the source has no behaviour chooser. ``SSOracleBehavior``
# sets ``movementBehavior`` from story actions (SeePlayer -> KeepDistance,
# conversation -> Talk, pearl -> ShowMedia) and, when nothing is happening,
# ``Move()`` (ss.cs:4110) runs Idle, which ignores the player entirely. A desktop
# pet has no story, so the three portable states are chosen by a WEIGHTED RANDOM
# DRAW at segment boundaries (see ``behavior.BehaviourSelector``). Every number
# below is OURS, not the game's.
BEHAVIOURS = ("roaming", "investigate", "talk")
BEHAVIOUR_MIN_TICKS = 80        # 2 s: a boundary may not flip the pose sooner
# DRAW WEIGHTS -- what "roaming is the most likely behaviour" now literally is.
# An argmax cannot express "probable": a base score high enough to win most ticks
# wins *every* tick (talk never fires), and one low enough to lose sometimes loses
# whenever the cursor shows up. That is why the old scorer sat at 68 % talk with
# the cursor parked even though roaming had the highest base score.
BEHAVIOUR_BASE = {"roaming": 1.00, "investigate": 0.55, "talk": 0.45}
# The MAXIMUM length of one segment, in ticks. A segment also ends the moment its
# mood's gate shuts -- and in practice that is what ends investigate and talk:
# measured with "mouse moves 5 s, rests 5 s" (10 min), all 16 investigate/talk
# segments ended on the gate (lengths 80-200 and 80-99) and only roaming ever
# served its nominal 450. The number below is therefore a ceiling; the panel
# prints it as ``held X/max Yt``.
#   roaming     450 = 11.25 s
#   investigate 240 =   6 s
#   talk        900 =  22.5 s
# OURS, not the source's (the source has no moods at all). They are fitted, not
# tasted: talk's 60 s cooldown is fixed by the user, so the split the user asked
# for ("roaming 75 % while the cursor sits still") fixes the talk length as
#     D / (D + 2400 + delay) = 0.25  ->  D ~= 900 ticks.
# Measured with these numbers, 15 min, 8 seeds (_work/behaviour_probe.py):
#   parked  roaming 75.0-76.2 %  talk 23.8-25.0 %
#   sweep   roaming 72.7-78.0 %  investigate 22.0-27.3 %  talk 0 %
BEHAVIOUR_SEGMENT_TICKS = {"roaming": 450, "investigate": 240, "talk": 900}
# "暂定为一分钟冷却" (user, 2026-10-27): after a conversation ends the pet will not
# start another one for a minute. 40 TPS -> 2400 ticks.
TALK_COOLDOWN_TICKS = 2400
# "talk 概率随鼠标悬停时间增加而增大": talk's draw weight is multiplied by a ramp
# that starts at TALK_GAIN_MIN when the cursor has just stopped and reaches
# TALK_GAIN_MAX after TALK_HOVER_FULL_TICKS of hovering. Measured P(talk wins the
# draw | talk was eligible), 15 min, 8 seeds:
#   hover 10-30 s   P = 0.43  (n=14 draw where talk was eligible)
#   hover >= 90 s   P = 0.77  (n=94)
# "| eligible" matters: the UNCONDITIONAL chance that a draw picks talk falls
# from 0.43 to 0.14 as the hover grows, because deep into a hover most draws are
# inside the cooldown. And there is no draw at all before hover = 450 ticks (the
# roaming segment), so nothing above describes the first 11 s. The two middle
# buckets have n=2 and n=0, so this is two measured points, not a measured curve.
# The time from "the cursor stops" to the first conversation is median 17.5 s
# (min 6.2, max 62.5, n=8) -- the pet is shy, not instant.
#
# How much hover the pet loses per tick in which the cursor moved at all. The
# GATE is still the single 0 px/tick line (below); this only damps the ramp, so a
# resting hand that twitches one mouse count loses a second of warm-up, not 90.
STILL_DECAY_TICKS = 40          # 1 s
TALK_GAIN_MIN = 0.10
TALK_GAIN_MAX = 3.0
TALK_HOVER_FULL_TICKS = 3600    # 90 s: the ramp is deliberately slow, so a mouse
                                # that just stopped is not a conversation yet
# The ONE motion threshold, in px/tick. Both states now read the same line, as the
# user asked ("判据统一成 0 px/tick"): moving at all -> investigate is available,
# exactly motionless -> talk is available. It used to be 1 px/tick for "still" and
# >0 for "moving", so a 0.5 px/tick drift counted as BOTH and the scores broke
# the tie on numbers nobody can see on screen.
SCORE_MOTION_MIN = 0.0
# Talk only starts if the cursor is within this distance of the BODY (sim px).
# Beyond it the pet would have to walk two arm's lengths to reach a standoff
# point it cannot support -- the same failure mode as ROADMAP 0.3.
SCORE_TALK_RANGE = 520.0
# FRESHNESS -- a recency dampener on the draw weight, and nothing more.
# Every behaviour owns a freshness in [0, 1]; doing it drains that freshness and
# leaving it alone restores it. It MULTIPLIES that behaviour's draw weight, so a
# mood that has just used up a long segment is less likely to be drawn again --
# but it can never forbid one (see FRESHNESS_FLOOR below), and it is NOT what
# ends a segment: ``BEHAVIOUR_SEGMENT_TICKS`` does.
# What it replaces: the old ``bored`` / ``since`` pair, which were ADDED to the
# incumbent's own score and multiplied a rival's score by ``1 - bored`` -- a
# one-way ratchet that locked the pet into perpetual roaming after ~12 s with no
# way back (parking the cursor on the taskbar was enough; see AGENTS 6.4).
# What it does NOT do: guarantee rotation. An earlier version of this comment
# claimed a state "can never hold forever" because its utility decays toward zero
# while rivals recover -- that was true of the argmax and is no longer the
# mechanism. Rotation now comes from the segment length plus the hand-over rule
# in ``BehaviourSelector._draw``.
# The shape is borrowed from slugcatpet's ``MoodArbiter.tick_freshness``
# (brain/desire.py), which decays the active activity and recovers the rest.
SCORE_FRESHNESS_DECAY = 1.0 / 480.0    # 12 s of one behaviour drains it to 0
SCORE_FRESHNESS_RECOVER = 1.0 / 240.0  # 6 s of not doing it refills it
# Freshness damps a mood's draw weight but never zeroes it, so the ONLY thing
# that can forbid a mood is its gate. Without this floor the mood that has just
# finished a segment is always at 0.06 weight while its rival is at 1.0, so every
# boundary is decided in advance by the durations. Measured with the floor set to
# 0.0: the parked split is 75.0 / 25.0 on ALL EIGHT seeds -- one distinct value,
# i.e. a draw that cannot change the outcome is not a draw. With 0.35 the seeds
# spread over parking 75.0-76.2 % and the hover ramp is what decides a boundary.
# Reproduce: _work/behaviour_ablation.py (group A).
FRESHNESS_FLOOR = 0.35
# ss.cs:4174-4204  Investigate: player.DangerPos + DegToVec(angle) * 150f, with
# the angle kept inside (-70, 70) and swept at 0.4-0.8 deg/tick, random sign.
INVESTIGATE_RADIUS = 150.0
INVESTIGATE_ANGLE_LIMIT = 70.0
INVESTIGATE_ANGLE_SPEED_MIN = 0.4
INVESTIGATE_ANGLE_SPEED_MAX = 0.8
# ss.cs:4167-4170  KeepDistance samples a room point and commits to it; the Talk
# branch of CommunicatePosScore parks the iterator ~250 px from the player in a
# 1400 px room -- 18 % of the room width, which on THIS chamber (1138 px) is
# 236 px and reads as "it runs away from my mouse". The user asked for "three
# puppets wide" instead, and measured on the live chamber the gown is 25.2 sim px
# wide at the median (1200 ticks; max 38.9), so the standoff is
#     3 x 25.2 = 76 sim px = 114 screen px at scale 1.5.
# It is a CONSTANT in sim px, not a ratio of the chamber: a standoff that scales
# with the room is 236 px on a 1138 px chamber and 194 px on a 776 px one, i.e.
# "how far away it stands" would depend on the monitor, which is not a thing the
# user asked for. Measure: `_work/talk_standoff.py` -- it prints
#   gown x-span over 1200 ticks: median 25.2  max 38.9  min 17.7 sim px
#   3 x median = 75.5 -> TALK_DISTANCE = 76.0 (3.02 gown widths)
# so the number is reproducible rather than remembered. (The median, not the max:
# the gown is a swinging cloth grid, and 3 x 38.9 = 117 would be "three widths"
# only at the widest instant of the wobble.)
TALK_DISTANCE = 76.0
TALK_REPICK = 60.0              # re-pick the talk spot if the cursor moved this far

SMALL_CORDS = 14
SMALL_CORD_NODES = 20
# UbilicalCord: rnd<0.5 ? 50+rnd*15 : Lerp(50, 200, rnd^1.5)
SMALL_CORD_MIN_LEN = 50.0
SMALL_CORD_MAX_LEN = 200.0
# og.cs:879  smallCords[n, k, 2] *= LerpMap(|vel|, 2f, 6f, 0.999f, 0.9f)
# A SPEED-DEPENDENT friction: a slow strand keeps almost all of its velocity
# (0.999) while a fast one is braked hard (0.9). This is what lets a short
# strand keep swinging instead of freezing, and the long ones whip.
SMALL_CORD_DAMP_SLOW_SPEED = 2.0
SMALL_CORD_DAMP_FAST_SPEED = 6.0
SMALL_CORD_DAMP_SLOW = 0.999
SMALL_CORD_DAMP_FAST = 0.9
# og.cs:905-907  the tail end is kicked along the cord's own last link; the head
# end is kicked AGAINST (lookDir + smallCordsHeadDirs[n]) at 2x / 1x. These
# kicks are the only reason the 14 strands do not collapse into one curve.
SMALL_CORD_TAIL_KICK = (5.0, 3.0, 1.5)   # nodes 1, 2, 3
SMALL_CORD_HEAD_KICK = (2.0, 1.0)        # nodes 18, 17
SMALL_CORD_HALF_WIDTH = 0.5              # og.cs:988  num3 = 0.5f
# Constraint passes per strand per tick. The source runs one backward + one
# forward sweep; two pairs are needed once the tail is dragged around by a
# roaming body (solver convergence, not physics -- see cord._step_filaments).
SMALL_CORD_PASSES = 2

# --------------------------------------------------------------- gown -----
GOWN_DIVS = 11
GOWN_NECK_HALF = 5.0       # a = 5f
GOWN_HEM_HALF = 11.0       # num2 = 11f
GOWN_LENGTH = 18.0         # 0f - (num2 + 7f)
GOWN_VEL_DAMPING = 0.999
GOWN_GRAVITY = 0.9
GOWN_TOLERANCE = 9.0       # Mathf.Lerp(0f, 9f, t)
GOWN_NEIGHBOUR_SPRING = 0.05

# --------------------------------------------------------------- halo -----
HALO_RINGS = (10, 30, 60)  # MemoryBit counts per ring
HALO_CONNECTIONS = 20
HALO_HEAD_OFFSET = 20.0    # Center = head + DirVec(firstChunk, head) * 20f
HALO_RADIUS_BASE = 3.0     # (3f + ring + push - 0.5f * avgVoice) * expand * 10f
HALO_EXPAND_BASE = 1.0
HALO_PUSH_BASE = 0.0

# ------------------------------------------------------------- colours ----
SKIN_COLOUR = (255, 102, 203)   # Color(1f, 0.4f, 0.796f)
# og.cs:1943-1949 -- the eye is a plain `new FSprite("pixel")`, and the near
# black Color(0.02f, 0f, 0f) is assigned only for IsMoon / IsPastMoon / IsStraw.
# Five Pebbles therefore keeps the FSprite default, and `FSprite::.ctor`
# (IL_0001..0006) is literally `_color = Futile::white`. His eyes are WHITE.
EYE_COLOUR = (255, 255, 255)
# og.cs:2168-2169 -- scaleX = 1 + InverseLerp(...) + (1 - eyesOpen), and
# scaleY = Lerp(1f, IsPebbles ? 2f : 3f, eyesOpen). The sprite is the 1x1
# `pixel`, so scaleX/scaleY ARE the pixel size. Eyes open (eyesOpen = 1):
# height 2 px, width 1..2 px; the two InverseLerps foreshorten whichever eye
# faces the look direction. The 3f is the Moon branch.
EYE_SCALE_Y = 2.0
EYE_WIDTH_MIN = 1.0
EYE_WIDTH_MAX = 2.0
METAL_BLEND = (31, 7, 0)        # palette.texture.GetPixel(5, 5)
BLACK = (0, 0, 0)

# Halo in SS_AI renders warm amber because of the room's orange lights.
HALO_COLOUR = (234, 123, 42)
HALO_GLOW = (255, 176, 92)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def hsv_to_rgb8(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (int(r * 255), int(g * 255), int(b * 255))


def hsl_to_rgb8(h: float, s: float, light: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, light)), max(0.0, min(1.0, s)))
    return (int(r * 255), int(g * 255), int(b * 255))


def gown_colour(f: float) -> tuple[int, int, int]:
    """``OracleGraphics.Gown.Color`` for Five Pebbles.

    HSL(Lerp(0.08, 0.02, f^2), Lerp(1.0, 0.8, f), 0.5) -- hot orange at the
    neck fading to deep red at the hem.
    """
    return hsl_to_rgb8(_lerp(0.08, 0.02, f**2), _lerp(1.0, 0.8, f), 0.5)


# SS_AI is a dim chamber; ArmJointGraphics multiplies lightness by
# (0.7 - 0.5 * room.Darkness) for the base and (0.85 - 0.65 * Darkness)
# for the highlight. 0.45 reproduces the video's mid-grey arm.
ROOM_DARKNESS = 0.45


def arm_base_colour(darkness: float = ROOM_DARKNESS) -> tuple[int, int, int]:
    """``ArmJointGraphics.BaseColor`` for Five Pebbles (non-SL branch)."""
    light = _lerp(0.05, 0.7 - 0.5 * darkness, 1.0)
    base = hsl_to_rgb8(0.025, _lerp(0.4, 0.1, 1.0), light)
    # Color.Lerp(..., new Color(0f, 0f, 0.1f), pow(InverseLerp(0.45,-0.05,1),0.9)*0.5)
    tint = max(0.0, min(1.0, (1.0 - 0.45) / (-0.05 - 0.45))) ** 0.9 * 0.5
    return mix(base, (0, 0, 26), tint)


def arm_highlight_colour(darkness: float = ROOM_DARKNESS) -> tuple[int, int, int]:
    light = _lerp(0.15, 0.85 - 0.65 * darkness, 1.0)
    base = hsl_to_rgb8(0.025, _lerp(0.5, 0.1, 1.0), light)
    tint = max(0.0, min(1.0, (1.0 - 0.45) / (-0.05 - 0.45))) ** 0.9 * 0.4
    return mix(base, (0, 0, 38), tint)


def metal_colour() -> tuple[int, int, int]:
    """Color.Lerp(palette.blackColor, palette.texture.GetPixel(5, 5), 0.12f)."""
    return tuple(int(_lerp(a, b, 0.12)) for a, b in zip(BLACK, METAL_BLEND))  # type: ignore[return-value]


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(_lerp(x, y, t)) for x, y in zip(a, b))  # type: ignore[return-value]


# ------------------------------------------------------------ geometry ----
def inverse_kinematic(
    start: tuple[float, float],
    target: tuple[float, float],
    upper: float,
    fore: float,
    flip: float = 1.0,
) -> tuple[float, float]:
    """``Custom.InverseKinematic`` - the two-bone elbow solver."""
    sx, sy = start
    tx, ty = target
    dx, dy = tx - sx, ty - sy
    distance = hypot(dx, dy)
    if distance < 1e-6:
        return (sx, sy - min(upper, fore))
    ux, uy = dx / distance, dy / distance
    reach = max(abs(upper - fore) + 1e-3, min(upper + fore - 1e-3, distance))
    a = (upper * upper - fore * fore + reach * reach) / (2.0 * reach)
    h = max(0.0, upper * upper - a * a) ** 0.5
    bx, by = sx + ux * a, sy + uy * a
    return (bx - uy * h * flip, by + ux * h * flip)


def bezier(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1.0 - t
    x = u * u * u * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t * t * t * d[0]
    y = u * u * u * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t * t * t * d[1]
    return (x, y)


def dir_vec(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return hypot(b[0] - a[0], b[1] - a[1])


def clamp_magnitude(v: tuple[float, float], limit: float) -> tuple[float, float]:
    length = hypot(v[0], v[1])
    if length <= limit or length < 1e-9:
        return v
    return (v[0] / length * limit, v[1] / length * limit)
