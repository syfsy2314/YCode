# YCode 流事件简化 Plan

> 状态：已批准

## 架构概览

重构后，公共流只传递 YCode 上层可消费的七种语义事件。供应商原始 SSE 生命周期在 Provider 内部终止，ChatSession 和 TUI 不再看到 message/block start/stop、signature 分片或工具 ID/name 分片。

```text
Anthropic SDK client
    ↓ 原始 Messages API SSE
AnthropicProvider
    ├─ 私有 block state：Thinking/signature、ToolCall/JSON
    ├─ 立即发出 TextDelta / ThinkingDelta
    ├─ 发出 ToolCallStart / ToolCallDelta
    ├─ block stop 时发出 ThinkingComplete / ToolCallComplete
    └─ message stop 后发出 StreamEnd
            ↓ 七种公共 StreamEvent
ChatSession
    ├─ 先交给 ResponseAssembler 校验和收集
    └─ 再原样转发给 TUI
            ↓ Provider 流自然结束
ResponseAssembler.finish()
    ↓ 按 index 排序的 Assistant ChatMessage
ChatSession
    ↓ 一次性提交 user + assistant 历史
```

各层职责如下：

- **AnthropicProvider**：识别 Anthropic SDK 事件，维护只在单次响应内存在的供应商解析状态；吸收原始 block 生命周期和 signature 分片；在工具块结束时解析一次完整 JSON；只输出七种公共事件。
- **公共 StreamEvent**：表达实时可见增量、工具调用语义、完整 Thinking/ToolCall 块和流结束原因；不表达供应商传输协议。
- **ResponseAssembler**：不导入 SDK；按 index 验证事件一致性，累积 TextDelta，校验 ThinkingComplete/ToolCallComplete 与此前事件一致，在 StreamEnd 后准备最终有序消息。
- **ChatSession**：创建单轮 ResponseAssembler、先消费再转发事件，等待 Provider 迭代器自然结束后调用 `finish()`，成功时事务提交历史。
- **TerminalUI**：只消费 TextDelta、ThinkingDelta 和 StreamEnd；当前忽略 ThinkingComplete 与三个工具事件。
- **OpenAIProvider**：仅把现有输出机械映射到同一七事件契约。供应商特有的 ID/name 分片在其内部缓存，不形成新的公共事件或产品能力。

该设计保留 Provider 对统一核心契约的必要依赖，但让依赖方向保持单向：Provider 了解 SDK 和核心事件；ChatSession、ResponseAssembler、TUI 只了解核心事件，不了解 Provider SDK。

## 核心数据结构

### 七种公共事件

所有事件使用冻结且带 slots 的 dataclass。内容事件构造时校验 `index >= 0`；Provider 不发出空增量。

```python
@dataclass(frozen=True, slots=True)
class TextDelta:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingComplete:
    index: int
    block: ThinkingBlock | RedactedThinkingBlock


@dataclass(frozen=True, slots=True)
class ToolCallStart:
    index: int
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class ToolCallComplete:
    index: int
    block: ToolCallBlock


@dataclass(frozen=True, slots=True)
class StreamEnd:
    stop_reason: StopReason
    provider_reason: str = ""
```

```python
type StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
```

不再定义 `StreamEventKind`，事件类也不保留 `kind` 属性。消费者统一使用 `isinstance()` 或结构模式匹配。

### ResponseAssembler

位置继续使用会话包，但类名从 `MessageAssembler` 改为 `ResponseAssembler`：

```python
class ResponseAssembler:
    def consume(self, event: StreamEvent) -> None: ...
    def finish(self) -> ChatMessage: ...
```

内部按索引保存三类状态：

```text
TextState
└─ text_parts

ThinkingState
├─ text_parts
└─ completed_block（完成前为空）

ToolCallState
├─ id
├─ name
├─ arguments_parts
└─ completed_block（完成前为空）
```

处理规则：

- 首个 `TextDelta(index)` 隐式创建 TextState，后续同索引只能继续接收 TextDelta。
- 首个 `ThinkingDelta(index)` 隐式创建 ThinkingState；`ThinkingComplete` 校验完成块文本与累计文本一致后保存完整块。
- Redacted Thinking 可以没有任何 ThinkingDelta，直接通过 `ThinkingComplete` 创建完成状态。
- `ToolCallStart` 显式创建 ToolCallState，要求 ID/name 非空且索引尚未使用。
- `ToolCallDelta` 只能跟随同索引 ToolCallStart，并按顺序追加 JSON 分片。
- `ToolCallComplete` 只出现一次；Assembler 将累计分片按空参数 `{}` 或完整 JSON object 解析，并校验 ID、name、arguments 与完成块一致。
- `StreamEnd` 只出现一次；到达时所有 Thinking 与 ToolCall 状态必须完成。TextState 在此时隐式完成。
- StreamEnd 后拒绝任何事件；`finish()` 只有在收到 StreamEnd 且 Provider 迭代器自然结束后由 ChatSession 调用。
- `finish()` 把 TextState 转为 TextBlock，与完成 Thinking/ToolCall 块一起按 index 排序，生成不可变 Assistant ChatMessage。
- 重复 `finish()`、空响应、缺少 StreamEnd、索引类型冲突或未完成状态均产生不包含敏感内容的 `MessageAssemblyError`。

## Provider 归一化设计

### AnthropicProvider

`AnthropicProvider` 在单次 `stream_chat()` 内维护私有内容块状态。该状态只用于解释 Anthropic 原始 SSE，不进入 `ycode.core`，也不暴露给 ChatSession：

```text
AnthropicBlockState
├─ kind
├─ thinking_parts
├─ signature_parts
├─ redacted_data
├─ tool_id
├─ tool_name
└─ argument_parts
```

原始事件映射如下：

| Anthropic 原始事件 | Provider 内部动作 | 公共事件 |
| --- | --- | --- |
| `message_start` | 校验消息只开始一次 | 无 |
| text `content_block_start` | 建立 text 状态；读取可能存在的初始文本 | 初始文本非空时发出 `TextDelta` |
| text `text_delta` | 校验块类型 | 非空时立即发出 `TextDelta` |
| thinking `content_block_start` | 建立 thinking 状态；缓存初始 signature | 初始思考非空时发出 `ThinkingDelta` |
| `thinking_delta` | 追加思考文本 | 非空时立即发出 `ThinkingDelta` |
| `signature_delta` | 仅在 Provider 内追加 signature | 无 |
| redacted thinking start | 保存加密数据 | 无 |
| tool_use `content_block_start` | 要求完整 ID/name，建立工具状态并保存初始 input | 发出 `ToolCallStart`；非空初始 input 规范化后发出 `ToolCallDelta` |
| `input_json_delta` | 追加 JSON 参数分片 | 非空时立即发出 `ToolCallDelta` |
| text `content_block_stop` | 关闭并移除 text 状态 | 无 |
| thinking `content_block_stop` | 合并文本与 signature，构造完整块 | 发出 `ThinkingComplete` |
| redacted thinking stop | 构造完整加密思考块 | 发出 `ThinkingComplete` |
| tool_use `content_block_stop` | 合并并解析 JSON object，构造完整工具块 | 发出 `ToolCallComplete` |
| `message_delta` | 保存供应商 stop reason | 无 |
| `message_stop` | 标记供应商消息已完整结束 | 无 |
| SDK 迭代器自然结束 | 检查已收到 `message_stop` 且所有块均关闭 | 唯一一次发出 `StreamEnd` |

补充约束：

- Provider 使用内容块 `index` 关联原始事件，并拒绝重复开始、类型不匹配、重复结束或未结束的块。
- 当配置关闭 Thinking 时，请求仍显式发送 `{"type": "disabled"}`；如果供应商异常返回 thinking 块，则整块忽略，不向上层泄露思考内容。
- Provider 负责验证 Thinking signature、工具 ID/name 和工具参数，但错误消息不包含 signature、API Key、完整工具参数或原始响应体。
- 不支持的内容块或增量类型转为不可重试的流协议错误；缺少 `message_stop`、块未关闭或 SDK 中断转为可重试的流错误。
- `StreamEnd` 在原始迭代器自然结束后生成，因此它是本次 Provider 输出的最后一个事件；ChatSession 随后的再次迭代用于确认生成器确实结束。

### OpenAIProvider 最小兼容

现有 OpenAI 后端只做满足新核心接口所需的机械迁移，不增加配置、端点、Thinking 或其他协议能力：

- 文本仍使用固定内容索引 `0`，收到非空 content 时立即发出 `TextDelta(0, content)`。
- 工具调用继续按 `tool_index + 1` 映射内容索引；Provider 私下缓存 ID、name 与参数分片。
- 因 OpenAI 可能分片返回 ID/name，而 `ToolCallStart` 要求完整字段，工具事件在 finish reason 到达后按索引集中发出：`ToolCallStart`、原顺序的 `ToolCallDelta`、`ToolCallComplete`。
- 收到 finish reason 后只记录完成状态；SDK 迭代器自然结束且状态有效时发出唯一一次 `StreamEnd`。
- 保留现有单 choice、防止完成后额外内容、错误映射和请求消息转换行为。
- 只迁移受事件类型改动影响的既有 OpenAI 测试，不新增或扩展 OpenAI 产品行为测试。

## 会话与展示层设计

### ChatSession 事务边界

`ChatSession.stream_reply()` 保持“一轮请求、一次提交”的事务语义：

```text
创建 user_message，但不写入 history
    ↓
构造 request_messages = history + user_message
    ↓
创建本轮 ResponseAssembler
    ↓
Provider 每产生一个事件
    ├─ ResponseAssembler.consume(event)
    └─ 校验成功后 yield 给调用方
    ↓
Provider 迭代器自然结束
    ↓
ResponseAssembler.finish()
    ↓
同时提交 user_message + assistant_message
```

具体约束：

- 必须先 `consume()` 再 `yield`，结构非法的事件不会泄露给 TUI。
- `StreamEnd` 到达时，ResponseAssembler 同时检查不存在未完成 Thinking/ToolCall、响应至少包含一个内容块，并保存结束原因。
- 收到 `StreamEnd` 不等于立刻提交；只有 Provider 生成器随后自然结束并且 `finish()` 成功，才更新历史。
- Provider 抛错、事件校验失败、流缺少 `StreamEnd`、调用方提前停止迭代或任务取消时，本轮 user/assistant 消息均不写入历史。
- `MessageAssemblyError` 继续在 ChatSession 边界转换为不暴露内部状态的 `ProviderError("stream", ...)`。
- ProviderError 保持原样向上传递；ChatSession 不解释供应商 SDK 错误。
- 本次只把类引用从 `MessageAssembler` 改为 `ResponseAssembler`，不改变 `ChatSession` 的公开调用方式。

### TerminalUI

TUI 使用类型判断消费事件，不依赖 `StreamEventKind`：

```python
if isinstance(event, ThinkingDelta):
    renderer.append_thinking(event.text)
elif isinstance(event, TextDelta):
    renderer.append_text(event.text)
elif isinstance(event, StreamEnd):
    await renderer.complete()
```

展示规则：

- `TextDelta` 与 `ThinkingDelta` 按到达顺序实时追加，维持现有首增量前的等待状态和流式纯文本展示。
- `StreamEnd` 触发本轮最终 Markdown 渲染和总耗时显示。
- `ThinkingComplete` 只用于组装含 signature 的历史，TUI 不重复渲染。
- `ToolCallStart`、`ToolCallDelta`、`ToolCallComplete` 当前全部忽略，不展示、不执行。
- 工具调用响应即使没有文本，也会正常结束 renderer；工具 UI 留待后续独立 Spec。
- 异常、键盘中断和任务取消继续分别调用 renderer 的 fail/cancel 路径，不伪造 `StreamEnd`。

## 文件改动与迁移

### 实现文件

| 文件 | 计划改动 |
| --- | --- |
| `ycode/core/events.py` | 删除 `StreamEventKind` 和旧事件类，定义七种新事件、`StopReason` 与新的 `StreamEvent` 联合类型 |
| `ycode/core/__init__.py` | 只导出新事件名称；不保留旧事件兼容别名 |
| `ycode/session/assembler.py` | 将 `MessageAssembler` 重写并更名为 `ResponseAssembler`；文件路径保持不变 |
| `ycode/session/__init__.py` | 导出 `ResponseAssembler`，移除 `MessageAssembler` |
| `ycode/session/chat.py` | 使用 `ResponseAssembler`，保持先消费、后转发、最后事务提交 |
| `ycode/providers/anthropic.py` | 增加私有 Anthropic block 状态与原始 SSE 校验，输出七种语义事件 |
| `ycode/providers/openai.py` | 仅重写受事件契约影响的流映射与私有工具分片缓存 |
| `ycode/ui/terminal.py` | 将 `MessageCompleted` 分支替换为 `StreamEnd`，其余 UI 行为不变 |

不修改配置模型、ProviderFactory、消息块模型、请求格式、renderer、输入框或其他 UI 组件。

### 测试文件

| 文件 | 覆盖重点 |
| --- | --- |
| `tests/unit/core/test_contracts.py` | 七种事件的数据契约、索引校验、不可变性、无 `kind` |
| `tests/unit/session/test_assembler.py` | 混合块组装、按 index 排序、完成块一致性、非法顺序、缺少结束、敏感数据不进入错误 |
| `tests/unit/session/test_chat.py` | consume-before-yield、成功提交、Provider/组装/取消时回滚 |
| `tests/unit/providers/test_anthropic.py` | 原始 SSE 到七事件的精确映射、signature、redacted thinking、工具 JSON、Thinking disabled 和异常流 |
| `tests/integration/test_anthropic_stream.py` | 本地 SSE 服务下的真实增量时序、多块响应和最终历史 |
| `tests/unit/providers/test_openai.py` | 将现有文本/工具/错误用例机械迁移到新事件契约 |
| `tests/integration/test_openai_stream.py` | 只迁移现有兼容断言，不增加 OpenAI 范围 |
| `tests/unit/ui/test_terminal.py` | 三种可见事件的分发、结束、错误和取消路径 |
| `tests/e2e/test_terminal_chat.py` | 真实终端会话中的流式文本、最终 Markdown、多轮历史与退出 |

### 实施顺序

1. 一次性替换核心事件定义及导出，删除旧枚举和旧事件。
2. 重写 `ResponseAssembler` 及会话包导出。
3. 重写 Anthropic 原始流归一化，并同步其单元测试。
4. 对 OpenAI 做最小兼容迁移，并同步既有测试。
5. 更新 ChatSession 与 TerminalUI 消费逻辑。
6. 更新核心、Assembler、Session、集成和 E2E 测试。
7. 搜索并确认仓库中不存在旧事件名、`StreamEventKind` 或 `MessageAssembler` 残留。

该迁移不设置兼容期：旧事件完全移除，避免新旧两套流协议同时存在。

## 验证策略

实现后依次执行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
```

另外执行：

- 使用 `rg` 检查旧事件与 `MessageAssembler` 均无残留。
- 使用项目本地 SSE 测试服务验证 Anthropic 文本分片不是在响应完成后一次性返回。
- 使用真实 ConPTY/交互式终端启动 YCode，完成至少两轮本地模拟对话，观察等待状态、流式文本、最终 Markdown、每轮计时重置和退出行为。
- 测试全部使用假 Key 或本地服务，不连接真实 Anthropic/OpenAI 账号，不在输出、异常或测试快照中写入密钥。

## 设计取舍

- 公共流保留工具生命周期，是为了让后续 AgentLoop 能实时观察工具参数，同时不把 SDK 协议泄露到核心层。
- Text 不增加完成事件，因为完整文本可由增量和 `StreamEnd` 无歧义地恢复。
- Thinking/ToolCall 保留 Complete 事件，因为签名、加密数据和解析后的工具参数需要一个可信的完整快照。
- `StreamEnd` 不携带完整 Assistant 消息，避免事件流和会话历史出现两个最终消息来源。
- ResponseAssembler 保留在 Session 层，Provider 不负责历史消息创建，ChatSession 也不承担多种内容块状态机。
- OpenAI 的工具事件允许延迟到响应结束，以换取新公共契约的完整 ID/name；这是现阶段最小兼容的明确限制，不代表未来正式适配方案。
