# 本地接手入口

> 当前主路径已实现持久化逐消息批量判定，用户已批准全群正式 live；请先读 [批量设计](docs/BATCH_JUDGMENTS.md) 和 [当前状态](docs/IMPLEMENTATION_STATUS.md)。下文旧 M1/M2 单群试验与未部署描述为历史路线，不代表当前开关。正常 Agent 历史桥接仍未实现。

M1 离线实现已经完成；M2 已有一次成功自主生成的 shadow 样本，但因内置主动回复竞争尚未验收通过。先读 `docs/M2_TRIAL_2026-10-31.md` 与 `docs/CONTEXT_DESIGN.md`，再阅读 `AGENTS.md`、`README.md`、
`docs/IMPLEMENTATION_STATUS.md`、`docs/ACCEPTANCE.md` 和
`docs/COMPATIBILITY.md`，了解实际证据与部署边界。

在项目目录复现：

```bash
/opt/homebrew/bin/uv sync --locked --group dev
.venv/bin/python -m pytest -q tests
.venv/bin/ruff check engine.py main.py tests
python3 tools/verify_handoff.py
python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/deployed-4.29.3/sources" python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py
```

产品测试使用假模型/事件与真实 SDK 类型；每组 12 项探针只是保存的 SDK
接口证据。当前没有真实模型、平台或 Pi 插件部署验收，不重做无边界研究，
也不把历史升级授权解释为本插件的部署许可。

后续修改继续测试先行。准备 M2 时，先只读核验 Pi 实际服务版本和关键能力，
再由用户确定 Provider、人设、机器人与测试群并放行真实调用及部署。
shadow 仍然会产生模型调用。保持出厂关闭、shadow 开启、空白名单不匹配任何群；
不要读取真实密钥或聊天记录进入仓库，也不要自行安装插件、重启服务或启用 live。
