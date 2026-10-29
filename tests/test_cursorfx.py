"""档 3 劫持光标：只测纯逻辑（掉落运动学 + 开关），不碰真实光标。"""

from iterator_pet.platform import cursorfx


def test_motion_falls_then_locks_then_ends():
    m = cursorfx.Motion(100.0, 0.0, bottom=200.0, lock_ticks=10)
    ticks = 0
    while m.tick() and ticks < 500:
        ticks += 1
    assert m.phase == "lock"
    assert abs(m.y - 200.0) < 1e-6, "应当掉到屏幕底部就不再动"
    assert 10 <= ticks < 100


def test_motion_lock_is_bounded():
    m = cursorfx.Motion(0.0, 190.0, bottom=200.0, lock_ticks=60)
    ticks = 0
    while m.tick() and ticks < 500:
        ticks += 1
    assert ticks < 80, f"1.5 s（60 tick）就该结束，实测 {ticks}"


def test_env_var_disables_cursor_fx(monkeypatch):
    monkeypatch.setenv("ITERATORPET_NO_CURSORFX", "1")
    assert cursorfx.enabled() is False


def test_toggle_enables_and_restores(monkeypatch):
    monkeypatch.delenv("ITERATORPET_NO_CURSORFX", raising=False)
    cursorfx.set_enabled(True)
    assert cursorfx.enabled() is True
    cursorfx.set_enabled(False)
    assert cursorfx.enabled() is False
    cursorfx.set_enabled(True)
