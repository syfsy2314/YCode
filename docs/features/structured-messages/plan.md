# YCode 结构化消息基础层 Plan

> 状态：已批准

## 总体设计

本次重构保持现有调用方向，但在 Provider 与 ChatSession 之间加入明确的块级事件和消息组装层：

```text
官方 SDK client
    ↓ 供应商流事件
AnthropicProvider / OpenAIProvider
    ↓ 统一 Typed StreamEvent
MessageAssembler
    ↓ 不可变 Assistant ChatMessage
ChatSession
    ├── 同步把增量事件交给 TUI
    └── 完整成功后提交结构化历史
```

Provider 只负责协议适配，不组装通用完整消息；MessageAssembler 不认识供应商类型；ChatSession 不解析 JSON 或内容块协议；TUI 只消费它关心的文本、Thinking 和完成事件。

## 参考设计原则

本设计参考 Anthropic Messages API 与 OpenAI Responses API/Codex 所体现的结构化消息原则，但不直接复制任一供应商的数据类型：

- **有序类型项是唯一真实数据源**：Anthropic 消息使用有序 content blocks；OpenAI Responses 使用有序 output items 和 content parts。YCode 因此以 `tuple[ContentBlock, ...]` 保存完成消息，不把文本、Thinking、ToolCall 和 ToolResult 分散成多组可变字段。
- **流事件携带稳定定位信息**：Anthropic 通过 block index 表达内容块生命周期；OpenAI 流事件通过 output index、content index 和 item ID 定位增量。YCode 的统一事件保留稳定 block index，由 MessageAssembler 负责增量归并。
- **调用与结果分离并显式关联**：ToolCallBlock 保存调用 ID，ToolResultBlock 通过 `tool_call_id` 关联对应调用。它们可以属于同一轮业务交互，但不能伪装成同一条 Assistant 协议消息。
- **完整上下文可重放**：需要继续推理或工具循环时，保留协议要求的 Thinking/signature、ToolCall 和 ToolResult，不把可见文本当作完整历史。
- **协议适配与业务状态分离**：核心层只表示统一消息和事件；Provider 负责 Anthropic/OpenAI 角色、内容块及工具结果格式差异。

当前 OpenAIProvider 继续适配项目已经确定的 Chat Completions 协议。本次只借鉴 Responses API 的 typed item、稳定索引和调用关联理念，不迁移 API 端点，也不扩大已批准范围。

参考资料：

- [Anthropic：Implement tool use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use)
- [OpenAI：Streaming events](https://platform.openai.com/docs/api-reference/responses-streaming)
- [OpenAI：Model guidance](https://developers.openai.com/api/docs/guides/latest-model)

## 核心消息模型

### JSON 类型

`core/messages.py` 定义递归 JSON 类型，并提供内部 `freeze_json()` / `thaw_json()`：

- JSON object 在核心消息中使用只读 Mapping。
- JSON array 转换为 tuple。
- scalar 保持 `str | int | float | bool | None`。
- Provider 发请求前使用 `thaw_json()` 转回普通 dict/list。

这样 `ToolCallBlock.arguments` 在完成消息中不可被外部原地修改。

### ContentBlock

使用冻结 dataclass 和显式联合类型：

- `TextBlock(text: str)`
- `ThinkingBlock(text: str, signature: str = "")`
- `RedactedThinkingBlock(data: str)`
- `ToolCallBlock(id: str, name: str, arguments: FrozenJsonObject)`
- `ToolResultBlock(tool_call_id: str, content: str, is_error: bool = False)`

构造时校验必要字符串非空；Text/Thinking 允许空增量，但完成块是否允许空由 Assembler 根据块类型判断。

### ChatMessage

```text
ChatMessage
├── role: user | assistant
└── content: tuple[ContentBlock, ...]
```

提供以下便利入口：

- `ChatMessage.user_text(text)`
- `ChatMessage.assistant_text(text)`
- `message.text`：按顺序拼接全部 TextBlock，供 Markdown、测试和普通文本兼容使用。
- `message.blocks(type)`：按块类型读取，不暴露供应商对象。

`message.text` 和 `message.blocks(type)` 都是从 `content` 即时派生的只读视图。模型不保存独立的 `tool_uses`、`tool_results` 或 `thinking_blocks` 列表，避免同一消息出现多个互相矛盾的数据源。

角色约束：

- user 可以包含 TextBlock 和 ToolResultBlock。
- assistant 可以包含 TextBlock、ThinkingBlock、RedactedThinkingBlock 和 ToolCallBlock。
- 空 content 被拒绝。

## Typed StreamEvent

`core/events.py` 不继续扩充带大量可选字段的单一 dataclass，而是定义冻结事件类型联合。每种事件只携带自身有效字段，并保留统一 `kind`：

- `MessageStarted`
- `TextBlockStarted(index)`
- `ThinkingBlockStarted(index)`
- `RedactedThinkingBlockStarted(index, data)`
- `ToolCallStarted(index)`
- `TextDelta(index, text)`
- `ThinkingDelta(index, text)`
- `ThinkingSignatureDelta(index, signature)`
- `ToolCallIdDelta(index, text)`
- `ToolCallNameDelta(index, text)`
- `ToolInputJsonDelta(index, partial_json)`
- `ContentBlockCompleted(index)`
- `MessageCompleted(stop_reason, provider_reason="")`

`StopReason` 枚举：

- `END_TURN`
- `TOOL_USE`
- `MAX_TOKENS`
- `STOP_SEQUENCE`
- `CONTENT_FILTER`
- `UNKNOWN`

`StreamEvent` 是上述事件的类型联合。TUI 改用 `isinstance()` 或稳定 `kind`，但不访问 Provider SDK 类型。

## MessageAssembler

### 位置与接口

新增 `ycode/session/assembler.py`：

```text
consume(event) -> None
finish() -> ChatMessage
```

每次 `stream_reply()` 创建一个 Assembler，不跨轮复用。

### 状态

Assembler 保存：

- 是否收到 MessageStarted。
- `dict[index, BlockBuilder]`。
- 已完成块及其索引。
- 消息是否完成、统一 stop reason 和 provider reason。
- 是否已经 finish，防止重复提交。

每个 BlockBuilder 只处理一种块：

- TextBuilder：文本分片列表。
- ThinkingBuilder：Thinking 分片与 signature 分片。
- RedactedThinkingBuilder：完整 data。
- ToolCallBuilder：ID、名称和 JSON 分片列表。

### 状态机规则

- MessageStarted 只能出现一次且先于块事件。
- 块索引必须为非负整数。
- 同一索引只能开始和结束一次。
- delta 必须匹配已开始且未结束的块类型。
- 完成消息前必须结束所有块。
- MessageCompleted 只能出现一次，且之后不得再消费事件。
- `finish()` 只有在消息完成后成功，并按索引排序生成 Assistant ChatMessage。

ToolCallBuilder 在块结束时执行：

1. 拼接全部 ID 分片、名称分片和 JSON 分片。
2. ID 与名称必须非空。
3. 空 JSON 视为 `{}`。
4. `json.loads()` 只执行一次。
5. 解析结果必须是 object。
6. 使用 `freeze_json()` 变成不可变参数。

错误使用新的 `MessageAssemblyError`，只包含事件类型和索引等安全定位，不包含原始 JSON、响应对象或认证信息。ChatSession 将它转换成统一 `ProviderError(code="stream")`。

## Provider 映射

### AnthropicProvider

Anthropic SDK 实例从 `self._client` 重命名为 `self.client`。

事件映射：

- `message_start` → MessageStarted。
- text `content_block_start` → TextBlockStarted；非空初始文本再产生 TextDelta。
- thinking start → ThinkingBlockStarted。
- redacted thinking start → RedactedThinkingBlockStarted。
- tool_use start → ToolCallStarted，并把完整 ID、名称转换为对应 Delta；非空初始 input 序列化为 JSON delta。
- text/thinking/signature/input_json delta → 对应统一 Delta。
- `content_block_stop` → ContentBlockCompleted。
- `message_delta.stop_reason` 暂存并映射统一 StopReason。
- `message_stop` → MessageCompleted。

请求转换：

- user TextBlock → Anthropic text content。
- assistant TextBlock → text content。
- ThinkingBlock → 带 signature 的 thinking content。
- RedactedThinkingBlock → redacted_thinking content。
- ToolCallBlock → tool_use content。
- ToolResultBlock → user 消息中的 tool_result content。

关闭 Thinking 时仍显式发送 `{"type": "disabled"}`；若兼容服务违规返回 Thinking 块，Provider 可以解析事件但 TUI 继续按配置过滤展示，Assembler 是否保留由完成结构决定。

### OpenAIProvider

OpenAI SDK 实例从 `self._client` 重命名为 `self.client`。

每次流调用维护局部映射：

- 文本使用标准化 block index `0`。
- OpenAI `tool_calls[n]` 使用标准化 block index `n + 1`。
- 首次收到 role、content 或 tool_calls 时产生一次 MessageStarted。
- 首次文本产生 TextBlockStarted。
- 每个工具索引首次出现时产生 ToolCallStarted。
- id、function.name、function.arguments 的每个非空分片分别产生统一 Delta。
- finish reason 到达时，先结束全部已开始块，再产生 MessageCompleted。

只支持默认单 choice 请求；收到多个有效 choice 时作为流协议错误，避免把多个候选答案组装成同一消息。

请求转换：

- 普通 user/assistant TextBlock 转为 Chat Completions messages。
- Assistant 的 TextBlock 与 ToolCallBlock 合并为一个 assistant message，tool calls 使用完整 ID、函数名和 thaw 后参数的 JSON 字符串。
- ToolResultBlock 转为一个或多个 role=`tool` 消息。
- OpenAI 协议不发送 ThinkingBlock；若历史意外包含不兼容块则产生明确转换错误，而不是静默丢失。

## ChatSession

`stream_reply()` 流程：

```text
user_text → ChatMessage.user_text()
    ↓
创建 MessageAssembler
    ↓
Provider.stream_chat(history + user)
    ├── assembler.consume(event)
    └── yield event 给 TUI
    ↓
流正常穷尽
    ↓
assembler.finish()
    ↓
一次性提交 user + assistant
```

提交延迟到 Provider 流完全结束，避免 Provider 在 MessageCompleted 后继续发送非法事件时提前污染历史。任何 ProviderError、MessageAssemblyError、取消或缺少完成事件都不提交本轮。

`history` 返回结构化 ChatMessage tuple。现有需要字符串的调用改用 `message.text`。

Thinking、signature、ToolCall 和 ToolResult 保留在进程内历史。Provider 转换时按当前固定协议使用；TUI 不遍历历史并重新打印 Thinking。

## TUI 兼容

`ui/terminal.py` 只处理：

- ThinkingDelta → `append_thinking()`。
- TextDelta → `append_text()`。
- MessageCompleted → `complete()`。

块开始、签名、工具参数和块结束事件对当前 TUI 是无视觉事件，直接忽略。由于本阶段请求中不发送 tools，真实正常对话不会产生工具调用卡片。

Renderer 的最终 Markdown 继续来自本轮全部 TextDelta 累计结果，不读取 Thinking 或 ToolCall。

## FakeProvider 与测试支持

FakeProvider 改为接受 Typed StreamEvent 序列，并记录结构化 ChatMessage。

本机 SSE 服务新增可编排序列：

- Anthropic Thinking + signature + text。
- Anthropic 两个 Tool Use 的交错 JSON。
- OpenAI 两个并行 tool_calls 的交错 ID/name/arguments。
- 无效 JSON、重复索引、缺少 block stop 和完成后多余事件。

所有测试仍使用占位 Key。

## 文件变化

```text
ycode/
├── core/
│   ├── messages.py          # 重写：ContentBlock、结构化 ChatMessage、冻结 JSON
│   ├── events.py            # 重写：Typed StreamEvent、StopReason
│   └── provider.py          # 更新返回事件联合
├── errors.py                # 增加 MessageAssemblyError
├── providers/
│   ├── anthropic.py         # 完整块事件映射、结构化请求、client 命名
│   └── openai.py            # tool_calls 映射、结构化请求、client 命名
├── session/
│   ├── assembler.py         # 新增 MessageAssembler
│   └── chat.py              # 结构化事务历史
└── ui/
    ├── terminal.py          # 适配 Typed StreamEvent
    └── renderer.py          # 保持视觉行为，只调整事件调用边界

tests/
├── unit/
│   ├── core/test_contracts.py
│   ├── session/test_assembler.py
│   ├── session/test_chat.py
│   ├── providers/test_anthropic.py
│   ├── providers/test_openai.py
│   └── ui/test_terminal.py
├── integration/
│   ├── test_anthropic_stream.py
│   └── test_openai_stream.py
└── e2e/test_terminal_chat.py
```

## 迁移顺序

1. 新增结构化消息和冻结 JSON工具。
2. 新增 Typed StreamEvent 和 StopReason。
3. 实现并单测 MessageAssembler。
4. 迁移 FakeProvider 和 ChatSession。
5. 迁移 TUI 到新事件类型。
6. 迁移 Anthropic Provider 与请求转换。
7. 迁移 OpenAI Provider 与请求转换。
8. 更新集成和 E2E 测试。
9. 搜索并移除旧 `StreamEvent(kind, text)`、字符串 `ChatMessage.content` 和 `_client` 使用。
10. 运行完整质量门禁。

迁移期间不保留两套并行消息协议；每一步修复直接依赖它的测试后再进入下一步。

## 技术决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 完成消息 | 冻结 dataclass + tuple blocks | 明确不可变边界，避免历史被工具层意外修改 |
| 工具参数 | 冻结递归 JSON object | 同时满足已解析对象和不可变要求 |
| 流事件 | Typed dataclass union | 避免单一事件包含大量互斥可选字段 |
| 工具名称/ID | 独立 delta | 兼容 OpenAI 字段分片，不依赖首包完整 |
| 组装位置 | 独立 MessageAssembler | Provider 保持协议适配，Session 保持事务协调 |
| OpenAI block index | text=0，tool[n]=n+1 | 避免文本和 tool_calls 原始索引碰撞 |
| 提交时机 | 流穷尽后再提交 | 拒绝完成事件后的非法增量，保持事务完整 |
| Thinking 保存 | 进程内结构化保留 | 满足 Anthropic 工具循环的签名往返，不改变可见 UI |
| SDK 命名 | `self.client` | 与 Provider 适配器清晰区分，符合项目命名约定 |
| Tool Use 执行 | 本阶段不实现 | 先稳定消息边界，避免协议解析与执行权限耦合 |

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1–F2 结构化内容与消息 | `core/messages.py` |
| F3–F4 Typed 事件与停止原因 | `core/events.py` |
| F5–F7 消息组装与并行块 | `session/assembler.py` |
| F8 Anthropic 映射 | `providers/anthropic.py` |
| F9 OpenAI 映射 | `providers/openai.py` |
| F10 结构化请求 | 两个 Provider 的消息转换 |
| F11 会话事务 | `session/chat.py` |
| F12 Thinking/Tool 上下文 | ChatMessage 历史与 Provider 转换 |
| F13 UI 兼容 | `ui/terminal.py`、`ui/renderer.py` |
| F14 client 命名 | 两个 Provider 与单元测试 |

所有需求都有明确模块归属；本阶段没有 Tool Executor、Agent Loop 或本地操作能力。
