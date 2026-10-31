# AstrBot `<noreply>` 插件 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. 其他本地 agent 可直接依照同一任务顺序执行，不要求具备 Hermes 专用工具。

**Goal:** 交付可离线验证的自主群聊插话插件：同一聊天模型输出正文或 `<noreply>`，默认关闭和 shadow，不改动现有部署。

**Architecture:** `main.py` 只负责 AstrBot 配置、事件、模型/人设查询和普通文本结果；`engine.py` 负责输出协议、近期观察窗口、生成尝试状态。模型调用一次，非流式，无 tools。正常 `@`、命令和私聊保留现有链路；正常会话与自主插话的双向历史桥接放到 M3。

**Tech Stack:** Python >=3.12、asyncio、AstrBot 4.29.1 插件 API；pytest 与 pytest-asyncio 用于开发测试；生产运行时不新增独立服务或第三方状态库。

---

## 0. 先确认本次交接状态

- [x] 路线与边界已定；含历史研究和两个明确区分的源码快照。
- [x] 已确认 Pi 实际安装的是 AstrBot 4.29.1，非 Git 工作树，Python 要求 >=3.12。
- [x] 已确认 Mac 默认 Python 是 3.9.6，存在 `/opt/homebrew/bin/uv`。
- [ ] 尚未实现 `main.py`、`engine.py`、插件配置和产品测试。
- [ ] 未创建完整开发 `.venv`，未安装 AstrBot 到 Mac 开发环境。
- [ ] 未调用真实模型、运行完整 AstrBot 或发送群消息。

当前文件优先于 `docs/archive/`。历史路线图内的“本目录 probe.py”和版本说明指向当时的报告目录，不是当前项目布局。

## 1. 第一版的决策记录

### 采用

1. 插件只在明确白名单群工作。空白名单表示任何群都不启用。
2. 对普通未唤醒文本有机会评估；显式 `@`、唤醒词、命令、私聊不走自主生成。
3. 自管有界观察窗口；读快照不消费原记录，沉默后仍能记住前文。
4. 通过原始 UMO 选择现有模型，使用明确配置的现有人设 ID。
5. 调用 `Context.llm_generate(contexts=[], tools=None)` 获取完整结果；禁止流式和工具循环。
6. `<noreply>` 仅作为输出控制动作；不发送、不记成助手发言。
7. live 时只 `yield event.plain_result(accepted_text)`，不二次提交 ProviderRequest。
8. `enabled=false`、`shadow_mode=true` 为出厂值；没有任何真实群或密钥写入默认配置。

### 暂不做

完整 Agent 工具/知识库能力、多模态、数据库迁移、定时任务、复杂意愿评分、主动改写正常会话 history、自动追随所有 persona 覆盖层、自动部署或公开发布。

### 明确代价

第一版的正常 `@` 对话可能不知道机器人刚才的自主发言；这是 M3 的工作，不掩盖成“已有完整共同记忆”。重启会丢失插件短期观察窗口；需要持久化时另做设计。

## 2. 数据流与状态契约

```text
白名单群事件
  → 校验插件与全局 AI 开关
  → 识别平台实例/机器人/物理群，记录合格文本
  → 命令或显式唤醒：返回原链路，不新增请求
  → 普通文本：检查 busy、尝试间隔、发言冷却
  → 取一次窗口快照 + 人设 + 稳定协议
  → 一次非流式 llm_generate
  → 检查响应 role / tools / 文本 / 长度 / epoch
  → silent/invalid/error：无发送结果
  → reply：shadow 仅记统计，live 返回普通文本结果
```

缓存键建议为 `(platform_id, self_id, group_id)`，不能把 UMO 的发送者隔离直接照搬成“群记忆”。每条记录至少有角色、发送者 ID、显示名、时间、消息 ID 和截断文本。不要把用户消息伪装成 system 指令。

每群状态最少包含：有界记录、去重信息、busy、最近尝试时间、最近自主发言时间、generation/epoch。开关关闭、清空、显式唤醒或卸载可使旧 epoch 结果失效。

忙时新消息仍可被观察，但**不要每条排一个待请求任务**。第一版不设后台定时补判；等下一条合格消息触发新尝试。没有后续消息就不自动补一次，先保持机制简单。

## 3. 建议目录

下面列的是**待实现文件**，不是仓库已经存在的产品代码：

```text
main.py
engine.py
metadata.yaml
_conf_schema.json
pyproject.toml
tests/
  test_protocol.py
  test_memory.py
  test_engine.py
  test_handler.py
  test_sdk_contract.py
```

不要为追求目录齐全先写空壳然后宣布插件完成。先写失败测试，再逐步产生能工作的实现。

## 4. 实施任务

各任务按“失败测试 → 运行确认失败 → 最小实现 → 运行通过 → 小步提交”推进。小步测试的函数名由实现者确定，但每条验收不得漏掉。

### 任务 0：建立项目内开发环境

**Files:** 创建 `.venv`（忽略入库），随后创建 `pyproject.toml`；不改任何全局环境。

1. 先运行已有交接检查与两组探针，保留实际结果。
2. 用明确 Python 3.12 创建本项目环境：

```bash
/opt/homebrew/bin/uv venv --python 3.12 .venv
/opt/homebrew/bin/uv pip install --python .venv/bin/python pytest pytest-asyncio
.venv/bin/python --version
```

3. 允许 uv 按需要下载用户级 Python，不允许 sudo。下载失败时先报告原因，不把系统 3.9 当替代。
4. 确认项目 Python >=3.12，依赖只在本项目 `.venv`。创建真实依赖声明并记录解析结果；不宣称这一步已完成产品测试。

### 任务 1：实现输出协议纯函数

**Files:** 创建 `engine.py`、`tests/test_protocol.py`。

协议固定为“独立 `<noreply>` 或直接正文”。可从以下最小函数开始：

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
        return "invalid", ""
    return "reply", value
```

先写的测试至少包括：标签、首尾空白、正常中文、正文引用标签、标签后混解释、空输出、超长结果。

例如：

```python
def test_marker_is_control_action():
    assert classify_output("  <noreply>\n", 600) == ("silent", "")


def test_marker_quoted_in_normal_prose_is_not_suppressed():
    text = "你可以用 <noreply> 表示沉默。"
    assert classify_output(text, 600) == ("reply", text)
```

运行 `.venv/bin/python -m pytest -q tests/test_protocol.py`，先看到缺失实现的失败，再实现并通过。这里没有断言模型真的会遵守协议；模型质量需另验。

### 任务 2：实现有界、不消费式观察窗口

**Files:** 修改 `engine.py`，创建 `tests/test_memory.py`。

测试再实现：

- 同群不同人共享观察窗口；跨群、平台实例和机器人账号隔离。
- 重复消息 ID 去重；没有可用消息 ID 时不要误把不同消息合并。
- 条数、单条字符数、总背景预算都有界；当前消息只放一次。
- A、B 后选择沉默，再收到 C；下次快照仍包含 A、B、C。
- shadow 中“本来要说”的内容不作为真的机器人发言加入窗口。
- 保留显示名和发送者身份区别；重名不等于同一个人。

运行 `.venv/bin/python -m pytest -q tests/test_memory.py`。不得通过修改 `research/snapshots/` 让测试通过。

### 任务 3：实现生成与并发状态机

**Files:** 修改 `engine.py`，创建 `tests/test_engine.py`。

把模型 await 函数与单调时钟作为依赖注入，先使用假模型：

- 同群最多一个自主尝试，跨群互不锁死。
- 忙时只记录，不创建逐消息请求队列。
- 生成间隔、发言冷却、总超时可测；沉默也计入生成尝试间隔。
- 无需 `sleep()` 的测试用假时钟；不要为每条消息建睡眠任务。
- 异常、超时和取消后释放 busy；关闭、清空、显式唤醒后的旧 epoch 不能外发。
- 一个机会仅调用一次注入的模型函数，失败不自动降级为普通聊天回复。
- 错误 role、意外工具调用、非文本输出不会成为回复正文。

运行 `.venv/bin/python -m pytest -q tests/test_engine.py`。先完成离线状态机，不在这一步接真实 Provider。

### 任务 4：配置与事件过滤

**Files:** 创建 `main.py`、`metadata.yaml`、`_conf_schema.json`、`tests/test_handler.py`。

在假事件与假 Context 下验证：

- 插件关闭、全局 AI 关闭、白名单为空或不匹配均不调用自主生成。
- 私聊、自发言、命令和已唤醒消息不新增模型请求，也不停止正常传播。
- 不同会话隔离设置下，物理群仍正确聚合。
- 原有内置概率主动回复属于部署前冲突检查，不能静默改用户配置。

建议初值：窗口 40 条、单条 500 字符、背景 12000 字符、尝试间隔 10 秒、自主发言冷却 30 秒、总超时 30 秒、输出 600 字符。它们是试用建议，不是模型性能实测值。

`metadata.yaml` 按 AstrBot 4.29.1 的真实插件格式填写；不要伪造作者、仓库 URL 或已选定许可证。运行 `.venv/bin/python -m pytest -q tests/test_handler.py`。

### 任务 5：接入 SDK 但仍使用假 Provider

**Files:** 修改 `main.py`，创建 `tests/test_sdk_contract.py`。

对照默认部署快照而非只看 master。所需调用链：

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

调用外围有整体超时。`request_max_retries=0` 不是所有 Provider 内部错误恢复的绝对保证。未配置模型或人设时失败关闭，不悄悄换人设、换模型或申请凭据。

如需真实 SDK 类型与导入验证，可在项目虚拟环境安装固定版本，但不启动服务器：

```bash
/opt/homebrew/bin/uv pip install --python .venv/bin/python 'astrbot==4.29.1'
.venv/bin/python -m pytest -q tests/test_sdk_contract.py tests/test_handler.py
```

用 monkeypatch/假 Context 覆盖网络边界。不得使用真实运行配置或模型 key 作为测试夹具。

### 任务 6：输出与普通链路共存

**Files:** 修改 `main.py`、`tests/test_handler.py`。

- silent、invalid、shadow reply 都不产生发送结果。
- 只有 live 的有效正文才 `yield event.plain_result(text)`。
- 不 `yield event.request_llm()`；不调用工具循环；不靠 `stop_event()` 沉默。
- 正常发送阶段观察的机器人文本可加入观察窗口，自身结果要防重复记录、自发言回流要过滤。
- 发送异常不能导致自动重试或重新请求模型；观察钩子并非可靠送达证明。
- 提供需要管理员权限的本群插件记忆清空入口；普通 `/reset` 是否桥接要明确，不能默认已统一。
- 明确关闭与卸载清理方式，防止未完成 await 结束后冒出消息。

运行产品测试全集。检查 README、默认配置与实际行为一致，并明确哪些标准 Agent 功能没有经过本插件路径。

### 任务 7：完成 M1 交付并停在真实调用之前

**Files:** 更新 `README.md`，新增 `docs/IMPLEMENTATION_STATUS.md`，更新验收清单。

```bash
.venv/bin/python -m pytest -q tests
python3 tools/verify_handoff.py
python3 research/probe.py
ASTRBOT_PROBE_SOURCE="$PWD/research/snapshots/research-master/sources" python3 research/probe.py
git diff --check
git status --short
```

以上测试次数与输出必须是真实执行结果。保存关键命令、未验证边界、安装包清单或打包方式。不要交付装有真实配置、聊天数据或 `.venv` 的插件包。

M1 完成后可以交付离线插件，但在真实模型、平台或 Pi 部署前停下请求用户放行。

## 5. 后续里程碑

### M2：影子试运行，再单群 live

1. 用户确定测试机器人/群、人设和聊天模型，并批准实际模型调用。
2. 检查群内其他主动回复插件及内置 `active_reply`，由用户同意后再调整。
3. shadow 不发消息，记录合适插话、误插话、过度沉默与遗漏的真实样例。
4. 在一次性、小范围测试中核实协议与费用，不能以解析器测试代替语义评估。
5. 用户批准后只开放一个白名单群 live；验证收件、无重复、`@`/命令回归、关闭开关和迟到结果丢弃。
6. 实际部署、服务重启、保存聊天内容和任何额外权限都单独说明，不默认授权。

### M3：与日常对话整合

- 给正常 `on_llm_request` 注入插件近期快照作为临时上下文，先做去重和不持久化测试，不并发覆盖整份 conversation history。
- 按实际 `resolve_selected_persona()` 的优先级桥接人设选择，而不是把默认人设当最终人设。
- 必要时增加每群单一 pending 快照或短时合并；不创建逐消息后台任务。
- 只有确认需要重启后记忆，再考虑复用现有持久化群记录。

## 6. 当前已验证与未验证

已验证的只是两版源码路径的离线接口探针与文档交接完整性，结果见 `docs/VERIFICATION.md`。探针使用实际方法的 AST 和外部 API 桩，不是完整 AstrBot 进程，也不是产品代码。

尚未验证：完整插件、真实模型协议遵循、插话质量、平台送达、M2 live 和 M3 历史桥接。不要把任何一项从“计划”改写成“完成”，除非有实际执行依据。
