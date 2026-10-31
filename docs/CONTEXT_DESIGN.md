# 上下文输入：当前插件与原版的区别

这不是“复用原版注入格式，仅替换概率判断”。M1 使用独立的观察窗口和直接生成支路；原版群上下文仍服务原有 Agent 对话，两者未在 M3 前统一。

## 当前插件

`Engine.observe()` 记录合格纯文本，以平台实例/机器人/物理群分组，保留角色、发送者 ID、显示名、时间、消息 ID 和截断正文。消息 ID 做摘要去重；超长身份有界。读快照不消费，只有滑窗淘汰、清空或生命周期变化等才移除观察。

当前触发消息先观察，再取窗口快照，因此它包含在 JSON 中一次。`serialize_snapshot()` 把记录编码成 JSON 数组。模型请求采用：

```python
response = await context.llm_generate(
    chat_provider_id=provider_id,
    system_prompt=persona.system_prompt + "\n\n" + PROTOCOL,
    prompt=serialize_snapshot(snapshot),
    contexts=[],
    tools=None,
    request_max_retries=0,
)
```

因此 contexts=[] 不表示没有背景；背景放在本次 user prompt 中，不是从 AstrBot 标准会话历史自动读入。JSON 中的 role 是记录字段，不是把每个观察还原成 API 的独立 assistant/user 消息。

PROTOCOL 要求模型参考人设，选择正文或独立 <noreply>，并把 JSON 当不可信观察而非指令。这是提示与输入边界，不是对提示注入的绝对防护。

插件能观察正常发送阶段的合格纯文本结果，并在 live 时记录本地接受的自主正文；shadow 候选和沉默标记不写成机器人已发言。两种观察都不是平台送达回执。带图片、引用、@等非 Plain 组件的整条输入目前跳过，不是多模态摘要。

## 原版（本次安装的 AstrBot 4.29.3）

`GroupChatContext.handle_message()` 把消息格式化为带昵称、时间及内容占位/摘要的文本，按 UMO 缓存。`on_req_llm()` 在原版 LLM 请求钩子中，将当前消息之前的缓存拼成 <system_reminder> 背景块，追加到 `req.extra_user_content_parts`，而不是另开一次判断模型调用。

该方法在请求阶段就取走此前缓存及当前记录，保留当前记录之后新到的内容；不是等平台成功回复后才消费。正常会话历史仍由原版 Agent 管理。多模态覆盖较广，支持图片占位/可选描述、分享卡片、@提示与引用摘要；不等于这些内容全部原样作为多模态发给模型。

## 关键差异

| 项目 | 当前插件 | 原版群聊上下文感知 |
| --- | --- | --- |
| 输入位置 | 本次 user prompt 的 JSON 观察快照 | 原版 ProviderRequest 的 extra_user_content_parts |
| 分组 | 平台实例/机器人/物理群 | 原始或隔离后的 UMO |
| 读取窗口 | 非消费式，沉默后保留 | LLM 请求阶段消费此前缓存和当前记录 |
| 本次消息 | 包含在快照一次 | 主 prompt 保留，额外背景排除当前记录 |
| 消息覆盖 | 纯文本，含可观察的机器人纯文本 | 较丰富的格式化内容与占位/摘要 |
| 正常会话历史 | 不自动加载、没有双向统一 | 保留原版会话/Agent 流程 |
| 原版 Agent 能力 | 自主支路不运行 tools、知识库与标准 LLM 钩子 | 按原配置运行 |

本轮部署的临时试验配置为 20 条、每条正文 500 字符、完整 JSON 背景 6000 字符；这些是字符预算，不是 token 数，且与出厂默认值不同。窗口仅驻留内存，不是持久会话库。

M3 才讨论正常会话与自主插话的临时上下文桥接及人设覆盖优先级。没有为了这次说明修改产品代码或正在运行的配置。
