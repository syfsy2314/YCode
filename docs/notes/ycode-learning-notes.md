# YCode 学习笔记

> 本笔记以当前代码为准，主要沿 Anthropic 调用链理解项目。

## 命令启动流程

YCode 有两个入口：

~~~powershell
ycode
python -m ycode
~~~

两者最终都会调用 `ycode.cli.main()`。

### 1. ycode 命令

`pyproject.toml` 注册命令：

~~~toml
[project.scripts]
ycode = "ycode.cli:main"
~~~

安装项目后会生成 `.venv\Scripts\ycode.exe`：

~~~text
PowerShell 执行 ycode
    ↓
ycode.exe
    ↓
ycode.cli.main()
~~~

### 2. python -m ycode

Python 执行 `ycode/__main__.py`：

~~~python
from ycode.cli import main

raise SystemExit(main())
~~~

调用链：

~~~text
python -m ycode
    ↓
ycode/__main__.py
    ↓
ycode.cli.main()
~~~

### 3. cli.main()

~~~python
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        asyncio.run(run_app(args.config))
    except (ConfigError, UIError) as error:
        print(f"YCode: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0

    return 0
~~~

`main()` 负责：

- 解析 `--config`。
- 使用 `asyncio.run()` 启动异步应用。
- 处理启动错误和 Ctrl+C。
- 返回进程退出码。

### 4. run_app()

`run_app()` 是组合根：它读取配置、创建 Provider，并根据协议选择对话运行器。

简化后的核心逻辑：

~~~python
provider = create_provider(config.active_provider)
workspace = Path(start_dir or Path.cwd()).resolve()

if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
    resolver = WorkspacePathResolver(workspace)
    registry = create_builtin_registry(
        resolver,
        TextFileService(),
        PowerShellCommandRunner(),
    )
    executor = ToolExecutor(registry)
    runner = AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, executor),
        SystemPromptBuilder(workspace),
        ToolContext(workspace),
    )
else:
    runner = PlainChatRunner(provider)

session = ChatSession(runner)
~~~

Anthropic 的对象关系：

~~~text
TerminalUI
    └── ChatSession
            └── AgentLoop
                    ├── AnthropicProvider
                    ├── ToolRegistry
                    ├── ToolScheduler
                    │       └── ToolExecutor
                    ├── SystemPromptBuilder
                    └── ToolContext
~~~

OpenAI 当前保持纯聊天：

~~~text
TerminalUI
    └── ChatSession
            └── PlainChatRunner
                    └── OpenAIProvider
~~~

这里有两个重要边界：

- Anthropic 进入 AgentLoop，能够使用工具并进行多轮 ReAct 循环。
- OpenAI 只通过 PlainChatRunner 发起一次模型请求，没有工具定义、Agent system prompt 或 plan-only 能力。

退出时，`finally` 调用 `session.close()`。Session 再关闭 Runner，Runner 最终关闭 Provider 和 SDK client。

### 面试表述

YCode 通过 `pyproject.toml` 注册 CLI 入口。`cli.main()` 负责参数解析和启动 asyncio 事件循环，`run_app()` 是组合根：它加载配置、创建 Provider，并按协议装配 Anthropic AgentLoop 或 OpenAI PlainChatRunner，最后注入 ChatSession 和 TerminalUI。资源关闭沿 Session、Runner、Provider 逐层传递。

## 核心数据与组件关系

当前系统存在两层事件流：

~~~text
Provider 单次请求层：StreamEvent
Agent 对外过程层：AgentEvent
~~~

完整依赖方向：

~~~text
TerminalUI
    ↓ AgentEvent
ChatSession
    ↓ ConversationRunner / AgentTurn
AgentLoop 或 PlainChatRunner
    ↓ StreamEvent
ChatProvider / AgentChatProvider
    ↓
具体 Provider 与供应商 SDK
~~~

### 1. ChatMessage 与 ContentBlock

`ChatMessage` 保存角色和有序内容块，而不是单个字符串：

~~~python
@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: tuple[ContentBlock, ...]
~~~

主要内容块：

| 类型 | 含义 |
|---|---|
| TextBlock | 普通文本 |
| ThinkingBlock | Thinking 文本和 signature |
| RedactedThinkingBlock | 不可读的加密 Thinking |
| ToolCallBlock | 工具调用 ID、名称和完整参数 |
| ToolResultBlock | 与原调用 ID 对应的工具执行结果 |

普通用户输入：

~~~python
user_message = ChatMessage.user_text(user_text)
~~~

得到：

~~~python
ChatMessage(
    role="user",
    content=(TextBlock(user_text),),
)
~~~

`message.text` 用于提取消息中所有 TextBlock 的文本。工具调用和工具结果仍保留在 `content` 中，不会被 `text` 混入。

### 2. Provider 层的 StreamEvent

Provider 公共流由七种不可变事件组成：

~~~text
TextDelta
ThinkingDelta
ThinkingComplete
ToolCallStart
ToolCallDelta
ToolCallComplete
StreamEnd
~~~

`StreamEvent` 只描述一次模型请求。`StreamEnd` 表示本次 Provider 响应结束，并不表示整次 Agent 对话已经结束。

消费者使用 `isinstance()` 判断事件：

~~~python
if isinstance(event, TextDelta):
    ...
~~~

`ResponseAssembler` 消费完整 StreamEvent 流，把它组装成一条 Assistant ChatMessage。

### 3. Agent 层的 AgentEvent

AgentLoop 或 PlainChatRunner 把 Provider 事件转换为供应商无关的 AgentEvent：

| AgentEvent | 含义 |
|---|---|
| UserMessageEvent | 当前用户消息 |
| AgentThinkingDelta | 指定 Agent 轮次的 Thinking 增量 |
| AgentTextDelta | 指定 Agent 轮次的文本增量 |
| ToolExecutionStarted | 工具开始执行 |
| ToolExecutionCompleted | 工具完成，携带完整执行记录 |
| ToolExecutionCancelled | 已启动工具被取消 |
| ModeChangedEvent | agent 与 plan-only 模式变化 |
| FinalResponseEvent | Agent 正常完成后的最终回复 |
| AgentLimitReachedEvent | 达到最大模型轮数 |
| AgentCancelledEvent | 用户取消当前回合 |
| AgentErrorEvent | Provider、组装或状态异常 |

关键区别：

~~~text
StreamEvent
    只服务于一次 Provider 请求
    StreamEnd 可能在一次 Agent 对话中出现多次

AgentEvent
    描述整个用户回合的可观察过程
    FinalResponseEvent 才代表正常最终回复
~~~

TerminalUI 只消费 AgentEvent，不导入或判断 StreamEnd。

### 4. Provider 与 ResponseAssembler

Provider 是协议适配器，负责两个方向：

~~~text
ChatMessage + system prompt + ToolDefinition
    ↓
供应商请求格式

供应商 SDK Event
    ↓
StreamEvent
~~~

`ChatProvider` 是纯聊天接口；`AgentChatProvider` 在它的基础上增加可选的 `system_prompt` 和 `tools` 参数。

AnthropicProvider 会把 ToolDefinition 转换成 Anthropic 顶层 `tools`：

~~~text
ToolDefinition.name         → name
ToolDefinition.description  → description
ToolDefinition.input_schema → input_schema
~~~

Provider 只负责一次请求的协议转换和流解析，不负责：

- 执行工具。
- 决定是否继续循环。
- 提交会话历史。
- 产生最终 Agent 状态。

`ResponseAssembler` 在每次 Provider 请求中重新创建：

~~~text
多个 StreamEvent
    ↓ consume(event)
ResponseAssembler
    ↓ finish()
完整 Assistant ChatMessage
~~~

它按内容块 index 保存状态，并校验：

- 是否收到 StreamEnd。
- 内容块索引与类型是否一致。
- Thinking 和 ToolCall 是否完整。
- 工具参数是否为有效 JSON object。
- 响应结束后是否出现额外事件。

### 5. ConversationRunner、AgentTurn 与终止结果

ChatSession 不直接依赖 Provider，而是依赖 `ConversationRunner`：

~~~python
class ConversationRunner(Protocol):
    supported_modes: frozenset[AgentMode]

    def start_turn(
        self,
        history,
        user_message,
        mode,
    ) -> AgentTurn:
        ...
~~~

当前有两个实现：

| Runner | 用途 |
|---|---|
| AgentLoop | Anthropic 工具调用和多轮循环 |
| PlainChatRunner | OpenAI 单次纯聊天 |

`AgentTurn` 同时是：

- AgentEvent 异步迭代器。
- 当前回合的取消入口。
- 回合结束后 AgentTurnResult 的容器。

Agent 对外只有四种终止结果：

| AgentTermination | 含义 |
|---|---|
| COMPLETED | 正常得到最终回复 |
| LIMIT_REACHED | 达到最大轮数 |
| CANCELLED | 用户取消 |
| ERROR | Provider、组装或状态异常 |

只有事件流完全消费结束后，`turn.result` 才可见。

### 6. ChatSession

ChatSession 负责：

- 保存已经提交的对话历史。
- 保存当前 AgentMode。
- 保证同一时间只有一个活动 AgentTurn。
- 处理 `/plan` 和 `/agent`。
- 传播取消和关闭。
- 根据 AgentTurnResult 决定提交或回滚。

简化调用链：

~~~text
用户输入
    ↓
ChatSession 创建 UserMessage
    ↓
runner.start_turn(history_snapshot, user_message, mode)
    ↓
转发非终态 AgentEvent
    ↓
完整消费 AgentTurn
    ↓
读取 AgentTurnResult
    ↓
COMPLETED：一次性提交 result.messages
其他状态：不提交本轮历史
    ↓
最后向 UI 转发终态事件
~~~

为什么终态事件要暂存？

如果 UI 先看到 FinalResponseEvent，而历史还没有提交，上层会观察到“界面已完成、Session 仍未完成”的短暂不一致。因此 Session 先处理事务，再把终态事件交给 UI。

一次完整 Agent 回合可能提交：

~~~text
UserMessage
Assistant(ToolCall)
User(ToolResult)
Assistant(ToolCall)
User(ToolResult)
Assistant(Final Text)
~~~

Provider 错误、响应组装错误、达到上限、用户取消或调用方提前停止消费时，本轮临时历史不提交。已经完成的文件或命令副作用不会自动回滚。

### 7. TerminalUI 与 Renderer

TerminalUI 从 Session 接收 AgentEvent：

~~~python
async for event in session.stream_reply(user_text):
    if isinstance(event, AgentThinkingDelta):
        renderer.append_thinking(event.text, event.round_number)
    elif isinstance(event, AgentTextDelta):
        renderer.append_text(event.text, event.round_number)
    elif isinstance(event, ToolExecutionStarted):
        renderer.add_tool_status(...)
    elif isinstance(event, FinalResponseEvent):
        await renderer.complete(event.message)
~~~

UI 的展示原则：

- Thinking 和过程文本按 Agent 轮次显示。
- 工具开始、成功、失败和取消显示安全摘要。
- 写文件内容和完整命令输出不会直接显示。
- 工具调用轮的文本是过程文本，不会混入最终 Markdown。
- 只有 FinalResponseEvent 携带的最后一轮消息进行 Markdown 渲染。
- 上限、错误和取消都会停止计时与 Rich Live，并恢复输入。

InputBox 的右下角显示当前模式。宽终端显示 `mode: agent` 或 `mode: plan-only`，窄终端会逐步降级，但优先保留模式信息。

### 8. 职责边界

| 组件 | 职责 |
|---|---|
| AnthropicProvider / OpenAIProvider | 单次模型请求和协议转换 |
| ResponseAssembler | 把单次 StreamEvent 流组装成 Assistant 消息 |
| AgentLoop | 多轮判断、工具执行、结果回填和 Agent 终止 |
| PlainChatRunner | 把单次纯聊天包装成 AgentTurn |
| ToolRegistry | 登记工具并提供当前可用定义 |
| ToolExecutor | 查找、权限、参数校验、超时和安全错误转换 |
| ToolScheduler | 读取并发、写入屏障和执行事件 |
| ChatSession | 历史、模式、活动回合和事务提交 |
| TerminalUI | 消费 AgentEvent 并控制输入、取消与展示 |
| Renderer | 多轮内容、工具摘要、计时和最终 Markdown |

可以记成：

~~~text
Provider 负责翻译一次响应
Assembler 负责拼装一次响应
AgentLoop 负责多轮行动
Tool 系统负责执行本地能力
ChatSession 负责事务和模式
TerminalUI 负责交互与展示
~~~

### 面试表述

YCode 使用两层供应商无关事件隔离职责：Provider 将 SDK SSE 转换为单次请求级 StreamEvent，AgentLoop 或 PlainChatRunner 再转换为整轮对话级 AgentEvent。ResponseAssembler 负责单次响应完整性，AgentLoop 负责 ReAct 循环和工具回填，ChatSession 只在 COMPLETED 后事务式提交整轮历史，TerminalUI 完全不感知 StreamEnd。

## Anthropic SSE 流式链路

YCode 不直接解析 SSE 文本，而是由 Anthropic SDK 解析，再由 AnthropicProvider 转成公共 StreamEvent。

~~~text
Anthropic API
    ↓ 原始 SSE
AsyncAnthropic client
    ↓ Anthropic SDK Event
AnthropicProvider
    ↓ StreamEvent
AgentLoop
    ├── ResponseAssembler
    └── AgentEvent
            ↓
        ChatSession
            ↓
        TerminalUI
            ↓
        Renderer
~~~

### 1. 建立单次响应流

~~~python
stream = await self.client.messages.create(
    model=self._config.model,
    max_tokens=16_000,
    messages=self._messages(messages),
    stream=True,
    thinking=thinking_config,
    system=system_prompt,
    tools=tool_definitions,
)
~~~

`system` 和 `tools` 只在非空时加入请求。`await create()` 等待请求与响应流建立，不等待完整回答；随后使用 `async for` 逐个读取 SDK Event。

### 2. Anthropic 原始事件映射

Provider 在单次请求内维护私有内容块状态，并按 index 关联事件。

| Anthropic 原始事件 | Provider 行为 | 公共事件 |
|---|---|---|
| message_start | 标记消息开始 | 无 |
| text_delta | 读取文本 | TextDelta |
| thinking_delta | 累计并输出 Thinking | ThinkingDelta |
| signature_delta | 只在 Provider 内累计 | 无 |
| input_json_delta | 累计工具参数碎片 | ToolCallDelta |
| thinking block stop | 构造完整 ThinkingBlock | ThinkingComplete |
| tool block start | 保存 ID 和名称 | ToolCallStart |
| tool block stop | 解析完整参数 | ToolCallComplete |
| message_delta | 保存停止原因 | 无 |
| message_stop | 标记供应商响应完成 | 无 |
| SDK 迭代器自然结束 | 验证响应完整 | StreamEnd |

工具参数 JSON 可能被拆成多个 `input_json_delta`。Provider 先产生 ToolCallDelta，等内容块结束时再解析完整 JSON，并产生带 ToolCallBlock 的 ToolCallComplete。

### 3. 事件逐层传递

一次文本增量的路径：

~~~text
Anthropic text_delta("你好")
    ↓
Provider yield TextDelta(index, "你好")
    ↓
AgentLoop: assembler.consume(event)
    ↓
AgentLoop yield AgentTextDelta(round_number, index, "你好")
    ↓
ChatSession yield AgentTextDelta
    ↓
TerminalUI
    ↓
Renderer 显示“你好”
~~~

一次工具调用的路径：

~~~text
Anthropic tool_use
    ↓
ToolCallStart / ToolCallDelta / ToolCallComplete / StreamEnd(TOOL_USE)
    ↓
ResponseAssembler.finish()
    ↓
Assistant ChatMessage(ToolCallBlock)
    ↓
AgentLoop 执行工具
    ↓
User ChatMessage(ToolResultBlock)
    ↓
下一次 Anthropic 请求
~~~

### 4. await、async for 与 yield

| 写法 | 作用 |
|---|---|
| await create() | 等待一个异步结果：Stream 对象 |
| async for | 逐个读取多个异步事件 |
| yield | 向上交付一个事件并暂停生成器 |

可以记成：

~~~text
await：向下等待
async for：逐个读取
yield：向上交付
~~~

### 5. 完成与错误

AnthropicProvider 只有在收到 `message_stop`、所有内容块关闭并且 SDK 迭代器自然结束后，才产生 StreamEnd。

但 StreamEnd 只结束当前模型请求。AgentLoop 随后检查：

- `END_TURN + 无 ToolCallBlock`：正常产生 FinalResponseEvent。
- `TOOL_USE + 有 ToolCallBlock`：执行工具并继续下一轮。
- 停止原因与内容矛盾：产生 AgentErrorEvent。
- `MAX_TOKENS`、`STOP_SEQUENCE`、`CONTENT_FILTER` 或未知原因：按异常结束。

### 面试表述

AnthropicProvider 负责把 SDK 的 message/block 生命周期转换成七种 StreamEvent，并在内容块结束时拼接工具 JSON 参数。每次模型请求由新的 ResponseAssembler 校验和组装；AgentLoop 根据停止原因和 ToolCallBlock 决定执行工具、继续请求或产生最终 AgentEvent。因此 Provider 仍是单次协议适配器，循环控制没有进入 Provider。

## 工具系统架构

工具系统位于 `ycode.tools`，核心层不导入 Anthropic 或 OpenAI SDK 类型。

### 1. Tool Protocol 与 ToolDefinition

每个工具通过结构化类型满足 Tool Protocol，无需继承共同基类：

~~~python
class Tool(Protocol):
    definition: ToolDefinition
    timeout_seconds: float

    async def execute(arguments, context) -> ToolExecutionResult:
        ...
~~~

ToolDefinition 保存：

- 唯一工具名。
- 描述。
- READ 或 WRITE 分类。
- 具体 Pydantic 参数模型。
- 从参数模型生成并冻结的 JSON Schema。

Pydantic 参数模型同时是“发送给模型的参数 Schema”和“执行前运行时校验”的唯一事实来源。

### 2. 六个内建工具

| 工具 | 分类 | 用途 |
|---|---|---|
| read_file | READ | 分页读取工作区 UTF-8 文本 |
| glob | READ | 按 POSIX Glob 查找文件 |
| grep | READ | 使用 Python 正则逐行搜索内容 |
| write_file | WRITE | 创建或显式覆盖文本文件 |
| edit_file | WRITE | 使用原文唯一匹配替换文件内容 |
| run_command | WRITE | 在工作区目录中执行 PowerShell |

READ/WRITE 是本阶段固定的二级分类。`run_command` 不分析命令文本，统一视为 WRITE。

### 3. Registry、Executor 与 Scheduler

三者分工：

~~~text
ToolRegistry
    登记工具、拒绝重名、按名称查找、导出当前允许的 ToolDefinition
        ↓
ToolExecutor
    查找 → 权限 → Pydantic 参数校验 → 超时 → 调用工具
        ↓
ToolScheduler
    按 READ/WRITE 分类安排同一响应中的多个工具调用
~~~

ToolExecutor 不让可预期错误直接抛出到 AgentLoop，而是转换成 ToolExecutionResult：

~~~text
unknown_tool
access_denied
invalid_arguments
timeout
具体 ToolError
internal_error
~~~

结果包含：

- `content`：给模型阅读的主要内容。
- `is_error`：是否失败。
- `metadata`：错误码、截断、数量、退出码等结构化信息。

因此工具失败通常不会终止 Agent。失败结果会回填给模型，让模型在下一轮调整。

### 4. 读取并发与写入屏障

ToolScheduler 的规则：

~~~text
连续 READ：并发执行
WRITE：等待前面的 READ 全部完成，再单独串行执行
WRITE 后的 READ：不能提前越过写入屏障
~~~

例如：

~~~text
read_file ─┐
glob      ─┼─ 并发完成
grep      ─┘
              ↓
write_file    串行
              ↓
edit_file     串行
~~~

并发读取的完成事件按实际完成顺序交给 UI，但回填模型的 ToolResultBlock 会按模型原始调用位置重新排序，保证调用 ID 与结果稳定对应。

### 5. 工作区和资源边界

所有文件工具通过 WorkspacePathResolver 解析路径：

- 相对路径以当前工作区为根。
- 绝对路径解析后也必须位于工作区。
- `..`、符号链接和 Junction 解析后不能越界。
- 工具返回的路径统一为工作区相对 POSIX 路径。

TextFileService 负责 UTF-8/BOM、换行规范化和同目录原子写入。PowerShellCommandRunner 负责异步排空 stdout/stderr，并在取消时使用 `taskkill /T /F` 终止完整进程树。

系统还限制文件读取量、搜索结果、命令输出、工具超时和 Agent 轮数，避免无限占用资源。

### 面试表述

YCode 的工具通过 Protocol 结构化满足统一接口，ToolDefinition 从 Pydantic 参数模型生成 Schema。Registry 负责发现，Executor 负责校验、权限、超时和错误归一化，Scheduler 负责连续读取并发及写入屏障。工具失败被包装成结构化结果回灌模型，因此模型可以调整，而不是让 Agent 直接崩溃。

## ReAct Agent Loop

### 1. 一轮循环

AgentLoop 的一轮是：

~~~text
调用 Provider
    ↓
流式接收 StreamEvent
    ↓
ResponseAssembler 组装 Assistant ChatMessage
    ↓
检查 StopReason 与 ToolCallBlock
    ↓
有工具：Scheduler 执行
    ↓
结果转成 ToolResultBlock
    ↓
追加到 working_messages
    ↓
进入下一轮
~~~

核心有两份消息集合：

| 集合 | 用途 |
|---|---|
| working_messages | 历史快照 + 当前回合全部临时消息，用于下一次 Provider 请求 |
| turn_messages | 当前用户回合产生的消息，正常完成后交给 Session 提交 |

工具结果以用户角色消息回填：

~~~python
ToolResultBlock(
    record.call.id,
    result_json,
    record.result.is_error,
)
~~~

调用 ID 保证模型知道每个结果对应哪个 ToolCallBlock。

### 2. 继续与终止

正常组合：

| StopReason 与内容 | Agent 行为 |
|---|---|
| TOOL_USE + 有工具调用 | 执行工具并继续 |
| END_TURN + 无工具调用 | 正常结束并产生 FinalResponseEvent |

异常组合：

- TOOL_USE 但没有工具调用。
- END_TURN 却包含工具调用。
- MAX_TOKENS、STOP_SEQUENCE、CONTENT_FILTER 或 UNKNOWN。
- Provider 错误或 ResponseAssembler 失败。

默认最多 10 轮。第 10 轮如果仍然调用工具，工具会执行并产生结果，但 Agent 不会发起第 11 次请求，而是以 LIMIT_REACHED 结束。

### 3. 为什么最终回复不是 StreamEnd

一次 Agent 对话可能包含多个 Provider 请求：

~~~text
第 1 次请求：StreamEnd(TOOL_USE)
第 2 次请求：StreamEnd(TOOL_USE)
第 3 次请求：StreamEnd(END_TURN)
~~~

前两个 StreamEnd 只表示模型暂时交出工具控制权。只有 AgentLoop 判断最后一次响应没有工具调用时，才产生 FinalResponseEvent。

### 面试表述

YCode 的 AgentLoop 实现最小 ReAct：每轮创建新的 ResponseAssembler，完成一次模型请求后检查停止原因；工具调用交给 Scheduler，结构化结果按调用 ID 回填，再继续请求。无工具调用的 END_TURN 才形成最终回复。循环默认 10 轮，并把正常完成、上限、取消和异常统一成四种终止结果。

## 模式、事务与取消

### 1. agent 与 plan-only

ChatSession 识别两个精确命令：

~~~text
/plan   → plan-only
/agent  → agent
~~~

模式命令：

- 不调用 Provider。
- 不进入对话历史。
- 产生 ModeChangedEvent。
- 只在当前进程内保存。

plan-only 有两层写入保护：

~~~text
第一层：Registry 只把 READ ToolDefinition 发给模型
第二层：Executor 仍使用 allowed_access 拦截伪造的 WRITE 调用
~~~

最终计划输出后不会自动退出 plan-only。OpenAI 使用 PlainChatRunner，不支持 plan-only；输入 `/plan` 会得到 unsupported_mode 错误且不会请求 Provider。

### 2. 整轮事务

ChatSession 的事务边界是整个用户回合，而不是单次 Provider 请求：

~~~text
COMPLETED
    → 提交用户消息、所有 ToolCall/ToolResult 和最终回复

LIMIT_REACHED / CANCELLED / ERROR
    → 当前回合历史不提交
~~~

“历史回滚”只指内存中的对话历史。已经成功执行的写文件、编辑或命令副作用不会自动撤销。

### 3. 取消传播

响应期间，InputBox 单独监听 Ctrl+C：

~~~text
Ctrl+C
    ↓
TerminalUI
    ↓ cancel_active_turn()
ChatSession
    ↓ AgentTurn.cancel()
AgentTurnStream 取消当前 active child
    ↓
Provider / Scheduler / ToolExecutor / CommandRunner
~~~

取消要求：

- 不再启动新的工具或下一轮模型请求。
- 已启动的读取任务被取消并等待清理。
- PowerShell 及其子进程树被终止。
- 当前回合历史不提交。
- Renderer 停止计时和 Rich Live。
- TUI 回到输入状态。

外层 asyncio 任务被取消时，清理完成后仍继续传播 CancelledError，不把它伪装成普通 Agent 取消。

### 面试表述

YCode 把整个用户回合作为会话事务：只有 COMPLETED 才提交历史，达到上限、用户取消和异常都丢弃临时消息。plan-only 同时限制模型可见工具和 Executor 执行权限。Ctrl+C 则沿 TerminalUI、Session、AgentTurn、Scheduler 一直传播到 PowerShell 进程树，清理后恢复输入。

## 当前验证状态

工具系统与 Agent Loop 已通过：

~~~text
完整 pytest：237 passed，1 skipped
Windows PTY：14 passed
Ruff format：通过
Ruff check：通过
compileall：通过
两个 CLI help：通过
~~~

唯一跳过项是文件符号链接越界测试：当前 Windows 环境没有创建符号链接的权限。使用相同工作区边界逻辑的 Junction 越界测试已经通过。

验证覆盖：

- 六个工具的真实 Windows PTY 执行。
- Anthropic 两个工具轮后的结果回填与最终回复。
- OpenAI 纯聊天请求不包含工具字段。
- READ 并发和 WRITE 屏障。
- plan-only 工具过滤与执行边界拦截。
- 10 轮上限。
- 活动 PowerShell 命令 Ctrl+C 取消和历史回滚。
- Provider 错误恢复、最终 Markdown 和窄终端布局。
