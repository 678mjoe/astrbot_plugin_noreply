# 单群试验的私有诊断记录

新增可选 `diagnostics_enabled`，出厂 false；只在插件已启用、来源 AI 开启且精确白名单匹配时记录自主评估。不会为记录启动模型或自动开启 live。

记录路径由 AstrBot 公共 StarTools.get_data_dir 生成：
`data/plugin_data/astrbot_plugin_noreply/diagnostics/trial-<uuid>.jsonl`。
目录 0700、文件 0600，每个插件实例单独文件，单文件容量上限 2 MiB，最多保留 5 份；满额后拒绝新试验记录，需人工导出归档，不静默删除旧记录。
所有真实输入、人设、群标识和模型输出仅保存在该私有目录，不进入项目或 GitHub。

## 逐次记录

- incoming：进入自主评估范围的解析后纯文本、Plain 片段、身份、消息 ID。
- request：实际发送的系统提示词、人设与协议、JSON 窗口、Provider ID、contexts/tools/retry 参数。
- response：模型返回的 assistant completion_text，保留空白、换行及原始 <noreply> 正文，另记角色/响应类型。不是完整 HTTP 抓包，不记录模型供应商密钥、请求头或隐藏推理。
- decision：silent/shadow/reply/busy/cooldown/error 等结果、候选正文、时延和错误类别。
- error：Provider 查询、人设查询或模型调用阶段、异常类型、可用 HTTP 状态和调用栈位置；不写可能含凭据的原始异常消息/后端错误体。已知 Gemini safety checks 错误归类为 provider_safety_block。

记录通过 event_id 关联；忙时没有新的 request，故不能将 incoming 数量当模型调用次数。异常或超时可能没有 response，不能编造补齐。标准 @/命令、不支持的多模态不是这条自主生成支路；可结合原消息历史复盘，但不伪称此文件是平台全量收发审计。

启用诊断后，无法写入、达到容量上限或发现异常文件时，跳过未记录的评估/发送，并留下不含正文的固定告警。已发出的远端请求不能撤销。关闭诊断时不再记录后续返回正文。默认控制台日志仍只含统计。

## 下轮试验

测试群已绑定独立配置 kawaii-noreply-shadow，保留原模型/人设/上下文设置，只关闭其内置概率主动回复；其他群仍使用原配置。插件评估间隔 10 秒、live 发言冷却 30 秒。

准备阶段保持 enabled=false、shadow_mode=true、diagnostics_enabled=false。下一轮在用户确认真实发送及完整记录后，才开启有限时单群 live 和诊断；到期关闭。decision=reply 只证明通过本地候选检查，不是平台送达凭证，仍须在群里实际核对。

这些记录只能保存新试验，不会恢复以前未落盘的候选回复。
