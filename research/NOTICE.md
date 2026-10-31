# 研究快照说明

本目录保存用于 AST 隔离探针的 AstrBot 原始源码，不是可运行的完整 AstrBot，也不是本插件的实现。

- `snapshots/deployed-4.29.1`：从 Pi 安装的 AstrBot 4.29.1 包只读复制；用 SHA-256 验证。
- `snapshots/research-master`：固定上游提交 `3d92821ad9d90df33fc242e7b159a3260075eb2c`；用 Git blob SHA-1 验证。
- `probe.py`：提取四个实际方法，使用外部 API 桩验证 12 项控制流；不 import 完整框架，不发送网络请求。

第三方源码保留 AstrBot 的原始许可，许可证随附于 `UPSTREAM-LICENSE.txt`。这份第三方许可证不表示已经替本项目新写的插件代码选择了发布许可证。

不要编辑快照以通过测试。需要新版本时新增有明确来源和 hash 的快照，而不是覆盖现有证据。
