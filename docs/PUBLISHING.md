# 开源与插件市场发布

这份文件是发布操作清单，**不是已经发布或通过市场审核的声明**。仓库可见性、远程推送和市场提交由维护者最后确认。

## 已准备的内容

- 面向使用者的 README 和完整配置说明。
- 原生 `_conf_schema.json`：18 个普通 UI 可见配置项；旧 `batch_mode` 兼容键保留但隐藏。
- 可选模型覆盖 `provider_id`，默认空值跟随会话；人设与模型使用官方下拉选择器。
- 可选参与策略 `reply_guidance`，不替换服务器端必回/JSON校验。
- MIT LICENSE，版权署名沿用项目作者 M.Joe；第三方材料另见根目录 NOTICE。
- `metadata.yaml`：仓库地址、语义版本、最低 AstrBot 范围及已验证平台声明。
- 固定允许列表的安装 ZIP 构建器，不打包运行数据、开发环境和研究快照。

## 本地验证

```bash
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
python3 tools/verify_handoff.py
python3 tools/build_release.py
```

生成 `dist/astrbot_plugin_noreply-v0.1.0.zip`。脚本检查文件齐全、ZIP 完整性和小于 16 MB，并输出 SHA-256。同一源文件构建两次应得到相同字节。只有明确列入允许列表的文件才能进入此包。

也应在**隔离、无生产密钥**的 AstrBot 环境验证安装和默认关闭行为；离线成功不等于所有消息平台、所有模型或后续 SDK 版本都通过实测。维护者若做真实模型或群发试验，需自行明确费用和目标范围。

## 公开仓库前

- [ ] 确认 README 功能、默认值、模型费用、数据保存和当前限制符合实际，不宣称固定缓存命中率或跨群共享记忆。
- [ ] `LICENSE` 是标准 MIT，作者与年份正确；保留 `research/UPSTREAM-LICENSE.txt` 等第三方许可，不把上游源码重新标成 MIT。
- [ ] 检查当前文件和**整个 Git 历史**，不要含 API Key、令牌、个人运行配置、真实群记录、诊断输出或数据库。
- [ ] 检查历史运维材料中的本地路径、内网地址、个人部署细节，决定是否适合公开。需要时采用经核验的清洁发布分支或新仓库；不要只删工作树中的文件就认为历史已经消失。
- [ ] 运行包必须包含 `prompt_cache.py` 等所有相对导入模块；不要只提交 main.py 而漏掉尚未跟踪的新文件。
- [ ] 检查 Git 暂存区，确认提交范围完整且不夹带环境文件；运行全部回归与安装包导入检查。

`.gitignore` 只预防未跟踪文件，不清除旧提交；`.gitattributes` 的 `export-ignore` 只精简 Git 归档，也不能把已经公开的源码或历史藏起来。若发现真正泄露的凭据，应先撤销/轮换，再处理历史。

### 已知历史元数据

本次基础扫描未发现已知 GitHub Token、长模型密钥或私钥头模式。旧版本的 `docs/OPERATIONS.md`、`docs/UPGRADE-2026-10-31.md`、`docs/VERIFICATION.md` 含本地用户目录和内网地址，当前文件已脱敏，但旧 Git 提交仍保留原值。它们不是访问凭据；若也不希望公开这些元数据，公开仓库前需自行决定采用清洁历史/新仓库，不能依赖当前文件脱敏。此扫描是有限模式检查，不是完整安全审计。

## 推送与提交市场

1. 确认仓库已公开，公开 URL 与 `metadata.yaml` 中 `repo` 一致：
   `https://github.com/678mjoe/astrbot_plugin_noreply`。
2. 将经过核验的提交推送到准备发布的分支，必要时创建与元数据一致的标签/Release。更新版本时同步 `metadata.yaml` 和 `pyproject.toml`；开发依赖中 SDK 的固定版本只是测试基线，不是生产安装要求。
3. 注册/登录 **AstrBot Cloud**，前往 [插件发布页面](https://cloud.astrbot.app/publish)，按页面要求提交 GitHub 仓库及信息。当前官方入口是这个发布页面，不需要套用旧教程去手改市场索引。
4. 等待页面上的审核/发布结果；未取得实际结果前，不在 README 或公告里声称“已上架”。
5. 从市场再做一次实际安装检查，确认版本、配置入口和出厂关闭行为正确。

可选 `logo.png`：正方形，官方推荐 256×256；不是发布的强制前置条件。没有额外运行依赖时，不需要为了形式添加空的 requirements.txt。

## 关于 GitHub 源码 ZIP

`.gitattributes` 从 Git 归档排除开发脚本、测试、研究快照和历史运维文档，只保留运行代码、许可及用户文档。它保留源码仓库中这些材料，不会删除研究证据。`tools/build_release.py` 使用独立的明确允许列表，适合验证本地安装包；发布时还应检查 GitHub 实际提供的归档。

## 官方参考

- [插件配置与可视化选择器](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [发布插件到插件市场](https://docs.astrbot.app/dev/star/plugin-publish.html)
- [元数据、Logo 与平台/版本声明](https://docs.astrbot.app/dev/star/plugin-new.html)
