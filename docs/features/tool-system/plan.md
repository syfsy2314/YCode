# YCode 工具系统与 Agent Loop 技术设计

> 状态：已批准（含 Agent 回合单标题展示修订）

## 1. 设计范围

本设计实现已批准 Spec 中的六个内建工具、工具注册与执行、批次调度、ReAct
Agent Loop、会话事务、Agent 事件流、plan-only 模式、Anthropic 工具请求接入和
Terminal UI 消费。

核心工具与 Agent 层保持供应商无关。本阶段只让 Anthropic 进入 Agent Loop；
OpenAI Provider 不增加工具或系统提示能力，只通过最薄的纯聊天运行器保持现有行为，
并执行完整回归测试。

本设计不引入 Agent 框架、插件系统、权限规则、子代理、跨平台 Shell 或用户配置项。

## 2. 总体架构

```text
TerminalUI
    │ 消费 AgentEvent
    ▼
ChatSession
    │ 已提交历史、当前模式、整轮事务
    ▼
ConversationRunner
    ├── AgentLoop（Anthropic）
    │     ├── SystemPromptBuilder
    │     ├── AgentChatProvider
    │     │     └── StreamEvent → ResponseAssembler
    │     └── ToolScheduler
    │           └── ToolExecutor
    │                 └── ToolRegistry → Tool
    └── PlainChatRunner（OpenAI）
          └── ChatProvider → ResponseAssembler
```

职责边界：

- Provider 只负责一次模型请求和供应商协议转换。
- AgentLoop 负责模型轮次、停止原因判断、工具回填和终止状态。
- ChatSession 负责已提交历史、模式和事务提交，不解析 Provider 事件。
- ToolScheduler 负责调用顺序和读工具并发，不实现具体工具。
- ToolExecutor 负责查找、权限、参数校验、超时和安全错误转换。
- Tool 只实现具体能力。
- TerminalUI 只根据 AgentEvent 决定展示方式，不参与业务状态判断。

应用装配层创建 Provider、工具依赖、注册中心、执行器、调度器、运行器、Session 和
UI。装配层根据已加载的 Provider 配置选择 `AgentLoop` 或 `PlainChatRunner`；
AgentLoop 和 UI 内部不判断具体 Provider 类。

## 3. 建议代码布局

```text
ycode/
├── agent/
│   ├── contracts.py
│   ├── events.py
│   ├── loop.py
│   ├── plain.py
│   └── prompt.py
├── tools/
│   ├── contracts.py
│   ├── errors.py
│   ├── registry.py
│   ├── executor.py
│   ├── scheduler.py
│   ├── paths.py
│   ├── text_files.py
│   ├── command.py
│   └── builtin/
│       ├── read_file.py
│       ├── write_file.py
│       ├── edit_file.py
│       ├── glob.py
│       ├── grep.py
│       └── run_command.py
├── core/
│   ├── messages.py
│   ├── events.py
│   └── provider.py
├── providers/
│   ├── anthropic.py
│   └── openai.py
├── session/
│   ├── assembler.py
│   └── chat.py
└── ui/
    ├── input_box.py
    ├── renderer.py
    └── terminal.py
```

测试继续按 `unit`、`integration` 和 `e2e` 分层，并镜像主要模块边界。

## 4. 工具领域契约

### 4.1 访问分类

```python
class ToolAccess(StrEnum):
    READ = "read"
    WRITE = "write"
```

本阶段只定义两级分类。过滤接口接受分类集合，以便未来增加分类，但不提前实现权限策略。

### 4.2 参数模型与工具定义

每个工具拥有独立的 Pydantic v2 参数模型，并直接继承 Pydantic `BaseModel`。不增加
项目自定义的共同参数基类，也不让工具类继承 `BaseModel`。

```python
ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolDefinition(Generic[ArgumentsT]):
    name: str
    description: str
    access: ToolAccess
    arguments_model: type[ArgumentsT]

    @property
    def input_schema(self) -> FrozenJsonObject:
        ...
```

参数模型统一使用 `ConfigDict(extra="forbid")`，拒绝模型返回的未知字段。路径、命令和
内容中的空白可能有语义，因此不全局启用字符串清理；本阶段也不启用没有明确需求的
`strict` 或 `frozen` 配置。

`input_schema` 由 `arguments_model.model_json_schema()` 生成后通过现有
`freeze_json()` 冻结。参数模型是 Schema 与运行时校验的唯一事实来源，不再手写另一份
JSON Schema，也不增加 `jsonschema` 依赖。

### 4.3 Tool Protocol

```python
class Tool(Protocol[ArgumentsT]):
    definition: ToolDefinition[ArgumentsT]
    timeout_seconds: float

    async def execute(
        self,
        arguments: ArgumentsT,
        context: ToolContext,
    ) -> ToolExecutionResult:
        ...
```

具体工具通过结构化类型满足 Protocol，不要求继承 `BaseTool`。注册中心保存异构工具时
使用 `Tool[Any]`；Executor 根据工具定义校验参数后调用对应实现。

### 4.4 上下文与结果

```python
@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace: Path


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    content: str
    is_error: bool
    metadata: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    position: int
    call: ToolCallBlock
    result: ToolExecutionResult
    elapsed_seconds: float
```

`content` 是进入工具结果消息的主要文本；`metadata` 保存错误码、路径、范围、数量、退出
码和截断状态等稳定字段。完整结果进入 AgentEvent，Agent 在回填模型时使用稳定文本格式
合并主要内容和必要元信息。

## 5. 注册、执行与调度

### 5.1 ToolRegistry

```python
class ToolRegistry:
    def register(self, tool: Tool[Any]) -> None: ...
    def get(self, name: str) -> Tool[Any] | None: ...
    def definitions(
        self,
        allowed_access: frozenset[ToolAccess] | None = None,
    ) -> tuple[ToolDefinition[Any], ...]: ...
```

- 工具名称使用 `snake_case`。
- 注册时拒绝空名称和重复名称，重复属于应用装配错误。
- 工具定义保持注册顺序，保证请求与测试结果稳定。
- 使用显式 `create_builtin_registry(...)` 工厂装配六个工具及其依赖。
- 不使用全局可变注册表、装饰器自动注册或动态目录扫描。

### 5.2 ToolExecutor

```python
async def execute(
    call: ToolCallBlock,
    context: ToolContext,
    allowed_access: frozenset[ToolAccess],
) -> ToolExecutionResult:
    ...
```

执行顺序固定为：

1. 按名称查找工具。
2. 检查工具访问分类。
3. 使用 `thaw_json()` 恢复普通 JSON 参数。
4. 使用 `arguments_model.model_validate()` 校验并应用默认值。
5. 按工具固定超时运行 `execute()`。
6. 将结果冻结并返回。

错误处理：

- 未知工具：`unknown_tool`。
- plan-only 拦截写工具：`access_denied`。
- Pydantic 参数失败：`invalid_arguments`，只返回整理后的字段路径和消息。
- 可预期工具失败：工具抛出受控 `ToolError`，保留稳定错误码和安全信息。
- 超时：`timeout`。
- 普通未预期异常：`internal_error`，不返回 traceback、异常对象或敏感参数。
- `asyncio.CancelledError`：等待工具清理后继续传播。
- `KeyboardInterrupt` 和 `SystemExit`：不捕获为普通工具失败。

工具失败是模型可以观察并调整的正常结果，不导致 Agent 自动终止。Provider、Assembler、
Scheduler 自身的不变量破坏或其他系统错误才终止 Agent。

### 5.3 ToolScheduler

Scheduler 按模型给出的调用顺序切分批次：

```text
READ、READ、WRITE、READ、READ、WRITE
└── 并发 ──┘  串行  └── 并发 ──┘  串行
```

- 连续 READ 调用使用 asyncio 并发。
- WRITE 调用等待前一个读取批次全部结束，再单独串行执行。
- WRITE 完成前，不启动它后面的 READ。
- 工具开始事件按实际启动时产生。
- 并发读取完成事件按实际完成顺序产生。
- Scheduler 用 `position` 收集结果，最终按模型原始顺序返回。
- 一旦取消，不再启动尚未开始的后续调用。
- 每个已经发出开始事件的工具必须对应完成、失败或取消事件。

Scheduler 产生内部调度事件，AgentLoop 将其转换为供应商无关的 AgentEvent。

## 6. 工作区与文本文件服务

### 6.1 WorkspacePathResolver

集中提供：

```python
resolve_existing_file(path)
resolve_existing_directory(path)
resolve_write_target(path)
relative_display(path)
```

规则：

- 启动时规范化工作区根目录。
- 相对路径以工作区根目录为准。
- 绝对路径只有在解析后仍位于工作区内才允许。
- 现有目标使用真实路径解析符号链接和 Junction。
- 新目标解析真实父目录，再拼接目标文件名。
- 使用 `commonpath` 和 Windows `normcase` 判断边界，不使用字符串前缀判断。
- 父目录跳转和解析后越界均返回工具失败。
- 后续操作使用已经验证的路径，避免校验和实际操作采用不同目标。
- 返回模型和 UI 的路径统一为工作区相对 POSIX `/` 形式。

### 6.2 TextFileService

```python
@dataclass(frozen=True, slots=True)
class DecodedTextFile:
    text: str
    has_bom: bool
    newline: str
    total_lines: int
```

- 只接受 UTF-8 和 UTF-8 BOM。
- 含 NUL 字节的内容视为二进制。
- 读取后内部统一为 `\n`，同时记录 BOM 和原换行风格。
- 混合换行使用首次观察到的换行风格，并在元信息中标记发生过规范化。
- 编辑写回时恢复记录的 BOM 和换行风格。
- 新文件使用 UTF-8 无 BOM 和 Windows 默认 `\r\n`。

原子写入过程：

1. 在已经验证的目标目录创建临时文件。
2. 写入完整内容并刷新、关闭句柄。
3. 新建且不允许覆盖时，使用 Windows 上拒绝目标冲突的原子重命名提交。
4. 明确覆盖或编辑时使用 `os.replace()` 原子替换。
5. 失败、超时或取消时清理本次临时文件。

## 7. 六个内建工具

### 7.1 `read_file`

参数：

```python
class ReadFileArguments(BaseModel):
    path: str
    offset: int = 1
    limit: int = 2000
```

约束：

- `path` 非空。
- `offset >= 1`。
- `1 <= limit <= 2000`。
- 目标必须是工作区内的现有普通文件。
- 输出带从 1 开始的行号。
- 单次最多 2,000 行或 100 KiB；截断不能产生无效 UTF-8。
- 元信息包含路径、请求范围、实际范围、总行数和截断状态。
- 超时 30 秒。

### 7.2 `write_file`

参数：

```python
class WriteFileArguments(BaseModel):
    path: str
    content: str
    overwrite: bool = False
```

约束：

- 可以创建新文件。
- 父目录必须已经存在，不自动创建目录。
- 已有文件只有 `overwrite=True` 时才允许替换。
- 目标为目录时失败。
- 新文件使用 UTF-8 无 BOM，通过同目录临时文件原子提交。
- 超时 30 秒。

### 7.3 `edit_file`

参数：

```python
class EditFileArguments(BaseModel):
    path: str
    old_text: str
    new_text: str
```

约束：

- `old_text` 至少一个字符。
- 参数中的换行按逻辑 `\n` 与解码后的文件比较。
- 使用字面匹配，不使用正则。
- 匹配零次返回 `match_not_found`。
- 匹配多次返回 `multiple_matches` 和匹配数量。
- `old_text == new_text` 返回 `no_change`。
- 只有恰好匹配一次时才替换。
- 保留原 BOM 和换行风格并原子提交。
- 超时 30 秒。

### 7.4 `glob`

参数：

```python
class GlobArguments(BaseModel):
    pattern: str
    max_results: int = 200
```

约束：

- 模式使用工作区相对 POSIX `/`。
- 支持 `*`、`?` 和 `**`。
- 只返回普通文件。
- 使用 `pathspec` 读取工作区根 `.gitignore`。
- 始终跳过 `.git/`，其他点目录只有被忽略时才跳过。
- 结果按相对路径稳定排序。
- `1 <= max_results <= 1000`。
- 元信息包含匹配总览和截断状态。
- 超时 30 秒。

### 7.5 `grep`

参数：

```python
class GrepArguments(BaseModel):
    pattern: str
    path: str = "."
    file_pattern: str | None = None
    case_sensitive: bool = True
    max_results: int = 100
```

约束：

- 使用 Python 正则逐行搜索，不支持跨行。
- `path` 可以是工作区内的文件或目录。
- `file_pattern` 使用与 Glob 一致的 POSIX 文件模式。
- 无效正则返回 `invalid_regex`。
- 遵循根 `.gitignore` 并始终跳过 `.git/`。
- 二进制和非 UTF-8 文件跳过并计数。
- 结果包含相对路径、行号和匹配行。
- 按路径和行号稳定排序。
- `1 <= max_results <= 500`。
- 超时 30 秒。

### 7.6 `run_command`

参数：

```python
class RunCommandArguments(BaseModel):
    command: str
    cwd: str = "."
```

命令后端通过 Protocol 隔离：

```python
class CommandRunner(Protocol):
    async def run(self, command: str, cwd: Path) -> CommandResult: ...
```

本阶段只实现 `PowerShellCommandRunner`：

- 使用 `asyncio.create_subprocess_exec()` 启动 `powershell.exe`。
- 参数包含 `-NoProfile`、`-NonInteractive` 和 `-Command`。
- 不使用 `shell=True`，也不通过拼接 `Set-Location` 改变目录。
- `cwd` 默认工作区，必须是工作区内的现有目录。
- stdout 和 stderr 使用独立管道并发排空，避免子进程阻塞。
- 合计只保留 100 KiB；达到上限后继续排空但不继续保存。
- 返回退出码、stdout、stderr、耗时和截断状态。
- 非零退出码转换为 `is_error=True` 的工具结果，仍允许模型继续。
- 超时固定 120 秒。
- 超时或取消时执行 `taskkill /PID <pid> /T /F`，等待进程树退出并完成管道清理。

公开工具名保持 `run_command`，为以后单独增加其他命令后端保留边界，但本阶段不实现
Bash、POSIX Shell 或自动平台选择。

## 8. Agent 领域模型与事件

### 8.1 模式与终止状态

```python
class AgentMode(StrEnum):
    AGENT = "agent"
    PLAN_ONLY = "plan-only"


class AgentTermination(StrEnum):
    COMPLETED = "completed"
    LIMIT_REACHED = "limit_reached"
    CANCELLED = "cancelled"
    ERROR = "error"
```

```python
@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    termination: AgentTermination
    messages: tuple[ChatMessage, ...]
    final_message: ChatMessage | None = None
    error_code: str = ""
    error_message: str = ""
```

`messages` 只包含当前回合新增的临时消息，不复制此前历史。

### 8.2 AgentEvent

事件联合至少包含：

- `UserMessageEvent`
- `AgentThinkingDelta(round_number, index, text)`
- `AgentTextDelta(round_number, index, text)`
- `ToolExecutionStarted(round_number, position, call)`
- `ToolExecutionCompleted(round_number, record)`
- `ToolExecutionCancelled(round_number, position, call)`
- `ModeChangedEvent(previous_mode, mode)`
- `FinalResponseEvent(message)`
- `AgentLimitReachedEvent(max_rounds, message)`
- `AgentCancelledEvent(message)`
- `AgentErrorEvent(code, message)`

所有事件只引用 YCode 自有类型，不携带 Anthropic/OpenAI SDK 类型。工具事件携带完整调用和
结果；UI 负责生成安全摘要。

### 8.3 AgentTurn

```python
class AgentTurn(AsyncIterator[AgentEvent], Protocol):
    @property
    def result(self) -> AgentTurnResult | None: ...

    def cancel(self) -> None: ...
```

正常消费完整事件流后 `result` 必须存在。`cancel()` 发出当前回合取消请求；内部通过
asyncio 取消正在等待的 Provider 或工具子任务，但不直接取消事件消费者，确保清理完成后
仍能产生取消事件。

## 9. ReAct Agent Loop

### 9.1 构造

```python
class AgentLoop:
    def __init__(
        self,
        provider: AgentChatProvider,
        registry: ToolRegistry,
        scheduler: ToolScheduler,
        prompt_builder: SystemPromptBuilder,
        *,
        max_rounds: int = 10,
    ) -> None:
        ...
```

默认最多 10 轮，只允许应用装配时覆盖，不增加 YAML、CLI 或用户命令配置。

### 9.2 每轮流程

```text
调用 Provider
    ↓
实时转换 Thinking/Text 事件，同时交给 ResponseAssembler
    ↓
取得完整 Assistant ChatMessage 与 StopReason
    ↓
检查停止原因和 ToolCallBlock
    ├── END_TURN + 无工具调用：正常完成
    ├── TOOL_USE + 有工具调用：执行并回填，进入下一轮
    └── 其他组合：异常终止
```

具体状态规则：

| StopReason | ToolCallBlock | 结果 |
|---|---:|---|
| `END_TURN` | 无 | `COMPLETED`，产生最终回复 |
| `TOOL_USE` | 有 | 执行工具并继续 |
| `TOOL_USE` | 无 | `ERROR`，响应状态矛盾 |
| `END_TURN` | 有 | `ERROR`，响应状态矛盾 |
| `MAX_TOKENS` | 任意 | `ERROR` |
| `STOP_SEQUENCE` | 任意 | `ERROR` |
| `CONTENT_FILTER` | 任意 | `ERROR` |
| `UNKNOWN` | 任意 | `ERROR` |

模型不必调用工具。第一轮直接返回 `END_TURN` 且没有工具调用时，该 Assistant 消息就是
最终回复；经过若干工具轮后不再调用工具时使用同一终止规则。

工具结果按原调用顺序组成一条用户角色 `ChatMessage`，其中每个调用对应一个
`ToolResultBlock`。工具失败也作为结果回填，下一轮由模型决定如何调整。

第 10 轮仍请求工具时，本轮工具正常执行并产生事件，结果加入临时上下文；随后不启动第
11 次 Provider 请求，以 `LIMIT_REACHED` 结束。本轮临时历史不提交，已经发生的本地副
作用不回滚。

### 9.3 Provider 事件边界

`StreamEnd` 由 Provider 产生，只表示一次模型请求完成：

```text
Anthropic stop_reason
    → AnthropicProvider
    → StreamEnd
    → AgentLoop / ResponseAssembler
```

AgentLoop 不把 `StreamEnd` 暴露给 TerminalUI。只有
`END_TURN + 无 ToolCallBlock` 时，AgentLoop 才产生 `FinalResponseEvent`，表示整次用户
对话完成。

### 9.4 最小系统提示

`SystemPromptBuilder` 位于 Agent 层，输入工作区、Shell、当前模式和允许的工具定义，
输出供应商无关字符串。提示只说明：

- 当前工作区。
- 当前 Shell 是 PowerShell。
- 可用工具及失败后应根据结果调整。
- 普通模式可以使用允许的读写工具。
- plan-only 只能调查并最终输出实施计划，不得尝试修改。

不建设模板系统、提示缓存、上下文压缩或复杂角色编排。

### 9.5 plan-only 双重限制

- 请求前：Registry 只向 Provider 提供 `READ` 工具定义。
- 执行前：Executor 再次根据允许分类拒绝 `WRITE` 工具。

第二层用于防止异常 Provider 响应、旧上下文或伪造调用绕过请求侧过滤。被拦截的调用
返回 `access_denied` 工具结果；模式不会自动退出。

## 10. Session 与运行器

### 10.1 ConversationRunner

```python
class ConversationRunner(Protocol):
    supported_modes: frozenset[AgentMode]

    def start_turn(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AgentTurn:
        ...

    async def close(self) -> None:
        ...
```

Anthropic 使用 `AgentLoop`；OpenAI 使用最薄的 `PlainChatRunner`。后者只执行当前单次
Provider 请求、组装响应并包装成 AgentEvent，不传工具、不增加系统提示、不进入循环。

### 10.2 ChatSession 状态

```python
class ChatSession:
    _runner: ConversationRunner
    _history: list[ChatMessage]
    _mode: AgentMode
    _active_turn: AgentTurn | None
    _closed: bool
```

- `history` 对外返回不可变 tuple。
- 默认模式为 `AGENT`。
- 同一 Session 同时只允许一个活动回合。
- 关闭后拒绝新消息。
- `close()` 先取消并等待活动回合，再幂等关闭运行器和 Provider。

### 10.3 会话事务

Session 创建用户消息和历史快照，然后启动运行器。本轮用户消息、中间 Assistant 工具
调用、工具结果和最终回复全部属于临时消息。

- `COMPLETED`：一次性提交 `AgentTurnResult.messages`。
- `LIMIT_REACHED`、`CANCELLED`、`ERROR`：不提交任何本轮消息。
- 此前已提交历史始终保持不变。
- 文件和命令副作用不属于对话事务，不自动回滚。

Session 暂存终态 AgentEvent，先耗尽 AgentTurn 并取得结果。正常完成时先提交历史，再把
`FinalResponseEvent` 转发给 UI；其他终态也在确认不提交历史后再向上转发。

`UserMessageEvent` 由 Session 产生，因为用户消息在这一层进入事务。模型、工具和终止
事件由运行器产生。

### 10.4 模式命令

- 精确、大小写不敏感的 `/plan` 切换到 `PLAN_ONLY`。
- 精确、大小写不敏感的 `/agent` 切换到 `AGENT`。
- 模式命令不发送模型、不进入历史，产生 `ModeChangedEvent`。
- 重复选择当前模式也产生事件，供 UI 显示当前状态。
- `/plan xxx` 等非精确形式作为普通用户消息。
- `/exit` 和 `/quit` 继续由 UI 管理应用生命周期。
- 模式在回合开始时快照，活动回合中不切换。

OpenAI 纯聊天运行器只支持 `AGENT`。意外输入 `/plan` 时只产生安全的“不支持当前模式”
提示，保持 `AGENT`，不调用 OpenAI Provider。本阶段不扩展此分支。

## 11. Provider 设计

### 11.1 接口

保留当前 `ChatProvider`：

```python
class ChatProvider(Protocol):
    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[StreamEvent]:
        ...

    async def close(self) -> None: ...
```

增加兼容原接口的 Agent 扩展：

```python
class AgentChatProvider(ChatProvider, Protocol):
    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        system_prompt: str = "",
        tools: Sequence[ToolDefinition[Any]] = (),
    ) -> AsyncIterator[StreamEvent]:
        ...
```

可选参数有默认值，因此 AnthropicProvider 同时满足两种 Protocol。AgentLoop 只依赖
`AgentChatProvider`。

### 11.2 AnthropicProvider

Anthropic 请求在非空时增加顶层字段：

```python
if system_prompt:
    request["system"] = system_prompt
if tools:
    request["tools"] = convert_tools(tools)
```

工具转换结果：

```python
{
    "name": definition.name,
    "description": definition.description,
    "input_schema": thaw_json(definition.input_schema),
}
```

工具列表是模型请求参数，不写入 ChatMessage 历史。以下逻辑保持不变：

- ChatMessage 到 Anthropic 消息的转换。
- ToolCallBlock 到 `tool_use`。
- ToolResultBlock 到 `tool_result`。
- 流式工具参数 JSON 碎片拼接。
- ToolCallStart、ToolCallDelta、ToolCallComplete。
- StopReason 映射、异常处理和关闭。

AnthropicProvider 不执行工具、不管理循环、不识别 plan-only。

### 11.3 OpenAIProvider

OpenAIProvider 实现和签名不增加工具参数。`PlainChatRunner` 继续调用现有
`stream_chat(messages)`。必须通过单元、集成、Session/UI 链路和真实 PTY 回归测试确认：

- 请求不出现 `tools` 或 Agent system prompt。
- 纯文本流、Markdown、历史和退出行为保持有效。

不实现或测试 OpenAI 工具调用、Agent Loop 和 plan-only。

## 12. Terminal UI

### 12.1 事件消费

TerminalUI 改为只消费 AgentEvent，不再导入或判断 `StreamEnd`：

- `AgentThinkingDelta`：追加当前轮 Thinking。
- `AgentTextDelta`：以纯文本实时显示当前轮文本。
- `ToolExecutionStarted`：显示工具开始摘要。
- `ToolExecutionCompleted`：显示成功或失败摘要。
- `ToolExecutionCancelled`：显示取消状态。
- `ModeChangedEvent`：显示模式确认。
- `FinalResponseEvent`：只把最终轮转换为 Markdown。
- 上限、取消和错误事件：显示原因并恢复输入。

Renderer 按 `round_number` 隔离各轮内容。出现工具执行事件后，该轮文本确定为过程文本；
出现 FinalResponseEvent 后，最后一轮才确定为最终回复。过程文本不得拼入最终 Markdown。

`round_number` 只用于内部归属文本和工具状态，不再用于创建多个 UI 回复块。
同一次 Agent 回合的所有 `_RoundContent` 按轮次连续渲染，只在第一个内容块中显示
一次 `● YCode` 标题。`_title()` 不再追加 `round N`，Thinking 标题也不显示轮次编号。

### 12.2 工具摘要

- 文件工具只显示安全路径，不显示写入或替换内容。
- Glob 显示截断后的模式。
- Grep 显示截断后的模式和路径。
- 命令显示截断后的单行命令。
- 完成摘要显示成功、失败、取消、数量、退出码和截断状态。
- 不直接打印完整 ToolExecutionResult、环境变量、异常对象或 traceback。

展示规则只影响 UI，不改变 Agent 状态或工具结果。

#### 12.2.1 同一调用的状态覆盖

Renderer 不再用全局列表累计工具状态，而是在每个 `_RoundContent` 内按
`ToolCallBlock.id` 保存有序状态表。首次收到某个调用的开始事件时，在事件指定
的 `round_number` 内创建状态位置；后续审批等待、完成或取消事件使用相同轮次和调用 ID
覆盖该位置的文本。Python 字典的插入顺序用于保持同一轮内工具首次出现顺序。

```python
class LiveResponseRenderer:
    # _RoundContent.tool_statuses: dict[str, str]

    def set_tool_status(
        self, round_number: int, call_id: str, status: str
    ) -> None: ...
```

TerminalUI 对工具事件统一传递事件的 `round_number` 和调用 ID：

- `ToolExecutionStarted.call.id`：设置开始摘要。
- `ToolApprovalRequested.decision.subject.call.id`：覆盖为等待审批摘要。
- `ToolExecutionCompleted.record.call.id`：覆盖为成功或失败最终摘要。
- `ToolExecutionCancelled.call.id`：覆盖为取消最终摘要。

渲染时，整个 Agent 回合先显示一次 YCode 标题；每轮再依次显示该轮 Thinking、模型文本和
工具状态，下一轮的模型文本直接接在这些工具结果之后，不再插入 YCode 标题或轮次标记。
终态事件即使没有对应的开始状态也可以在指定轮次创建最终状态。多个不同调用
各自保留一条状态；同一调用无论经历多少临时状态，Renderer 最终只渲染最后一个值。
审批输入、AgentEvent、工具执行顺序和模型回填内容均不改变。

### 12.3 模式提示

`InputBox.read(mode=session.mode)` 在底部提示行右侧显示：

```text
? for help                                      mode: agent
? for help                                  mode: plan-only
```

宽度策略：

1. 足够宽时显示帮助和完整模式。
2. 空间不足时先隐藏左侧帮助。
3. 更窄时显示 `agent` 或 `plan-only`。
4. 极窄时显示 `A` 或 `P`。

布局字符串由独立纯函数计算并进行宽度测试。模式命令处理后，下一个输入框立即显示新状态。

### 12.4 取消与终端恢复

- 等待输入时 Ctrl+C/EOF 保持退出应用的现有行为。
- Agent 活动期间 Ctrl+C 只取消当前回合。
- UI 调用 `session.cancel_active_turn()` 并等待 Provider、工具和 PowerShell 清理。
- 收到 AgentCancelledEvent 后停止计时与 Live Renderer，恢复输入循环。
- 应用整体被外部任务取消时，清理后继续传播 `asyncio.CancelledError`。
- 所有 Renderer 终态方法幂等，避免 Rich Live、计时任务或光标残留。

Windows Ctrl+C 的实际接入必须通过真实 PTY 测试验证，不能只以模拟事件代替。

## 13. 取消、超时与资源清理

- AgentTurn 使用取消信号控制当前 Provider 或 Scheduler 子任务。
- Provider 流取消后关闭本次流上下文，不再发起下一轮。
- Scheduler 取消所有已启动读取任务并等待它们结束。
- 等待写屏障时取消，不启动该写工具及后续调用。
- 文件工具取消时清理临时文件，原目标保持可用。
- PowerShell 取消或超时时终止完整进程树并排空管道。
- 外层事件消费者直接被取消时，Agent 完成必要清理后重新抛出 CancelledError。
- 只有用户可观察的正常取消路径产生 AgentCancelledEvent；系统异常产生 AgentErrorEvent。

超时由 ToolExecutor 统一施加：文件、编辑、Glob 和 Grep 为 30 秒，run_command 为 120 秒。
模型参数不能覆盖这些值。

## 14. 依赖变化

继续复用现有 `pydantic>=2`，不增加 JSON Schema 校验依赖。只增加：

```text
pathspec
```

它用于解析根 `.gitignore`。不调用外部 Git、rg、Bash 或跨平台 Shell 框架。

## 15. 验证策略

后续 `task.md` 将把验证拆到每个实现任务。本设计要求至少覆盖：

- ToolDefinition Schema、注册、重复名称、查找和分类过滤。
- 六个工具的成功、边界、截断、编码、原子性和错误结果。
- 工作区内外路径、`..`、符号链接和 Windows Junction。
- Pydantic 参数错误的安全转换。
- READ 并发、WRITE 屏障、结果原序回填和取消。
- Agent 正常完成、多工具多轮、四种终态和十轮上限。
- Session 正常提交以及错误、取消、上限时回滚临时历史。
- plan-only 请求过滤和执行边界拦截。
- Anthropic 请求 system/tools、流式工具参数和工具结果历史。
- OpenAI 请求、SSE、Session/UI 和 PTY 纯聊天回归。
- UI 多轮内容、单 YCode 标题、工具摘要、同一工具调用的状态覆盖、工具状态的轮次归属和模式窄宽布局。
- 真实 Windows PTY 中的读、搜、写、编辑、命令、失败、取消、上限和退出。

最终仍执行仓库规定的 Ruff 格式检查、Ruff 静态检查、完整 Pytest、compileall 和
`checklist.md` 全部项目。
