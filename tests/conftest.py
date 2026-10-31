"""测试分组标记：按文件名打组，不逐个文件加 decorator。

日常只跑被改到的那一组（AGENTS §2）：

    python -m pytest -m workshop      # 改编辑器
    python -m pytest -m llm           # 改大模型
    python -m pytest -m sim           # 改仿真/渲染
    python -m pytest                   # 默认：全部非 slow（跳过长仿真）

分组标记由 ``pytest.ini`` 声明；``slow`` 标在用例上（见各文件），默认被
``addopts = -m "not slow"`` 排掉。发布前跑**真·全量**：

    python -m pytest -m ""            # 覆盖 addopts，一条不落
"""

from __future__ import annotations

import pytest

#: 文件名（不含 .py）→ 分组
GROUPS = {
    "test_simulation": "sim",
    "test_arm_render": "sim",
    "test_puppet_anatomy": "sim",
    "test_kill": "sim",
    "test_drag": "sim",
    "test_behaviour": "sim",
    "test_devpanel": "sim",
    "test_cursor": "sim",
    "test_fixed_loop": "sim",
    "test_bubble": "ui",
    "test_cursorfx": "ui",
    "test_assets_extractor": "io",
    "test_dialogue": "talk",
    "test_ritual_dialogue": "talk",
    "test_editor": "talk",
    "test_llm": "llm",
    "test_llm_client": "llm",
    "test_llm_prompt": "llm",
    "test_llm_route": "llm",
    "test_llm_actions": "llm",
    "test_llm_switch": "llm",
    "test_file_drop": "llm",
    "test_llm_templates": "llm",
    "test_workshop_v019": "workshop",
}


def pytest_collection_modifyitems(config, items):
    """给每条用例补一个组标记（文件里已经标过的不重复加）。"""
    for item in items:
        group = GROUPS.get(item.path.stem if hasattr(item.path, "stem") else "")
        if group:
            item.add_marker(getattr(pytest.mark, group))
