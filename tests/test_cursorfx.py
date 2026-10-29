"""档 2 光标特效的开关契约（不碰真实光标：只测 enabled/环境变量）。"""

import os

from iterator_pet.platform import cursorfx


def test_env_var_disables_cursor_fx(monkeypatch):
    monkeypatch.setenv("ITERATORPET_NO_CURSORFX", "1")
    assert cursorfx.enabled() is False


def test_toggle_enables_and_restores(monkeypatch):
    monkeypatch.delenv("ITERATORPET_NO_CURSORFX", raising=False)
    cursorfx.set_enabled(True)
    assert cursorfx.enabled() is True
    cursorfx.set_enabled(False)
    assert cursorfx.enabled() is False
    cursorfx.set_enabled(True)                 # 还原默认，免得影响别的测试
