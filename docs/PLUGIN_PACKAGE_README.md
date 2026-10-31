# NoReply 运行包

本包使用持久化逐消息批量判定；默认 batch_mode=true、每批最多 32 条、最近背景 300 条、字符 caps 默认 0。每条 pending 都必须获得合法回复/不回复结果，busy/cooldown 不会丢待判，也不会等新消息才补判。

## 安全开关

出厂 enabled=false、shadow_mode=true、all_groups=false、allowed_groups=[]。配置明确的现有人设后才能生成。空白名单不代表全群；全群使用显式 all_groups=true。维护现有部署时保留用户已批准的 live/全群模式，不自动改回限时试验。diagnostics_enabled 默认 false。

仅接管未显式唤醒的普通纯文本群消息，来源 AI 必须开启。私聊、正常 @、命令及混合组件不加入自主待判。is_admin 指 AstrBot 后台管理员，不是群管理员；is_bot 为平台标记，unknown 保留 null。QQ 白名单的 bot 字段采用 qq_official，以统一 @/未 @ 的群键。

## 文件

放入同一插件目录：main.py、engine.py、diagnostics.py、native_context.py、batch_protocol.py、batch_store.py、batch_runner.py、batch_transport.py、metadata.yaml、_conf_schema.json、README.md。要求 Python >=3.12，无新增第三方运行依赖。SDK 升级后重新验证 API，不永久锁定旧部署版本。

## 数据与恢复

队列在插件数据根的 batch/queue.sqlite3（目录 0700、DB 0600），不在代码目录。待判、合法结论与发送状态分别落盘。重启自动续判；未判不随 300 条背景淘汰。队列含真实正文，禁止入库/公开；关闭完整诊断不会关闭必要队列持久化。覆盖/去重元数据不自动删除，需监控磁盘。

QQ 官方异步发送使用当前客户端群消息 API，有返回消息 ID 才记 API 确认，并非终端已读。发送中断/超时记 unknown，不自动重复；unknown 或更早未判项会阻止该群后续越序发送。其他平台的 handed_off 仅代表 SDK 派发。

AstrBot 管理员在群内可用 /noreply_status 查看 received/pending/judged/cancelled/send_pending/accepted/unknown。/noreply_clear 清空旧背景、取消待判和排队发言，并明确放弃 unknown 以解除阻塞；不会补认送达、重发旧正文或撤回已送消息。

## 边界

正常 Agent 历史桥接未实现；原版额外群背景通过可撤销 guard 屏蔽，不改原版配置。自主支路不执行完整 Agent/常规发送后 hook。失败会保留并重试待判，但不保证外部模型永久失败、平台拒绝或磁盘不可写时有限时间内完成。旧快照算法遗留消息不可凭空恢复。batch_mode=false 为旧兼容路径，不再有逐条覆盖保证。
