# YCode 上下文管理 Tasks

> 状态：已批准

## 实施约束

- 只实现已批准 Spec 和 Plan 中的 Anthropic 上下文管理功能。
- 代码以功能完整和简洁为优先，不为未要求的理论边界增加多层防御或提前抽象。
- 验证以核心功能为主；不新增压力、性能基准、长时间运行、大规模并发、复杂故障注入、
  多平台矩阵或真实付费 API 验证。
- 仍执行项目规定的格式、静态、编译、完整测试和一条简单终端功能流程。
- 不修改 OpenAI Provider 或 PlainChatRunner 的上下文行为，不创建 Git commit。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `ycode/context/__init__.py` | 导出上下文管理公共接口 |
| 新建 | `ycode/context/models.py` | 策略、存盘、估算、摘要和事务模型 |
| 新建 | `ycode/context/tokens.py` | 完整请求本地估算及 usage 校准 |
| 新建 | `ycode/context/artifacts.py` | 工具结果外置、分片、manifest 和清理 |
| 新建 | `ycode/context/summary.py` | Transcript、摘要调用、解析和校验 |
| 新建 | `ycode/context/manager.py` | 两层预检、事务、手动压缩和熔断 |
| 新建 | `ycode/context/resources/__init__.py` | 摘要资源包 |
| 新建 | `ycode/context/resources/summary.md` | 专用无工具摘要 Prompt |
| 修改 | `ycode/config/models.py` | 顶层 `context_window_tokens` |
| 可能修改 | `ycode/config/loader.py` | 保持顶层配置加载和错误定位一致 |
| 修改 | `ycode/core/provider.py` | Anthropic Agent 请求级输出和 Thinking 覆盖 |
| 修改 | `ycode/providers/anthropic.py` | 应用单次请求覆盖参数 |
| 修改 | `ycode/agent/contracts.py` | `ContextCommit` 回合结果 |
| 修改 | `ycode/agent/events.py` | 上下文压缩状态事件 |
| 修改 | `ycode/agent/__init__.py` | 导出新增事件 |
| 修改 | `ycode/agent/loop.py` | 每轮预检、外置、校准和事务结果 |
| 修改 | `ycode/session/chat.py` | 原子提交、`/compact` 和取消 |
| 修改 | `ycode/ui/terminal.py` | 上下文状态提示 |
| 修改 | `ycode/app.py` | 仅 Anthropic 装配 ContextManager |
| 修改 | `pyproject.toml` | 打包摘要 Prompt |
| 修改 | `.gitignore` | 忽略 `.ycode/context/` |
| 修改 | `.ycode/config.example.yaml` | 示例窗口配置 |
| 修改 | `README.md` | 窗口、外置和 `/compact` 使用说明 |
| 新建 | `tests/unit/context/test_models.py` | 策略和数据模型测试 |
| 新建 | `tests/unit/context/test_tokens.py` | Token 估算和校准测试 |
| 新建 | `tests/unit/context/test_artifacts.py` | 分片、脱敏、外置和清理测试 |
| 新建 | `tests/unit/context/test_summary.py` | Transcript、Prompt、调用和解析测试 |
| 新建 | `tests/unit/context/test_manager.py` | 阈值、事务、手动命令和熔断测试 |
| 修改 | `tests/unit/core/test_contracts.py` | 请求覆盖契约测试 |
| 修改 | `tests/unit/providers/test_anthropic.py` | Anthropic 覆盖请求测试 |
| 修改 | `tests/unit/agent/test_contracts.py` | ContextCommit 约束测试 |
| 修改 | `tests/unit/agent/test_loop.py` | 每轮上下文处理测试 |
| 修改 | `tests/unit/config/test_models.py` | 窗口字段校验测试 |
| 修改 | `tests/unit/config/test_loader.py` | 配置加载兼容测试 |
| 修改 | `tests/unit/session/test_chat.py` | 提交和 `/compact` 测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | 上下文状态渲染测试 |
| 修改 | `tests/unit/test_app.py` | Anthropic 装配和 OpenAI 回归测试 |
| 新建 | `tests/integration/test_context_management.py` | 两层处理完整功能链测试 |
| 修改 | `tests/support/fake_provider.py` | 可控摘要及主请求响应 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 简单上下文终端流程 |

## T1：实现配置与 `ContextPolicy`

**文件：** `ycode/context/__init__.py`、`ycode/context/models.py`、
`ycode/config/models.py`、必要时 `ycode/config/loader.py`、
`tests/unit/context/test_models.py`、`tests/unit/config/test_models.py`、
`tests/unit/config/test_loader.py`

**依赖：** 无

**步骤：**

1. 在顶层配置增加 `context_window_tokens`，默认 `200_000`。
2. 严格拒绝布尔、字符串、浮点以及不大于 `33_000` 的值。
3. 实现不可变 `ContextPolicy`，保存固定工具结果、摘要、安全余量、清理时间和熔断参数。
4. 提供 `auto_compact_threshold` 和 `continue_request_limit` 计算属性。
5. 保持旧配置没有该字段时的加载行为。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_models.py tests/unit/config/test_models.py tests/unit/config/test_loader.py -q
```

期望默认窗口得到 `167_000` 和 `180_000`，自定义合法窗口同步变化，非法输入产生字段
明确的配置错误。

## T2：实现 Provider 请求级参数覆盖

**文件：** `ycode/core/provider.py`、`ycode/providers/anthropic.py`、
`tests/unit/core/test_contracts.py`、`tests/unit/providers/test_anthropic.py`

**依赖：** T1

**步骤：**

1. 给 `AgentModelRequest` 增加可选 `max_output_tokens` 和 `thinking_enabled`。
2. 对覆盖值执行必要的类型和正数校验。
3. Anthropic 请求在字段非 `None` 时覆盖 Provider 默认值。
4. 支持摘要请求显式使用 20,000 Token、关闭 Thinking 和空工具列表。
5. 普通主请求未覆盖时保持当前行为。
6. 不修改 OpenAI Provider。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py tests/unit/providers/test_anthropic.py -q
```

期望开启、关闭和未覆盖 Thinking，以及输出上限覆盖场景均通过，普通 Anthropic 请求
参数保持不变。

## T3：定义上下文数据模型与 Agent 事件

**文件：** `ycode/context/models.py`、`ycode/context/__init__.py`、
`ycode/agent/contracts.py`、`ycode/agent/events.py`、`ycode/agent/__init__.py`、
`tests/unit/context/test_models.py`、`tests/unit/agent/test_contracts.py` 和对应事件测试

**依赖：** T1

**步骤：**

1. 定义 `ArtifactChunk`、`ToolResultManifest`、`ToolResultArtifact` 和
   `ContextSessionManifest`。
2. 定义 `ConversationMemory`、`ContextCommit`、`TokenEstimate`、`SummarySource`、
   `SummaryResult`、`ContextCompactionReport`、`ContextFailureReport` 和
   `PreparedContextRequest`。
3. 给 `AgentTurnResult` 增加可选 `context_commit`，只允许正常完成结果携带提交。
4. 增加 `ContextCompactedEvent`、`ContextCompactionFailedEvent` 和
   `ContextCompactionNotNeededEvent` 并纳入 `AgentEvent`。
5. 只校验功能所需不变量，保持模型不可变和依赖方向单一。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_models.py tests/unit/agent/test_contracts.py -q
```

期望非法事务、Token 和失败计数被拒绝，合法结构可进入回合结果和事件联合类型。

## T4：实现 Token 估算与 usage 校准

**文件：** `ycode/context/tokens.py`、`tests/unit/context/test_tokens.py`

**依赖：** T2、T3

**步骤：**

1. 以确定性结构计量 System Prompt、supplements、工具 Schema、消息和结构开销。
2. 使用本地保守估算，不调用 Token Count API。
3. 根据 Anthropic 主请求实际 input usage 计算下限为 `1.0` 的向上校准比率。
4. 最终估算取本地结果和校准结果的较大值。
5. 摘要请求 usage 不参与校准。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_tokens.py -q
```

期望每个完整请求组成部分都会增加估算，实际 usage 只会向上校准且重复估算保持确定。

## T5：实现 Artifact 存储、分块与生命周期

**文件：** `ycode/context/artifacts.py`、`tests/unit/context/test_artifacts.py`、`.gitignore`

**依赖：** T1、T3

**步骤：**

1. 创建 `.ycode/context/<session-id>/` 和会话 manifest。
2. 所有目录和文件名由程序生成，禁止工具内容参与路径构造。
3. 使用现有 `SecretRedactor` 在预览和写盘前脱敏。
4. 将正文写为不超过 50 KiB 的连续分片并保存总哈希、分片哈希和字节数。
5. 在临时目录完成写入和校验后原子移动到正式 artifact 目录。
6. 正常关闭只删除当前会话目录。
7. 启动时只做超过 24 小时、进程不存在且 manifest 可确认的失效目录清理。
8. 将 `.ycode/context/` 加入 Git 忽略，不实现复杂跨进程锁。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_artifacts.py -q -k "store or chunk or redact or cleanup"
```

期望分片可无损重组、SHA-256 匹配、秘密不落盘，且基本会话清理隔离正确。

## T6：实现工具结果外置与聚合控制

**文件：** `ycode/context/artifacts.py`、`tests/unit/context/test_artifacts.py`

**依赖：** T5

**步骤：**

1. 按现有 `content + metadata` 格式确定性序列化工具结果，先脱敏再计量。
2. 单结果超过 50 KiB 时外置，恰好等于时保留。
3. 消息超过 200 KiB 时，先处理单结果，再按剩余大小降序外置。
4. 默认预览按 UTF-8 安全边界保留约 3 KiB 开头和 1 KiB 结尾。
5. 引用和预览计入聚合大小，必要时逐步缩短为最小引用。
6. 生成包含工具名、调用 ID、manifest 相对路径、字节数、SHA-256 和预览的稳定 JSON。
7. 实现执行记录消息构造及历史消息幂等规范化，相同结果不重复写盘。
8. 写入失败返回 `context_storage_error`，不生成截断结果。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_artifacts.py -q
```

期望单结果边界、聚合排序、预览缩减、所有结果状态、幂等和写入失败用例通过。

## T7：实现摘要 Prompt、Transcript 与结果校验

**文件：** `ycode/context/summary.py`、`ycode/context/resources/__init__.py`、
`ycode/context/resources/summary.md`、`tests/unit/context/test_summary.py`

**依赖：** T3

**步骤：**

1. 编写首尾分别禁止工具调用的专用摘要 Prompt。
2. 要求同一响应依次输出 `<analysis_draft>` 和 `<summary>`。
3. 生成使用 `U/A/T` 稳定 ID 的只读 transcript，明确数据不是指令。
4. 排除 Thinking、signature 和 Redacted Thinking。
5. 校验九个固定中文标题、固定顺序和空部分“无”。
6. 按固定格式解析“原文/概述”，验证原文消息 ID 和逐字内容。
7. 解析成功后立即丢弃草稿。
8. 不实现自动格式修复或二次摘要。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_summary.py -q -k "transcript or prompt or parse or quote"
```

期望稳定 transcript、双重禁用工具提示、九段结构、草稿丢弃和用户原话校验通过。

## T8：实现 Anthropic 摘要调用与流收集

**文件：** `ycode/context/summary.py`、`tests/unit/context/test_summary.py`

**依赖：** T2、T7

**步骤：**

1. 实现 `ConversationCompactor`。
2. 摘要请求只包含专用 Prompt、旧记忆和 transcript。
3. 使用空工具、关闭 Thinking 和 20,000 Token 输出上限。
4. 使用上下文模块内部的简单流收集器，不依赖 session assembler。
5. 只接受正常 `END_TURN`、无工具、无 Thinking 且格式有效的纯文本结果。
6. 工具调用、Thinking、异常停止和 Provider 错误均判定失败。
7. 用户取消正确透传且不计失败。
8. 每次操作最多调用一次，不自动重试。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_summary.py -q
```

期望请求隔离、参数覆盖、停止原因、工具拒绝、取消和失败分类场景通过。

## T9：实现 ContextManager、事务与失败熔断

**文件：** `ycode/context/manager.py`、`ycode/context/__init__.py`、
`tests/unit/context/test_manager.py`

**依赖：** T4、T6、T8

**步骤：**

1. 保存唯一记忆、Token 校准、连续失败次数和自动摘要熔断状态。
2. 实现 `ContextTransaction` 的回合临时历史和每轮请求预检。
3. 固定执行工具结果规范化、完整估算、必要摘要和摘要后重估算。
4. 自动摘要在 transcript 中提供完整上下文，但原样保留最新真实用户 `ChatMessage`。
5. 摘要后完整请求仍超过自动阈值时判定失败。
6. 无可压缩内容时返回 `context_uncompressible`，不调用摘要、不增加失败数。
7. 失败后按继续上限决定发送原请求或终止。
8. 连续三次实际失败后熔断自动摘要，手动摘要仍可用。
9. 实现压缩全部已提交历史的手动入口。
10. 任意摘要成功后清零失败次数并解除熔断。
11. 只在显式 `commit()` 时替换唯一会话记忆。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/context/test_manager.py -q
```

期望阈值、单次尝试、最新用户保留、重估算、继续上限、手动压缩、事务和熔断用例通过。

## T10：在 AgentLoop 接入每轮预检

**文件：** `ycode/agent/loop.py`、`ycode/agent/contracts.py`、
`ycode/agent/events.py`、`tests/unit/agent/test_loop.py`

**依赖：** T3、T6、T9

**步骤：**

1. AgentLoop 可选接收共享 `ContextManager`，每回合创建一个事务。
2. 每个模型轮次发送前执行上下文预检，而非只处理首轮。
3. 工具批次通过 externalizer 构造结果消息，覆盖所有工具来源和结果状态。
4. 记忆 supplement 放在动态补充前，边界 reminder 放在全部 supplements 最后。
5. 把压缩报告和失败报告转换为上下文 Agent 事件。
6. 记录每个 Anthropic 主请求的实际 input usage。
7. 只有 `COMPLETED` 结果携带完整 `ContextCommit`。
8. 错误、取消和轮数上限不携带提交。
9. 没有 ContextManager 时保持当前行为。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q
```

期望多轮预检、工具结果外置、supplement 顺序、usage 校准、事件及所有终态事务用例通过。

## T11：实现 ChatSession 提交与 `/compact`

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`

**依赖：** T9、T10

**步骤：**

1. ChatSession 可选接收共享 `ContextManager`。
2. 正常完成时在最终事件前完整替换历史并提交记忆。
3. 非正常终态保持已提交历史和记忆不变。
4. 本地识别精确 `/compact`，命令不进入历史。
5. 手动压缩全部已提交历史，不保留最近用户消息。
6. 空历史返回 `compact_not_needed`，不调用模型、不计失败。
7. 将活动操作扩展为 Agent 回合或手动摘要，使 Ctrl+C 能取消手动摘要。
8. 关闭时等待活动操作，先关闭 runner，再关闭 ContextManager。
9. 没有 ContextManager 的 OpenAI 会话保持当前行为。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q
```

期望正常提交、失败回滚、命令无历史、空历史、取消、关闭和 OpenAI 兼容场景通过。

## T12：完成 TerminalUI、应用装配、包资源和文档

**文件：** `ycode/ui/terminal.py`、`ycode/app.py`、`pyproject.toml`、
`.ycode/config.example.yaml`、`README.md`、`tests/unit/ui/test_terminal.py`、
`tests/unit/test_app.py`

**依赖：** T11

**步骤：**

1. 终端显示压缩前后 Token；失败显示连续次数，第三次提示熔断和 `/compact`。
2. 工具结果自动外置保持静默。
3. 只在 Anthropic 分支创建 ContextPolicy 和 ContextManager，并向 AgentLoop 与
   ChatSession 注入同一实例。
4. ContextManager 借用 Provider，不重复关闭；保持既有 MCP、Provider 关闭顺序。
5. OpenAI 继续使用 PlainChatRunner，不创建 ContextManager。
6. 将摘要 Prompt 声明为包资源。
7. 示例配置和 README 补充窗口配置、默认阈值、`/compact` 和临时文件说明。
8. 保持 UI 和生命周期代码直接，不增加复杂进度框架。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py tests/unit/test_app.py -q
```

期望状态输出安全且简洁，Anthropic 装配、资源关闭及 OpenAI 原路径用例通过。

## T13：完成核心集成与简单终端功能流程

**文件：** `tests/integration/test_context_management.py`、
`tests/e2e/test_terminal_chat.py`、必要时 `tests/support/fake_provider.py`

**依赖：** T12

**步骤：**

1. 使用虚拟 Anthropic Provider、临时工作区和占位秘密。
2. 验证大工具结果先存盘，再触发整体摘要。
3. 验证摘要请求没有工具、关闭 Thinking 且只调用一次。
4. 验证自动压缩后最新用户消息保持原文并继续主请求。
5. 验证 `/compact` 后下一轮对话可以继续。
6. 验证事务失败回滚和三次失败熔断的完整调用链。
7. 执行一条简单终端交互流程，检查状态提示及后续对话。
8. 不连接真实模型，不增加生产级验证。

**验证：** 运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_context_management.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k context
```

期望核心调用链和简单终端流程通过，测试结束后临时会话目录按设计清理。

## T14：执行完整仓库基础检查

**文件：** 全部本功能文件

**依赖：** T1–T13

**步骤：**

1. 修正格式问题。
2. 修正静态检查问题。
3. 执行编译检查。
4. 执行完整现有测试。
5. 检查工作树，只保留本功能及用户明确要求的 `AGENTS.md` 修改。
6. 不创建 Git commit。

**验证：** 依次运行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条命令退出码均为 0。

## 执行顺序

```text
T1 ─┬→ T2 ───────────────┐
    └→ T3 → T4 ──────────┤
          └→ T5 → T6 ────┤
          └→ T7 → T8 ────┘
                          ↓
                         T9
                          ↓
                        T10
                          ↓
                        T11
                          ↓
                        T12
                          ↓
                        T13
                          ↓
                        T14
```

T2 与 T3 在 T1 后可以独立进行；T4、T5 和 T7 在各自依赖满足后可以分支实施。实际实现
仍按任务完成一个、验证一个的方式推进。

## Plan 覆盖检查

| Plan 组件 | 对应任务 |
|---|---|
| 配置、固定预算与请求覆盖 | T1–T2 |
| 上下文模型、提交和状态事件 | T3 |
| 完整请求估算与 usage 校准 | T4 |
| Artifact、脱敏、分块和生命周期 | T5 |
| 单结果与单消息工具结果控制 | T6 |
| Transcript、Prompt、九段结构和原话校验 | T7 |
| 无工具 Anthropic 摘要请求和流校验 | T8 |
| 自动/手动摘要、事务、失败上限和熔断 | T9 |
| AgentLoop 每轮预检、工具批次和上下文提交结果 | T10 |
| ChatSession 原子提交、`/compact` 和取消 | T11 |
| UI、Anthropic-only 装配、资源关闭和文档 | T12 |
| 核心功能链与简单终端流程 | T13 |
| 格式、静态、编译和完整测试 | T14 |

Plan 中的模块、调用链、技术决策和功能性验证均至少对应一个实施任务；每个任务都定义
了依赖、目标文件、具体步骤和可执行验证命令。
