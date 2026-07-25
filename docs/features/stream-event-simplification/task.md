# YCode 流事件简化 Tasks

> 状态：已完成

## 实施约束

- 只实现已批准 Spec 与 Plan 中的七事件契约、Provider 归一化、ResponseAssembler、Session 和必要 TUI 迁移。
- Anthropic 是本次主要实现目标；OpenAI 只做维持现有功能所需的最小兼容修改。
- 不新增 OpenAI 配置、端点、Thinking 或产品行为，不发送 tools 定义，不执行工具。
- 不修改消息块模型、ProviderFactory、配置、输入框、renderer 或其他 UI 设计。
- 每个任务完成后运行其局部验证；验证失败时先在当前任务内修复。
- 不连接真实 API，不读取或改写用户的 `.ycode/config.yaml`，不创建 Git commit。

## 文件变化

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 重写 | `ycode/core/events.py` | 七种公共事件、StopReason 和 StreamEvent 联合类型 |
| 修改 | `ycode/core/__init__.py` | 删除旧事件导出，导出七种新事件 |
| 重写 | `ycode/session/assembler.py` | ResponseAssembler 状态机 |
| 修改 | `ycode/session/__init__.py` | 导出 ResponseAssembler |
| 修改 | `ycode/session/chat.py` | 新 Assembler 接入与事务提交 |
| 重写 | `ycode/providers/anthropic.py` | Anthropic 原始 SSE 私有归一化 |
| 修改 | `ycode/providers/openai.py` | 新事件契约的最小兼容 |
| 修改 | `ycode/ui/terminal.py` | 使用 StreamEnd 完成渲染 |
| 修改 | `tests/unit/core/test_contracts.py` | 七事件公共契约 |
| 重写 | `tests/unit/session/test_assembler.py` | ResponseAssembler 正常与非法状态 |
| 修改 | `tests/unit/session/test_chat.py` | 事务提交与回滚 |
| 修改 | `tests/unit/providers/test_anthropic.py` | Anthropic 精确事件映射 |
| 修改 | `tests/unit/providers/test_openai.py` | OpenAI 既有行为兼容 |
| 修改 | `tests/unit/ui/test_terminal.py` | TUI 新事件分发 |
| 修改 | `tests/integration/test_anthropic_stream.py` | Anthropic 本地 SSE 与完整消息 |
| 修改 | `tests/integration/test_openai_stream.py` | 既有 OpenAI 集成断言迁移 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 真实终端聊天回归 |

## 实现任务

### SE1：替换核心流事件契约

**文件：** `ycode/core/events.py`、`ycode/core/__init__.py`、`tests/unit/core/test_contracts.py`  
**依赖：** 无

**步骤：**

1. 保留现有 `StopReason`，删除 `StreamEventKind`、`kind` 属性和全部旧事件类。
2. 定义冻结且带 slots 的 `TextDelta`、`ThinkingDelta`、`ThinkingComplete`、`ToolCallStart`、`ToolCallDelta`、`ToolCallComplete`、`StreamEnd`。
3. 为六种内容事件校验非负 index，并拒绝构造空文本或参数增量。
4. 使用七种事件组成新的 `StreamEvent` 联合类型。
5. 更新核心包导出，不保留旧名称兼容别名。
6. 验证事件不可变、字段精确、完成事件携带正确 ContentBlock，且实例没有 `kind`。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q
```

### SE2：实现 ResponseAssembler

**文件：** `ycode/session/assembler.py`、`ycode/session/__init__.py`、`tests/unit/session/test_assembler.py`  
**依赖：** SE1

**步骤：**

1. 将类名从 `MessageAssembler` 改为 `ResponseAssembler`，保持模块路径 `ycode.session.assembler`。
2. 分别实现 Text、Thinking 和 ToolCall 的索引状态；Text/Thinking 由首个 delta 隐式开始，ToolCall 由 Start 显式开始。
3. 累积 Text delta，并校验 ThinkingComplete 的文本及 ToolCallComplete 的 ID、name、arguments 与此前事件一致。
4. 支持无 delta 的 RedactedThinkingBlock 完成事件和空工具参数 `{}`。
5. 在 StreamEnd 校验响应非空、所有 Thinking/ToolCall 已完成、结束事件唯一且结束后不能再消费事件。
6. `finish()` 只在有效 StreamEnd 后生成按 index 排序的不可变 Assistant ChatMessage，并拒绝重复调用。
7. 覆盖索引类型冲突、缺少 Start/Complete/StreamEnd、无效或非 object JSON、重复事件和安全错误文本。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_assembler.py -q
```

### SE3：归一化 Anthropic 原始 SSE

**文件：** `ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`  
**依赖：** SE1

**步骤：**

1. 在单次 `stream_chat()` 内增加私有 block 状态，缓存 Thinking 文本/signature、Redacted data、工具 ID/name/参数。
2. 把 text 与 thinking 的非空增量立即映射为 TextDelta、ThinkingDelta。
3. signature 只在 Provider 内累积，在 thinking block stop 时发出含完整签名的 ThinkingComplete。
4. 在 tool_use start 时要求完整 ID/name 并发出 ToolCallStart；实时转发参数分片，在 block stop 时解析 JSON object 并发出 ToolCallComplete。
5. 在 redacted thinking stop 时发出携带 RedactedThinkingBlock 的 ThinkingComplete。
6. message/block 生命周期只用于内部校验；原始迭代器自然结束且已收到 message_stop 后，发出唯一 StreamEnd。
7. Thinking disabled 时继续显式发送 disabled，并完整忽略服务端意外返回的 thinking 块。
8. 保持请求消息转换、停止原因和 SDK 错误映射不变；新增错误不得包含签名、参数原文或密钥。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q
```

### SE4：最小兼容 OpenAI Provider

**文件：** `ycode/providers/openai.py`、`tests/unit/providers/test_openai.py`  
**依赖：** SE1

**步骤：**

1. 文本继续固定映射到 index 0，并实时发出非空 TextDelta。
2. 按 `tool_index + 1` 私下缓存工具 ID、name 和 arguments 分片。
3. finish reason 到达后，按索引发出完整 ToolCallStart、原顺序 ToolCallDelta 和 ToolCallComplete。
4. 原始迭代器自然结束且完成状态有效时发出唯一 StreamEnd。
5. 保留单 choice、完成后额外内容、请求历史转换、停止原因和 SDK 错误映射。
6. 只迁移现有测试断言，不新增 OpenAI 配置、Thinking、端点或其他行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q
```

### SE5：迁移 ChatSession 与 TerminalUI

**文件：** `ycode/session/chat.py`、`ycode/ui/terminal.py`、`tests/unit/session/test_chat.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** SE2–SE4

**步骤：**

1. ChatSession 每轮创建 ResponseAssembler，并保持 `consume(event)` 成功后才向调用方 yield。
2. Provider 迭代器自然结束后调用 `finish()`，成功时一次性提交 user + assistant 历史。
3. 覆盖 Provider 错误、组装错误、缺少 StreamEnd、提前停止和取消时不提交残缺历史。
4. TerminalUI 用 `isinstance()` 处理 ThinkingDelta、TextDelta 和 StreamEnd。
5. ThinkingComplete 与工具事件不产生视觉输出；StreamEnd 调用现有 renderer.complete()。
6. 保持等待状态、流式纯文本、完成后 Markdown、每轮计时、错误恢复和退出行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py tests/unit/ui/test_terminal.py -q
```

### SE6：验证 Anthropic 本地 SSE 集成

**文件：** `tests/integration/test_anthropic_stream.py`  
**依赖：** SE2、SE3、SE5

**步骤：**

1. 迁移普通文本、Thinking/signature、Thinking disabled 和 Redacted Thinking 的事件断言。
2. 验证文本与思考增量在相邻 SSE 延迟期间实时可见，而不是结束后一次性返回。
3. 构造工具参数分片，验证 Start、Delta、Complete 的顺序及 ResponseAssembler 最终 ToolCallBlock。
4. 验证 StreamEnd 唯一、位于公共流末尾，并保留 stop reason。
5. 保持现有请求体、认证、网络、服务错误和敏感信息检查。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q
```

### SE7：迁移既有 OpenAI 集成测试

**文件：** `tests/integration/test_openai_stream.py`  
**依赖：** SE2、SE4、SE5

**步骤：**

1. 将既有文本流、工具调用和完成原因断言迁移到七事件契约。
2. 验证文本仍实时到达，工具 ID/name 分片只在 Provider 内部存在。
3. 使用 ResponseAssembler 验证最终文本和工具消息与迁移前一致。
4. 保持既有请求转换、错误和单 choice 覆盖，不添加新的 OpenAI 场景。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_openai_stream.py -q
```

### SE8：执行真实终端与完整质量门禁

**文件：** `tests/e2e/test_terminal_chat.py`、全部受影响代码和测试  
**依赖：** SE1–SE7

**步骤：**

1. 在 Windows ConPTY 中使用本地 SSE 服务完成至少两轮对话，验证等待状态、逐增量文本、最终 Markdown、每轮计时重置和正常退出。
2. 回归 Thinking enabled/disabled、流错误后恢复、配置发现和用户消息/input 布局。
3. 搜索并删除 `StreamEventKind`、所有旧事件类、旧 `kind` 访问和 `MessageAssembler` 残留。
4. 确认没有新增 tools 请求字段、工具执行、AgentLoop、OpenAI 功能或真实网络调用。
5. 运行格式、静态检查、完整测试和编译检查，并记录实际结果。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

旧契约清理：

```powershell
rg -n "StreamEventKind|MessageStarted|TextBlockStarted|ThinkingBlockStarted|RedactedThinkingBlockStarted|ToolCallStarted|ThinkingSignatureDelta|ToolCallIdDelta|ToolCallNameDelta|ToolInputJsonDelta|ContentBlockCompleted|MessageCompleted|MessageAssembler" ycode tests
```

该搜索预期无匹配并返回退出码 1。

## 执行顺序

```text
SE1
├─→ SE2 ───────────────┐
├─→ SE3 ───────────────┤
└─→ SE4 ───────────────┤
                       ↓
                      SE5
                 ┌─────┴─────┐
                 ↓           ↓
                SE6         SE7
                 └─────┬─────┘
                       ↓
                      SE8
```

任务按依赖顺序执行。任何局部验证失败时，先在所属任务内修复并重新运行，再开始依赖任务。

## Plan 覆盖检查

| Plan 组件 | 对应任务 |
| --- | --- |
| 七种公共事件与无枚举契约 | SE1 |
| ResponseAssembler 组装与一致性校验 | SE2 |
| Anthropic 原始 SSE 私有归一化 | SE3、SE6 |
| OpenAI 最小兼容 | SE4、SE7 |
| ChatSession 事务与回滚 | SE5 |
| TUI 可见事件和 Markdown 完成 | SE5、SE8 |
| 本地 SSE 实时时序 | SE6–SE7 |
| Windows 真实终端回归 | SE8 |
| 旧契约清理与质量门禁 | SE8 |

Plan 中的每项设计均有对应实现任务和验证方式；任务依赖无循环，未包含工具执行、AgentLoop、配置改动或新的 OpenAI 能力。
