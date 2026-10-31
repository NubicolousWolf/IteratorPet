"""v0.1.9 工作室（人偶外观 / 角色卡 / 世界书）的测试。

这个版本卖的是"**真的能读写**"，所以测试的重心不是界面长什么样，而是四件会被玩家
当场发现的事：

1. 世界书存下去**回来还是那份**（含未知键、越界值被夹）；
2. 坏 JSON **不会**因为我们点了一下保存就被覆盖掉（除非明确 force）；
3. 保存角色卡**不冲掉** `extensions`（外观草稿就住在里面）；
4. 像素画能序列化/反序列化，颜色过多时说人话，不是静默丢色。

再加一层"能开起来"的冒烟：三个标签页构造、切角色、保存两个文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.llm import cards as llm_cards  # noqa: E402
from iterator_pet.llm import worldbook as wb  # noqa: E402
from iterator_pet.platform.puppet_tab import PuppetTab  # noqa: E402
from iterator_pet.rendering.pixel_art import PixelArt  # noqa: E402
from iterator_pet.simulation import appearance as ap  # noqa: E402

APP = QApplication.instance() or QApplication([])

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 世界书写回
# --------------------------------------------------------------------------
def _book_file(tmp_path: Path, **extra) -> Path:
    payload = {
        "version": 1,
        "scan_depth": 4,
        "token_budget": 800,
        "recursive": 1,
        "match_whole_words": False,
        "entries": [
            {"id": "a", "comment": "旧", "keys": ["卵石"], "content": "正文A",
             "future_field": 42},
            {"id": "b", "keys": ["皓月"], "content": "正文B"},
        ],
    }
    payload.update(extra)
    path = tmp_path / "worldbook.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_worldbook_save_round_trips_the_edited_entry(tmp_path):
    path = _book_file(tmp_path)
    book = wb.Worldbook(path).load()
    book.entries[0].content = "改过的A"
    assert book.save() is True
    assert book.last_error == ""

    again = wb.Worldbook(path).load()
    assert again.entries[0].content == "改过的A"
    assert again.entries[1].content == "正文B", "没改的那条不许被动"


def test_worldbook_save_keeps_unknown_keys_at_both_levels(tmp_path):
    """顶层未知键、条目未知字段都要活着 —— 未来版本/玩家手写的东西不能被我们吃掉。"""
    path = _book_file(tmp_path, future_top="保留我")
    book = wb.Worldbook(path).load()
    book.entries.append(wb.Entry(id="c", comment="新", keys=("红日",), content="正文C"))
    assert book.save() is True

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["future_top"] == "保留我"
    assert document["entries"][0]["future_field"] == 42
    assert document["entries"][2]["id"] == "c"
    assert document["entries"][2]["selective_logic"] == "AND_ANY", "写回用文件约定的键名"


def test_worldbook_save_clamps_out_of_range_numbers(tmp_path):
    path = _book_file(tmp_path)
    book = wb.Worldbook(path).load()
    book.scan_depth = 999
    book.token_budget = -5
    book.entries[0].probability = 500
    book.entries[0].order = 10 ** 6
    assert book.save() is True

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["scan_depth"] == wb.SCAN_DEPTH_LIMIT
    assert document["token_budget"] == 0
    assert document["entries"][0]["probability"] == 100
    assert document["entries"][0]["order"] == wb.ORDER_LIMIT


def test_a_broken_worldbook_is_not_overwritten_without_permission(tmp_path):
    path = tmp_path / "worldbook.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    book = wb.Worldbook(path)
    book.entries = [wb.Entry(id="x", content="y")]

    assert book.save() is False, "非空坏文件必须停下来问"
    assert path.read_text(encoding="utf-8") == "{ 这不是 json", "原文不许被吃掉"
    assert "JSON" in book.last_error

    assert book.save(force=True) is True, "玩家确认之后才允许覆盖"
    assert path.with_suffix(".json.bak").is_file(), "覆盖前要留一份 .bak"


# --------------------------------------------------------------------------
# 角色卡写回
# --------------------------------------------------------------------------
def test_saving_a_card_never_touches_extensions(tmp_path):
    directory = tmp_path / "characters"
    directory.mkdir(parents=True)
    path = directory / "pebbles.json"
    path.write_text(json.dumps({
        "spec": "chara_card_v2",
        "name": "五块卵石",
        "system_prompt": "旧提示词",
        "extensions": {"rainworld": {"oracle_id": "SS",
                                     "appearance": {"skin": [1, 2, 3]}}},
        "future_key": "别动我",
    }, ensure_ascii=False), encoding="utf-8")

    card, error = llm_cards.read_card("pebbles", folder=tmp_path)
    assert error == ""
    card.system_prompt = "新提示词"
    assert llm_cards.save_card("pebbles", card, folder=tmp_path) == ""

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["system_prompt"] == "新提示词"
    assert document["extensions"]["rainworld"]["appearance"] == {"skin": [1, 2, 3]}
    assert document["extensions"]["rainworld"]["oracle_id"] == "SS"
    assert document["future_key"] == "别动我"


def test_reading_a_missing_card_gives_an_empty_card_not_an_error(tmp_path):
    card, error = llm_cards.read_card("nobody", folder=tmp_path)
    assert error == ""
    assert card.name == "" and card.system_prompt == ""


def test_cards_are_listed_with_builtins_first(tmp_path):
    directory = tmp_path / "characters"
    directory.mkdir(parents=True)
    (directory / "zzz_custom.json").write_text(
        json.dumps({"name": "自建"}, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "pebbles.json").write_text(
        json.dumps({"name": "五块卵石"}, ensure_ascii=False), encoding="utf-8"
    )
    refs = llm_cards.list_cards(tmp_path)
    keys = [ref.key for ref in refs]
    assert keys[:2] == ["pebbles", "past_moon"], "内置两位永远在最前面"
    assert keys[2] == "zzz_custom"
    assert refs[0].builtin is True and refs[2].builtin is False
    assert refs[2].name == "自建", "菜单标签取卡里的 name"


def test_appearance_draft_can_be_written_read_and_deleted(tmp_path):
    assert llm_cards.save_appearance("pebbles", {"skin": [9, 9, 9]}, folder=tmp_path) == ""
    assert llm_cards.read_appearance("pebbles", folder=tmp_path)["skin"] == [9, 9, 9]

    card, _ = llm_cards.read_card("pebbles", folder=tmp_path)
    card.name = "改个名"
    assert llm_cards.save_card("pebbles", card, folder=tmp_path) == ""
    assert llm_cards.read_appearance("pebbles", folder=tmp_path)["skin"] == [9, 9, 9], \
        "存卡不能把外观冲掉"

    assert llm_cards.save_appearance("pebbles", None, folder=tmp_path) == ""
    assert llm_cards.read_appearance("pebbles", folder=tmp_path) == {}
    document = json.loads((tmp_path / "characters" / "pebbles.json").read_text(encoding="utf-8"))
    assert document["name"] == "改个名", "删外观不能动卡上的文字"


# --------------------------------------------------------------------------
# 像素画
# --------------------------------------------------------------------------
def test_pixel_art_round_trips_through_json():
    art = PixelArt.blank()
    art.set(3, 4, "#ff0000")
    art.set(4, 4, "#00ff00")
    art.set(5, 4, "#ff0000")
    document = art.to_dict()
    assert document["w"] == 24 and document["h"] == 20
    assert document["palette"] == ["#ff0000", "#00ff00"], "按首次出现的顺序去重"
    assert PixelArt.from_dict(document).to_dict() == document
    assert PixelArt.from_dict(document).filled() == 3


def test_pixel_art_refuses_silently_losing_colours():
    art = PixelArt.blank()
    for index in range(37):                      # 36 色是字符表上限
        art.set(index % 24, index // 24, f"#{index:02x}0000")
    with pytest.raises(ValueError):
        art.to_dict()


def test_pixel_art_survives_garbage():
    assert PixelArt.from_dict(None) is None
    assert PixelArt.from_dict({"w": 0, "h": 20, "palette": [], "grid": []}) is None
    assert PixelArt.from_dict({"w": 4, "h": 2, "palette": ["#zzzzzz"], "grid": ["aaaa", "aaaa"]}) is None
    art = PixelArt.from_dict({"w": 3, "h": 2, "palette": ["#ffffff"],
                              "grid": ["0.9", "000"]})
    assert art is not None and art.get(0, 0) == "#ffffff" and art.get(2, 0) is None


# --------------------------------------------------------------------------
# 外观合成
# --------------------------------------------------------------------------
def test_build_appearance_without_override_is_the_source_instance():
    assert ap.build_appearance("pebbles", "pebbles", {}) is ap.PEBBLES
    assert ap.build_appearance("past_moon", "past_moon", None) is ap.PAST_MOON
    custom = ap.build_appearance("srs", "past_moon", {})
    assert custom.key == "srs" and custom.skin == ap.PAST_MOON.skin


def test_build_appearance_applies_colours_and_two_point_gown():
    appearance = ap.build_appearance("x", "pebbles", {
        "skin": "#112233", "eye": [255, 0, 0], "halo": "ff6600",
        "halo_spark": "#00ffff", "gown_top": "#ffffff", "gown_bottom": "#000000",
    })
    assert appearance.skin == (17, 34, 51)
    assert appearance.eye == (255, 0, 0)
    assert appearance.halo == (255, 102, 0)
    assert appearance.halo_spark == (0, 255, 255)
    assert appearance.gown(0.0) == (255, 255, 255)
    assert appearance.gown(1.0) == (0, 0, 0)
    assert appearance.gown(0.5) == (127, 127, 127)


def test_build_appearance_turns_the_pixel_headpiece_on_and_the_third_eye_off():
    art = PixelArt.blank()
    art.set(10, 10, "#ff00ff")
    appearance = ap.build_appearance("x", "past_moon", {
        "headpiece_mode": "pixel", "headpiece_pixels": art.to_dict(),
        "headpiece_scale": 9.0,
    })
    assert appearance.headpiece is not None and appearance.headpiece.filled() == 1
    assert appearance.third_eye is False, "画了自己的头饰就别再叠一个圆点"
    assert appearance.headpiece_scale == 2.0, "缩放要夹在 0.2–2.0"

    dot = ap.build_appearance("x", "pebbles", {"headpiece_mode": "builtin_dot"})
    assert dot.third_eye is True and dot.headpiece is None
    assert dot.third_eye_colour == ap.default_third_eye_colour(ap.PEBBLES.skin)


def test_builtin_spark_colour_equals_what_the_renderer_drew_before_it_was_wired():
    """接通 `halo_spark` 之前渲染层画的是 ``int(halo * 1.05)``；默认值必须和它一致，
    否则这一版会悄悄改变两个角色的观感。"""
    for appearance in (ap.PEBBLES, ap.PAST_MOON):
        expected = tuple(min(255, int(channel * 1.05)) for channel in appearance.halo)
        assert appearance.halo_spark == expected


# --------------------------------------------------------------------------
# 画板
# --------------------------------------------------------------------------
def test_pixel_canvas_paints_erases_and_mirrors():
    from iterator_pet.platform.puppet_tab import PixelCanvas

    canvas = PixelCanvas(None, cell=8)
    canvas.colour = "#abcdef"
    canvas._paint(3, 5)
    assert canvas.art.get(3, 5) == "#abcdef"

    canvas.symmetric = True
    canvas._paint(4, 5)
    assert canvas.art.get(canvas.art.w - 1 - 4, 5) == "#abcdef", "对称开启时另一边也要有"

    canvas.erasing = True
    canvas._paint(3, 5)
    assert canvas.art.get(3, 5) is None

    before = canvas.art.filled()
    canvas.push_undo()
    canvas.art.set(0, 0, "#000000")
    canvas.undo()
    assert canvas.art.filled() == before, "撤销回到落笔前"


# --------------------------------------------------------------------------
# 三个标签页 + 保存
# --------------------------------------------------------------------------
def test_workshop_opens_with_three_tabs_and_saves_both_files(tmp_path):
    from iterator_pet.platform.workshop import Workshop

    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "pebbles.json").write_text(
        json.dumps({"spec": "chara_card_v2", "name": "五块卵石",
                    "extensions": {"rainworld": {"oracle_id": "SS"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    book_path = _book_file(tmp_path)
    workshop = Workshop(book=wb.Worldbook(book_path), folder=tmp_path)

    assert [workshop.tabs.tabText(i) for i in range(workshop.tabs.count())] == [
        "人偶外观", "角色卡", "世界书"
    ]
    assert workshop.puppet_tab.preview._snapshot is not None, "预览必须有真实快照"
    assert workshop.new_button.isEnabled() is False, "v0.1.9 先置灰"
    assert workshop.delete_button.isEnabled() is False

    workshop.puppet_tab.buttons["skin"].set_colour("#123456")
    workshop.puppet_tab._on_colour()
    workshop.card_tab._editors["system_prompt"].setPlainText("新的系统提示词")
    workshop.save()

    document = json.loads((tmp_path / "characters" / "pebbles.json").read_text(encoding="utf-8"))
    assert document["extensions"]["rainworld"]["appearance"]["skin"] == [18, 52, 86]
    assert document["system_prompt"] == "新的系统提示词"
    assert document["extensions"]["rainworld"]["oracle_id"] == "SS"
    workshop.close()


def test_untouched_puppet_tab_does_not_write_an_appearance_block(tmp_path):
    """一个色都没改就点保存 → **不留覆盖层**。

    内置两位的默认值必须永远来自 `appearance.py`（那里有源码行号）。在卡里存一份
    "和源码一模一样"的副本，就成了第二份真相：以后我们改默认值会被玩家文件盖住。
    """
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    assert tab.save() == ""
    assert llm_cards.read_appearance("pebbles", folder=tmp_path) == {}


def test_puppet_tab_translates_a_stored_draft_back_into_widgets(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    llm_cards.save_appearance("pebbles", {
        "base_template": "past_moon",
        "skin": [10, 20, 30],
        "headpiece_mode": "builtin_dot",
    }, folder=tmp_path)

    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    assert tab.base_template() == "past_moon"
    assert tab.buttons["skin"].colour() == "#0a141e"
    assert tab.headpiece_mode() == "builtin_dot"
    draft = tab.collect()
    assert draft["skin"] == [10, 20, 30]
    assert draft["base_template"] == "past_moon"


# --------------------------------------------------------------------------
# 画笔选色（第一版漏了：画板只有一个写死的默认色）
# --------------------------------------------------------------------------
def test_the_brush_colour_reaches_the_canvas(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.brush_colour.set_colour("#ff0000")
    tab._on_brush_colour("#ff0000")
    assert tab.current_canvas().colour == "#ff0000"
    for canvas in tab.canvases.values():
        assert canvas.colour == "#ff0000", "三个画板共用画笔颜色"


def test_the_eyedropper_takes_the_colour_from_the_pixel():
    from iterator_pet.platform.puppet_tab import PixelCanvas

    canvas = PixelCanvas(None, cell=8)
    canvas.art.set(2, 2, "#00ff88")
    picked: list[str] = []
    canvas.picked.connect(picked.append)
    canvas._pick(2, 2)
    assert canvas.colour == "#00ff88"
    assert picked == ["#00ff88"]

    canvas._pick(0, 0)                      # 空白处不该改色
    assert canvas.colour == "#00ff88"


def test_recent_colours_show_up_after_using_them(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab._use_colour("#123456")
    assert "#123456" in tab._recent
    assert tab.brush_colour.colour() == "#123456"


# --------------------------------------------------------------------------
# 天线与衣袍画板
# --------------------------------------------------------------------------
def test_antenna_and_gown_boards_round_trip_through_the_card(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.load("pebbles")

    antenna = tab.canvases["antenna"].art
    antenna.set(1, 1, "#ff0000")
    tab.antenna_enabled.setChecked(True)

    gown = tab.canvases["gown"].art
    gown.set(5, 5, "#00ff00")
    tab.gown_enabled.setChecked(True)
    tab._mark_dirty()

    assert tab.save() == ""
    stored = llm_cards.read_appearance("pebbles", folder=tmp_path)
    assert "antenna_pixels" in stored and "gown_pixels" in stored

    other = PuppetTab(tmp_path)
    other.load("pebbles")
    assert other.antenna_enabled.isChecked() and other.gown_enabled.isChecked()
    assert other.canvases["antenna"].art.get(1, 1) == "#ff0000"
    assert other.canvases["gown"].art.get(5, 5) == "#00ff00"


def test_boards_that_are_off_do_not_get_saved(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    tab.canvases["gown"].art.set(3, 3, "#ffffff")     # 画了但开关没开
    tab._mark_dirty()
    assert tab.save() == ""
    stored = llm_cards.read_appearance("pebbles", folder=tmp_path)
    assert "gown_pixels" not in stored


def test_appearance_carries_the_antenna_and_gown_pixels():
    antenna = PixelArt.blank(12, 24)
    antenna.set(2, 3, "#abcdef")
    gown = PixelArt.blank(11, 11)
    gown.set(1, 1, "#123123")

    appearance = ap.build_appearance("x", "pebbles", {
        "antenna_pixels": antenna.to_dict(), "antenna_scale": 0.9,
        "gown_pixels": gown.to_dict(),
        "pod_colour": "#101010", "pod_rim_colour": "#202020",
        "antenna_colour": "#303030",
    })
    assert appearance.antenna_art is not None
    assert appearance.antenna_scale == 0.9
    assert appearance.gown_art is not None
    assert appearance.pod_colour == (16, 16, 16)
    assert appearance.antenna_colour == (48, 48, 48)


def test_the_renderer_draws_all_three_custom_boards_without_blowing_up():
    """三个画板一起开、真的画一遍 —— 预览和桌面走的是同一批函数。"""
    from PySide6.QtGui import QImage, QPainter, QColor
    from iterator_pet.rendering import renderer as renderer
    from iterator_pet.simulation import constants as constants
    from iterator_pet.simulation.state import SimulationState

    head = PixelArt.blank()
    head.set(11, 2, "#ffcc00")
    antenna = PixelArt.blank(12, 24)
    for y in range(4, 20):
        antenna.set(6, y, "#88ccff")
    gown = PixelArt.blank(11, 11)
    for y in range(11):
        for x in range(11):
            gown.set(x, y, "#3366aa" if (x + y) % 2 else "#224488")

    sim = SimulationState((0.0, 0.0, 260.0, 300.0), warmup=0, character="pebbles")
    for _ in range(20):
        sim.step(1.0 / constants.GAME_HZ)
    appearance = ap.build_appearance("x", "pebbles", {
        "headpiece_mode": "pixel", "headpiece_pixels": head.to_dict(),
        "antenna_pixels": antenna.to_dict(), "gown_pixels": gown.to_dict(),
    })
    import dataclasses

    state = dataclasses.replace(sim.interpolate(1.0), appearance=appearance)
    image = QImage(300, 340, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    renderer.draw_gown(painter, state)
    renderer.draw_puppet_head(painter, state)
    painter.end()
    assert not image.isNull()

