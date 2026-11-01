"""v0.2.0「自定义迭代器」：注册表、外观接线、每角色世界书。

这一组测试盯的是**接线**，不是外观数值（那些在 ``test_workshop_v019`` 里，
每条都对着源码行号）。四类断言：

1. **注册表生命周期**：新建/复制/删除/非法 key/内置保护。任何一条退化的
   后果都是"玩家建完角色后菜单里没有她"或者"删了别人的卡"。
2. **外观合成口径**：卡里没改色的内置角色必须拿到 ``appearance.py`` 里**同一个对象**
   （源码数值一个字段都没被重算）；自定义角色套皓月模板时**不能白得第三只眼**。
3. **每角色世界书**：有自己的就用自己的，没有就无缝回落全局（老玩家零迁移）。
4. **`load_card` 不再串台**：自定义角色缺 ``system_prompt`` 时用中性兜底，
   不许顶五块卵石的人设说话。
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from iterator_pet import characters as ch  # noqa: E402
from iterator_pet.llm import prompt as llm_prompt  # noqa: E402
from iterator_pet.llm import worldbook as wb  # noqa: E402
from iterator_pet.simulation import appearance as ap  # noqa: E402
from iterator_pet.simulation.lines import Lines  # noqa: E402

APP = None  # 懒建：只有界面组的用例才需要 QApplication


def _app():
    global APP
    if APP is None:
        from PySide6.QtWidgets import QApplication

        APP = QApplication.instance() or QApplication([])
    return APP


@pytest.fixture()
def folder(tmp_path):
    return tmp_path


# ---------------------------------------------------------------- 注册表 --
def test_the_registry_lists_the_two_builtins_even_without_any_file(folder):
    """第一次启动时 ``characters/`` 目录不存在，菜单也得有两位内置角色。"""
    rows = ch.characters(folder)

    assert [row.key for row in rows] == ["pebbles", "past_moon"]
    assert [row.name for row in rows] == ["五块卵石", "仰望皓月"]
    assert all(row.builtin for row in rows)
    assert not (folder / "characters").exists(), "只读扫描不该顺手建目录"


def test_custom_characters_sort_after_the_builtins(folder):
    ch.create("zulu", "Z", "pebbles", folder)
    ch.create("alpha", "A", "pebbles", folder)

    assert [row.key for row in ch.characters(folder)] == [
        "pebbles", "past_moon", "alpha", "zulu",
    ]
    assert ch.names(folder)[0] == ("pebbles", "五块卵石")


@pytest.mark.parametrize(
    "bad",
    ["", "UPPER", "has space", "slash/es", "点", "con", "nul", "x" * 33],
)
def test_unsafe_keys_are_refused(bad, folder):
    """key 就是文件名：路径穿越与 Windows 保留名必须在这里被拦下。"""
    assert ch.validate_key(bad) != ""

    ref, error = ch.create(bad, "name", "pebbles", folder)

    assert ref is None and error
    assert not (folder / "characters").exists()


def test_create_writes_only_the_skeleton_choice_not_a_colour_snapshot(folder):
    """新建只记「用哪套骨骼」，不把基底的色值抄进卡里。

    抄进去的后果很隐蔽：以后我们改 ``appearance.py`` 里的源码数值，
    玩家这个角色会**永远停在旧值**上，而且没人找得到原因。
    """
    ref, error = ch.create("seven_red_suns", "七轮红日", "past_moon", folder)

    assert error == "" and ref is not None and ref.base == "past_moon"
    raw = json.loads((folder / "characters" / "seven_red_suns.json").read_text("utf-8"))
    appearance = raw["extensions"]["rainworld"]["appearance"]
    assert appearance == {"base_template": "past_moon"}
    assert raw["name"] == "七轮红日"
    assert raw["spec"] == "chara_card_v2"


def test_create_refuses_to_overwrite_an_existing_key(folder):
    ch.create("dup", "一", "pebbles", folder)

    ref, error = ch.create("dup", "二", "pebbles", folder)

    assert ref is None and "已经存在" in error
    raw = json.loads((folder / "characters" / "dup.json").read_text("utf-8"))
    assert raw["name"] == "一", "失败的那次不许改动原卡"


def test_duplicate_carries_the_whole_card_over_and_renames_it(folder):
    ch.create("origin", "原版", "past_moon", folder)
    path = folder / "characters" / "origin.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["personality"] = "冷淡"
    raw["extensions"]["rainworld"]["appearance"]["skin"] = [1, 2, 3]
    raw["extensions"]["someone_else"] = {"keep": True}
    path.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")

    ref, error = ch.duplicate("origin", "clone", "", folder)

    assert error == "" and ref is not None and ref.name == "原版（副本）"
    copied = json.loads((folder / "characters" / "clone.json").read_text("utf-8"))
    assert copied["personality"] == "冷淡"
    assert copied["extensions"]["rainworld"]["appearance"]["skin"] == [1, 2, 3]
    assert copied["extensions"]["someone_else"] == {"keep": True}, "未知字段也要带走"


def test_duplicate_refuses_a_broken_source_instead_of_copying_the_damage(folder):
    (folder / "characters").mkdir()
    (folder / "characters" / "broken.json").write_text("{ not json", "utf-8")

    ref, error = ch.duplicate("broken", "fresh", "", folder)

    assert ref is None and "不是合法 JSON" in error
    assert not (folder / "characters" / "fresh.json").exists()


def test_builtin_characters_cannot_be_deleted(folder):
    error = ch.delete("past_moon", folder)

    assert "内置角色不能删除" in error
    assert ch.exists("past_moon", folder)


def test_delete_removes_the_card_but_keeps_the_chat_log_by_default(folder):
    ch.create("temporary", "临时", "pebbles", folder)
    chat = folder / "chats" / "temporary.jsonl"
    chat.parent.mkdir(parents=True)
    chat.write_text('{"role":"user"}\n', "utf-8")

    assert ch.delete("temporary", folder) == ""
    assert not (folder / "characters" / "temporary.json").exists()
    assert chat.is_file(), "聊天记录是玩家的东西，默认不许连带删除"


def test_delete_can_take_the_chat_log_too_when_asked(folder):
    ch.create("temporary", "临时", "pebbles", folder)
    chat = folder / "chats" / "temporary.jsonl"
    chat.parent.mkdir(parents=True)
    chat.write_text('{"role":"user"}\n', "utf-8")

    assert ch.delete("temporary", folder, drop_chats=True) == ""

    assert not chat.exists()


def test_a_stale_config_key_falls_back_to_the_first_builtin(folder):
    """玩家删掉正在用的角色后，配置里那个 key 还在——不兜底就变成"菜单里没一项打勾"。"""
    assert ch.resolve_key("gone_forever", folder) == "pebbles"
    assert ch.resolve_key("", folder) == "pebbles"
    ch.create("here", "在", "pebbles", folder)
    assert ch.resolve_key("here", folder) == "here"


def test_a_broken_card_still_shows_up_in_the_list(folder):
    """卡坏了不等于角色不存在：玩家得能在编辑器里看见它、修它。"""
    (folder / "characters").mkdir()
    (folder / "characters" / "half.json").write_text("{ broken", "utf-8")

    ref = ch.find("half", folder)

    assert ref is not None and ref.name == "half"
    assert ref.base == "pebbles", "读不出基底时退回 pebbles（和工坊一致）"


# ---------------------------------------------------------------- 外观 ----
def test_untouched_builtins_still_get_the_source_object_itself(folder):
    """内置两位没改色时必须是**同一个对象**：任何字段都不许被重算。"""
    assert ch.appearance_of("pebbles", folder) is ap.PEBBLES
    assert ch.appearance_of("past_moon", folder) is ap.PAST_MOON


def test_a_custom_character_on_the_moon_skeleton_does_not_inherit_her_third_eye(folder):
    """套皓月模板 → 长袍/身高/眼高继承，但头顶那颗圆点默认不跟来。"""
    ch.create("seven_red_suns", "七轮红日", "past_moon", folder)

    appearance = ch.appearance_of("seven_red_suns", folder)

    assert appearance.key == "seven_red_suns"
    assert appearance.body0_h == 16.0, "躯干高度来自基底（16 px）"
    assert appearance.third_eye is False, "自定义角色默认无头顶部件"
    assert appearance.third_eye_colour is None
    assert appearance.headpiece is None


def test_the_workshop_preview_and_the_desktop_agree_on_the_default_headpiece(folder):
    """预览与桌面必须走同一条默认值规则——两边相反就是最难查的那类偏差。"""
    ch.create("custom", "自定义", "past_moon", folder)
    desktop = ap.apply_headpiece_default("custom", {})
    preview = ap.apply_headpiece_default("custom", {})

    assert desktop == preview == {"headpiece_mode": "none"}
    assert ap.apply_headpiece_default("pebbles", {}) == {}, "内置两位不许被塞键"


def test_an_appearance_draft_from_the_workshop_reaches_the_desktop(folder):
    """工坊里存的覆盖层必须原样变成桌面那份 Appearance（这就是 v0.2.0 的承诺）。"""
    ch.create("painted", "涂过的", "pebbles", folder)
    from iterator_pet.llm import cards

    assert cards.save_appearance(
        "painted",
        {"base_template": "pebbles", "skin": [10, 20, 30], "halo": [40, 50, 60],
         "halo_spark": [70, 80, 90], "headpiece_mode": "none"},
        folder,
    ) == ""

    appearance = ch.appearance_of("painted", folder)

    assert appearance.skin == (10, 20, 30)
    assert appearance.halo == (40, 50, 60)
    assert appearance.halo_spark == (70, 80, 90), "电光是独立字段，不许再被算成 halo×1.05"


# ------------------------------------------------------------- 世界书 -----
def test_worldbook_resolution_falls_back_to_the_shared_file(folder):
    """老玩家只有根目录一份 worldbook.json：升级后必须照旧生效。"""
    assert wb.resolve_path("pebbles", folder) == folder / "worldbook.json"

    (folder / "worldbooks").mkdir()
    (folder / "worldbooks" / "pebbles.json").write_text("{}", "utf-8")

    assert wb.resolve_path("pebbles", folder) == folder / "worldbooks" / "pebbles.json"
    assert wb.resolve_path("past_moon", folder) == folder / "worldbook.json", (
        "别的角色没有自己的那份 → 仍然回落全局"
    )


def test_worldbook_paths_cannot_escape_the_data_folder(folder):
    path = wb.character_path("../../evil", folder)

    assert path.parent == folder / "worldbooks"
    assert ".." not in path.name and "/" not in path.name and "\\" not in path.name
    assert path.name.endswith("evil.json")


def test_each_character_can_have_its_own_worldbook(folder):
    shared = wb.Worldbook(wb.global_path(folder))
    shared.entries = [wb.Entry(id="a", comment="全局", constant=True, content="全局设定")]
    assert shared.save()

    (folder / "worldbooks").mkdir()
    own = wb.Worldbook(wb.character_path("pebbles", folder))
    own.entries = [wb.Entry(id="b", comment="独立", constant=True, content="独立设定")]
    assert own.save()

    reloaded = wb.Worldbook(wb.resolve_path("pebbles", folder)).load()
    assert [entry.comment for entry in reloaded.entries] == ["独立"]
    other = wb.Worldbook(wb.resolve_path("past_moon", folder)).load()
    assert [entry.comment for entry in other.entries] == ["全局"]


# -------------------------------------------------------- 角色卡与台词 -----
def test_load_card_does_not_borrow_pebbles_identity_for_custom_characters(folder):
    """自定义角色没填 system_prompt 时用中性兜底，不许顶五块卵石的人设。"""
    ch.create("blank", "空白", "pebbles", folder)

    card, error = llm_prompt.load_card("blank", folder)

    assert error == ""
    assert card.name == "空白"
    assert "五块卵石" not in card.system_prompt
    assert "迭代器" in card.system_prompt


def test_builtin_cards_keep_their_own_fallbacks(folder):
    card, _error = llm_prompt.load_card("past_moon", folder)

    assert card.name == "仰望皓月"
    assert "仰望皓月" in card.system_prompt


def test_a_new_character_without_lines_still_says_something(tmp_path):
    """新建角色在 lines.json 里还没有台词组：给中性占位，不是彻底哑巴。"""
    lines = Lines(path=tmp_path / "lines.json", seed=1)

    assert lines.pool("brand_new", "drag") == ["……"]
    assert lines.next_line("brand_new", "talk") == "……"
    assert lines.pool("pebbles", "drag")[0] == "滚开。", "内置两位不受影响"


# ------------------------------------------------------------ 工作室 ----
def _workshop(tmp_path, current="pebbles", on_activate=None):
    _app()
    from iterator_pet.platform.workshop import Workshop

    return Workshop(
        book=wb.Worldbook(wb.resolve_path(current, tmp_path)),
        folder=tmp_path,
        current=current,
        on_activate=on_activate,
    )


def test_the_workshop_dropdown_comes_from_the_registry(tmp_path):
    ch.create("seven_red_suns", "七轮红日", "past_moon", tmp_path)
    workshop = _workshop(tmp_path)

    keys = [
        workshop.character_box.itemData(i) for i in range(workshop.character_box.count())
    ]

    assert keys == ["pebbles", "past_moon", "seven_red_suns"], "内置在前，自定义按 key 排序"
    assert "七轮红日" in workshop.character_box.itemText(2)
    assert "自定义" in workshop.character_box.itemText(2)
    workshop.close()


def test_creating_a_character_loads_it_into_every_tab(tmp_path):
    workshop = _workshop(tmp_path)

    key, error = workshop._perform_create("fresh_one", "新的她", "past_moon")

    assert error == "" and key == "fresh_one"
    assert workshop._key == "fresh_one", "新建完直接进入编辑，玩家不用再点一次下拉框"
    assert workshop.puppet_tab.key == "fresh_one"
    assert workshop.card_tab.key == "fresh_one"
    assert workshop.worldbook_tab.character == "fresh_one"
    assert workshop.delete_button.isEnabled() is True, "自定义角色能删"
    workshop.close()


def test_activate_moves_the_desktop_and_then_greys_itself_out(tmp_path):
    calls: list[str] = []
    workshop = _workshop(tmp_path, current="pebbles", on_activate=calls.append)
    workshop._perform_create("second", "第二位", "pebbles")

    assert workshop.activate_button.isEnabled() is True
    workshop.activate_character("second")

    assert calls == ["second"]
    assert workshop._desktop == "second"
    assert workshop.activate_button.isEnabled() is False, "已经是当前角色 → 置灰"

    workshop.activate_character("second")
    assert calls == ["second"], "重复点不重复回调"
    workshop.close()


def test_deleting_the_running_character_moves_the_desktop_away(tmp_path):
    """删掉正在跑的那位 → 桌面必须被切走，否则配置里点着一个不存在的角色。"""
    calls: list[str] = []
    ch.create("doomed", "要删的", "pebbles", tmp_path)
    workshop = _workshop(tmp_path, current="doomed", on_activate=calls.append)

    error = workshop._perform_delete("doomed")

    assert error == ""
    assert not ch.exists("doomed", tmp_path)
    assert calls == ["pebbles"], "删掉当前角色要切回第一位内置角色"
    assert workshop._desktop == "pebbles"
    assert [r.key for r in workshop._refs] == ["pebbles", "past_moon"]
    workshop.close()


def test_deleting_another_character_leaves_the_desktop_alone(tmp_path):
    calls: list[str] = []
    ch.create("other", "别人", "pebbles", tmp_path)
    workshop = _workshop(tmp_path, current="pebbles", on_activate=calls.append)

    assert workshop._perform_delete("other") == ""

    assert calls == [], "删的不是当前角色，桌面不该动"
    workshop.close()


def test_the_worldbook_tab_follows_the_selected_character(tmp_path):
    """切到自定义角色时，世界书页换到"她实际会用的那份"（没有独立 → 仍回落全局）。"""
    (tmp_path / "worldbook.json").write_text('{"entries": []}', "utf-8")
    ch.create("with_book", "有书", "pebbles", tmp_path)
    (tmp_path / "worldbooks").mkdir()
    (tmp_path / "worldbooks" / "with_book.json").write_text('{"entries": []}', "utf-8")
    workshop = _workshop(tmp_path)

    workshop._load_character("with_book")
    assert workshop.worldbook_tab.book.path == tmp_path / "worldbooks" / "with_book.json"

    workshop._load_character("past_moon")
    assert workshop.worldbook_tab.book.path == tmp_path / "worldbook.json", "回落全局"
    workshop.close()


def test_forking_the_global_worldbook_gives_the_character_her_own_copy(tmp_path):
    """「从全局复制一份」= 原样复制文件，排版和未知键一点都不许变。"""
    text = json.dumps(
        {"version": 1, "future_key": {"keep": True}, "entries": [
            {"id": "a", "comment": "全局", "content": "正文"},
        ]}, ensure_ascii=False, indent=2,
    )
    (tmp_path / "worldbook.json").write_text(text, "utf-8")
    ch.create("forker", "分叉", "pebbles", tmp_path)
    workshop = _workshop(tmp_path)
    workshop._load_character("forker")

    workshop.worldbook_tab.fork_from_global()

    own = tmp_path / "worldbooks" / "forker.json"
    assert own.is_file()
    assert own.read_text("utf-8") == text, "复制不是「读进来再写出去」，排版也得一样"
    assert workshop.worldbook_tab.book.path == own
    workshop.close()

# ------------------------------------------------ 切尺寸不丢会话状态 ----
def test_switching_size_keeps_llm_appearance_sparks_and_lines():
    """切尺寸会整个重建仿真：**不属于腔室**的状态必须一起搬。

    用户 2026-10-31 报的两个 bug 是同一个根因——`apply_scale()` 重建
    `SimulationState` 时只搬了 `temper.value`：

    * 电光档位掉回默认 `eco`（"切完尺寸电光自己变回节能"）；
    * `simulation.llm` 变回 `None`（"自定义角色接不上 LLM，只会说……；
      调试入口弹「这个窗口没有接 LLM」"）；
    * 以及 v0.2.0 自己带来的第三份：卡里的外观掉回源码模板，
      连 `appearance.key` 都退回 `pebbles`（台词池与角色卡跟着串）。

    这条测试必须能真红：把 `adopt_session_from` 里任一行删掉，它都会失败。
    """
    _app()
    from iterator_pet.config import Config
    from iterator_pet.platform.pet_window import PetWindow
    from iterator_pet.simulation.state import SimulationState

    config = Config()
    config.scale = 1.5
    config.character = "my_sun"
    old = SimulationState((0.0, 0.0, 1136.0, 608.0), seed=7, warmup=0,
                          character="my_sun")
    router = object()          # 真的 LLMRouter 要碰网络/文件，这里只要"是不是同一个"
    from dataclasses import replace

    custom = replace(ap.PEBBLES, key="my_sun", skin=(1, 2, 3))
    old.llm = router
    old.appearance = custom
    old.dialogue_enabled = False
    old.last_spoken = "上一次说的话"
    old.halo.set_spark_rate("computing")
    lines = old.lines
    computing = old.halo.spark_rate

    class _Rect:
        @staticmethod
        def width():
            return 1707

        @staticmethod
        def height():
            return 912

    class _Window:
        """只借 `apply_scale` 用到的那几个属性，不造真窗口（整屏透明窗口在离屏里不可靠）。"""

    window = _Window()
    window.simulation = old
    window.config = config
    window.character = "my_sun"
    window.scale = 1.5
    window._last = 0.0
    window.geometry = lambda: _Rect()
    window.update = lambda: None

    PetWindow.apply_scale(window)
    new = window.simulation

    assert new is not old, "切尺寸就是换腔室 = 重建一个新仿真"
    assert new.llm is router, "路由器丢了 → 只会说…… / 「这个窗口没有接 LLM」"
    assert new.appearance.key == "my_sun", "外观丢了 → key 退回 pebbles，台词池跟着串"
    assert new.appearance.skin == (1, 2, 3), "工坊里调过的颜色必须还在"
    assert new.dialogue_enabled is False, "托盘里的对话开关不许被重新打开"
    assert new.last_spoken == "上一次说的话", "喂给 LLM 的上一句不许清空"
    assert new.lines is lines, "台词表要带着（里面记着上一句说过什么）"
    assert new.halo.spark_rate == computing, "电光档位丢了 → 自己变回节能"


def test_adopt_session_does_not_carry_the_ritual_in_flight():
    """仪式/对话是"这一刻、这个腔室"的状态，重建时该复位，不跟着搬。"""
    from iterator_pet.simulation.state import SimulationState

    old = SimulationState((0.0, 0.0, 1136.0, 608.0), seed=3, warmup=0)
    old.dialogue.start("测试台词")
    old.kill.begin((100.0, 100.0), old.ticks, tremble_only=False)

    new = SimulationState((0.0, 0.0, 1136.0, 608.0), seed=3, warmup=0)
    new.adopt_session_from(old)

    assert new.dialogue.text == "", "气泡属于旧腔室，重建时收起"
    assert new.kill.phase == "idle", "不许留半个仪式"


# ------------------------------------------------ 孤儿世界书（2026-11-01）--
def test_worldbooks_that_match_no_character_are_reported(tmp_path):
    """用户报"独立世界书没起作用"的真因：角色 key 变了，文件名对不上。

    ``resolve_path`` 按设计回落到全局（老玩家零迁移），但"文件存在却没人读"这件事
    必须能被**发现**，否则玩家只会看到"改了没生效"。
    """
    (tmp_path / "worldbooks").mkdir()
    (tmp_path / "worldbooks" / "test_iterator.json").write_text("{}", "utf-8")
    (tmp_path / "worldbooks" / "test.json").write_text("{}", "utf-8")

    found = [path.name for path in wb.orphans(["test", "pebbles"], tmp_path)]
    assert found == ["test_iterator.json"]
    assert wb.orphans(["test_iterator", "test"], tmp_path) == []


def test_the_worldbook_tab_offers_to_migrate_an_orphan_book(tmp_path):
    """工坊里给"对不上的那份"一个一键迁移入口（复制而不是 load→save）。"""
    from PySide6.QtWidgets import QApplication

    from iterator_pet.platform.worldbook_tab import WorldbookTab

    QApplication.instance() or QApplication([])
    (tmp_path / "worldbooks").mkdir()
    source = tmp_path / "worldbooks" / "test_iterator.json"
    source.write_text('{"version": 1, "entries": []}', "utf-8")
    global_book = tmp_path / "worldbook.json"
    global_book.write_text('{"version": 1, "entries": []}', "utf-8")

    tab = WorldbookTab(wb.Worldbook(path=global_book), folder=tmp_path,
                       character="test")
    assert tab._orphan == source, "没有独立那份时应该发现孤儿文件"
    assert "test_iterator.json" in tab.orphan_button.text()

    tab.migrate_orphan()
    own = tmp_path / "worldbooks" / "test.json"
    assert own.is_file(), "迁移必须复制到当前角色的 key"
    assert own.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert tab.book.path == own, "迁移完当场切过去，不用再点一次作用域"
    tab.deleteLater()

