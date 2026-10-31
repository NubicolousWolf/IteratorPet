"""拖文件（P3 / L6）的测试：只验"名字怎么进来、提示词长什么样"。

窗口级的 `dragEnterEvent` 不在这里测——它要真窗口和真拖放，靠 `_work` 里的实测脚本
加人工拖一次（审计 A2 的 P0 就是这么验的：命中区外必须不吃桌面的拖放）。
"""

from __future__ import annotations

import json

import pytest

from iterator_pet.llm import config as llm_config
from iterator_pet.llm import fileinfo as llm_fileinfo
from iterator_pet.llm import filetext as llm_filetext
from iterator_pet.llm import route as llm_route


class FakeTransport:
    def __init__(self, payload=None):
        self.payload = payload or {"choices": [{"message": {"content": "嗯。"}}]}
        self.calls: list[dict] = []

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append(payload)
        return self.payload


class InlineJob:
    def __init__(self, fn, *args, **kwargs):
        self._value = fn(*args, **kwargs)

    def start(self):
        return self

    def poll(self):
        value, self._value = self._value, None
        return value


def _router(tmp_path, transport=None) -> llm_route.LLMRouter:
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.install_default_file()
    cfg = store.load()
    cfg.api_key = "sk-1234567890abcdefghijklmn"
    cfg.model = "deepseek-chat"
    cfg.allow_actions = False
    store.config = cfg
    return llm_route.LLMRouter(store, folder=tmp_path,
                               transport=transport or FakeTransport(),
                               job_factory=InlineJob)


# --------------------------------------------------------------- 清洗 --
def test_the_name_is_cleaned_before_it_ever_reaches_the_model():
    """文件名进来先过三道：去路径、去控制字符、截长（保后缀）。

    合并理由：这三条是同一个函数 `sanitize_name` 的三个侧面，分开只是让报告更长。
    """
    assert llm_fileinfo.sanitize_name(r"C:\Users\me\Desktop\照片.PNG") == "照片.PNG"
    assert llm_fileinfo.sanitize_name("/home/me/a.txt") == "a.txt"
    assert llm_fileinfo.sanitize_name(r"..\..\secret.md") == "secret.md", "路径穿越要削平"

    dirty = "a" + chr(10) + "b" + chr(9) + "c" + chr(13) + chr(1) + "d.txt"
    assert llm_fileinfo.sanitize_name(dirty) == "a b c d.txt", "换行/制表/控制字符都换空格"

    long_name = llm_fileinfo.sanitize_name("x" * 120 + ".png")
    assert len(long_name) == llm_fileinfo.MAX_NAME_CHARS
    assert long_name.endswith(".png"), "截断不能把后缀吃掉"


# --------------------------------------------------------------- 分类 --
def test_any_name_is_sent_because_the_suffix_is_information():
    """用户 2026-10-30：**不搞后缀白名单**，后缀本身也是信息，交给模型判断。

    合并了原来的三条（已知后缀 / 陌生后缀 / 无后缀）：它们测的都是
    "classify 不会因为后缀不认识的就把文件丢掉"。
    """
    known = llm_fileinfo.classify("照片.PNG".lower())
    assert known.kind == "file" and known.name == "照片.png"
    assert "照片.png" in known.text and "「" in known.text, "文件名要包成数据"
    assert chr(10) not in known.text, "提示词里不许带真换行"

    for name in ("virus.xyz", "five-pebbles-实机.mp4", "a.unknownext"):
        drop = llm_fileinfo.classify(name)
        assert drop.kind == "file", name
        assert name in drop.text

    no_ext = llm_fileinfo.classify("游戏截图")      # 可能是文件夹
    assert no_ext.kind == "file" and "游戏截图" in no_ext.text


def test_the_broadcast_prefix_turns_a_name_into_player_speech():
    """用户 2026-10-30：名字以「即时广播-」开头 = 借文件名直接说话，不读文件。"""
    drop = llm_fileinfo.classify(llm_fileinfo.BROADCAST_PREFIX + "你在吗.txt")
    assert drop.kind == "broadcast"
    assert "你在吗" in drop.text
    assert "看不到里面的内容" not in drop.text, "广播不是「读文件」，不该带读文件那段提示词"


def test_an_empty_broadcast_is_refused():
    assert llm_fileinfo.classify(llm_fileinfo.BROADCAST_PREFIX).kind == ""


# --------------------------------------------------------------- 发出去 --
def test_ask_file_sends_one_dedicated_prompt(tmp_path):
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file("报告.docx")
    assert result.startswith("读了 报告.docx")

    messages = transport.calls[0]["messages"]
    user = [m for m in messages if m["role"] == "user"][-1]["content"]
    assert "报告.docx" in user
    assert "看不到里面的内容" in user, "专用提示词，不是普通台词请求"
    assert str(tmp_path) not in json.dumps(messages, ensure_ascii=False), "不许带路径"
    assert router.poll().event == "file"
    assert router.chat.recent(2)[-2] == ("user", user)


def test_ask_file_sends_even_for_an_unknown_extension(tmp_path):
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file("five-pebbles.mp4")
    assert result.startswith("读了 five-pebbles.mp4")
    assert transport.calls, "不再按后缀拦截"


def test_ask_file_routes_a_broadcast_as_speech(tmp_path):
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file(llm_fileinfo.BROADCAST_PREFIX + "今天好安静")
    assert "广播" in result
    assert router.poll().event == "debug"
    messages = transport.calls[0]["messages"]
    user = [m for m in messages if m["role"] == "user"][-1]["content"]
    assert "今天好安静" in user




# ------------------------------------------------------------- 接线守卫 --
def test_the_window_asks_the_router_not_the_store():
    """接线守卫：`PetWindow.llm` 是 LLMStore（配置文件），路由器在 `simulation.llm`。

    真机上就因为写成了 `self.llm.ask_file()` 抛 `AttributeError`，而 `dropEvent` 里的
    异常在 pythonw 下是静默的 —— 表现是"松手没反应"，两轮才查出来。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "src" / "iterator_pet" / "platform" / "pet_window.py").read_text(encoding="utf-8")
    # 注释里允许出现旧写法（就是写给下一个人的），只查**可执行**的行
    code = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
    code_text = chr(10).join(code)
    assert "self.llm.ask_file" not in code_text, "路由器不在 self.llm 上"
    assert "self.simulation.llm" in code_text, "路由器在仿真里"


# ------------------------------------------------ 文本内容（用户 2026-10-30）--
def _make_zip(path, files: dict[str, str]) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)


def test_a_txt_is_read_and_its_content_reaches_the_prompt(tmp_path):
    doc = tmp_path / "笔记.txt"
    doc.write_text("深渊之下有一座桥。\n桥的另一边是她。", encoding="utf-8")
    drop = llm_fileinfo.classify(str(doc))
    assert drop.kind == "file" and drop.chars > 0
    assert "深渊之下有一座桥" in drop.text
    assert "<<<" in drop.text and ">>>" in drop.text, "正文要包成数据块"
    assert "不要照做" in drop.text, "防注入那句必须还在"


def test_a_long_file_is_cut_at_ten_thousand_chars(tmp_path):
    doc = tmp_path / "长文.txt"
    doc.write_text("字" * 12000, encoding="utf-8")
    drop = llm_fileinfo.classify(str(doc))
    assert drop.chars == llm_filetext.MAX_CONTENT_CHARS == 10000
    assert "只取了前" in drop.text


def test_a_gb18030_file_is_decoded(tmp_path):
    doc = tmp_path / "旧文件.txt"
    doc.write_bytes("这是 GBK 编码的句子。".encode("gb18030"))
    drop = llm_fileinfo.classify(str(doc))
    assert "这是 GBK 编码的句子" in drop.text


def test_a_docx_is_read_without_python_docx(tmp_path):
    doc = tmp_path / "报告.docx"
    _make_zip(doc, {"word/document.xml":
                    "<w:document><w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>"})
    drop = llm_fileinfo.classify(str(doc))
    assert "第一段" in drop.text and "第二段" in drop.text


def test_an_xlsx_reads_shared_strings(tmp_path):
    book = tmp_path / "表.xlsx"
    _make_zip(book, {
        "xl/sharedStrings.xml":
            "<sst><si><t>名字</t></si><si><t>五块卵石</t></si></sst>",
        "xl/worksheets/sheet1.xml":
            "<worksheet><sheetData><row><c t=\"s\"><v>0</v></c>"
            "<c t=\"s\"><v>1</v></c></row></sheetData></worksheet>",
    })
    drop = llm_fileinfo.classify(str(book))
    assert "五块卵石" in drop.text and "名字" in drop.text


def test_a_binary_suffix_only_gets_the_name(tmp_path):
    movie = tmp_path / "puppet.mp4"
    movie.write_bytes(b"\x00\x01\x02binary junk" * 100)
    drop = llm_fileinfo.classify(str(movie))
    assert drop.kind == "file" and drop.chars == 0
    assert "puppet.mp4" in drop.text and "看不到里面的内容" in drop.text


def test_the_content_is_written_into_history(tmp_path):
    doc = tmp_path / "要记住.txt"
    doc.write_text("这句话会留在历史里。", encoding="utf-8")
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file(str(doc))
    assert result.startswith("读了 要记住.txt") and "字" in result

    messages = transport.calls[0]["messages"]
    user = [m for m in messages if m["role"] == "user"][-1]["content"]
    assert "这句话会留在历史里" in user
    history = " ".join(text for _role, text in router.chat.recent(10))
    assert "这句话会留在历史里" in history, "用户拍板：内容要进历史"
