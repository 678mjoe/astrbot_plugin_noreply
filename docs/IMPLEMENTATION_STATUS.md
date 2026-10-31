# M1 离线实现状态

日期：2026-10-31。M1 本地实现及离线产品验收完成；实际运行环境、真实模型和平台验收保留为 M2 门槛。

## 已实现

- `engine.py`：独立输出协议；平台实例/机器人/物理群隔离；不消费式观察窗口；消息 ID 去重；正文、元数据、JSON 背景、去重信息与群状态容量均有界。
- 每群一个生成任务，忙时只观察，不排逐条请求；注入单调时钟和假模型；尝试间隔、live 冷却、整体截止时间；异常与取消释放 busy。
- epoch/ticket 使清空、显式唤醒、开关与路由变更、卸载后的旧结果失效。Provider 吞掉超时或调用者取消后返回的文本也不被接纳。
- `main.py`：明确的插件/来源 AI 开关与白名单检查；自发言、私聊、显式唤醒、命令与非纯文本不新增自主调用；原始 UMO 选现有模型、指定 ID 查询现有人设。
- 一次非流式 `Context.llm_generate(contexts=[], tools=None, request_max_retries=0)`；检查角色、工具字段、分片、消息链类型、文本与长度，失败不回退到 Agent。
- shadow 无发送结果且不把假想回复写入窗口；live 只产生普通文本结果，不 `stop_event()`，不返回 ProviderRequest。
- `/noreply_clear` 由 AstrBot 管理员权限过滤器和入口权限检查保护，只清本群插件记忆；`terminate()` 失效、取消并收尾生成任务。
- `_conf_schema.json` 出厂关闭、shadow、空白名单；`metadata.yaml` 使用现有本机作者身份，不填写虚构仓库 URL 或未决定的许可证。

## 环境与真实执行结果

开发环境仅位于项目 `.venv`：Python 3.12.13、AstrBot 4.29.3、pytest 9.2.0、pytest-asyncio 1.4.1、Ruff 0.17.3。实际 SDK 发行包元数据要求 Python >=3.12。`.python-version` 和 `uv.lock` 用于本地复现，不给 Pi 加版本锁。

初始化执行：

```bash
/opt/homebrew/bin/uv venv --python 3.12 .venv
/opt/homebrew/bin/uv sync --group dev --prerelease if-necessary-or-explicit
/opt/homebrew/bin/uv sync --locked --group dev
```

本机 uv 不支持旧升级记录里的 `--prerelease-package`，首次使用该参数退出 2；改用当前支持的解析策略后安装成功。项目只显式选择稳定的 AstrBot 4.29.3；锁文件保存实际解析出的全部开发依赖，不修改全局 uv 设置。最后一条锁文件复现命令退出 0。

测试先行证据：

| 阶段 | 真实结果 |
| --- | --- |
| 创建协议、记忆、状态机测试后运行 | 缺少 `engine`，3 个收集错误，退出 2 |
| 最小引擎实现后运行这三份测试 | 31 passed |
| 创建事件适配测试后运行 | 缺少插件 `main`，退出 2 |
| 事件适配实现后运行 | 40 passed |
| Provider 吞掉取消的补充测试 | 截止时间后错误得到 `reply`；调用者取消后未抛 `CancelledError`，分别退出 1 |
| 修复显式截止与取消检查后完整回归 | 83 passed |
| 增加完整配置边界并使用真实 SDK 配置/元数据加载器后 | **93 passed，1 warning，退出 0** |

最终产品命令：

```bash
PYTHONASYNCIODEBUG=1 .venv/bin/python -m pytest -q tests
.venv/bin/ruff check engine.py main.py tests
/opt/homebrew/bin/uv pip check --python .venv/bin/python
python3 tools/verify_handoff.py
python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/deployed-4.29.3/sources" python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py
GIT_MASTER=1 GIT_PAGER=cat git --no-pager diff --check
```

产品测试 93 项通过，Ruff 检查通过，156 个安装包依赖兼容；三组探针各 12 项通过。文档更新后完整性重检通过，覆盖 9 份源码快照和 11 个本地 Markdown 链接；最终 `git diff --check` 退出 0。`main.py` 和 `engine.py` 语言服务诊断为空。唯一测试告警来自 SDK 的 `tencent_record_helper.py` 导入标准库 `audioop`，未隐藏或修改第三方代码。

SDK 契约测试实际执行安装包的 Context、PersonaManager、ProcessStage、权限过滤器、AstrBotConfig 与插件元数据加载器；模型、路由管理器和数据库外部边界使用假对象。测试根目录临时隔离并清理，不启动完整 AstrBot，不监听端口，不调用真实模型。另把适配器接到三组保存的 AST 方法上验证已知接口。

## 使用与交付

白名单每项为 `平台实例ID|机器人ID|真实群ID`，精确匹配，不支持通配符。
默认窗口 40 条、正文每条 500 字符、完整 JSON 背景 12000 字符；
默认尝试间隔 10 秒、发言冷却 30 秒、总截止时间 30 秒、回复上限 600 字符、
群状态最多 128 组。达到群容量后拒绝分配新群，已有状态保留到卸载。

开关、人设、shadow 与白名单在消息入口和生成返回后重读；已观察到的变更使旧结果失效。
更改窗口、容量或时间限制需重载插件；重载前自主插话失败关闭并给出固定日志提示。
框架禁用/重载通过 `terminate()` 收尾；仅改写内存配置而没有任何事件、
钩子或框架重载时，不承诺能观察两次检查之间瞬时发生又恢复的变化。

普通 `/reset` 保留正常行为并使本插件旧生成失效，不清空插件窗口。
`/noreply_clear` 只允许 AstrBot 管理员，按框架唤醒前缀调用，无额外回复。
发送阶段观察的是本地结果，不是送达回执；live 本地正文在提交发送时记入窗口并开始冷却，
即使后来发送失败也可能保留。插件没有发送重试或 exactly-once 保证。

离线运行包文件清单：

```text
astrbot_plugin_noreply/
  main.py
  engine.py
  metadata.yaml
  _conf_schema.json
  README.md
```

不要打包 `.venv`、`tests`、源码快照、开发依赖、缓存、运行配置、密钥或聊天数据。
以上描述是 M1 原交付时状态；随后 M1 已提交为 `4dae7cf`，用户已创建 GitHub 仓库并授权本次上传。M2 的部署、真实调用与未通过项见 [试验记录](M2_TRIAL_2026-10-31.md)，不据此倒改 M1 历史测试。

## 未验证边界

1. 本轮未连接 Pi 复核服务版本或 Python，最近部署记录 4.29.3 仅是已有观测；部署前必须重新核验。
2. 未把插件安装到 Pi，未重启任何真实服务。实际框架加载、平台发送、关闭与 @/命令回归仍需 M2。
3. 未调用真实模型；协议遵循率、插话质量、过度沉默、费用与平台送达未知。真实 shadow 也需要另行放行。
4. 4.29.1 与固定研究提交只测试保存的接口，不声称完整旧 SDK 或所有新版兼容。
5. 不含完整 Agent 的工具、知识库、摘要与标准 LLM 钩子；不自动同步所有人设覆盖规则。
6. 正常会话历史与插件窗口没有双向统一，窗口重启丢失，M3 尚未实施。
7. 整体超时依赖 Provider 配合取消完成清理；Provider 吞掉取消后返回的正文会被拒绝，插件不会提前放开 busy 再启动第二次请求，也不能撤销已发出的远端请求。
