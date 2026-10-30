"""LLM 模板落地（审计 A8）的测试，外加"两份模板不许漂移"的交叉校验。

三件事：

1. 首次运行把 ``llm.json`` / ``worldbook.json`` / ``characters/*.json`` 写到数据目录，
   并建好 ``chats/``；
2. **非空文件绝不被覆盖**（玩家手改过的东西不能被我们"修好"）；
3. 包内模板（``assets/llm/``）与 ``docs/templates/`` 内容必须一致——docs 那份是给人
   看的说明书，包内那份是真正发出去的；两边漂移过一次，玩家就会照着过期的字段名改。
"""

from __future__ import annotations

import json
from pathlib import Path

from iterator_pet.llm import templates as llm_templates

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO.parent / "docs" / "templates"


def test_first_run_writes_all_templates(tmp_path):
    written = llm_templates.ensure_template_files(tmp_path)
    assert set(written) == {
        "llm.json",
        "worldbook.json",
        "characters/pebbles.json",
        "characters/past_moon.json",
    }
    assert (tmp_path / "chats").is_dir(), "chats/ 也要建出来（历史写这里）"
    for name in written:
        data = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data


def test_second_run_writes_nothing(tmp_path):
    llm_templates.ensure_template_files(tmp_path)
    assert llm_templates.ensure_template_files(tmp_path) == []
    assert llm_templates.missing_templates(tmp_path) == []


def test_empty_files_are_refilled_but_non_empty_ones_are_never_touched(tmp_path):
    (tmp_path / "worldbook.json").write_text("   \n", encoding="utf-8")
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "pebbles.json").write_text('{"name": "我改过的"}', encoding="utf-8")

    written = llm_templates.ensure_template_files(tmp_path)
    assert "worldbook.json" in written, "0 字节/全空白要补"
    assert "characters/pebbles.json" not in written, "非空文件一律不动"
    assert json.loads((tmp_path / "characters" / "pebbles.json").read_text(encoding="utf-8")) == {
        "name": "我改过的"
    }


def test_shipped_templates_match_the_docs(tmp_path):
    if not DOCS.is_dir():                      # 文档不在仓库里（工作区的 docs/ 才是）
        return
    pairs = [
        ("llm.json", DOCS / "llm.json"),
        ("worldbook.json", DOCS / "worldbook.json"),
        ("characters/pebbles.json", DOCS / "characters" / "pebbles.json"),
        ("characters/past_moon.json", DOCS / "characters" / "past_moon.json"),
    ]
    shipped = llm_templates.assets_dir()
    for relative, doc_path in pairs:
        assert doc_path.is_file(), f"docs 里缺 {relative}"
        left = json.loads((shipped / relative).read_text(encoding="utf-8"))
        right = json.loads(doc_path.read_text(encoding="utf-8"))
        assert left == right, f"包内模板与 docs 漂移了：{relative}"


def test_worldbook_template_has_usable_constant_entries():
    """开箱即用的最低要求：至少有一条常驻设定，别让模型对着空气演。"""
    book = json.loads((llm_templates.assets_dir() / "worldbook.json").read_text(encoding="utf-8"))
    constants = [e for e in book["entries"] if e.get("constant")]
    assert constants, "模板里必须有常驻条目"
    assert all(str(e.get("content", "")).strip() for e in constants)
