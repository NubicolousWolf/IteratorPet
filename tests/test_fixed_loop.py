from iterator_pet.platform.fixed_loop import GAME_DT, GAME_HZ, FixedStepLoop


def test_fixed_step_and_pause_resume():
    loop = FixedStepLoop(dt=0.1, max_substeps=8)
    calls = []
    assert loop.advance(0.25, lambda dt: calls.append(dt)) == 2
    loop.pause()
    assert loop.advance(1.0, lambda dt: calls.append(dt)) == 0
    loop.resume()
    assert loop.advance(0.1, lambda dt: calls.append(dt)) == 1


def test_long_frame_is_bounded():
    loop = FixedStepLoop(dt=0.1, max_substeps=2)
    assert loop.advance(10, lambda dt: None) == 2
    assert loop.dropped_time > 0


def test_native_cadence_is_40hz():
    assert GAME_HZ == 40.0
    assert abs(GAME_DT - 0.025) < 1e-12
    assert FixedStepLoop().dt == GAME_DT


def test_sixty_hz_render_interpolates_between_two_ticks():
    """At 60 fps rendering a frame runs 0-2 physics ticks and averages 1.5."""
    loop = FixedStepLoop()
    per_frame = []
    for _ in range(600):  # 10 s of 60 fps frames
        per_frame.append(loop.advance(1.0 / 60.0, lambda dt: None))
    assert set(per_frame) <= {0, 1, 2}
    assert abs(sum(per_frame) - 400) <= 1  # 10 s * 40 Hz
    assert 0.0 <= loop.alpha < 1.0


def test_alpha_spans_zero_to_one():
    loop = FixedStepLoop()
    seen = []
    for _ in range(40):
        loop.advance(1.0 / 60.0, lambda dt: None)
        seen.append(loop.alpha)
    assert min(seen) < 0.4
    assert max(seen) > 0.6


def test_single_huge_frame_is_capped_not_unbounded():
    """A 0.5 s hitch must not run 20 ticks in one frame."""
    loop = FixedStepLoop()
    loop.advance(0.5, lambda dt: None)
    assert loop.steps_total == loop.max_substeps  # capped at 8 ticks = 0.2 s
    assert loop.dropped_time >= 0.2


def test_steps_are_counted_for_diagnostics():
    loop = FixedStepLoop()
    for _ in range(60):  # 1 s of 60 fps frames
        loop.advance(1.0 / 60.0, lambda dt: None)
    assert loop.steps_total == 40  # 1 s at 40 Hz
    assert abs(loop.seconds_simulated - 1.0) < 1e-9
