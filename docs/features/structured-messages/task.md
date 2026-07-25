# YCode 结构化消息基础层 Tasks

> 状态：已完成

## 实施约束

- 只重构消息、事件、Assembler、Session、Provider 和必要的 TUI 事件边界。
- 不发送 tools 定义，不执行工具，不实现 Agent Loop。
- 保留现有配置、输入提示区、用户消息背景板、Thinking、计时和 Markdown 行为。
- 每个任务完成后运行其局部验证；失败时先在当前任务内修复。
- 不创建 Git commit。

## 文件变化

| 操作 | 文件 | 职责 |
|---|---|---|
| 重写 | `ycode/core/messages.py` | ContentBlock、结构化 ChatMessage、冻结 JSON |
| 重写 | `ycode/core/events.py` | Typed StreamEvent、StopReason |
| 修改 | `ycode/core/__init__.py` | 导出新的消息和事件类型 |
| 修改 | `ycode/core/provider.py` | Provider 返回 Typed StreamEvent |
| 修改 | `ycode/errors.py` | MessageAssemblyError |
| 新建 | `ycode/session/assembler.py` | 按索引组装完整 Assistant 消息 |
| 修改 | `ycode/session/__init__.py` | 导出 Assembler |
| 重写 | `ycode/session/chat.py` | 结构化历史与事务提交 |
| 修改 | `ycode/providers/anthropic.py` | Anthropic 块事件与结构化请求 |
| 修改 | `ycode/providers/openai.py` | OpenAI tool_calls 与结构化请求 |
| 修改 | `ycode/ui/terminal.py` | 消费 Typed 文本/Thinking/完成事件 |
| 修改 | `tests/support/fake_provider.py` | Typed 事件和结构化消息记录 |
| 修改 | `tests/support/sse_server.py` | 工具调用 SSE 测试序列支持 |
| 修改 | 现有 core/session/provider/UI 测试 | 迁移新契约 |
| 新建 | `tests/unit/session/test_assembler.py` | Assembler 状态机测试 |
| 修改 | 两个 Provider 集成测试 | 结构化块和工具分片 |
| 修改 | Windows E2E 测试 | 纯聊天行为回归 |

## 实现任务

### SM1：实现不可变结构化消息

**文件：** `ycode/core/messages.py`、`ycode/core/__init__.py`、`tests/unit/core/test_contracts.py`  
**依赖：** 无

**步骤：**

1. 定义递归 JSON 类型及 `freeze_json()`、`thaw_json()`。
2. 定义 Text、Thinking、RedactedThinking、ToolCall 和 ToolResult 内容块。
3. 定义结构化 ChatMessage、角色约束和非空校验。
4. 只以有序 `content` 保存消息状态，不增加分类内容的平行存储字段。
5. 提供 `user_text()`、`assistant_text()`、`text` 和按类型读取块的只读便利接口。
6. 验证冻结对象不能被原地修改，thaw 后可安全交给 SDK。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q -k "message or block or json"
```

### SM2：实现 Typed StreamEvent 与停止原因

**文件：** `ycode/core/events.py`、`ycode/core/__init__.py`、`ycode/core/provider.py`、`tests/unit/core/test_contracts.py`  
**依赖：** SM1

**步骤：**

1. 定义 StopReason 和 StreamEventKind。
2. 定义消息开始、各块开始、各类 Delta、块完成和消息完成事件。
3. 使用类型联合导出 StreamEvent。
4. 更新 ChatProvider Protocol 的返回注解。
5. 验证每种事件不可变、字段组合明确且不依赖 SDK。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q
```

### SM3：实现 MessageAssembler 正常组装

**文件：** `ycode/session/assembler.py`、`ycode/session/__init__.py`、`ycode/errors.py`、`tests/unit/session/test_assembler.py`  
**依赖：** SM1、SM2

**步骤：**

1. 定义 MessageAssemblyError。
2. 实现 MessageStarted 和块生命周期状态。
3. 实现 Text、Thinking/signature、RedactedThinking 和 ToolCall Builder。
4. 按索引组装交错增量，并按索引排序完成块。
5. 工具块结束时一次性解析并冻结 JSON object。
6. MessageCompleted 后由 `finish()` 生成 Assistant ChatMessage。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_assembler.py -q -k "success or interleaved or parallel"
```

### SM4：补齐 MessageAssembler 非法状态

**文件：** `ycode/session/assembler.py`、`tests/unit/session/test_assembler.py`  
**依赖：** SM3

**步骤：**

1. 拒绝 delta 早于块开始、重复开始、类型不匹配和重复结束。
2. 拒绝负索引、重复 MessageStarted、重复 MessageCompleted 和完成后事件。
3. 拒绝消息完成时仍有未关闭块。
4. 拒绝无效 JSON、非 object JSON、空工具 ID 和空工具名。
5. 确保错误仅包含安全事件与索引定位，不包含 JSON 原文。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_assembler.py -q
```

### SM5：迁移 FakeProvider 与 ChatSession

**文件：** `tests/support/fake_provider.py`、`ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** SM2–SM4

**步骤：**

1. FakeProvider 接收 Typed 事件并记录结构化 ChatMessage 快照。
2. ChatSession 将用户文本转换为结构化用户消息。
3. 每轮创建 Assembler，在 yield 给上层前消费事件。
4. Provider 流完全结束后调用 `finish()` 并一次性提交结构化轮次。
5. MessageAssemblyError 转换为安全 ProviderError。
6. 迁移多轮、失败、取消、缺少完成和幂等关闭测试。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q
```

### SM6：迁移 TUI 事件消费

**文件：** `ycode/ui/terminal.py`、`ycode/ui/renderer.py`、`tests/unit/ui/test_terminal.py`、`tests/unit/ui/test_renderer.py`  
**依赖：** SM5

**步骤：**

1. TerminalUI 识别 Typed ThinkingDelta、TextDelta 和 MessageCompleted。
2. 对块生命周期、signature 和工具 JSON 事件不产生视觉输出。
3. 保持计时、纯文本流、完成 Markdown、错误恢复和退出路径。
4. 保留四行输入提示区和用户消息背景板，不修改其视觉设计。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py tests/unit/ui/test_renderer.py -q
```

### SM7：迁移 Anthropic Provider

**文件：** `ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`  
**依赖：** SM1–SM2

**步骤：**

1. SDK 实例统一重命名为 `self.client`。
2. 实现 message/content block 生命周期映射。
3. 映射 text、thinking、signature、redacted thinking 和 input_json delta。
4. tool_use start 转换成 ToolCallStarted 及 ID/name/初始 input 增量。
5. 映射 message_delta/message_stop 和统一 StopReason。
6. 实现全部结构化内容块到 Anthropic 请求的转换。
7. 保持 thinking adaptive/disabled 和错误映射行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q
```

### SM8：迁移 OpenAI Provider

**文件：** `ycode/providers/openai.py`、`tests/unit/providers/test_openai.py`  
**依赖：** SM1–SM2

**步骤：**

1. SDK 实例统一重命名为 `self.client`。
2. 为文本和 tool_calls 合成统一块生命周期。
3. 逐片映射工具 ID、函数名和 arguments。
4. 文本固定使用索引 0，工具索引映射为原始索引加 1。
5. 映射 finish reason，拒绝多个有效 choice。
6. 实现普通消息、assistant tool_calls 和 role=tool 请求转换。
7. 对 OpenAI 不支持的 Thinking 历史显示明确转换错误。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q
```

### SM9：验证 Anthropic SDK 与本机 SSE

**文件：** `tests/support/sse_server.py`、`tests/integration/test_anthropic_stream.py`  
**依赖：** SM3–SM7

**步骤：**

1. 验证普通文本与 Thinking/signature 流。
2. 构造两个 Tool Use 块及交错 JSON 分片。
3. 用 MessageAssembler 生成完整结构化消息。
4. 检查请求中的结构化历史往返、模型、认证、thinking 和 base_url。
5. 覆盖缺少 block stop、无效 JSON、认证、网络和服务错误。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q
```

### SM10：验证 OpenAI SDK 与本机 SSE

**文件：** `tests/support/sse_server.py`、`tests/integration/test_openai_stream.py`  
**依赖：** SM3–SM6、SM8

**步骤：**

1. 验证普通文本流与 finish reason。
2. 构造两个并行 tool_calls，交错发送 ID、名称和 arguments 分片。
3. 用 MessageAssembler 生成独立 ToolCallBlock。
4. 检查 assistant tool_calls 和 role=tool 请求转换。
5. 覆盖多 choice、防御性流错误、认证、网络和服务错误。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_openai_stream.py -q
```

### SM11：迁移应用边界和 Windows 对话回归

**文件：** `tests/unit/test_app.py`、`tests/unit/test_cli.py`、`tests/e2e/test_terminal_chat.py`  
**依赖：** SM5–SM10

**步骤：**

1. 更新 App/CLI 测试中的结构化消息夹具。
2. 运行 OpenAI 双轮、Anthropic Thinking、错误恢复和配置失败场景。
3. 保持四行输入提示区、用户消息背景板、计时和 Markdown 行为。
4. 验证不发送 tools 定义、不执行工具、不产生历史文件。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/test_cli.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q
```

### SM12：完整质量门禁与旧契约清理

**文件：** 全部受影响代码、测试和文档  
**依赖：** SM1–SM11

**步骤：**

1. Ruff 格式和 lint 全部通过。
2. 完整 pytest、compileall 和两个 CLI 入口通过。
3. 搜索并移除旧 `StreamEvent(kind, text)` 构造、字符串历史读取和 Provider `_client`。
4. 搜索确认没有 tools 请求字段、Tool Executor、Agent Loop 或本地执行能力。
5. 检查错误和捕获输出不存在测试 Key 或完整响应。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

## 执行顺序

```text
SM1 → SM2 → SM3 → SM4 → SM5 → SM6
  ├──────────────→ SM7 → SM9  ─┐
  └──────────────→ SM8 → SM10 ─┤
                                ↓
                              SM11 → SM12
```

Provider 分支在核心契约稳定后可以独立实现，但合并到应用前必须同时通过两种协议测试。

## Plan 覆盖

| Plan 组件 | 任务 |
|---|---|
| ContentBlock、ChatMessage、冻结 JSON | SM1 |
| Typed StreamEvent、StopReason | SM2 |
| MessageAssembler 正常与错误状态机 | SM3–SM4 |
| ChatSession 结构化事务 | SM5 |
| TUI 兼容 | SM6、SM11 |
| Anthropic 结构化映射 | SM7、SM9 |
| OpenAI 结构化映射 | SM8、SM10 |
| client 命名统一 | SM7–SM8、SM12 |
| 完整回归与范围检查 | SM11–SM12 |
