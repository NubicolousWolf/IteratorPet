"""拖文件（P3 / L6）的测试：只验"名字怎么进来、提示词长什么样"。

窗口级的 `dragEnterEvent` 不在这里测——它要真窗口和真拖放，靠 `_work` 里的实测脚本
加人工拖一次（审计 A2 的 P0 就是这么验的：命中区外必须不吃桌面的拖放）。
"""

from __future__ import annotations

import json

import pytest

from iterator_pet.llm import config as llm_config
from iterator_pet.llm import fileinfo as llm_fileinfo
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
def test_only_the_basename_survives():
    assert llm_fileinfo.sanitize_name(r"C:\Users\me\Desktop\照片.PNG") == "照片.PNG"
    assert llm_fileinfo.sanitize_name("/home/me/a.txt") == "a.txt"
    assert llm_fileinfo.sanitize_name("..\\..\\secret.md") == "secret.md"


def test_newlines_and_control_characters_are_stripped():
    dirty = "a\nb\tc\r\x01d.txt"
    assert llm_fileinfo.sanitize_name(dirty) == "a b c d.txt"


def test_a_long_name_is_cut_but_keeps_the_extension():
    name = llm_fileinfo.sanitize_name("x" * 120 + ".png")
    assert len(name) == llm_fileinfo.MAX_NAME_CHARS
    assert name.endswith(".png")


# --------------------------------------------------------------- 分类 --
def test_a_known_extension_is_a_file():
    drop = llm_fileinfo.classify("照片.PNG".lower())
    assert drop.kind == "file" and drop.name == "照片.png"
    assert "照片.png" in drop.text and "「" in drop.text, "文件名要包成数据"
    assert drop.text.count("\n") == 0


def test_an_unknown_extension_is_refused_not_guessed():
    drop = llm_fileinfo.classify("virus.xyz")
    assert drop.kind == "" and "xyz" in drop.reason


def test_a_file_without_extension_is_refused():
    drop = llm_fileinfo.classify("README")
    assert drop.kind == "" and drop.reason


def test_an_empty_name_is_refused():
    assert llm_fileinfo.classify("   ").kind == ""


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


def test_ask_file_refuses_an_unknown_extension_without_sending(tmp_path):
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file("payload.xyz")
    assert result.startswith("没看：")
    assert transport.calls == [], "不认识的后缀一个请求都不发"


def test_ask_file_routes_a_broadcast_as_speech(tmp_path):
    transport = FakeTransport()
    router = _router(tmp_path, transport)
    result = router.ask_file(llm_fileinfo.BROADCAST_PREFIX + "今天好安静")
    assert "广播" in result
    assert router.poll().event == "debug"
    messages = transport.calls[0]["messages"]
    user = [m for m in messages if m["role"] == "user"][-1]["content"]
    assert "今天好安静" in user


def test_the_extension_whitelist_is_not_empty_and_lowercase():
    assert all(ext == ext.lower() and ext.startswith(".") for ext in llm_fileinfo.EXTENSIONS)
    assert ".png" in llm_fileinfo.EXTENSIONS
