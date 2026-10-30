"""大语言模型接入（P1）。

分层，和仓库其他部分一致：

* :mod:`.config` —— ``llm.json`` 的读写、容错、脱敏。**不联网**。
* :mod:`.models` —— ``/models`` 解析、最小可用性探测、后台任务。**不碰 Qt**。
* ``platform/llm_settings.py`` —— 一屏设置页（Qt），只调用上面两层。

硬约束（`docs/PLAN-llm.md` §8 审计）：

* worker 线程只投队列，不碰 Qt、不碰仿真（A3）；
* ``api_key`` 只经 :func:`iterator_pet.llm.config.redacted` 进日志（A9）；
* 关掉 LLM 时必须和 v0.1.7 行为一致 —— 这一层不 import 任何仿真模块。
"""

from .config import LLMConfig, LLMStore, redacted  # noqa: F401
