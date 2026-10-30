"""总开关与可调冷却（用户 2026-10-30）的测试。

三条要求：

1. `llm.json` 的 `enabled=false` 等于"完全离线"：一次请求都不发、不写历史、
   对话退回本地台词池 —— **不用清 key**；
2. 三档冷却（拖拽 / 搭话 / 仪式）能在设置页改，秒数正确换算成 40 TPS 的 tick；
3. 越界值（比如 999 秒）落盘前被夹回 600，文件永远是合法合同。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.llm import client as llm_client  # noqa: E402
from iterator_pet.llm import config as llm_config  # noqa: E402
from iterator_pet.llm import route as llm_route  # noqa: E402
from iterator_pet.platform.llm_settings import LLMSettingsDialog  # noqa: E402
from iterator_pet.simulation.state import SimulationState  # noqa: E402

APP = QApplication.instance() or QApplication([])
SECRET = "sk-1234567890abcdefghijklmn"


class FakeTransport:
    def __init__(self, payload=None):
        self.payload = payload
        self.calls: list[tuple] = []

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append((url, payload))
        return self.payload or {"choices": [{"message": {"content": "在。"}}]}


class InlineJob:
    def __init__(self, fn, *args, **kwargs):
        self._value = fn(*args, **kwargs)

    def start(self):
        return self

    def poll(self):
        value, self._value = self._value, None
        return value


def _store(tmp_path, **overrides) -> llm_config.LLMStore:
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.install_default_file()
    cfg = store.load()
    cfg.api_key = SECRET
    cfg.model = "deepseek-chat"
    for name, value in overrides.items():
        setattr(cfg, name, value)
    store.config = cfg
    return store


# ---------------------------------------------------------------- 总开关 --
def test_the_master_switch_defaults_to_on(tmp_path):
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.install_default_file()
    assert store.load().enabled is True
    assert json.loads(store.path.read_text(encoding="utf-8"))["enabled"] is True


def test_switching_it_off_makes_the_client_silent_without_touching_the_key(tmp_path):
    store = _store(tmp_path, enabled=False)
    transport = FakeTransport()
    client = llm_client.LLMClient(store, transport=transport)

    assert client.enabled is False
    result = client.ask([{"role": "user", "content": "在吗"}])
    assert not result.ok and "已关闭" in result.reason
    assert transport.calls == [], "关掉 = 一次请求都不发"
    assert store.config.api_key == SECRET, "key 要原样留着"


def test_the_switch_survives_a_round_trip(tmp_path):
    store = _store(tmp_path)
    store.config.enabled = False
    assert store.save() is True
    again = llm_config.LLMStore(path=store.path)
    assert again.load().enabled is False
    assert again.state()["enabled"] is False


def test_a_router_with_the_switch_off_never_writes_history(tmp_path):
    store = _store(tmp_path, enabled=False)
    transport = FakeTransport()
    router = llm_route.LLMRouter(store, folder=tmp_path, transport=transport,
                                 job_factory=InlineJob)
    assert router.request("talk") is False
    assert transport.calls == []
    assert not (tmp_path / "chats" / "pebbles.jsonl").exists()


# ---------------------------------------------------------------- 冷却 --
def test_cooldowns_reach_the_event_layer(tmp_path):
    sim = SimulationState((0.0, 0.0, 776.0, 415.0), character="pebbles", warmup=0)
    sim.set_dialogue_cooldowns(2.0, 3.0, 4.0)
    # 40 TPS：2 s = 80 tick、3 s = 120 tick、4 s = 160 tick
    assert sim.events.drag_cooldown_ticks == 80
    assert sim.events.talk_cooldown_ticks == 120
    assert sim.events.ritual_cooldown_ticks == 160


def test_cooldowns_default_to_the_constants(tmp_path):
    sim = SimulationState((0.0, 0.0, 776.0, 415.0), character="pebbles", warmup=0)
    from iterator_pet.simulation import constants as C

    assert sim.events.drag_cooldown_ticks == C.DIALOGUE_DRAG_COOLDOWN
    assert sim.events.ritual_cooldown_ticks == C.DIALOGUE_RITUAL_COOLDOWN


def test_cooldowns_are_clamped_when_saved(tmp_path):
    store = _store(tmp_path)
    cfg = store.config
    cfg.dialogue_cooldown_drag_s = 999.0
    cfg.dialogue_cooldown_talk_s = -5.0
    assert store.save(cfg) is True

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["dialogue_cooldown_drag_s"] == 600.0
    assert saved["dialogue_cooldown_talk_s"] == 0.0


# ---------------------------------------------------------------- 设置页 --
def test_the_settings_page_shows_and_saves_the_switch(tmp_path):
    store = _store(tmp_path, enabled=True)
    dialog = LLMSettingsDialog(store)
    assert dialog.enabled.isChecked() is True

    dialog.enabled.setChecked(False)
    dialog._save()
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    dialog.close()


def test_the_settings_page_saves_the_three_cooldowns(tmp_path):
    store = _store(tmp_path)
    dialog = LLMSettingsDialog(store)
    assert dialog.cooldown_drag.value() == 8.0
    assert dialog.cooldown_talk.value() == 60.0
    assert dialog.cooldown_ritual.value() == 60.0

    dialog.cooldown_drag.setValue(20.0)
    dialog.cooldown_talk.setValue(0.0)
    dialog.cooldown_ritual.setValue(120.0)
    dialog._save()

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["dialogue_cooldown_drag_s"] == 20.0
    assert saved["dialogue_cooldown_talk_s"] == 0.0
    assert saved["dialogue_cooldown_ritual_s"] == 120.0
    dialog.close()


def test_the_settings_page_keeps_fields_it_does_not_own(tmp_path):
    store = _store(tmp_path)
    store.config.action_cooldown_s = 7.5          # P2 的字段，设置页不该动它
    dialog = LLMSettingsDialog(store)
    dialog._save()
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["action_cooldown_s"] == 7.5
    dialog.close()


# ------------------------------------------------------------- 老文件升级 --
def test_upgrade_adds_missing_keys_without_touching_existing_ones(tmp_path):
    path = tmp_path / "llm.json"
    # 老玩家的文件：没有 enabled、没有冷却，而且历史条数是"手改过的 3"
    path.write_text(
        json.dumps({"api_key": SECRET, "model": "m", "history_messages": 3},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    store = llm_config.LLMStore(path=path)
    assert store.upgrade() is True
    assert store.upgraded is True

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["api_key"] == SECRET, "已有的值一个都不能动"
    assert saved["history_messages"] == 3, "手改过的值也不许被默认值覆盖"
    assert saved["enabled"] is True
    assert saved["dialogue_cooldown_drag_s"] == 8.0
    assert saved["dialogue_cooldown_ritual_s"] == 60.0


def test_upgrade_is_a_no_op_when_the_file_is_complete(tmp_path):
    store = _store(tmp_path)
    store.save()                                   # 写一份完整的
    assert store.upgrade() is False
    assert store.upgraded is False


def test_upgrade_does_not_resurrect_a_broken_file(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text("{ 手改坏了", encoding="utf-8")
    store = llm_config.LLMStore(path=path)
    assert store.upgrade() is False
    assert path.read_text(encoding="utf-8") == "{ 手改坏了"
