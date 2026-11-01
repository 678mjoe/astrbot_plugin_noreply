# 验证记录

> 历史维护记录：主机地址和个人目录已脱敏，执行命令前请替换占位符。

## 最初交接的验证范围

最初交接只包含文档与源码接口探针，不是生产插件测试。后续 M1 产品验证另列于下文。两阶段均没有调用真实模型、连接聊天平台或启动完整 AstrBot；Pi 升级属于另一次明确授权的操作。

`research/probe.py` 每个基线包含 12 项测试，实际执行从对应快照提取的 `Context.llm_generate()`、`Context.get_current_chat_provider_id()`、`ProcessStage.process()` 和 `PersonaManager.get_persona()`。外部 Provider、事件和子阶段使用标准库最小桩。

## Mac 项目目录最终复核

已在最终目录 `<MAC_HOME>/projects/astrbot_plugin_noreply`、Mac 系统 Python 3.9.6 下实际运行：

| 命令 | 真实结果 |
| --- | --- |
| `python3 tools/verify_handoff.py` | 通过；6 份源码快照 hash、5 个本地 Markdown 链接校验正常 |
| `python3 research/probe.py` | 交接时部署快照 4.29.1：12 tests，OK；不代表后续 Pi 版本 |
| `ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py` | 固定研究提交：12 tests，OK |

这些验证在 Pi 暂存目录也执行通过。交接文件的 SHA-256 在传输后另行核对。Git 状态与初始提交请以项目内 `git status`、`git log -1` 为准；不把探针或文档检查计为 M1 产品测试。

## 用户授权升级后的追加验证

- Pi 当前观测版本 4.29.3、用户服务运行、面板 HTTP 200、依赖检查和数据库 quick_check 正常。
- Mac 已用新增 4.29.3 快照实际运行 12 项接口探针，OK；这不是完整插件或真实聊天功能测试。
- 当前完整性检查覆盖 9 份源码快照；最初交接表的 6 份属于当时记录。
- 详细范围和文转图端点超时告警见 [升级记录](UPGRADE-2026-10-31.md)。

## M1 产品实现追加验证

2026-10-31 在项目 Python 3.12.13 / AstrBot 4.29.3 开发环境实际运行：

| 命令 | 结果 |
| --- | --- |
| `PYTHONASYNCIODEBUG=1 .venv/bin/python -m pytest -q tests` | 93 passed，1 条 SDK `audioop` 弃用告警；没有隐藏告警 |
| `.venv/bin/ruff check engine.py main.py tests` | All checks passed |
| `/opt/homebrew/bin/uv pip check --python .venv/bin/python` | 156 个已安装包，依赖兼容 |
| `python3 tools/verify_handoff.py` | 通过，9 份源码快照与 8 个本地文档链接；文档更新后的重检计数以实现状态为准 |
| `python3 research/probe.py` | 历史 4.29.1 快照：12 tests，OK |
| `ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/deployed-4.29.3/sources" python3 research/probe.py` | 4.29.3 快照：12 tests，OK |
| `ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py` | 固定研究快照：12 tests，OK |
| `GIT_MASTER=1 git diff --check` | 退出码 0；最终文档更新后另行复核 |

首轮协议/记忆/引擎测试因缺少 `engine` 产生 3 个收集错误，首轮事件适配测试因缺少 `main` 失败，均在实现后通过。补充取消边界测试曾真实复现两项失败：超时后得到 `reply`，以及调用者取消后未抛 `CancelledError`；改为显式截止状态检查与调用者取消状态检查后，完整回归通过。

SDK 验证没有启动服务或连接真实 Provider；外部模型与数据库边界使用假对象，产品解析、观察窗口、状态机、适配器以及被测 SDK 方法实际执行。产品测试不代替 M2。

## 未验证项（当前）

- Pi 实时版本、插件加载和实际服务运行环境的兼容性：需部署前重新核验。
- 保存快照之外的旧/新版本，以及完整旧版 SDK 的当前产品行为。
- 真实模型的协议遵守、插话质量、费用、平台送达：未测试。
- M2 shadow/live 与 M3 正常会话历史桥接：未实施。
