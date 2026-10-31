# 版本与源码边界

## 实际目标

在 Pi 的 AstrBot 用户服务入口解析到的独立 uv 工具环境中，`importlib.metadata.distribution("astrbot")` 返回：版本 **4.29.1**，`Requires-Python: >=3.12`。其工作目录 `~/astrbot` 不是 Git checkout，因此没有给它编造提交号。

`research/snapshots/deployed-4.29.1/` 保存三个只读源码文件和 SHA-256；不包含数据库、模型凭据、运行配置或聊天数据。默认探针读取这一组。

## 原研究基线

`research/snapshots/research-master/` 来自固定提交 `3d92821ad9d90df33fc242e7b159a3260075eb2c`，保存原始 Git blob hash。对应证据和更多源码链接保留在 `docs/archive/`；不把它称为当前最新 master。

对两版 AST 去除格式差异后比较：

| 方法 | 比较结果 |
| --- | --- |
| `Context.llm_generate` | 一致 |
| `Context.get_current_chat_provider_id` | 一致 |
| `PersonaManager.get_persona` | 一致 |
| `ProcessStage.process` | 不完全一致：研究版多了 session-waiter 分派；目标 4.29.1 不含这段 |

不可据这四个方法推断整个 AstrBot 完全兼容。插件实施还应核对事件装饰器、配置 schema 和消息结果类型。开发目标固定为 4.29.1；支持更多版本需各自验证。

## Mac 环境

Mac SSH 默认 `python3` 为 3.9.6；只够运行本仓库标准库交接检查。`/opt/homebrew/bin/uv` 已发现，版本 0.11.32。实际 SDK 开发环境应由 uv 在本项目创建 Python >=3.12 的 `.venv`，不改系统默认 Python。

这份记录不包含模型可用性、平台接入或收费授权结论。
