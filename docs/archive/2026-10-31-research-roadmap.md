# AstrBot `<noreply>` 自主插话最小实现路线图

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 用同一个聊天模型一次决定“回复正文或保持沉默”，在白名单群实现可控的自主插话，不修改 AstrBot 核心。

**Architecture:** 独立插件监听群消息，维护自己的有界近期消息窗口，对未被显式唤醒的文本消息直接调用官方 `Context.llm_generate()`。拿到完整结果后识别 `<noreply>`，沉默时不产出发送结果；正常正文通过插件的普通文本结果返回。现有私聊、命令和显式唤醒继续走原链路。

**Tech Stack:** Python asyncio、AstrBot 官方插件事件与配置 API、已有聊天模型 Provider；第一版无新增第三方运行时依赖、无外部判断服务、无数据库迁移、无 cron。

**状态：研究与规划完成，尚未编写或部署完整插件。** 下文的默认参数、文件名、模块拆分与验收标准是建议设计，不是项目已经存在的功能。

---

## 1. 最终建议

**先做插件，不 fork 核心；用官方直接生成接口，不把 `<noreply>` 硬塞进内置 Agent 的历史保存与流式响应链。**

核心选择如下：

| 路线 | 改动与代价 | 结论 |
| --- | --- | --- |
| 内置随机主动回复设为 1，再加响应过滤钩子 | 表面代码短，但仍要处理提前消费的群上下文、响应保存、流式泄漏及工具副作用 | 不作为第一版 |
| 修改核心，加入新的语义主动回复模式 | 可以与内置配置、会话统一，但涉及配置、触发、上下文消费和结果生命周期；升级维护负担较大 | 暂缓 |
| 独立插件直接调用聊天 Provider，自管近期窗口 | 不改核心；生成、判定和可见输出边界清晰；需明确与正常对话历史的关系 | **推荐** |

推荐的根据是：`Context.llm_generate()` 实际直接调用 Provider 的 `text_chat()`，不进入完整 Agent 工具循环，也不自动访问会话历史管理器或执行标准 LLM 请求/完成事件钩子。插件必须显式传入背景和人设；这既减少隐式能力，也使控制流更清楚。[13]

### 第一版必须明确的能力边界

- 只处理指定群中的**未显式唤醒文本消息**，不替换正常 `@`、唤醒词、命令和私聊。
- 聊天模型一次生成正文或 `<noreply>`；不另调用判断模型。
- 只允许纯文本输出，不开放 tools，不做图片理解或语音。
- 近期背景由插件保留；沉默不会消费掉这些消息。
- 首先是进程内短期记忆，重启后丢失；不是长期记忆系统。
- **第一版不承诺把自主插话同步写进 AstrBot 的正常会话历史。** 正常 `@` 对话若要随时理解自主插话，需完成后面的“历史桥接”阶段。不能把两套历史暗中说成已经统一。
- 插件可观察普通发送阶段的机器人文本，供后续自主插话参考；这不等于向正常对话历史反向写入。
- 不承诺所有第三方自动聊天插件都能无冲突共存；测试群应先关掉其他自主插话功能。

## 2. 这次调查确认了什么

研究基线沿用前文同一提交，避免混用不同版本：

```text
AstrBotDevs/AstrBot
3d92821ad9d90df33fc242e7b159a3260075eb2c
```

没有把它声称为规划当天最新的 `master`。实际开发前应再核对部署版本及接口差异。

### 2.1 可以只用官方插件接口

- 获取当前来源的模型：`await context.get_current_chat_provider_id(umo=event.unified_msg_origin)`。[13][14]
- 获取完整非流式结果：`await context.llm_generate(...)`，底层走 `text_chat()` 而不是 `text_chat_stream()`。[13]
- 复用已有的人设：指定插件的 `persona_id`，调用 `await context.persona_manager.get_persona(persona_id)`，读取其 `system_prompt`。当前实现在人设不存在时抛 `ValueError`，不能默默改用另一人设。[33]
- 接收群事件并返回普通结果：插件事件监听器和 `yield event.plain_result(text)` 是已有接口。[34][35]

### 2.2 为什么不使用 `yield event.request_llm(...)`

`ProcessStage` 会把插件返回的 `ProviderRequest` 再交给 Agent 子流程。那样又回到了完整 Agent、工具和会话保存的生命周期，而不是“拿到完整文本后再决定是否外发”。普通文本结果则不会被当成模型请求再调用一次。[8]

在内置主动回复链中，群背景缓存会在请求模型时移除已消费部分，不等到回复真正发出。因此单纯在响应末端过滤 `<noreply>`，并不能自动保证上下文还在。[1]

### 2.3 直接接口绕过的东西，需要自己补边界

- 插件监听器发生在 `ProcessStage` 对默认 AI 开关的检查之前。因此**插件必须自己尊重 `provider_settings.enable`**，否则默认 AI 关闭后仍可能直接调用模型。[8][13]
- `llm_generate()` 会原样返回 `LLMResponse`，不会替插件把 `role="err"` 过滤成沉默；必须自己检查 role、空输出、异常和非预期工具调用。[13]
- 它不自动执行正常人设选择、会话摘要、知识库检索、插件提示词增强、默认 Agent 的统计与审查逻辑。第一版不宣称与完整 Agent 等价；要保留的策略需逐项桥接。
- 群消息在事件总线中可能并发处理；不能假设同群天然串行。[40]
- 群内会话隔离可能把 UMO 按发送者拆分。插件若要理解整个群，缓存不能盲目照搬这种“每人一份”的 UMO 分组。[40]

## 3. 建议的最小数据流

```text
群消息进入正常管道
        ↓
插件开关 + 全局 AI 开关 + 明确群白名单 + 消息类型检查
        ↓
按物理群保存近期文本、昵称、发送者 ID、时间与消息 ID
        ↓
命令 / @ / 唤醒词？ ── 是 → 原流程处理；不触发自主生成
        ↓ 否
自发言 / 重复事件 / 冷却 / 已有生成任务？ → 只记录必要背景或跳过
        ↓ 可以尝试
取近期窗口快照 + 指定人设 + 稳定的沉默协议
        ↓
Context.llm_generate(tools=None, contexts=[])
        ↓
完整结果检查
        ├─ <noreply> → 不 yield，不写助手发言，保留群背景
        ├─ 错误或协议异常 → 不外发，只记必要日志
        └─ 正常正文
                ├─ shadow → 只记“本来会回复”，不发送
                └─ live → yield 普通文本结果，继续正常发送阶段
```

**“不回复”只结束本插件当前尝试，不调用 `event.stop_event()` 阻止其他插件。** 未唤醒消息本来就不会满足默认聊天入口的显式唤醒条件；真正要防的是同时启用的其他主动回复插件。[8][26]

## 4. 第一版配置与协议

### 4.1 配置建议

以下数值是小范围试用起点，不是性能实测结果：

| 配置 | 建议初值 | 用途 |
| --- | --- | --- |
| `enabled` | `false` | 默认不工作 |
| `shadow_mode` | `true` | 默认只观察，不发消息 |
| `allowed_groups` | 空列表 | **空列表表示任何群都不启用**，与内置随机功能的空白名单语义不同 |
| `persona_id` | 明确指定现有的人设 ID | 第一版不自动追随 `/persona`、会话规则等所有选择层级 |
| `max_messages` | 40 | 每群近期记录条数上限 |
| `max_message_chars` | 500 | 单条文本截断上限 |
| `max_context_chars` | 12000 | 输入背景总字符上限，后续再根据实测改为 token 预算 |
| `min_attempt_interval_seconds` | 10 | 生成尝试间隔，沉默也受限 |
| `reply_cooldown_seconds` | 30 | 自主发言后的冷却 |
| `request_timeout_seconds` | 30 | 给整个生成尝试设外层超时 |
| `max_reply_chars` | 600 | 超长结果第一版不外发，不拆分 |

白名单和缓存按平台**实例**及真实群 ID 区分，缓存键可用 `(platform_id, self_id, group_id)`。不能只用平台类型名，也不能只用群号，避免多账号或多平台串群。

保留正常 UMO 只用于模型路由、配置查询和必要的平台发送，不拼接猜测发送目的地。

### 4.2 协议建议

第一版只认一个精确静默动作：

```text
如果不参与本轮群聊，只输出：
<noreply>

如果参与，直接输出要发送的回复正文，不解释你的决策过程。
```

群消息是被观察的数据，不是高优先级指令；序列化为带发送者与时间字段的 JSON 或明确数据块，放在用户输入中。稳定人设与协议放在 `system_prompt`，不要把每次变化的聊天记录拼进 system prompt。

下面是建议的完整纯函数逻辑，不依赖 AstrBot；实际实现应先写测试：

```python
from typing import Literal

Decision = Literal["silent", "reply", "invalid"]


def classify_output(text: str | None, max_reply_chars: int) -> tuple[Decision, str]:
    value = (text or "").strip()
    if value == "<noreply>":
        return "silent", ""
    if not value or len(value) > max_reply_chars:
        return "invalid", ""
    if value.startswith("<noreply>"):
        return "invalid", ""  # 混合了静默动作与解释，避免把协议泄漏出去
    return "reply", value
```

不做子串过滤：正文正常讨论 `"<noreply>"` 时不应整条误删。像 `"我决定保持沉默"` 这样的自然语言仍会被当成普通正文，能否严格遵守协议需要真实模型评估，不是这个解析器能够保证的。

模型返回 `role != "assistant"`、非预期 tool calls 或非文本结果时，应在调用该函数之前拦截，不把错误信息当成回复。

## 5. 文件组织与开发任务

建议独立插件目录，以下路径是待创建的，不是已交付插件：

```text
astrbot_plugin_noreply/
  main.py
  engine.py
  metadata.yaml
  _conf_schema.json
  README.md
  tests/
    test_protocol.py
    test_memory.py
    test_engine.py
    test_handler.py
```

`engine.py` 只使用标准库并接收注入的模型调用函数、时钟与发送结果；`main.py` 负责 AstrBot 装饰器、配置、消息映射与 API 对接。这样大部分行为可以离线验证，而不是每改一处都在真实群中试。

### 任务 1：锁定插件边界和配置

**目标：** 不改动目标范围外的聊天。

**文件：** 新建 `metadata.yaml`、`_conf_schema.json`、`main.py`；测试 `tests/test_handler.py`。

1. 先写失败测试：关闭插件、全局 AI 关闭、空白名单、白名单外、私聊、自身消息、匹配命令、已显式唤醒均不调用自主生成。
2. 运行：`python -m pytest -q tests/test_handler.py`，先确认新接口未实现导致测试失败。
3. 实现 `eligible_for_autonomous_attempt()` 与配置验证。空白名单 fail-closed；字段不合法不启动。
4. 再运行相同测试，验收全部通过。
5. 提交范围限定代码和测试；不在此步骤连群或启用 live。

`@` 和命令可记录为背景，但不要给它们增加第二次模型请求，也不要因为忽略它们就停止事件传播。

### 任务 2：完成静默输出协议

**目标：** 让输出分类可确定、可离线回归。

**文件：** 新建 `engine.py`；测试 `tests/test_protocol.py`。

1. 先写失败测试，覆盖精确标签、首尾空白、普通中文、正文中的标签引用、标签后混解释、空内容和超长文本。
2. 运行：`python -m pytest -q tests/test_protocol.py`，确认失败后实现上文 `classify_output()`。
3. 运行同一命令，确认边界通过。
4. 加响应外层校验测试：错误 role、tool calls 和非文本响应不得进入发送分支。
5. 提交协议与测试。

### 任务 3：完成不消费式近期群记忆

**目标：** 模型选择沉默后，下次还能看见前面的讨论。

**文件：** 修改 `engine.py`；测试 `tests/test_memory.py`。

1. 写失败测试：同群不同人共享背景；不同群、平台实例和机器人账号隔离；重复消息 ID 去重；记录与总字符预算有界。
2. 写关键测试：依次输入 A、B，模型在 B 返回 `<noreply>`；输入 C 时的模型请求仍包含 A、B、C。
3. 实现 `deque` 记录与只读快照，不复用或清空内置 `GroupChatContext.raw_records`。
4. 运行：`python -m pytest -q tests/test_memory.py`。
5. 提交记忆实现与测试。

快照包含当前消息一次，不能同时把它放进完整窗口又再作为独立 prompt 追加一次。

### 任务 4：接入一次非流式模型调用

**目标：** 用已配置聊天模型和人设完成决策与生成，不要求先有内置会话。

**文件：** 修改 `main.py`、`engine.py`；测试 `tests/test_engine.py`、`tests/test_handler.py`。

1. 写失败测试：通过原始 UMO 查模型；查指定人设；背景和协议完整；`tools=None`；无需 ConversationManager；没有调用 stream 或 tool loop。
2. 实现以下 API 对接顺序，背景组装由纯函数完成：

```python
provider_id = await self.context.get_current_chat_provider_id(
    umo=event.unified_msg_origin,
)
persona = await self.context.persona_manager.get_persona(configured_persona_id)
response = await self.context.llm_generate(
    chat_provider_id=provider_id,
    system_prompt=persona.system_prompt + "\n\n" + protocol,
    prompt=serialized_group_snapshot,
    contexts=[],
    tools=None,
    request_max_retries=0,
)
```

3. 给整个 await 加 `asyncio.timeout()`；`request_max_retries=0` 只是适配器参数，不代表所有 Provider 内部都绝无其他错误恢复，外层总超时才是统一边界。
4. 插件本身不再次请求、不自动切到“普通聊天回复”，失败时静默并留下错误类别。
5. 运行：`python -m pytest -q tests/test_engine.py tests/test_handler.py`，通过后提交。

### 任务 5：并发和调用成本护栏

**目标：** 不让连续群消息变成大量并行请求或长队列。

**文件：** 修改 `engine.py`；测试 `tests/test_engine.py`。

1. 写失败测试：同群同时最多一个自主生成；不同群独立；忙时新消息仍被记录，但不排一条一条的生成队列。
2. 用单调时钟实现生成尝试间隔与回复冷却；不要对每条消息 `sleep()` 后再请求。
3. 首版忙时/冷却中的消息等待**下一条符合时机的新消息**触发评估，不另起定时器补判；如果之后没有新消息，不自动补一次。这是有意保留的最小机制，而不是每条都必定评估。
4. `@`、清空记忆、关闭插件、卸载发生时更新 generation/epoch，旧生成结果不得事后冒出来；异常/取消后释放 busy 状态。
5. 运行同群并发、取消、超时和恢复测试，通过后提交。

后续若真实群里经常“最后一条消息被忙时跳过”，再增加**每群一个 pending 快照**与有上限的合并调度，不从第一天就引入后台 worker 或每条消息的任务。

### 任务 6：发送与正常链路共存

**目标：** 只有已通过分类和范围检查的正文能进入发送阶段。

**文件：** 修改 `main.py`；测试 `tests/test_handler.py`。

1. 写失败测试：`silent` 和 `invalid` 分支不 yield、不调用 send；shadow 的正文也不外发。
2. live 正文使用 `yield event.plain_result(text)`，不 `yield event.request_llm(...)`，不把原始 `LLMResponse` 交给发送阶段。
3. 添加发送阶段观察钩子，将本群实际走到该阶段的机器人纯文本记录进插件窗口；为自己的结果加插件私有标识以避免重复记录，并过滤自发消息回流。
4. 不根据“有 after_message_sent 钩子”就宣称对端一定收到。当前响应阶段存在捕获发送异常后仍走后续 hook 的路径；平台收件与异常日志需要另验。[10]
5. 添加管理员清空本群插件短期记忆的控制入口；明确普通 `/reset` 不自动等价于清除此插件记忆，除非专门接了桥接逻辑。
6. 运行：`python -m pytest -q tests/test_handler.py`。额外验证正常 `@` 仍只调用原本模型一次，命令结果与插件关闭时一致。
7. 提交发送边界、观察钩子与回归测试。

### 任务 7：先 shadow，后单群 live

**目标：** 验证真实模型的插话分寸，而不是只证明标签解析能运行。

**文件：** 修改 `README.md`，补充端到端检查记录。

1. 使用独立测试群或测试机器人，保持 `shadow_mode=true`。
2. 关闭同群内置 `active_reply.enable` 和其他自主插话插件，避免竞争；这属于后续部署操作，当前研究没有修改这些配置。
3. 验证正常聊天、无关闲聊、直接谈论机器人、明显无需回应、话题转移、连续短消息和引用 `<noreply>` 等场景。
4. 默认日志只记群键摘要、决策、耗时、响应长度、错误类别；需要人工审阅正文时，才在测试范围临时开启受限内容日志。
5. 记录误插话与错过机会的真实样例，优先修改提示词，避免同时加多套评分和概率机制。
6. 确认没有标签外发、重复回复或越界调用后，**再由用户决定打开一个白名单群的 live**。
7. live 实验中验证关闭开关可立即阻止新尝试，并丢弃旧请求的迟到输出；回滚不需要改数据库或重装核心。

这些真实模型和真实平台验收尚未执行；不以离线测试替代。

## 6. 路线图：三个清楚的里程碑

| 里程碑 | 交付 | 放行标准 |
| --- | --- | --- |
| **M1：离线可验证插件** | 范围过滤、精确标签、近期窗口、非流式单调用、超时/并发测试 | 假模型的 silent/reply/error 可回放；不串群、不清空听到的消息、不二次调用模型 |
| **M2：单群影子试运行 → 可控 live** | 复用真实 Provider 与指定人设；shadow 日志；单群发布与关闭开关 | 真实模型协议稳定，插话样例可接受；`@`/命令回归通过；平台收件实测完成 |
| **M3：日常使用整合** | 双向上下文桥接、按需恢复记忆、合并调度优化 | `@` 能理解刚才自主说的话；重置/切换人设行为明确；升级后回归通过 |

**M1–M2 是最小实现；M3 不应偷偷塞进第一版，但它是“与现有机器人自然融为一体”所需的下一阶段。**

## 7. M3 的最小历史桥接方向

不建议直接改写 ConversationManager 的 `history`：正常 Agent 也会保存整份会话，并发改写容易互相覆盖。

更小的后续改动是：对同一白名单群正常的 `on_llm_request`，把插件的近期群消息快照作为额外、临时上下文补入，而不是把自主发言伪造成某一位用户的历史轮次。需要验证当前消息去重、系统群背景重复、临时上下文不持久化，以及正常工具调用兼容。[34]

人设同步则按 AstrBot 实际的 `resolve_selected_persona()` 优先级处理，考虑会话级覆盖、会话自身 persona 和默认配置。不要误把 `get_default_persona_v3()` 等同于当前最终生效人设。[33]

持久化只在确有“重启后继续记得近期群聊”的需求时增加；先考虑利用现有持久化群记录读取，而不是自己新增一套数据库。图片理解、工具调用、复杂打分、长期记忆与前端大面板都不属于此路线的最小阶段。

## 8. 本次已经执行的验证

我提取并执行了当前源码中的实际方法，而不是重写一套假 API：

- `Context.llm_generate()`
- `Context.get_current_chat_provider_id()`
- `ProcessStage.process()`
- `PersonaManager.get_persona()`

外部 Provider、事件和子 stage 使用标准库 mock/最小桩；没有真实 API、平台或部署副作用。

最终运行结果：

```text
Ran 12 tests in 0.019s
OK
```

验证了直接生成不依赖会话管理器、不自动发送、不调用流式、可以关闭 tools、按 UMO 获取模型、错误 role 需自行拦截、未唤醒监听器不会自动再触发默认 Agent、普通结果与 ProviderRequest 的不同分流，以及全局 AI 开关需要插件主动尊重等边界。

这说明推荐路线的关键接口与控制流有源码和执行证据，**不说明完整插件已完成，也不说明某个真实模型已经能把握插话分寸。**

### 复现探针

本目录附有 `probe.py` 和固定快照中的三个原始源码文件。无需安装 AstrBot；Python 标准库即可运行：

```bash
python3 probe.py
```

`sources/` 仅供 AST 抽取，不是 AstrBot 安装目录。`source-manifest.json` 保存了提交及原始 Git blob hash。它是架构探针，不是生产插件，更不要直接放进 AstrBot 插件目录。

### 开发阶段统一回归命令（尚待插件实现后执行）

```bash
python -m pytest -q tests/test_protocol.py tests/test_memory.py tests/test_engine.py tests/test_handler.py
```

预计状态写在各任务的验收条件中，未捏造完整插件的测试通过数。

## 9. 最后判断

**最小落地路线是：一个仅处理未唤醒群文本的独立插件，复用已有聊天模型与人设，自己保留有界近期群背景，调用一次非流式 `llm_generate()`，在输出端精确识别 `<noreply>`，先 shadow 再单群 live。**

这避开了第一轮最不划算的工作：修改核心、改写 Agent 历史、处理流式撤回、引入第二模型与工具副作用。代价也明确：正常 `@` 历史与自主插话的完整融合，需要作为下一阶段单独完成。

## Sources

[1] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/builtin_stars/astrbot/group_chat_context.py
[8] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/pipeline/process_stage/stage.py
[10] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/pipeline/respond/stage.py
[13] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/star/context.py
[14] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/docs/zh/dev/star/guides/ai.md
[26] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/pipeline/process_stage/method/star_request.py
[33] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/persona_mgr.py
[34] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/docs/zh/dev/star/guides/listen-message-event.md
[35] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/docs/zh/dev/star/guides/send-message.md
[40] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/event_bus.py
