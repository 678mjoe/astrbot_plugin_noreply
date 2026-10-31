# AstrBot NoReply

让同一个聊天模型根据群聊背景，选择**接话**或输出 `<noreply>` 保持沉默的 AstrBot 插件项目。

**当前状态：开发交接仓库，不是已经实现的插件。** 已有路线图、约束、验收清单和可运行的源码接口探针；`main.py`、`engine.py` 等生产实现尚未创建。

## 给本地 agent 的入口

先读 [AGENTS.md](AGENTS.md)，再读 [ROADMAP.md](ROADMAP.md) 和 [验收清单](docs/ACCEPTANCE.md)。可直接复制 [START_HERE.md](START_HERE.md) 中的提示词开始。

```bash
cd ~/projects/astrbot_plugin_noreply
python3 tools/verify_handoff.py
python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py
```

这些命令只用标准库，不调用真实模型，不连接聊天平台。**探针可以使用 Mac 系统 Python 3.9；已知回归基线 AstrBot 4.29.1 需要 Python >=3.12；实际实施时须按 Pi 当前版本的 SDK 要求选择开发 Python。** 不要因为探针通过，就使用系统 Python 3.9 安装完整 AstrBot。

Mac 已发现 `/opt/homebrew/bin/uv`。创建开发虚拟环境是后续实施步骤，不在此交接阶段预先安装依赖或启动服务。详见路线图任务 0。

## 已定的最小路线

- 独立插件，不修改 AstrBot 核心。
- 监听白名单群中未被 `@`、唤醒词、命令触发的普通文本；正常对话和私聊保留原链路。
- 自管有界近期群消息窗口，沉默后仍保留背景。
- 调用一次 `Context.llm_generate()`，非流式、不开放工具；用明确指定的现有人设。
- 精确识别独立 `<noreply>`；不把它外发或当成助手发言写入历史。
- 默认关闭，默认 shadow，空白名单表示任何群都不启用。
- 先完成离线插件，再 shadow，最后经用户批准在单群 live。

第一版不承诺自主插话自动进入正常 `@` 对话历史；完整上下文桥接单列为后续阶段。

## 版本基线

| 对象 | 基线 |
| --- | --- |
| 交接时观测到的部署基线 | AstrBot **4.29.1**，要求 Python **>=3.12**；以后可能更新 |
| 本次升级后观测 | AstrBot **4.29.3**；参见 [升级与兼容性记录](docs/UPGRADE-2026-10-31.md) |
| 首次源码研究 | `3d92821ad9d90df33fc242e7b159a3260075eb2c` |
| 固定回归探针 | 默认读取交接时保存的 4.29.1 快照，不代表 Pi 当前仍是该版本 |

两版 `llm_generate()`、`get_current_chat_provider_id()`、`get_persona()` 的 AST 归一化结果一致；`ProcessStage.process()` 在研究快照中增加了 session-waiter 分派，不能把两个版本视为完全相同。详见 [兼容性记录](docs/COMPATIBILITY.md)。

**兼容性原则：4.29.1 只是已验证的回归基线，不是永久锁定目标。** 实施、部署与升级后均要重新核验实际运行版本及所需 API；优先使用稳定公开接口，不为保持旧基线而降级、锁定或自动升级 Pi。未知版本不能仅凭版本号宣称兼容。

## 仓库内容

```text
AGENTS.md                 本地 agent 的工作边界
START_HERE.md             可直接复制的实施提示词
ROADMAP.md                当前实施路线图，优先于历史材料
docs/ACCEPTANCE.md        尚待插件实现后的验收清单
docs/COMPATIBILITY.md     实际部署与研究快照的区别
docs/OPERATIONS.md        部署定位与人工放行边界
docs/archive/            原研究报告及详细历史路线图
research/probe.py         可离线复现的 12 项源码接口探针
research/snapshots/       两个明确区分的只读源码快照及 hash
research/NOTICE.md        快照来源与第三方许可证说明
tools/verify_handoff.py   文档路径和源码快照完整性检查
```

仓库只初始化本地 Git，不创建 GitHub 仓库、不设置远端、不自动安装到正在运行的 AstrBot。第三方快照保持 AstrBot 原许可证；本插件自身的许可证和公开发布事项暂未决定。
