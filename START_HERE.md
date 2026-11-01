# 项目接手与审阅入口

当前主路径是持久化批量逐消息判定，普通消息与实际 @ 共用同群背景，@必须回复。模型统一沿用 AstrBot 原版会话选择，不提供插件模型覆盖。

## 阅读顺序

1. [发布前审阅单](RELEASE_REVIEW.md)：本次范围、实际验证、部署状态与发布边界。
2. [README](README.md)：用户安装、配置、费用、数据和功能边界。
3. [完整配置](docs/CONFIGURATION.md) 与 [发布操作](docs/PUBLISHING.md)。
4. [批量设计](docs/BATCH_JUDGMENTS.md)、[实现状态](docs/IMPLEMENTATION_STATUS.md) 和 [工作约定](AGENTS.md)。

`ROADMAP.md` 和旧 M1/M2 报告保留历史证据，不再作为待办，也不能覆盖上述现状。不要重新启动已取消的缓存调查，不要使用独立 Codex 审查。

## 离线复现

```bash
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
python3 tools/verify_handoff.py
python3 tools/build_release.py
```

开发依赖只是可复现测试基线，不反向锁定或升级生产。测试使用隔离数据、假模型和假发送，不能借验证之名调用收费模型或向真实群发消息。

## 当前授权

用户授权本轮备份并更新 Pi 插件，保留现有正式运行范围、参数及队列。用户将自行审阅再发布；没有授权推送、公开仓库、重写历史或上架。后续维护仍须核对当时的实际版本、配置与授权。
