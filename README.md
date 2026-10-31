# AstrBot NoReply

让同一个聊天模型根据人设和群聊背景，对**每条待判消息**明确决定回复或不回复，再按消息顺序发送需要回复的正文。

当前主路径为持久化批量判定，`batch_mode=true`。用户已批准的生产范围是全群正式 live；出厂仍为关闭、shadow、非全群。历史 shadow/M1 记录不代表当前运行开关。完整设计与边界见 [批量判定](docs/BATCH_JUDGMENTS.md)。

## 现在如何工作

1. 收到符合条件的普通群文本，先事务性写入 SQLite，分配稳定 ID。相同群的重复平台消息 ID 不重复入队。
2. 一次请求提交最多 32 条待判消息，并附最近最多 300 条背景观察；两者去重。待判数据不受背景窗口淘汰影响。
3. 要求模型按本批 ID 顺序返回 `decisions`：每项必须有整数 `id` 和布尔 `reply`，要回复时还必须有合法 `text`。
4. 缺项、重复、陌生 ID、乱序、非法正文或工具调用都会使本批无效；不把失败当成沉默，不清待判。自动退避、缩小重试批次，个别失败项不会独占后续判定。
5. 合法批次一次性落盘，再独立发送 `reply=true` 的正文。忙时、尝试间隔和发言冷却不会丢消息，也不依赖下一条消息来推进。
6. 插件初始化自动恢复待判和待发送工作。发送中断/超时但结果不确定时记 `unknown`，不自动重放；严格按序发送不会越过更早的未判或不确定项。

**保证边界：**保证已持久化的合格输入不会因 busy、cooldown、300 条淘汰或正常重启静默消失，不保证外部模型永久失败时仍能在有限时间内成功，也不把 API 确认等同终端送达。主动清空的输入单列 `cancelled`，不是已判。

## 范围与身份

- 只接管来源 AI 开启、插件范围内的未显式唤醒纯文本群消息。私聊、命令、正常 `@`、混合图片/引用/提及组件不进入自主待判；普通链路不增加额外模型调用。
- `all_groups=true` 明确开启所有群；默认 false 时仅匹配 `平台实例 ID|机器人 ID|真实群 ID` 的准确白名单，空列表不开放任何群。
- QQ 官方 `@` 与未 `@` 消息的 self_id 表达不同，批量模式用适配器实例 + 固定 `qq_official` 标记 + 真实群 ID 归并物理群。QQ 白名单的机器人字段使用该固定标记。
- 每条观察保留 nullable `is_bot` 和 `is_admin`。后者是 **AstrBot 后台配置的管理员**，不是群管理员；读取失败独立降级 unknown。本地 assistant 为 bot=true/admin=null。其他 bot 不自动过滤；明确 `is_you=true` 的自身回声不再入队。
- 接管群通过可撤销运行期 gate 屏蔽原版额外群背景，不改原版配置。停用/卸载恢复；普通 Agent 历史不被清空。插件窗口尚未桥接到正常 `@` 历史。

## 配置

出厂 `enabled=false`、`shadow_mode=true`、`all_groups=false`、`allowed_groups=[]`、`persona_id=""`。保持生产已有开关，不把维护部署当成重新做限时试验。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `batch_mode` | true | 持久化逐条批量判定；false 为不保证覆盖的旧快照兼容路径 |
| `batch_size` | 32 | 每次最多待判条数，范围 1–128 |
| `batch_retry_base` | 5 秒 | 失败项独立指数退避，最长 60 秒；仍遵守尝试间隔 |
| `window_messages` | 300 | 近期背景观察数；未判输入独立保留 |
| `message_chars` | 0 | 正文字数上限，0 不截断 |
| `context_chars` | 0 | 完整观察 JSON 上限，0 不限制；正值先删旧背景，待判超限则拆小重试，不悄悄漏 ID |
| `attempt_interval` | 10 秒 | 同群模型调用最小间隔，包含沉默和失败 |
| `reply_cooldown` | 30 秒 | 同群相邻 live 正文确认之间的间隔；不阻止新消息被判定 |
| `timeout` | 30 秒 | 单次模型/发送操作截止时间 |
| `max_reply_chars` | 600 | 每条正文最长字符数 |
| `max_groups` | 128 | 批量模式并发群 worker 数；更多群留队，不拒绝接收 |
| `diagnostics_enabled` | false | 可选完整私有请求/模型输出记录，与必需的队列数据库分开 |

变更 `Limits` 类参数仍需重载插件；重载前暂停自主执行。开关、人设、范围变化与显式唤醒会使旧发言失效；已持久化待判不因此冒充已判。私有队列保存输入正文、逐条结论和发送状态，即使完整诊断关闭也存在；目录 0700、数据库 0600，勿入库。旧正文随背景淘汰释放，未判和未确认发送正文保留；覆盖元数据和去重账本不自动删除，需监控磁盘。

## 管理命令

- `/noreply_status`：AstrBot 管理员查看当前物理群 `received/pending/judged/cancelled/send_pending/accepted/unknown`。
- `/noreply_clear`：管理员清空当前群背景与待判，取消排队发言，并明确放弃 unknown 发送以解除顺序阻塞；不重发旧正文，不撤回已发送消息。清空不是成功判定。

发送已在平台进行时，任何清空/停用都无法撤回网络请求。QQ 官方使用现有客户端的群消息 API，并要求实际返回消息 ID 才记 API 确认；其他平台的 `session:handed_off` 只代表 SDK 派发，不能混称终端送达。异步自主发送不走完整 Agent 或常规发送后 hook 链；标准会话历史桥接仍未实现。

## 开发与验证

```bash
cd ~/projects/astrbot_plugin_noreply
/opt/homebrew/bin/uv sync --locked --group dev
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
python3 tools/verify_handoff.py
```

Python >=3.12，无新增第三方运行依赖。开发 SDK AstrBot 4.29.3 是复现基线，不锁定生产；部署前重新核对实际 SDK。完整测试使用临时 ASTRBOT_ROOT，拒绝网络连接；真实 QQ/模型质量不可由离线测试代替。详见 [状态](docs/IMPLEMENTATION_STATUS.md)、[兼容性](docs/COMPATIBILITY.md)、[Agent 约定](AGENTS.md)。

运行包包含 `main.py`、`engine.py`、`diagnostics.py`、`native_context.py`、四个 `batch_*.py`、`metadata.yaml`、`_conf_schema.json` 与运行 README。不要打包开发环境、快照、运行配置、SQLite 或真实聊天记录。仓库为 [678mjoe/astrbot_plugin_noreply](https://github.com/678mjoe/astrbot_plugin_noreply)；后续推送仍需相应授权。
