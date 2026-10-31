"""工坊「本地对话」页的测试（离屏 Qt，不弹真窗口）。

这页是从独立窗口（`dialogue_editor.py`）收进来的，所以四件事必须钉住：

1. 打开时按默认组显示（pebbles × drag = 4 行）；
2. 切换事件/角色时，**之前那组的改动不能丢**（draft 机制）；
3. 保存后文件被原子改写、只动对应组、并且立刻热重载（`pool()` 马上能读到）；
4. **菜单整理**：这个入口在菜单里已经没有了，只在工坊里；「对话」那个歧义开关
   变成页内勾选框「不接入LLM时，启用本地对话」。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.platform.dialogue_tab import DialogueTab  # noqa: E402
from iterator_pet.simulation import constants as C  # noqa: E402
from iterator_pet.simulation.lines import Lines  # noqa: E402

APP = QApplication.instance() or QApplication([])

REPO = Path(__file__).resolve().parents[1]


def _tab(tmp_path) -> tuple[DialogueTab, Lines]:
    lines = Lines(path=tmp_path / "lines.json")
    return DialogueTab(lines), lines


def test_the_tab_opens_on_the_default_group(tmp_path):
    tab, _ = _tab(tmp_path)

    assert tab.list.count() == 4, "默认显示 pebbles × drag 的四条"
    assert tab.event_box.currentData() == "drag"
    assert tab.character_box.currentData() == "pebbles"


def test_the_tab_keeps_the_previous_group_when_switching(tmp_path):
    tab, _ = _tab(tmp_path)

    tab.add_line()
    tab.list.item(tab.list.count() - 1).setText("临时加的一句")
    tab.event_box.setCurrentIndex(1)                     # 切到 talk
    assert "临时加的一句" not in [
        tab.list.item(i).text() for i in range(tab.list.count())
    ]
    tab.event_box.setCurrentIndex(0)                     # 切回 drag

    texts = [tab.list.item(i).text() for i in range(tab.list.count())]
    assert "临时加的一句" in texts, "切换分组不能把刚才的改动丢掉"


def test_saving_only_touches_the_edited_group_and_hot_reloads(tmp_path):
    tab, lines = _tab(tmp_path)
    tab.event_box.setCurrentIndex(1)                     # talk
    tab.add_line()
    tab.list.item(tab.list.count() - 1).setText("我还在。")

    assert tab.is_dirty() is True
    assert tab.save() == ""
    assert tab.is_dirty() is False

    assert lines.pool("pebbles", "talk") == ["……", "我还在。"], "保存后应立刻热重载"
    assert lines.pool("pebbles", "drag") == [
        "滚开。", "别烦我。", C.DIALOGUE_SILENT_ENTRY, "你没有更重要的事可做吗？"
    ], "别的组不能被动"
    assert lines.pool("past_moon", "talk") == ["你还在这里吗？"], "别的角色不能被动"


def test_the_tab_survives_a_broken_file(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    lines = Lines(path=path)

    tab = DialogueTab(lines)

    assert tab.list.count() >= 1, "坏文件时应当退回内置默认，而不是空白"


def test_the_enable_switch_is_applied_immediately(tmp_path):
    """勾选框是**开关**，不像台词那样等「保存」：它一改就交给窗口层。"""
    calls: list[bool] = []
    lines = Lines(path=tmp_path / "lines.json")
    tab = DialogueTab(lines, on_enabled=calls.append, enabled=True)

    assert tab.enabled_box.isChecked() is True
    assert "不接入LLM时，启用本地对话" == tab.enabled_box.text()

    tab.enabled_box.setChecked(False)
    assert calls == [False]
    assert tab.is_dirty() is False, "开关不参与工坊的「保存」"


def test_new_characters_show_up_in_the_tab(tmp_path):
    """注册表变了，这页的角色下拉要跟着变（否则新角色没法编台词）。"""
    from iterator_pet import characters as ch

    lines = Lines(path=tmp_path / "lines.json")
    tab = DialogueTab(lines)
    assert tab.character_box.findData("newcomer") == -1

    ch.create("newcomer", "新来的", "pebbles", tmp_path)
    tab.refresh()

    assert tab.character_box.findData("newcomer") >= 0
    tab.character_box.setCurrentIndex(tab.character_box.findData("newcomer"))
    assert tab.list.count() == 1, "新角色没有台词组时显示中性占位「……」"


def test_the_workshop_has_the_dialogue_tab_between_appearance_and_card(tmp_path):
    from iterator_pet.llm import worldbook as wb
    from iterator_pet.platform.workshop import Workshop

    workshop = Workshop(book=wb.Worldbook(tmp_path / "worldbook.json"),
                        folder=tmp_path)
    titles = [workshop.tabs.tabText(i) for i in range(workshop.tabs.count())]

    assert titles == ["人偶外观", "本地对话", "角色卡", "世界书"]
    workshop.close()


def test_the_menu_labels_were_reorganised():
    """菜单整理（用户 2026-10-31）：

    * 「角色」→「当前角色」；
    * 「自定义对话」入口删除（已收进工坊）；
    * 「使用大语言模型」删除（设置页里已有）；
    * 「对话」删除（歧义），改成「本地对话」页顶部的勾选框。
    """
    window = (REPO / "src" / "iterator_pet" / "platform" / "pet_window.py").read_text(
        encoding="utf-8")
    tray = (REPO / "src" / "iterator_pet" / "platform" / "tray.py").read_text(
        encoding="utf-8")

    for text in (window, tray):
        assert 'addMenu("当前角色")' in text
        assert 'addMenu("角色")' not in text
        assert "自定义对话" not in text
        assert "使用大语言模型" not in text
        assert 'QAction("对话"' not in text
        assert "def open_dialogue_editor" not in text
