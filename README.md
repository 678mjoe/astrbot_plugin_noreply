# AstrBot NoReply

让同一个聊天模型根据群聊背景，选择**接话**或输出 `<noreply>` 保持沉默的 AstrBot 插件项目。

**当前状态：M1 离线验收通过，M2 已安装并完成一次有真实自主生成样本的单群 shadow。** 观察到一条候选回复，未启用本插件 live；同时发现测试群绑定档的内置概率回复仍开启，须先处理竞争再继续验收。试验已自动关闭。见 [M2 记录](docs/M2_TRIAL_2026-10-31.md) 与 [上下文设计对照](docs/CONTEXT_DESIGN.md)。

## 给本地 agent 的入口

先读 [AGENTS.md](AGENTS.md)、[实现状态](docs/IMPLEMENTATION_STATUS.md) 和 [验收清单](docs/ACCEPTANCE.md)。[ROADMAP.md](ROADMAP.md) 保留实施步骤及 M2/M3 边界；[START_HERE.md](START_HERE.md) 是后续接手入口。

```bash
cd ~/projects/astrbot_plugin_noreply
/opt/homebrew/bin/uv sync --locked --group dev
.venv/bin/python -m pytest -q tests
.venv/bin/ruff check engine.py main.py tests
python3 tools/verify_handoff.py
python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/deployed-4.29.3/sources" python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py
```

`uv sync` 会下载开发依赖；测试使用假 Provider、假数据库边界和真实 SDK 类型，不启动完整 AstrBot。测试执行阶段拒绝网络套接字连接，SDK 临时数据根目录在测试后清理。

`.python-version` 将本地复现环境选为 Python 3.12，`uv.lock` 固定开发依赖，含 AstrBot 4.29.3。这是开发环境选择，不改变 Pi 的版本。插件自身不新增第三方运行依赖；标准库探针仍可使用系统 Python 3.9。产品代码要求 Python >=3.12。

## 已定的最小路线

- 独立插件，不修改 AstrBot 核心。
- 监听白名单群中未被 `@`、唤醒词、命令触发的普通文本；正常对话和私聊保留原链路。
- 自管有界近期群消息窗口，沉默后仍保留背景。
- 调用一次 `Context.llm_generate()`，非流式、不开放工具；用明确指定的现有人设。
- 精确识别独立 `<noreply>`；不把它外发或当成助手发言写入历史。
- 默认关闭，默认 shadow，空白名单表示任何群都不启用。
- 先完成离线插件，再 shadow，最后经用户批准在单群 live。

第一版不承诺自主插话自动进入正常 `@` 对话历史；完整上下文桥接单列为后续阶段。

## 配置与使用边界

`_conf_schema.json` 中的出厂值为 `enabled=false`、`shadow_mode=true`、`all_groups=false`、`allowed_groups=[]`、`persona_id=""`。必须显式配置现有人设，并选择全群模式或准确群条目；没有模型或人设时不回退到其他模型或人设。

每个白名单条目采用 `平台实例 ID|机器人自身 ID|真实群 ID`，三个字段精确匹配。例如 `test-adapter|test-bot|test-group` 只说明格式，不对应真实群。不支持通配符；默认 `all_groups=false` 时空列表不匹配任何群。显式设置 `all_groups=true` 才开放所有群，不再检查该白名单；仍尊重来源 AI 开关，私聊、显式唤醒和命令不增加自主调用。同群不同发送者共享窗口，即使 AstrBot 的 UMO 按发送者隔离。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `all_groups` | false | 显式全群模式；关闭时仅匹配准确白名单 |
| `window_messages` | 300 | 每群最多保留的观察条数 |
| `message_chars` | 0 | 可选正文字符上限；0 为不截断 |
| `context_chars` | 0 | 可选完整 JSON 字符上限；0 为不限制，启用时至少 512 |
| `attempt_interval` | 10 秒 | 包括沉默和失败的生成尝试间隔 |
| `reply_cooldown` | 30 秒 | 本地 live 结果交给发送阶段后的发言冷却 |
| `timeout` | 30 秒 | 模型路由、人设查询与生成的整体截止时间 |
| `max_reply_chars` | 600 | 可接受回复的字符上限 |
| `max_groups` | 128 | 群状态容量；满后拒绝新群，不驱逐忙碌任务 |

数值和开关类型不合法时关闭自主插话。修改窗口、容量或时间参数后需重载插件；重载前自主插话关闭并记录提示。开关、shadow、人设或白名单在事件入口及生成返回后重新检查，已观察到的变更使旧结果失效；框架禁用或重载调用 `terminate()` 取消未完成任务。没有后台定时器或逐消息队列，忙时只观察，下一条消息才触发下一次机会。

AstrBot 管理员可在当前群使用 `/noreply_clear` 清空插件窗口并使旧生成失效；命令前缀遵守 AstrBot 的唤醒配置。该命令不输出确认文本，也不调用模型。普通 `/reset` 仍由 AstrBot 处理，会使本插件旧生成失效，但不清空插件窗口，不表示两套历史已经统一。

自主 live 正文在交给本地发送阶段时加入窗口并开始冷却；普通纯文本发送结果通过 `after_message_sent` 观察，每个事件结果去重。两者均不是平台送达证明；发送失败时窗口可能保留本地已提交的正文，插件不重发、不重新请求模型。shadow 假想发言不会加入窗口。

插件默认日志仅包含决策、时延、输出长度、错误类别和固定配置提示，不记录群聊全文、用户 ID、完整响应或密钥。新增诊断开关默认关闭；经明确放行可将实际输入、模型输出正文与决策写入私有 JSONL，见 [诊断记录说明](docs/DIAGNOSTICS.md)。窗口仅驻留内存，重启丢失。Provider 和 AstrBot 自身的日志策略仍由原系统管理。

这条直接生成路径不执行完整 Agent 的工具循环、知识库检索、会话摘要、标准 LLM 请求/响应钩子或全部人设覆盖规则，只使用明确配置的人设系统提示词。

## 版本基线

| 对象 | 基线 |
| --- | --- |
| 交接时观测到的部署基线 | AstrBot **4.29.1**，要求 Python **>=3.12**；以后可能更新 |
| 最近部署核验 | Pi 实际服务为 AstrBot **4.29.3** / Python **3.12.13**；关键源码 hash 与快照一致，见 [M2 记录](docs/M2_TRIAL_2026-10-31.md) |
| 本地产品验证 | Python **3.12.13**，实际安装的 AstrBot **4.29.3** SDK；不启动服务器 |
| 首次源码研究 | `3d92821ad9d90df33fc242e7b159a3260075eb2c` |
| 固定回归探针 | 默认读取交接时保存的 4.29.1 快照，不代表 Pi 当前仍是该版本 |

三组快照保留各自来源和 hash。产品测试同时把适配器接到各组 AST 提取方法上；这只覆盖保存的接口，不等于用完整 4.29.1 框架或所有平台验证了插件。详见 [兼容性记录](docs/COMPATIBILITY.md)。

**兼容性原则：4.29.1 只是已验证的回归基线，不是永久锁定目标。** 实施、部署与升级后均要重新核验实际运行版本及所需 API；优先使用稳定公开接口，不为保持旧基线而降级、锁定或自动升级 Pi。未知版本不能仅凭版本号宣称兼容。

## 仓库内容

```text
AGENTS.md                 本地 agent 的工作边界
main.py                   配置、事件、模型/人设查询、发送阶段与生命周期适配
engine.py                 输出协议、有界观察窗口与生成状态机
diagnostics.py            默认关闭的私有、有容量上限的逐次诊断记录
metadata.yaml             插件元数据，无虚构的仓库 URL 或许可证
_conf_schema.json         AstrBot 配置 schema 与安全默认值
pyproject.toml / uv.lock  开发依赖与复现锁文件，不随插件运行包安装
tests/                    产品测试与实际 SDK/快照契约测试
START_HERE.md             后续接手入口
ROADMAP.md                实施路线及后续阶段，优先于历史材料
docs/IMPLEMENTATION_STATUS.md  已执行命令、实现状态与未验证边界
docs/ACCEPTANCE.md        离线证据与部署待验事项
docs/COMPATIBILITY.md     实际部署与研究快照的区别
docs/OPERATIONS.md        部署定位与人工放行边界
docs/archive/            原研究报告及详细历史路线图
research/probe.py         可离线复现的 12 项源码接口探针
research/snapshots/       三组明确区分的只读源码快照及 hash
research/NOTICE.md        快照来源与第三方许可证说明
tools/verify_handoff.py   文档路径和源码快照完整性检查
```

仓库目标为 [678mjoe/astrbot_plugin_noreply](https://github.com/678mjoe/astrbot_plugin_noreply)，由用户创建并授权本次上传；不自动更改可见性或部署。第三方快照保持 AstrBot 原许可证；本插件自身的许可证和公开发布事项暂未决定。

当前离线交付文件清单为 `main.py`、`engine.py`、`diagnostics.py`、`metadata.yaml`、`_conf_schema.json` 和运行说明 README。后续获准部署时可把这六个文件放入同一插件目录；不要复制 `.venv`、开发锁文件、源码快照、缓存、运行配置或聊天数据。M2 的真实模型调用、安装和必要服务操作仍需另行放行。
