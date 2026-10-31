# AstrBot 群聊上下文感知与主动回复：源码研究

## 结论先行

**`possibility_reply` 是“每条符合条件的群消息独立按固定概率触发回复”，不是让模型评估消息值不值得回复。** 随机命中后，才进入聊天模型生成回复的流程。[1][2]

**当前内置“群聊上下文感知 → 主动回复”没有可直接切换的“模型自主判断是否回复”模式；但框架已经提供 Decision Provider（判断提供商）及 `self.context.decision` API，官方文档明确以“是否回复、回复哪条消息”为使用场景。需要插件或自定义逻辑把它接到群消息处理流程。** 两者不能混为一谈：有判断基础设施，不等于内置主动回复已经在使用它。[1][3][14]

## 一、研究范围与验证方法

研究对象为 `AstrBotDevs/AstrBot` 的 `master` 快照，提交：

```text
3d92821ad9d90df33fc242e7b159a3260075eb2c
```

通过 GitHub Git Trees API 获得完整文件目录和各文件 blob ID，再通过 Git Blobs API 获取相关源码。本文所有源码链接都固定在该提交，不随后续 `master` 更新漂移。

沿消息入口、群聊记录、主动回复判定、LLM 请求、历史查询工具和判断提供商逐层阅读。额外以 AST 提取源码中的实际类和方法，用标准库测试验证分支行为；外部框架依赖和事件对象使用最小桩替代。**不是完整 AstrBot 部署测试，没有接入真实 QQ 群，也没有调用收费模型。**

## 二、“群聊上下文感知”实际上包含不同功能

| 功能 | 配置 | 实际工作 | 会不会因此自己开口 |
| --- | --- | --- | --- |
| 群聊消息记录注入上下文 | `provider_ltm_settings.group_icl_enable` | 临时缓存群消息，在请求 LLM 前补入尚未消费的群聊背景 | 不会，只负责提供背景 |
| 持久化群聊消息记录 | `provider_ltm_settings.group_message_history_enable` | 保存标准化消息，向模型提供 `get_group_message_history` 工具查询历史 | 不会，只负责保存和查询 |
| 主动回复 | `provider_ltm_settings.active_reply.enable` | 对未被显式唤醒的符合条件群消息做概率判定，命中后请求聊天模型 | 会，但当前内置触发方法是随机概率 |

这三个开关并不是一个能力的不同名字，配置中也彼此分开。默认均关闭；临时缓存默认上限为 1000 条，持久化记录默认上限为 700 条，主动回复概率默认值为 `0.1`。[1][2][3]

### 2.1 临时群聊背景如何进入模型

主要实现位于 `GroupChatContext.handle_message()` 和 `on_req_llm()`：[1]

1. 以 `event.unified_msg_origin` 为键，在进程内维护 `deque` 缓存，并配有每个消息来源的异步锁。
2. 将消息整理为类似 `[昵称/时:分:秒]: 消息内容` 的文本，追加进缓存；超过配置条数上限则移除最旧项。
3. 在发送 LLM 请求之前，定位本次触发消息，将它之前的缓存消息包成 `<system_reminder>` 群聊背景块，追加到 `req.extra_user_content_parts`。
4. 这些背景不包括当前触发消息本身，当前消息通过正常 prompt 路径提交；已消费的记录与当前触发记录从这份临时缓存移除。若处理期间已经收到后续消息，后续消息保留，留待之后请求使用。

因此，它更接近“自上次消费以来的群聊背景补充”，而不是每次都从数据库重新塞入完整群历史。这份 `raw_records` 本身是进程内缓存，不是重启后自动恢复的持久化历史。[1]

消息格式化支持文字、图片占位或图片描述、分享卡片、提及和引用等。自动理解图片开启且指定了图片转述提供商时，记录图片消息可能额外调用模型；不开启时通常只记 `[Image]`。所以“不主动回复”不代表开启图片理解后一定没有模型开销。[1][3]

入口 `Main.on_message()` 只对包含 `Plain`、`Image` 或 `Json` 组件的消息进入这段处理；已匹配命令处理器的消息不记入这份临时群聊背景。**只开启主动回复而不开启 `group_icl_enable`，不会自动积累这份背景缓存。**[2]

### 2.2 持久化群历史是另一条路径

`Main.persist_group_message()` 保存收到的群消息，LLM 回复另由 `persist_llm_response()` 保存；模型请求构建阶段按配置加入 `GetGroupMessageHistoryTool`。[2][17]

工具名为 `get_group_message_history`，支持数量、向前分页、关键词和发送者筛选，查询限制在当前群会话范围。它是模型需要时主动调用的工具，并不是自动将持久化记录全文注入每次请求。[11]

“持久化”也不等于完整原始聊天备份：保存路径会把图片、语音、视频、文件等转换为标准化类型占位，避免保留本地路径。[20]

## 三、`possibility_reply` 的完整触发逻辑

### 3.1 判定函数就是固定概率抽样

核心代码在 `GroupChatContext.need_active_reply()`，第 112–130 行：[1]

```python
async def need_active_reply(self, event):
    cfg = self.cfg(event)
    if not cfg["enable_active_reply"]:
        return False
    if event.get_message_type() != MessageType.GROUP_MESSAGE:
        return False
    if event.is_at_or_wake_command:
        return False
    if cfg["ar_whitelist"] and (
        event.unified_msg_origin not in cfg["ar_whitelist"]
        and (
            event.get_group_id() and event.get_group_id() not in cfg["ar_whitelist"]
        )
    ):
        return False
    match cfg["ar_method"]:
        case "possibility_reply":
            return random.random() < cfg["ar_possibility"]
    return False
```

以上保留原始分支逻辑，省略了函数签名类型标注。

按正常群消息具有群 ID 的情况，其含义是：[1]

- 必须开启主动回复。
- 必须是群消息。
- 如果已被 `@`、唤醒词等正常唤醒，就不通过这条主动回复路径重复触发。
- 白名单为空时不做这层过滤；非空时接受匹配的 UMO 或群 ID。
- 方法为 `possibility_reply` 时，从 `[0, 1)` 抽取一个随机数，小于配置概率才返回真。
- 方法填成未实现的其他值，不会神奇切换到智能判断，而是返回假。

默认概率 `0.1` 对应 10%；`0.01` 对应 1%；`0` 不会随机触发；`1` 表示每条通过上述筛选、且进入该入口的消息都命中随机门槛。**这不是“每十条必回一条”，也不保证回复均匀分布。**[1][3]

这里没有读取消息语义，也没有调用判断模型；没有根据是否与机器人相关、是否值得插话或群聊热度动态算出概率。这段主动触发判定本身也没有冷却时间或“一轮话题只回复一次”的逻辑。系统另有通用管道限制，但不能把它们当作这里已有的智能插话策略。[1]

### 3.2 随机命中以后发生什么

`Main.on_message()` 的顺序是先得到 `need_active`，按 `group_icl_enable` 决定是否记录消息，然后在命中时：[2]

1. 获取当前 UMO 对应的聊天模型提供商。
2. 查找当前已有会话 ID 和会话对象。
3. 以当前消息文字为 prompt，收集当前消息中的图片。
4. `yield event.request_llm(...)`，交给正常聊天生成流程。

**随机命中不等于最终必定成功发送。** 没有可用聊天模型、没有已有会话、生成或发送失败，都可能导致没有回复。特别是这条路径不会主动新建会话；源码在找不到当前会话时给出的提示是关闭会话隔离，并用 `/new` 创建会话。实际应以对应 UMO 能否查到已有会话为准。[2]

显式 `@`、唤醒词、引用机器人的消息等由常规唤醒路径处理，不是由 `possibility_reply` 的概率决定。因此把主动概率设为零，不等于关闭所有正常唤醒回复。[7]

### 3.3 两个容易误读的地方

**配置提示中的“合适的时机”不是语义判断的证据。** UI 对主动回复的描述确实写着“是否在合适的时机主动发消息”，但方法选项只有 `possibility_reply`，实际判定如上，是随机抽样。[1][3]

**源码里虽然读了 `active_reply.prompt`，但这条链路没有使用它。** `cfg()` 取出它并返回 `ar_prompt`，`need_active_reply()` 没有将其交给模型，`Main.on_message()` 构造主动回复请求时也没有用它。不能据此认为存在一个隐藏的“通过 prompt 判断要不要回复”功能。[1][2]

## 四、有没有让机器人自己决定要不要回复的功能？

需要分两层回答。

### 4.1 现成内置配置：没有接好的语义决策模式

在这个提交中，内置主动回复的配置选项只有 `possibility_reply`，运行时 `match` 分支也只有这一项。这条入口不存在“先让 LLM 理解群聊，再决定是否插话”的第二阶段判断。[1][3]

因此，如果问题是：

> 我能否直接在“主动回复方法”里选择，让模型自己看群聊判断该不该回？

**答案是：当前这份内置实现不能，仅靠该配置做不到。**[1][3]

### 4.2 官方开发能力：有，而且文档直接以“是否回复”为例

官方开发文档的“结构化判断”明确写道：判断提供商用于低延迟结构化选择，例如“是否回复、回复哪条消息或选择执行分支”；插件经 `self.context.decision` 调用，与聊天模型分开。[14]

其示例包括：[14]

```python
should_reply = await self.context.decision.yes_no(
    question="Should the assistant reply now?",
    state=recent_messages,
    threshold=0.7,
)
```

这只是官方提供的**判断接口用法**，不是打开配置后自动接入的完整群聊插件。

实现上，`DecisionService.yes_no()` 将问题和调用方传入的 `state` 交给 Decision Provider，再以返回的真假概率与阈值比较。可以指定 `provider_id`；不指定时使用默认判断提供商。参数 `default=False` 可用于在发生 `DecisionError` 时默认不回复。这个概率与 `possibility_reply` 的固定抽样概率含义不同：前者来自判断模型，后者由用户直接配置。[6]

框架在 `Context` 初始化时创建 `self.decision = DecisionService(provider_manager)`。但 `GroupChatContext.need_active_reply()` 并未调用它，这就是“框架提供判断能力”和“内置主动回复尚未接入”的代码分界。[1][13]

当前提供的判断适配器是 `system_one_decision`，即 System One / Jev 接口；配置模板包含 TypeSafe、OpenRouter、Vercel 和 Custom。它不是把随便一个现有普通聊天模型 ID 填进去就自动成为判断模型；普通聊天模型若要承担判断，需要插件自行调用并处理结构化结果。[3][25]

### 4.3 真正需要“自然插话”时的建议

以下是基于源码的设计建议，不是声称项目已经实现：

```text
接收群消息并缓存近期背景
          ↓
群白名单、机器人自身消息过滤、冷却或短时合并
          ↓
判断模型：当前场景是否适合插话？
          ├─ 否 → 不请求聊天模型、不发送
          └─ 是 → 聊天模型结合人设与背景生成回复
```

可以让插件用官方 Decision API 实现，也可以用普通聊天模型做单独的结构化判定。若插件接管自主插话，建议关闭内置随机主动回复，避免它绕过判断逻辑另行触发。

不建议把“概率设为 1，再只在人设里要求不该回时输出空字符串”当成同等替代方案：这仍会使每条合格消息进入聊天模型请求，没有解决前置决策的调用成本；框架也没有把某个统一的 `NO_REPLY` 标记接到这条概率入口。若要用特殊标记，应该由插件明确拦截处理，而不是依赖口头提示。[1][2]

## 五、与“主动型能力 / 未来任务”的区别

官方另有“主动型能力”，通过 `future_task` 在指定时间或按计划唤醒 Agent，执行任务后投递结果。文档明确说它不是一直运行的自主监控。因此，“能定时主动发消息”不能作为“能实时理解群聊并自行决定插话”的证据。[15]

## 六、实际执行的逻辑测试

提取当前快照的 `GroupChatContext` 原始类，以及 `Main.group_context_enabled()`、`Main.on_message()` 原始方法，保留算法本体、移除外部注册装饰器，使用标准库 `unittest` 和最小事件桩验证。随机值通过 mock 固定，目的是检查分支，不是用模拟统计替代真实模型结果。

真实执行结果：

```text
Ran 13 tests in 0.023s
OK
```

覆盖：

- 概率判断使用严格小于；等于阈值不命中。
- 概率为零和一的边界。
- 主动回复关闭、私聊、已唤醒消息均不抽样。
- 白名单拒绝，以及群 ID、UMO 两种匹配。
- 未实现的方法返回假。
- 修改 `active_reply.prompt` 不改变概率门槛结果。
- 判定函数没有相邻调用冷却。
- 临时缓存超过上限移除最旧记录。
- 注入只包含触发消息之前的内容，并保留后来消息。
- 只开主动回复不自动记录群聊背景。
- 已匹配命令处理器的消息不记入临时背景。

这些测试验证的是源码控制流，不验证真实平台权限、线上模型质量、网络可用性或插件兼容性。

## 最终判断

**AstrBot 当前内置群聊主动回复是“随机决定何时调用聊天模型”；官方已经提供“让模型判断要不要回复”的开发接口，但还需要插件或自定义逻辑把判断接入群消息流程，不能把 `possibility_reply` 理解成智能插话。**[1][2][14]

## Sources

[1] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/builtin_stars/astrbot/group_chat_context.py
[2] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/builtin_stars/astrbot/main.py
[3] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/config/default.py
[6] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/provider/decision.py
[7] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/pipeline/waking_check/stage.py
[11] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/tools/message_tools.py
[13] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/star/context.py
[14] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/docs/zh/dev/star/guides/ai.md
[15] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/docs/zh/use/proactive-agent.md
[17] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/astr_main_agent.py
[20] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/platform_message_history_mgr.py
[25] https://github.com/AstrBotDevs/AstrBot/blob/3d92821ad9d90df33fc242e7b159a3260075eb2c/astrbot/core/provider/sources/system_one_decision_source.py
