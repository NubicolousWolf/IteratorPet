"""「自定义对话」编辑器的测试（离屏 Qt，不弹真窗口）。

三件事必须钉住：
1. 打开时按默认组显示（pebbles × drag = 3 行）；
2. 切换事件/角色时，**之前那组的改动不能丢**（draft 机制）；
3. 保存后文件被原子改写、只动对应组、并且立刻热重载（`pool()` 马上能读到）；
4. 宠物窗口暴露的是 GUI 入口，不再是"靠 .json 文件关联"的 `edit_lines_file`。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.platform.dialogue_editor import DialogueEditor  # noqa: E402
from iterator_pet.simulation import constants as C  # noqa: E402
from iterator_pet.simulation.lines import Lines  # noqa: E402

APP = QApplication.instance() or QApplication([])

REPO = Path(__file__).resolve().parents[1]


def _editor(tmp_path) -> tuple[DialogueEditor, Lines]:
    lines = Lines(path=tmp_path / "lines.json")
    return DialogueEditor(lines), lines


def test_editor_opens_on_the_default_group(tmp_path):
    editor, _ = _editor(tmp_path)
    assert editor.list.count() == 4, "默认显示 pebbles × drag 的四条"
    assert editor.event_box.currentData() == "drag"
    assert editor.character_box.currentData() == "pebbles"
    editor.close()


def test_editor_keeps_previous_group_when_switching(tmp_path):
    editor, _ = _editor(tmp_path)
    editor.list.addItem("临时加的一句")
    editor.event_box.setCurrentIndex(1)                     # 切到 talk
    assert editor.list.count() == 1, "talk 组默认只有一句「……」"
    editor.event_box.setCurrentIndex(0)                     # 切回 drag
    texts = [editor.list.item(i).text() for i in range(editor.list.count())]
    assert "临时加的一句" in texts, "切换分组不能把刚才的改动丢掉"
    editor.close()


def test_editor_saves_only_the_edited_group_and_hot_reloads(tmp_path):
    editor, lines = _editor(tmp_path)
    editor.event_box.setCurrentIndex(1)                     # talk
    editor.add_line()
    editor.list.item(editor.list.count() - 1).setText("我还在。")
    editor.save()
    assert lines.pool("pebbles", "talk") == ["……", "我还在。"], "保存后应立刻热重载"
    assert lines.pool("pebbles", "drag") == [
        "滚开。", "别烦我。", C.DIALOGUE_SILENT_ENTRY, "你没有更重要的事可做吗？"
    ], "别的组不能被动"
    assert lines.pool("past_moon", "talk") == ["你还在这里吗？"], "别的角色不能被动"
    editor.close()


def test_editor_survives_a_broken_file(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    lines = Lines(path=path)
    editor = DialogueEditor(lines)
    assert editor.list.count() >= 1, "坏文件时应当退回内置默认，而不是空白"
    editor.close()


def test_pet_window_uses_the_gui_entry_not_the_file_association():
    text = (REPO / "src" / "iterator_pet" / "platform" / "pet_window.py").read_text(encoding="utf-8")
    assert "def open_dialogue_editor" in text
    assert "def edit_lines_file" not in text, "os.startfile 那条路在没关联的机器上没反应，已删除"
    assert "自定义对话" in text
