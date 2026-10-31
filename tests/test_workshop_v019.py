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
from iterator_pet.platform.puppet_tab import BOARDS, BOARD_SIZES, PuppetTab  # noqa: E402
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


def test_worldbook_save_round_trips_keeps_unknown_keys_and_clamps(tmp_path):
    """存回去之后：改过的正文在、没改的不动、未知键活着、越界值被夹。

    合并理由：这四条都在验 `Worldbook.save()` 的同一个承诺——“写出去的东西玩家还能读回来”。
    """
    path = _book_file(tmp_path, future_top="保留我")
    book = wb.Worldbook(path).load()
    book.entries[0].content = "改过的A"
    book.entries.append(wb.Entry(id="c", comment="新", keys=("红日",), content="正文C"))
    book.scan_depth = 999
    book.token_budget = -5
    book.entries[0].probability = 500
    book.entries[0].order = 10 ** 6
    assert book.save() is True and book.last_error == ""

    again = wb.Worldbook(path).load()
    assert again.entries[0].content == "改过的A", "改过的要写进去"
    assert again.entries[1].content == "正文B", "没改的不许被动"

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["future_top"] == "保留我", "顶层未知键要活着"
    assert document["entries"][0]["future_field"] == 42, "条目未知字段也要活着"
    assert document["entries"][2]["selective_logic"] == "AND_ANY", "写回用文件约定的键名"
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
def test_pixel_art_round_trips_and_survives_garbage():
    """序列化往返一致；任何垃圾输入都退化成 `None`/透明，绝不抛异常。
    """
    art = PixelArt.blank()
    art.set(3, 4, "#ff0000")
    art.set(4, 4, "#00ff00")
    art.set(5, 4, "#ff0000")
    document = art.to_dict()
    assert document["w"] == 24 and document["h"] == 20
    assert document["palette"] == ["#ff0000", "#00ff00"], "按首次出现的顺序去重"
    assert PixelArt.from_dict(document).to_dict() == document
    assert PixelArt.from_dict(document).filled() == 3

    # 垃圾输入：坏尺寸、坏色号、坏字符、缺行，全部不抛
    assert PixelArt.from_dict(None) is None
    assert PixelArt.from_dict({"w": 0, "h": 20, "palette": [], "grid": []}) is None
    assert PixelArt.from_dict({"w": 4, "h": 2, "palette": ["#zzzzzz"], "grid": ["aaaa", "aaaa"]}) is None
    partial = PixelArt.from_dict({"w": 3, "h": 2, "palette": ["#ffffff"], "grid": ["0.9", "000"]})
    assert partial is not None and partial.get(0, 0) == "#ffffff"
    assert partial.get(2, 0) is None, "认不出的字符当透明，不整张丢弃"


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
def test_build_appearance_overlays_colours_onto_the_chosen_base():
    """无覆盖 = 源码实例；有覆盖 = 色值/两点袍色/基底继承都对。
    """
    assert ap.build_appearance("pebbles", "pebbles", {}) is ap.PEBBLES
    assert ap.build_appearance("past_moon", "past_moon", None) is ap.PAST_MOON
    custom = ap.build_appearance("srs", "past_moon", {})
    assert custom.key == "srs" and custom.skin == ap.PAST_MOON.skin, "空覆盖也要认得基底的骨头"

    appearance = ap.build_appearance("x", "pebbles", {
        "skin": "#112233", "eye": [255, 0, 0], "halo": "ff6600",
        "halo_spark": "#00ffff", "gown_top": "#ffffff", "gown_bottom": "#000000",
    })
    assert appearance.skin == (17, 34, 51)
    assert appearance.eye == (255, 0, 0)
    assert appearance.halo == (255, 102, 0)
    assert appearance.halo_spark == (0, 255, 255), "电光要能独立于光环"
    assert appearance.gown(0.0) == (255, 255, 255)
    assert appearance.gown(1.0) == (0, 0, 0)
    assert appearance.gown(0.5) == (127, 127, 127), "两点之间线性插值"


def test_build_appearance_turns_the_pixel_headpiece_on():
    """画了像素头饰就装上；缩放夹在 0.2–2.0；选了但画布是空的当作没开。
    """
    art = PixelArt.blank()
    art.set(10, 10, "#ff00ff")
    appearance = ap.build_appearance("x", "past_moon", {
        "headpiece_mode": "pixel", "headpiece_pixels": art.to_dict(),
        "headpiece_scale": 9.0,
    })
    assert appearance.headpiece is not None and appearance.headpiece.filled() == 1
    assert appearance.headpiece_scale == 2.0, "缩放要夹在 0.2–2.0"

    empty = ap.build_appearance("x", "pebbles", {"headpiece_mode": "pixel"})
    assert empty.headpiece is None, "选了自定义但没画东西 → 当作没开"


def test_headpiece_modes_inherit_none_and_pixel():
    """三种模式各管一件事（用户 2026-10-31 澄清的分工）：

    * ``inherit``：跟随基底 —— 皓月有那颗第三只眼（`og.cs:2238-2254`），卵石没有；
    * ``none``：明确不要 —— **自定义角色套皓月模板时的默认值**，不该白得一颗圆点；
    * ``pixel``：玩家自己画。
    """
    inherit_moon = ap.build_appearance("x", "past_moon", {"headpiece_mode": "inherit"})
    assert inherit_moon.third_eye is True, "皓月本人的第三只眼必须留着"
    assert inherit_moon.headpiece is None
    inherit_pebbles = ap.build_appearance("x", "pebbles", {"headpiece_mode": "inherit"})
    assert inherit_pebbles.third_eye is False, "卵石本来就没有"

    none_moon = ap.build_appearance("x", "past_moon", {"headpiece_mode": "none"})
    assert none_moon.third_eye is False, "「无」就是连第三只眼也不要"
    assert none_moon.headpiece is None

    assert ap.PAST_MOON.third_eye is True, "仰望皓月自己的数据不许被改掉"
    assert ap.PEBBLES.third_eye is False

    art = PixelArt.blank()
    art.set(11, 2, "#ffcc00")
    pixel = ap.build_appearance("x", "past_moon", {
        "headpiece_mode": "pixel", "headpiece_pixels": art.to_dict(),
    })
    assert pixel.headpiece is not None and pixel.third_eye is False, "自己画了就别叠圆点"


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
    # v0.2.0 解锁了新建/复制；删除只对自定义角色开放（内置两位是源码数值，删了也还在）
    assert workshop.new_button.isEnabled() is True
    assert workshop.copy_button.isEnabled() is True
    assert workshop.delete_button.isEnabled() is False, "内置角色不能删"

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
    art = PixelArt.blank()
    art.set(11, 3, "#ffcc00")
    llm_cards.save_appearance("pebbles", {
        "base_template": "past_moon",
        "skin": [10, 20, 30],
        "headpiece_mode": "pixel",
        "headpiece_pixels": art.to_dict(),
    }, folder=tmp_path)

    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    assert tab.base_template() == "past_moon"
    assert tab.buttons["skin"].colour() == "#0a141e"
    assert tab.headpiece_mode() == "pixel"
    assert tab.canvases["headpiece"].art.get(11, 3) == "#ffcc00"
    draft = tab.collect()
    assert draft["skin"] == [10, 20, 30]
    assert draft["base_template"] == "past_moon"


# --------------------------------------------------------------------------
# 画笔选色（第一版漏了：画板只有一个写死的默认色）
# --------------------------------------------------------------------------
def test_the_brush_colour_reaches_every_canvas_and_can_be_picked(tmp_path):
    """画笔色要传到三块画板；吸管能把画布上的颜色吸回来；用过的色进"最近"。
    """
    from iterator_pet.platform.puppet_tab import PixelCanvas

    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.brush_colour.set_colour("#ff0000")
    tab._on_brush_colour("#ff0000")
    for canvas in tab.canvases.values():
        assert canvas.colour == "#ff0000", "三块画板共用画笔颜色"

    picker = PixelCanvas(None, cell=8)
    picker.art.set(2, 2, "#00ff88")
    picked: list[str] = []
    picker.picked.connect(picked.append)
    picker._pick(2, 2)
    assert picker.colour == "#00ff88" and picked == ["#00ff88"]
    picker._pick(0, 0)
    assert picker.colour == "#00ff88", "空白处不该改色"

    tab._use_colour("#123456")
    assert "#123456" in tab._recent
    assert tab.brush_colour.colour() == "#123456"


def test_antenna_board_round_trips_through_the_card(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.load("pebbles")

    antenna = tab.canvases["antenna"].art
    antenna.set(1, 1, "#ff0000")
    tab.antenna_enabled.setChecked(True)
    tab._mark_dirty()

    assert tab.save() == ""
    stored = llm_cards.read_appearance("pebbles", folder=tmp_path)
    assert "antenna_pixels" in stored

    other = PuppetTab(tmp_path)
    other.load("pebbles")
    assert other.antenna_enabled.isChecked()
    assert other.canvases["antenna"].art.get(1, 1) == "#ff0000"


def test_a_drawn_antenna_is_not_saved_while_the_switch_is_off(tmp_path):
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    tab.canvases["antenna"].art.set(3, 3, "#ffffff")   # 画了但开关没开
    tab._mark_dirty()
    assert tab.save() == ""
    stored = llm_cards.read_appearance("pebbles", folder=tmp_path)
    assert "antenna_pixels" not in stored


def test_the_gown_board_is_gone():
    """衣袍画板做过又删了（用户 2026-10-31：效果不好）。别让它悄悄回来。"""
    from iterator_pet.platform.puppet_tab import BOARDS

    keys = [board[0] for board in BOARDS]
    assert keys == ["headpiece", "antenna"]
    assert not hasattr(ap.PEBBLES, "gown_art"), "渲染层的纹理分支也一起删干净"


def test_each_canvas_matches_its_board_size(tmp_path):
    """控件尺寸必须由画布数据尺寸推出来。

    2026-10-31 用户报「天线轮廓只露出最左边一部分」：构造时给的是 24×20 的空白画布，
    却把控件 setFixedSize 成 8×24 的尺寸 —— 控件只有 88 px 宽，paintEvent 却按
    24 格 × 11 px = 264 px 画，右边三分之二被裁掉。这条断言就是那个 bug 的网。
    """
    (tmp_path / "characters").mkdir(parents=True)
    tab = PuppetTab(tmp_path)
    for key, _title, width, height, cell, _ref in BOARDS:
        canvas = tab.canvases[key]
        assert (canvas.art.w, canvas.art.h) == (width, height), f"{key} 的画布数据尺寸不对"
        assert (canvas.width(), canvas.height()) == (width * cell, height * cell), \
            f"{key} 的控件尺寸和画布尺寸对不上"


def test_a_stored_art_of_the_wrong_size_is_fitted_to_the_board(tmp_path):
    """存档里留着旧尺寸（或玩家手改过 w/h）时不许把画板撑变形。

    两种画板的对齐规则不同：头顶画板对称 → 左上对齐；天线画板偏心 →
    **按耳麦圆心对齐**（见 `test_a_legacy_antenna_art_lands_on_the_same_pixels_after_the_board_grew`）。
    """
    (tmp_path / "characters").mkdir(parents=True)
    stale_head = PixelArt.blank(24, 20)          # 顶层尺寸不对，但内容能左上对齐
    stale_head.set(1, 1, "#ff0000")
    legacy_antenna = PixelArt.blank(12, 29)      # 第一版的天线画板
    legacy_antenna.set(6, 22, "#00ff00")
    llm_cards.save_appearance("pebbles", {
        "antenna_pixels": legacy_antenna.to_dict(),
        "headpiece_pixels": stale_head.to_dict(),
    }, folder=tmp_path)

    tab = PuppetTab(tmp_path)
    tab.load("pebbles")
    assert (tab.canvases["antenna"].art.w, tab.canvases["antenna"].art.h) == BOARD_SIZES["antenna"]
    assert tab.canvases["antenna"].art.get(6, 24) == "#00ff00", "按圆心对齐，内容别丢"
    assert (tab.canvases["headpiece"].art.w, tab.canvases["headpiece"].art.h) == (24, 20)
    assert tab.canvases["headpiece"].art.get(1, 1) == "#ff0000"


def test_both_boards_carry_a_reference_outline():
    """参考线曾经整个消失过：`_paint_reference` 判的是 "head"，而构造时传的是 "headpiece"。

    这里钉的是**每个画板都有非空的参考线类型**，而且两张图确实画出了东西。
    """
    from PySide6.QtGui import QImage, QPainter, QColor

    kinds = {board[0]: board[5] for board in BOARDS}
    assert kinds == {"headpiece": "head", "antenna": "antenna"}

    for key, kind in kinds.items():
        from iterator_pet.platform.puppet_tab import PixelCanvas

        canvas = PixelCanvas(None, cell=6, reference=kind)
        image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        canvas._paint_reference(painter)
        painter.end()
        coloured = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).blue() > 30 or image.pixelColor(x, y).red() > 190
        )
        assert coloured > 20, f"{key} 画板的参考线没画出来"


def test_appearance_carries_the_antenna_pixels():
    antenna = PixelArt.blank(8, 24)
    antenna.set(2, 3, "#abcdef")

    appearance = ap.build_appearance("x", "pebbles", {
        "antenna_pixels": antenna.to_dict(), "antenna_scale": 0.9,
        "pod_colour": "#101010", "pod_rim_colour": "#202020",
        "antenna_colour": "#303030",
    })
    assert appearance.antenna_art is not None
    assert appearance.antenna_scale == 0.9
    assert appearance.pod_colour == (16, 16, 16)
    assert appearance.antenna_colour == (48, 48, 48)


def test_the_renderer_draws_both_custom_boards_without_blowing_up():
    """两个画板一起开、真的画一遍 —— 预览和桌面走的是同一批函数。"""
    from PySide6.QtGui import QImage, QPainter, QColor
    from iterator_pet.rendering import renderer as renderer
    from iterator_pet.simulation import constants as constants
    from iterator_pet.simulation.state import SimulationState

    head = PixelArt.blank()
    head.set(11, 2, "#ffcc00")
    antenna = PixelArt.blank(*ap.antenna_board_size(0.5))
    for y in range(8, 26):
        antenna.set(antenna.w // 2, y, "#88ccff")

    sim = SimulationState((0.0, 0.0, 260.0, 300.0), warmup=0, character="pebbles")
    for _ in range(20):
        sim.step(1.0 / constants.GAME_HZ)
    appearance = ap.build_appearance("x", "pebbles", {
        "headpiece_mode": "pixel", "headpiece_pixels": head.to_dict(),
        "antenna_pixels": antenna.to_dict(),
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


def test_the_antenna_board_covers_from_the_tip_to_the_chin_and_inward():
    """画板要装得下：触角尖（上）、耳麦外缘（左）、**下巴（下）、脑袋中线（右）**。

    2026-10-31 用户第三条：原来的 12×29 只够画那根杆，想画贴着下巴/脸颊的须子根本没地方。
    现在把"半个脑袋"也划进可画范围，圆心因此**偏左上**（6, 24）而不是画布中心。
    """
    from iterator_pet.platform.puppet_tab import BOARD_ANCHORS

    cell = 0.5
    width, height = BOARD_SIZES["antenna"]
    assert (width, height) == ap.antenna_board_size(cell)
    col, row = BOARD_ANCHORS["antenna"]
    assert (col, row) == ap.antenna_board_anchor(cell), "锚点必须和尺寸同源"

    assert col >= ap.ANTENNA_POD_DIAMETER_PX * 0.5 / cell, "耳麦外半边不许出画布"
    assert row >= ap.ANTENNA_STALK_LENGTH_PX / cell, "触角尖不许出画布（尖在顶）"
    assert width - col >= ap.ANTENNA_HEAD_MIDLINE_PX / cell, "右侧必须够到脑袋中线"
    assert height - row >= ap.ANTENNA_CHIN_BOTTOM_PX / cell, "下侧必须够到下巴"


def test_a_legacy_antenna_art_lands_on_the_same_pixels_after_the_board_grew():
    """旧版 12×29 的图**不许**因为画板变大就整体漂一截：按耳麦圆心对齐迁移。

    第一版圆心在第 ``h-1-5.5 = 22.5`` 行、横向居中；新画板圆心在 (6, 24)。
    左上对齐会让整张图往下挪 1.5 格（= 0.75 仿真 px），玩家画好的天线会在桌面上跳一下。
    """
    from iterator_pet.platform.puppet_tab import BOARD_ANCHORS, fit_art

    legacy = PixelArt.blank(12, 29)
    legacy.set(6, 22, "#ff0000")          # 圆心所在那两行之一
    legacy.set(6, 0, "#00ff00")           # 触角尖

    fitted = fit_art("antenna", legacy)
    col, row = BOARD_ANCHORS["antenna"]

    assert (fitted.w, fitted.h) == BOARD_SIZES["antenna"]
    assert fitted.get(int(col), int(row)) == "#ff0000", "圆心附近的内容要落在新圆心上"
    assert fitted.get(int(col), int(row) - 22) == "#00ff00", "触角尖也要跟着搬"


def test_the_grid_and_reference_lines_never_paint_over_the_art():
    """用户 2026-10-31 第四条：画好的天线上浮着一层网格线。

    绘制顺序必须是"底色 → 棋盘 → 网格 → 参考线 → 像素"，所以任何一个落笔的格子
    **整格**都得正好是那个颜色。把网格/参考线挪回像素之前，这条会红。
    """
    from iterator_pet.platform.puppet_tab import BOARD_ANCHORS, PixelCanvas

    canvas = PixelCanvas(
        PixelArt.blank(*BOARD_SIZES["antenna"]), cell=9, reference="antenna",
        anchor=BOARD_ANCHORS["antenna"],
    )
    marks = {(6, 24): "#ff00ff", (10, 20): "#00ff00", (2, 34): "#00ffff"}
    for (x, y), colour in marks.items():
        canvas.art.set(x, y, colour)

    image = canvas.grab().toImage()
    blurry = []
    for (x, y), colour in marks.items():
        for dy in range(canvas.cell):
            for dx in range(canvas.cell):
                got = image.pixelColor(x * canvas.cell + dx, y * canvas.cell + dy).name()
                if got != colour:
                    blurry.append(f"({x},{y})+({dx},{dy})={got}")
    assert not blurry, "落笔的格子被辅助线描脏了：" + ", ".join(blurry[:5])


def test_symmetric_painting_mirrors_around_the_pod_not_the_canvas():
    """「左右对称」的镜像轴是**耳麦圆心**。用画布中心会把对称轴右移 4.5 格，
    在偏心的新画板上直接画出两只不对称的须子。"""
    from iterator_pet.platform.puppet_tab import BOARD_ANCHORS, PixelCanvas

    canvas = PixelCanvas(
        PixelArt.blank(*BOARD_SIZES["antenna"]), cell=9, reference="antenna",
        anchor=BOARD_ANCHORS["antenna"],
    )
    canvas.symmetric = True
    canvas._paint(8, 30)

    assert canvas.art.get(8, 30) == "#ff66cb"
    assert canvas.art.get(3, 30) == "#ff66cb", "关于圆心（第 6 列）镜像：8 → 3"
    assert canvas.art.get(21 - 8, 30) is None, "不是关于画布中心镜像"


def test_the_antenna_points_outward_not_inward():
    """画板上**顶部是触角尖、底部是内侧（下巴那一带）**，画到屏幕上必须是"尖朝外"。

    这块被"上下颠倒"坑了三次（2026-10-31 用户报了两次）。这里用像素距离直接量：
    第 0 行涂红、最后一行涂蓝，红点平均位置必须比蓝点离头更远。
    """
    from PySide6.QtGui import QImage, QPainter, QColor
    import dataclasses
    import math
    from iterator_pet.rendering import renderer as renderer
    from iterator_pet.simulation import constants as constants
    from iterator_pet.simulation.state import SimulationState

    art = PixelArt.blank(*ap.antenna_board_size(0.5))
    for x in range(art.w):
        art.set(x, 0, "#ff0000")                  # 顶行 = 尖
        art.set(x, art.h - 1, "#0000ff")          # 底行 = 耳麦

    sim = SimulationState((0.0, 0.0, 260.0, 300.0), warmup=0, character="pebbles")
    for _ in range(40):
        sim.step(1.0 / constants.GAME_HZ)
    appearance = ap.build_appearance("x", "pebbles", {
        "antenna_pixels": art.to_dict(), "antenna_scale": 0.5,
    })
    state = dataclasses.replace(sim.interpolate(1.0), look_dir=(0.0, 0.0),
                                appearance=appearance)
    width, height, zoom = 300, 360, 10.0
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    painter.translate(width * 0.5 - state.head[0] * zoom,
                      height * 0.5 - state.head[1] * zoom)
    painter.scale(zoom, zoom)
    renderer.draw_puppet_head(painter, state)
    painter.end()

    def average(match):
        points = [(x, y) for y in range(height) for x in range(width)
                  if match(image.pixelColor(x, y))]
        if not points:
            return None
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))

    red = average(lambda c: c.red() > 200 and c.green() < 60 and c.blue() < 60)
    blue = average(lambda c: c.blue() > 200 and c.red() < 60 and c.green() < 60)
    assert red is not None and blue is not None, "两端都得画出来才谈得上朝向"
    centre = (width * 0.5, height * 0.5)
    red_dist = math.hypot(red[0] - centre[0], red[1] - centre[1])
    blue_dist = math.hypot(blue[0] - centre[0], blue[1] - centre[1])
    assert red_dist > blue_dist + 20, (
        f"触角尖应该在远端（红 {red_dist:.0f}px），耳麦在近端（蓝 {blue_dist:.0f}px）"
    )


def test_the_antenna_art_has_no_antialiased_seams():
    """用户 2026-10-31：「预览图里还是有网格」。

    画布和特写预览都开着 ``Antialiasing``。天线以前是**逐格 fillRect**，每一格的边缘
    都被抗锯齿混一次，实心区域内部就浮出一条条暗线（实测 ``#ff8800×2 / #de7803×1 / …``）。
    现在整张图画成一张 pixmap 并关掉抗锯齿，判据是**像素个数正好等于理论值**：
    只要出现一条混色缝，纯色像素就会少。
    """
    from PySide6.QtGui import QColor, QImage, QPainter
    import dataclasses
    from iterator_pet.platform.puppet_tab import BOARD_ANCHORS, BOARD_SIZES
    from iterator_pet.rendering import renderer as renderer
    from iterator_pet.simulation import constants as constants
    from iterator_pet.simulation.state import SimulationState

    cell = 0.5
    colour = "#ff8800"
    col, row = BOARD_ANCHORS["antenna"]
    art = PixelArt.blank(*BOARD_SIZES["antenna"])
    # 一整条实心块，放在耳麦**外侧**（脑袋和眼睛碰不到的地方）
    block_w, block_h = 6, 10
    for y in range(int(row) - block_h - 1, int(row) - 1):
        for x in range(int(col) - 3, int(col) - 3 + block_w):
            art.set(x, y, colour)

    sim = SimulationState((0.0, 0.0, 260.0, 300.0), warmup=0, character="pebbles")
    for _ in range(140):
        sim.step(1.0 / constants.GAME_HZ)
    appearance = ap.build_appearance("probe", "pebbles", {
        "antenna_pixels": art.to_dict(),
        "antenna_anchor": list(BOARD_ANCHORS["antenna"]),
        "antenna_scale": cell,
        "headpiece_mode": "none",
    })
    state = dataclasses.replace(sim.interpolate(1.0), look_dir=(0.0, 0.0),
                                appearance=appearance)

    zoom = 12.0
    image = QImage(460, 460, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    # 和 PetCanvas / PuppetPreview 一样开着抗锯齿 —— 缝就是它混出来的
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.translate(230 - state.head[0] * zoom, 230 - state.head[1] * zoom)
    painter.scale(zoom, zoom)
    renderer.draw_puppet_head(painter, state)
    painter.end()

    # 判据：穿过实心块**中线的横/竖两条扫描线必须连续**。
    # （块是倾斜的，包围盒四角本来就有背景色，所以不能整盒比对；缝的特征正是
    #   "2 个纯色 + 1 个混色"交替，任何一条中线被切断就说明有缝。）
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).name() == colour]
    assert points, "实心块根本没画出来"
    xs = sorted({p[0] for p in points})
    gap = max(range(len(xs) - 1), key=lambda k: xs[k + 1] - xs[k])
    split = (xs[gap] + xs[gap + 1]) * 0.5          # 左右两条块之间的空当
    for side in ([p for p in points if p[0] < split],
                 [p for p in points if p[0] > split]):
        assert side, "有一侧的块不见了"
        x0 = min(p[0] for p in side); x1 = max(p[0] for p in side)
        y0 = min(p[1] for p in side); y1 = max(p[1] for p in side)
        ymid = (y0 + y1) // 2
        row = [x for x in range(x0, x1 + 1)
               if image.pixelColor(x, ymid).name() == colour]
        assert row and max(row) - min(row) + 1 == len(row), (
            f"实心块横向中线有断口（{len(row)} 个纯色像素跨 {max(row) - min(row) + 1} 列）"
            "—— 抗锯齿又把它混花了"
        )
        xmid = (x0 + x1) // 2
        col = [y for y in range(y0, y1 + 1)
               if image.pixelColor(xmid, y).name() == colour]
        assert col and max(col) - min(col) + 1 == len(col), (
            f"实心块纵向中线有断口（{len(col)} 个纯色像素跨 {max(col) - min(col) + 1} 行）"
        )


def test_the_board_background_can_be_changed_from_the_toolbar(tmp_path):
    """画板底色可改（用户 2026-10-31：默认深色会和深色部件糊在一起看不清）。

    底色存在 config.json（编辑器偏好，不写进角色卡），改完三块画板一起生效。
    """
    from iterator_pet.config import Config

    (tmp_path / "characters").mkdir(parents=True)
    config = Config()
    tab = PuppetTab(tmp_path, config=config)
    assert tab.canvases["antenna"].bg == config.board_bg, "默认跟着配置走"

    tab._on_board_bg("#d0d0d4")
    for key, canvas in tab.canvases.items():
        assert canvas.bg == "#d0d0d4", f"{key} 画板没换底色"
    assert config.board_bg == "#d0d0d4", "选择要落回 config"

    # 参考线颜色要跟着底色反着来，否则换了底色就看不见
    tab.canvases["headpiece"].bg = "#101010"
    dark_ink = tab.canvases["headpiece"]._palette_of_board()[2]
    tab.canvases["headpiece"].bg = "#f0f0f0"
    light_ink = tab.canvases["headpiece"]._palette_of_board()[2]
    assert dark_ink != light_ink, "深底用亮线、浅底用暗线"
