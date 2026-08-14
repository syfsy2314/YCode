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
        if args.continue_session:
            asyncio.run(run_app(args.config, continue_session=True))
        else:
            asyncio.run(run_app(args.config))
    except (ConfigError, UIError) as error:
        print(f"YCode: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0

    return 0
~~~

`main()` 负责：

- 解析 `--config` 和 `--continue`。
- 使用 `asyncio.run()` 启动异步应用。
- 处理启动错误和 Ctrl+C。
- 返回进程退出码。

### 4. run_app()

`run_app()` 是组合根：它读取配置、只创建 `active` 指向的 Provider，并根据协议选择
对话运行器。Provider 工厂在协议分支内局部导入实现，因此活动配置为 Anthropic 时不会
导入 OpenAI Provider 或 OpenAI SDK。Anthropic 路径还会装配命令框架、内建工具、MCP、
权限系统、项目 Hook 和 AgentLoop；OpenAI 仍保持纯聊天，也不加载 Hook。

简化后的核心逻辑：

~~~python
config = load_config(path)
if continue_session and config.active_provider.protocol is ProviderProtocol.OPENAI:
    raise ConfigError("--continue 当前仅支持 Anthropic 会话")

provider = create_provider(config.active_provider)
workspace = config.project_root

if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
    command_runtime = build_command_runtime()
    memory_store = MemoryStore(workspace)
    project_context = ProjectContextLoader(workspace, memory_store).load()
    prompt_runtime = PromptRuntimeContext()
    for supplement in project_context.supplements:
        prompt_runtime.set_session_supplement(supplement)

    session_manager = SessionManager(workspace)
    context_manager = ContextManager(...)
    resolver = WorkspacePathResolver(workspace)
    registry = create_builtin_registry(...)

    if config.mcp.servers or config.mcp.issues:
        manager = McpManager(config.mcp, registry, config.redactor)
        has_enabled_mcp = any(server.enabled for server in config.mcp.servers)
        if has_enabled_mcp:
            registry.register(ToolSearchTool(registry))

    security_result = load_security_config(workspace, registry)
    permission_session = PermissionSession(security_result.config.mode)
    permission_engine = PermissionEngine(
        registry, resolver, security_result.config, PowerShellSafetyChecker(workspace)
    )
    hook_result = load_hook_config(workspace)
    hook_runtime = HookRuntime(hook_result.rules, workspace)
    hook_context = HookContextFactory(workspace, uuid4().hex)
    runner = AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, ToolExecutor(registry)),
        build_builtin_prompt(),
        prompt_runtime,
        EnvironmentCollector(workspace),
        ToolContext(workspace),
        permission_engine=permission_engine,
        permission_session=permission_session,
        plan_only_mcp_tools=frozenset(
            security_result.config.plan_only.allow_mcp_tools
        ),
        resource_manager=manager,
        context_manager=context_manager,
        hook_runtime=hook_runtime,
        hook_context=hook_context,
    )
else:
    runner = PlainChatRunner(provider)
    permission_session = None

if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
    session = ChatSession(
        runner,
        permission_session,
        manager,
        context_manager,
        session_manager=session_manager,
        prompt_runtime=prompt_runtime,
        memory_store=memory_store,
        memory_updater=MemoryUpdater(provider),
        command_runtime=command_runtime,
        hook_runtime=hook_runtime,
        hook_context=hook_context,
    )
    if continue_session:
        await session.restore()
    await session.start_hooks()
else:
    session = ChatSession(runner)

if manager is not None and has_enabled_mcp:
    manager.start_background()

ui = TerminalUI(config.active_provider, session)
await ui.run()
~~~

Anthropic 的对象关系：

~~~text
TerminalUI
    ├── InputBox
    │       └── CommandCompleter
    └── ChatSession
            ├── CommandRuntime
            │       ├── CommandRegistry
            │       └── CommandDispatcher
            └── AgentLoop
                    ├── AnthropicProvider
                    ├── ToolRegistry
                    │       ├── 六个基础内建工具
                    │       ├── LoadSkillTool / InstallSkillTool
                    │       ├── ToolSearchTool（配置 MCP 时）
                    │       └── MCPToolWrapper...
                    ├── McpManager
                    │       └── McpConnection...
                    ├── ToolScheduler
                    │       └── ToolExecutor
                    ├── PermissionEngine
                    │       ├── PermissionSession
                    │       └── PowerShellSafetyChecker
                    ├── HookRuntime
                    │       ├── RuntimeHookRule
                    │       └── HookActionExecutors
                    ├── HookContextFactory
                    ├── PromptBundle
                    ├── PromptRuntimeContext
                    ├── ProjectContextLoader
                    ├── EnvironmentCollector
                    ├── ContextManager
                    └── ToolContext
            ├── SessionManager
            ├── HookRuntime / HookContextFactory
            ├── MemoryStore
            └── MemoryUpdater
~~~

OpenAI 当前保持纯聊天：

~~~text
TerminalUI
    └── ChatSession
            └── PlainChatRunner
                    └── OpenAIProvider
~~~

这里有两个重要边界：

- Anthropic 进入 AgentLoop，能够使用内建工具和 MCP 工具并进行多轮 ReAct
  循环。
- Anthropic 装配集中式命令运行时；OpenAI 不装配命令注册、分发或补全。
- Anthropic 使用 `config.project_root` 统一工具、项目指令、会话、上下文和记忆的路径
  边界，并支持 `--continue`。
- Anthropic 从最近的 `.ycode/hooks.yaml` 创建一个应用会话级 `HookRuntime`，只注入主
  AgentLoop 与 ChatSession；隔离 Skill AgentLoop 不继承它。
- OpenAI 只通过 PlainChatRunner 发起一次模型请求，没有工具定义、Agent system prompt 或 plan-only 能力。
- OpenAI 不装配持久化会话、项目记忆或 Hook，使用 `--continue` 会在创建 Provider 前失败。
- `enabled: true` 的 MCP 在 UI 装配完成后后台连接；`enabled: false` 不创建连接任务。

退出时，`finally` 调用 `session.close()`。Session 先触发 `session.end`，再让
`HookRuntime.close()` 最多等待后台 Hook 3 秒并取消剩余任务，然后关闭 Runner。
AgentLoop 通过 `resource_manager` 关闭 MCP：未完成的后台启动先取消，READY 连接再正常
退出，最后关闭 HTTP Client、stdio 子进程和 Provider。

### 面试表述

YCode 通过 `pyproject.toml` 注册 CLI 入口。`cli.main()` 负责参数解析和启动 asyncio
事件循环，`run_app()` 是组合根：它按 `active` 延迟导入单个 Provider，在 Anthropic
路径装配命令、工具、权限、Hook 与 AgentLoop，然后让启用的 MCP 后台连接并立即进入
UI；OpenAI 保持 PlainChatRunner 且不加载 Hook。资源关闭从 Session 先经过 Hook 收尾，
再沿 Runner 传递，MCP 后台任务和 Provider 都会被释放。

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

`StreamEvent` 只描述一次模型请求。`StreamEnd` 表示本次 Provider 响应结束，并不表示整次
Agent 对话已经结束。它还携带当前请求的 `TokenUsage`。

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
| ToolApprovalRequested | 工具需要用户确认，携带权限决策和安全摘要 |
| ToolExecutionStarted | 工具开始执行 |
| ToolExecutionCompleted | 工具完成，携带完整执行记录 |
| ToolExecutionCancelled | 已启动工具被取消 |
| ModeChangedEvent | agent 与 plan-only 模式变化 |
| PermissionModeChangedEvent | 权限模式查询或变化 |
| PermissionGrantsClearedEvent | 会话授权已清除 |
| SessionRestoredEvent | 会话恢复成功，包含 ID、消息数和安全警告摘要 |
| ContextCompactedEvent | 自动或手动上下文压缩成功 |
| ContextCompactionFailedEvent | 上下文压缩失败及熔断状态 |
| ContextCompactionNotNeededEvent | 当前没有需要压缩的历史 |
| HookNoticeEvent | Hook 产生的终端通知，不进入消息或会话历史 |
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
AgentModelRequest
    ├── 稳定 system_prompt
    ├── 动态 supplements
    ├── 真实 ChatMessage
    └── ToolDefinition
    ↓
供应商请求格式

供应商 SDK Event
    ↓
StreamEvent
~~~

`ChatProvider.stream_chat(messages)` 是纯聊天接口。`AgentChatProvider` 在其基础上增加
`stream_agent(request)`，一次性接收供应商无关的 `AgentModelRequest`。这避免在
Provider 方法签名中继续堆叠 system、工具、记忆等可选参数。

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
- 当前待审批工具的唯一选择提交入口。
- 回合结束后 AgentTurnResult 的容器。

Agent 对外只有四种终止结果：

| AgentTermination | 含义 |
|---|---|
| COMPLETED | 正常得到最终回复 |
| LIMIT_REACHED | 达到最大轮数 |
| CANCELLED | 用户取消 |
| ERROR | Provider、组装或状态异常 |

只有事件流完全消费结束后，`turn.result` 才可见。

当前 `AgentTurnResult` 不再直接保存裸 `ChatMessage`，而是保存消息产生时刻：

~~~python
@dataclass(frozen=True, slots=True)
class TurnMessage:
    message: ChatMessage
    created_at: datetime  # 必须为 UTC

class AgentTurnResult:
    turn_messages: tuple[TurnMessage, ...]

    @property
    def messages(self) -> tuple[ChatMessage, ...]: ...
~~~

`messages` 是只读兼容视图。用户消息、每条完整 Assistant 消息和工具结果消息分别在
形成时记录 UTC 时间，后续由 `SessionManager` 原样写入 JSONL。

### 6. ChatSession

ChatSession 负责：

- 保存已经提交的对话历史。
- 保存当前 AgentMode。
- 保存可选的 PermissionSession。
- 保证同一时间只有一个活动 AgentTurn。
- 处理 `/plan`、`/agent` 和 `/permission`。
- 处理 `/compact` 和 `/resume <session-id>`。
- 触发 `session.start/end` 和手动压缩 Hook，并保存共享 HookRuntime。
- 协调 `SessionManager`、`ContextManager`、`MemoryStore` 和 `MemoryUpdater`。
- 累积本进程中新提交的 `SessionCommit`，只供退出记忆整理使用。
- 把终端审批选择转交给当前 AgentTurn。
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
COMPLETED：先持久化 result.turn_messages，再提交上下文和内存历史
其他状态：不提交本轮历史
    ↓
最后向 UI 转发终态事件
~~~

为什么终态事件要暂存？

如果 UI 先看到 FinalResponseEvent，而 JSONL 或历史还没有提交，上层会观察到“界面已
完成、事实来源仍未完成”的不一致。因此 Session 暂存终态事件，严格按
“JSONL 刷新 → ContextManager 提交 → 内存历史替换 → FinalResponseEvent”执行。

一次完整 Agent 回合可能提交：

~~~text
UserMessage
Assistant(ToolCall)
User(ToolResult)
Assistant(ToolCall)
User(ToolResult)
Assistant(Final Text)
~~~

Provider 错误、响应组装错误、达到上限、用户取消或调用方提前停止消费时，本轮临时
历史既不进入内存，也不写入 JSONL。已经完成的文件或命令副作用不会自动回滚。

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
    elif isinstance(event, ToolApprovalRequested):
        # 暂停普通 Ctrl+C 监听，进入三选一审批输入
        ...
    elif isinstance(event, HookNoticeEvent):
        console.print(f"hook: {event.message}")
    elif isinstance(event, FinalResponseEvent):
        await renderer.complete(event.message)
~~~

UI 的展示原则：

- Thinking 和过程文本按 Agent 轮次显示。
- 工具开始、成功、失败和取消显示安全摘要。
- 写文件内容和完整命令输出不会直接显示。
- 工具调用轮的文本是过程文本，不会混入最终 Markdown。
- HookNoticeEvent 只显示为终端通知，不会变成 ChatMessage 或提交到会话。
- 只有 FinalResponseEvent 携带的最后一轮消息进行 Markdown 渲染。
- 上限、错误和取消都会停止计时与 Rich Live，并恢复输入。

Anthropic 的 InputBox 右下角同时显示任务模式和权限模式。审批输入只有“拒绝、本次
允许、本会话允许”三个选择；进入审批前会暂停普通 Ctrl+C 监听，避免两个输入应用竞争
同一终端设备。启用命令运行时时，输入框左侧提示为 `/help for commands`，并使用
`CommandCompleter` 从注册中心读取公开名称和别名完成 Tab 补全。

普通输入框是自行构造的 prompt_toolkit `Application`。`load_key_bindings()` 中基础
`Ctrl+C` 绑定只会忽略按键，所以 YCode 还必须显式合并一个 `c-c` 绑定并让应用以
`KeyboardInterrupt` 结束。TerminalUI 捕获后进入正常退出管线。三种场景的语义不同：

- 空闲输入框 `Ctrl+C`：直接退出 YCode，并执行记忆整理和资源关闭。
- Agent 回合、工具执行或手动压缩期间 `Ctrl+C`：取消当前活动操作，回到输入框。
- 工具审批期间 `Ctrl+C`：取消审批 Future 和整个当前 Agent 回合。

OpenAI 没有装配权限会话和命令运行时，保持兼容输入路径。

### 8. 职责边界

| 组件 | 职责 |
|---|---|
| AnthropicProvider / OpenAIProvider | 单次模型请求和供应商协议转换 |
| PromptBuilder / PromptBundle | 加载、校验和稳定排列内置提示词章节 |
| PromptRuntimeContext | 管理模式提醒和会话级动态补充 |
| ProjectContextLoader | 启动时展开 `YCODE.md` 并组合有效记忆索引快照 |
| EnvironmentCollector | 采集请求级环境与 Git 摘要 |
| ResponseAssembler | 把单次 StreamEvent 流组装成 Assistant 消息 |
| AgentLoop | 多轮判断、工具执行、结果回填和 Agent 终止 |
| HookRuntime | 按事件与条件分发规则，维护 executed、Reminder、权限汇总和后台任务 |
| HookContextFactory | 构造供应商无关、可供匹配与模板读取的 Hook 事件上下文 |
| PermissionEngine | 分离工具硬预检与普通策略，供 Hook 插入两阶段之间 |
| PermissionSession | 当前权限模式和内存中的本会话授权 |
| PowerShellSafetyChecker | 使用 PowerShell AST 识别已定义的危险命令 |
| PlainChatRunner | 把单次纯聊天包装成 AgentTurn |
| ToolRegistry | 登记工具并提供当前可用定义 |
| ToolExecutor | 执行前再次查找、校验访问分类和参数，处理超时及工具错误 |
| ToolScheduler | 合并预先拒绝结果，并保持读取并发和非读取屏障 |
| ChatSession | 历史、任务模式、权限模式、活动回合和事务提交 |
| SessionManager | 独占 `.ycode/sessions/`，负责 JSONL CRUD、提交、重放与修复 |
| ContextManager | Token 预检、会话摘要、恢复候选和检查点状态 |
| MemoryStore | 校验、规范化并安全应用 `.ycode/memory/` 内容 |
| MemoryUpdater | 退出时发起隔离请求并解析结构化记忆操作 |
| CommandRegistry | 保存不可变命令元数据，统一处理名称、别名、冲突、帮助和补全来源 |
| CommandDispatcher | 在普通对话前解析和安全分发斜杠命令 |
| UIController | 隔离命令处理器与 Rich、prompt_toolkit 等具体 UI 框架 |
| TerminalUI | 消费 AgentEvent 并控制输入、取消与展示 |
| Renderer | 多轮内容、工具摘要、计时和最终 Markdown |

可以记成：

~~~text
Provider 负责翻译一次响应
Prompt System 负责决定发什么系统上下文
Assembler 负责拼装一次响应
AgentLoop 负责多轮行动
HookRuntime 负责生命周期扩展、临时提醒和工具权限干预
Tool 系统负责执行本地能力
SessionManager 负责磁盘会话事实
ChatSession 负责跨组件事务和活动状态
CommandRuntime 负责斜杠命令的目录、解析和分发
TerminalUI 负责交互与展示
~~~

### 面试表述

YCode 使用两层供应商无关事件隔离职责：Provider 将 SDK SSE 转换为单次请求级
StreamEvent，AgentLoop 或 PlainChatRunner 再转换为整轮对话级 AgentEvent。
ResponseAssembler 负责单次响应完整性，AgentLoop 负责 ReAct 循环、Hook 节点和工具回填，
ChatSession 只在 COMPLETED 后事务式提交整轮历史，TerminalUI 完全不感知 StreamEnd；
HookNoticeEvent 也只属于过程展示，不属于对话事实。

## 提示词系统

提示词系统的核心目标是把“长期稳定、适合缓存的内容”和“每轮可能变化的上下文”
分开，同时保持动态内容的 system 语义。

### 1. 请求内容分层

`AgentModelRequest` 明确区分四个字段，当前用户输入已经包含在真实消息中：

| 内容 | 字段 | 生命周期 |
|---|---|---|
| 内置全局指令 | `system_prompt` | 应用启动后稳定 |
| 环境、模式、工具状态、项目指令、项目记忆、会话摘要 | `supplements` | 请求级或会话级 |
| 对话历史和当前输入 | `messages` | 由 Session 事务管理 |
| 当前允许的工具定义 | `tools` | 随模式和工具状态变化 |

动态补充不进入 `ChatMessage` 模型，也不会提交到 `ChatSession.history`。它们只在
Provider 序列化时成为 system message；因此既不会伪装成用户请求，也不会在历史中
无限累积。

### 2. 稳定内置提示词

六个 Markdown 章节位于 `ycode/prompt/resources/`：

~~~text
identity → behavior → tool-use → coding → safety → output
   100        200         300        400       500      600
~~~

`PromptBuilder` 使用包资源 API 加载正文，`PromptBundle` 再按 `(priority, id)` 排序。
章节 ID 必须是小写 kebab-case，优先级必须是非负整数，正文不能为空，重复 ID 会在
启动时失败。相同资源重复构建会得到相同的 `content_blocks`。

这些 Markdown 文件通过 `pyproject.toml` 的 package-data 进入 wheel，避免源码运行
正常、安装后却找不到提示词资源。

### 3. 动态补充和生命周期

`SystemSupplement` 包含：

- `kind`：environment、task mode、tool state、project instructions、project memory、
  conversation memory、reminder、`system-reminder`、Skill 或 tool catalog 等。
- `scope`：`request` 或 `session`。
- `content`：正文。

发送前会渲染固定边界标签，例如：

~~~text
<environment_context>
Workspace: D:\project
Operating system: Windows
...
</environment_context>
~~~

`PromptRuntimeContext` 按补充类型保存会话级内容；同一类型的新内容会替换旧内容。
请求级内容只参加当前 `begin_turn()`。首次用户任务或模式变化后的任务使用完整模式
指令，其余任务只发送精简提醒，不维护“第几轮”的计数。恢复会话时调用
`reset_mode()`，保证下一次请求重新得到完整模式说明。

会话级补充采用显式顺序：项目指令、项目记忆、其他会话状态；如果存在上下文摘要，
`ContextManager` 会把 `<conversation_memory>` 放在它们之前，并把“摘要不是代码事实”
的边界提醒放在最后。三类长期信息不会再共用模糊的 `<memory>` 标签。

`EnvironmentCollector` 每个用户任务采集一次工作区、操作系统、Shell、本地时间与时区。
Git 通过两秒超时的只读 `git status --porcelain=v1 --branch` 获取分支和 staged、
modified、untracked 数量。Git 缺失、超时、非仓库或解析失败时只省略 Git 字段，不阻止
对话，也不会注入环境变量、diff 或完整文件列表。

### 4. AgentLoop 的注入时机

应用启动时只构建一次稳定 `PromptBundle`。每个普通用户任务开始时，`AgentLoop`：

~~~text
采集一次 EnvironmentSnapshot
    ↓
PromptRuntimeContext.begin_turn(mode, environment)
    ↓
得到带标签的 supplements
    ↓
创建 AgentModelRequest
~~~

同一用户任务中的多次工具轮次只扩展 `working_messages`，稳定提示词和任务开始时确定的
补充继续复用；但每次请求发送前仍会触发 `message.before_send`。Hook 产生的请求级
`<system-reminder>` 会在这里追加到当前请求，随后由 `take_reminders()` 原子取出并清空，
所以不会重复注入，也不会写入 ChatMessage 或会话历史。

### 5. Anthropic 缓存与兼容降级

AnthropicProvider 把稳定章节序列化为顶层 `system` 文本块，并在最后一个稳定块上设置：

~~~json
{"cache_control": {"type": "ephemeral", "ttl": "5m"}}
~~~

动态补充优先追加为 `messages` 中的 `role: system`。如果服务在响应流建立前明确返回
“不支持 system message”的 400 错误，Provider 会把动态补充移到顶层 `system`，只重试
一次，并在当前 Provider 生命周期内记住降级结果。降级不会把补充伪装成 user message，
也不依赖硬编码模型名单。

这里的职责边界是：

~~~text
Prompt System：决定发送什么内容和生命周期
AgentLoop：决定每个用户任务何时生成、工具轮次何时复用
AnthropicProvider：只做协议序列化、兼容重试和响应解析
~~~

### 6. TokenUsage

`StreamEnd.usage` 使用供应商无关的 `TokenUsage`：

| 字段 | 含义 |
|---|---|
| `input_tokens` | 普通输入量 |
| `output_tokens` | 输出量 |
| `cache_creation_input_tokens` | 写入缓存的输入量 |
| `cache_read_input_tokens` | 从缓存读取的输入量 |

缺失或无效字段按零或已有值处理。`ResponseAssembler` 保存单次请求用量，
`AgentLoop` 使用加法汇总一个用户任务内所有工具轮次，最终写入
`AgentTurnResult.usage`。当前 TUI 默认不展示这些统计。

### 面试表述

YCode 用 `PromptBundle` 保存确定性排序的稳定指令，用 `SystemSupplement` 表达带请求级
或会话级生命周期的动态系统上下文，再通过 `AgentModelRequest` 与真实历史和工具定义
分离。AgentLoop 每个用户任务生成固定动态上下文，同时允许 Hook 在单次请求边界追加并
消费 `<system-reminder>`；AnthropicProvider 只负责缓存断点、system message 兼容降级和
usage 解析，因此提示词策略没有泄漏到供应商协议层。

## 项目上下文、会话持久化与项目记忆

这一组功能只在 Anthropic 路径装配，但模型、JSONL 和记忆数据结构本身不依赖具体
Provider。最重要的理解方式是先区分三个概念：

| 概念 | 保存位置 | 作用 | 是否直接进入普通历史 |
|---|---|---|---|
| 会话历史 | `.ycode/sessions/*.jsonl` | 恢复用户、Assistant、Thinking 和工具链 | 恢复后成为 `ChatSession.history` |
| 会话压缩摘要 | JSONL 的 `context_checkpoint` | 控制单个会话的 Token 大小 | 作为 `<conversation_memory>` 系统补充 |
| 项目记忆 | `.ycode/memory/` | 跨会话保存偏好、纠正、项目知识和参考资料 | 只注入 `MEMORY.md` 索引 |

项目指令 `YCODE.md` 是第四种独立来源：它是用户人工维护的项目规则，不属于自动记忆，
也不属于会话历史。

### 1. 项目根和启动快照

所有新组件统一使用 `config.project_root`，不再把进程启动目录当作工作区：

~~~text
活动配置文件
    ↓ resolve_project_root()
config.project_root
    ├── 工具 WorkspacePathResolver
    ├── YCODE.md
    ├── .ycode/sessions/
    ├── .ycode/memory/
    ├── .ycode/context/
    └── .ycode/security.yaml
~~~

`ProjectContextLoader.load()` 在应用启动时生成一次 `ProjectContextSnapshot`：

~~~text
YCODE.md
    → 递归展开独占行 @include
    → PROJECT_INSTRUCTIONS

.ycode/memory/MEMORY.md
    → MemoryStore 校验索引和主题 frontmatter
    → 重新生成只含有效条目的索引
    → PROJECT_MEMORY
~~~

两者都以 `SESSION` 生命周期写入 `PromptRuntimeContext`，因此每次普通请求都会注入，
但不会写入 `ChatMessage`、JSONL 或上下文摘要。运行期间修改文件不会热加载；下一次
启动才会形成新快照。

`@include` 的规则是：

- 指令必须独占一行，路径相对当前包含文件解析。
- 根 `YCODE.md` 是第 0 层，最多到第 5 层。
- 使用活动递归栈检测循环；同一文件在不同分支重复引用仍允许。
- 缺失文件、绝对路径、`..` 逃逸和符号链接逃逸都会抛出 `ConfigError`，阻止启动。
- 记忆索引和主题文件不支持 `@include`。

这里的失败策略有意不同：项目指令是明确规则，损坏时停止启动；项目记忆是可选辅助
信息，损坏条目只形成 `ProjectContextWarning`，其余有效条目继续工作。

### 2. 会话 ID 与延迟创建

`SessionManager` 是 `.ycode/sessions/` 的唯一读写者。新启动调用 `begin_new()` 后并不
立即创建空文件，第一次成功回合提交时才确定 ID：

~~~text
本地时间 YYYYMMDD-HHmmss
    +
第一条用户消息的小标题
    ↓
20260803-190810-read the project
~~~

标题保留中文和普通空格，连续空白折叠为一个空格，删除 Windows 文件名非法字符，
最多保留 32 个字符；同秒同标题使用 `-2`、`-3` 后缀。因为 ID 本身可以包含空格，
`/resume` 不能用普通 `split()` 取参数，而是把命令名之后的整段文本视为 ID。

列出会话只扫描 `*.jsonl` 文件名并解析时间，不读取正文，因此会话数量增长不会让列表
操作退化成全量历史扫描。删除只接受通过格式校验的精确 ID，并拒绝删除当前活动会话。

### 3. JSONL 的三类记录

每行是独立、版本化的单行 JSON：

| `type` | 模型 | 关键字段 |
|---|---|---|
| `message` | `SessionMessageRecord` | session ID、turn ID、UTC 时间、完整 `ChatMessage` |
| `turn_commit` | `TurnCommitRecord` | turn ID、UTC 时间、本轮消息数量 |
| `context_checkpoint` | `ContextCheckpointRecord` | 覆盖回合、摘要、压缩后保留历史 |

`session/codec.py` 显式编码 Text、Thinking、Redacted Thinking、Tool Use 和 Tool Result，
而不是序列化 Python 对象内部状态。这样文件格式能独立演进，也能在恢复时拒绝未知版本、
未知记录类型和多余字段。

一个工具回合的物理顺序类似：

~~~jsonl
{"type":"message","turn_id":"000001","message":{"role":"user",...}}
{"type":"message","turn_id":"000001","message":{"role":"assistant","content":[{"type":"tool_use",...}]}}
{"type":"message","turn_id":"000001","message":{"role":"user","content":[{"type":"tool_result",...}]}}
{"type":"message","turn_id":"000001","message":{"role":"assistant",...}}
{"type":"turn_commit","turn_id":"000001","message_count":4,...}
~~~

消息时间来自 `TurnMessage.created_at`，以 UTC ISO 8601 `Z` 格式保存；会话文件名使用
本地时间，二者用途不同。

### 4. 写前提交为什么是核心事务边界

正常 Agent 回合结束后，`ChatSession` 不会立刻修改历史：

~~~text
AgentTurnResult(COMPLETED)
    ↓
SessionManager.commit_turn(result.turn_messages)
    1. 追加所有 message
    2. 追加可选 context_checkpoint
    3. 最后追加 turn_commit
    4. flush
    ↓ 成功
ContextManager.commit(context_commit)
    ↓
替换 ChatSession.history
    ↓
向 TerminalUI 发出 FinalResponseEvent
~~~

写入异常时，`SessionManager` 尝试把本次追加截回起始偏移；活动 turn 编号不会推进，
`ChatSession` 不提交上下文或内存历史，而是返回 `session_storage_error`。这里的 JSONL
是事实来源，内存状态必须追随磁盘成功，而不是相反。

只执行 `flush()`，没有逐行 `fsync()`。保障范围是普通进程崩溃后最多损坏尾部并可修复，
不承诺断电、系统崩溃或磁盘故障下的强持久性。失败、取消和达到 Agent 轮数上限的回合
不会写入会话文件。

### 5. 恢复状态机与文件修复

`SessionManager.load(id)` 是有磁盘修复副作用、但不切换活动会话的预检：

~~~text
逐行读取并记录字节结束偏移
    ↓
JSON 无法解析：警告并跳过
    ↓
结构化记录：校验 session ID、递增 turn ID、message_count
    ↓
校验消息角色和 tool_use / tool_result ID 集合
    ↓
只有合法 turn_commit 才推进 safe_offset
    ↓
发现半回合、错配工具链或非法顺序
    → truncate(safe_offset)
    → 返回 repaired_tail 警告
~~~

坏 JSON 行可以跳过，是因为后续记录可能仍组成合法回合；结构错误不能跳过，是因为那会
把未配对工具调用发送回模型。恢复成功后可以继续追加，再次恢复不会重复报告已截断尾部。

`load_latest()` 根据文件名选择最新会话，`--continue` 在 UI 启动前调用它；
`/resume <id>` 在空闲状态调用 `load(id)`。恢复失败只返回安全错误，当前活动 ID、历史、
模式和权限保持不变。

### 6. 无副作用上下文恢复与检查点

会话加载成功不代表它一定能装进当前模型窗口。`ContextManager.prepare_restore()` 接收
历史和已有摘要，但不修改当前 ContextManager：

~~~text
估算 history + conversation memory
    ├── 未超阈值 → 返回普通 RestoreContextResult
    └── 超阈值 → 最多调用一次 ConversationCompactor
                    ↓
                 返回带摘要的候选
~~~

如果候选需要新检查点，`ChatSession.restore()` 先把
`memory + retained_history + covered_turn_id` 追加到目标 JSONL，再依次激活
`SessionManager`、`ContextManager` 和活动历史。压缩失败或检查点写入失败时不切换会话。
下一次恢复会直接使用最新有效检查点和其后的回合，不再压缩已覆盖的原始历史。

手动 `/compact` 使用同样的候选模式：先 `prepare_manual_compaction()`，检查点落盘后才
`activate_compaction()`。自动压缩则通过 `ContextCommit.checkpoint_required` 告知
`ChatSession`，与当前回合一起写入检查点。

会话切换成功后还会：

- 重置任务模式为 `agent`。
- 清空 `PermissionSession` 的临时授权。
- 清空上下文失败计数和自动摘要熔断。
- 调用 `PromptRuntimeContext.reset_mode()`，下一轮重新注入完整模式说明。
- 保留应用级 MCP 连接、工具 Registry、项目指令和记忆索引启动快照。

最后活跃时间超过 24 小时时，AgentLoop 只为下一次普通请求排队一个 `REMINDER`，包含
本地格式的上次时间、当前时间和天数。斜杠命令不会消费它，下一次普通请求取走后不会
再次出现。内部检查点时间不算用户活跃时间。

### 7. 项目记忆目录和四种类型

记忆目录结构：

~~~text
.ycode/memory/
├── MEMORY.md
├── user-*.md
├── feedback-*.md
├── project-*.md
└── reference-*.md
~~~

四类 `MemoryType`：

| 类型 | 文件前缀 | 内容示例 |
|---|---|---|
| `user_preference` | `user-` | 用户稳定的编码或沟通偏好 |
| `correction_feedback` | `feedback-` | 用户对错误行为的纠正 |
| `project_knowledge` | `project-` | 项目约定、技术选型和领域知识 |
| `reference` | `reference-` | 可复用资料和外部参考入口 |

主题文件必须是单层、小写 kebab 风格 Markdown，并使用固定 frontmatter：

~~~markdown
---
name: 偏好 any 语法
description: 用户要求使用 any
type: user_preference
---
使用 any 替代 interface{}。
~~~

索引只包含指针：

~~~markdown
- [偏好 any 语法](user-prefers-any.md) — 用户要求使用 any
~~~

`MemoryStore.load()` 会同时校验索引格式、单层相对路径、真实路径边界、类型与文件前缀、
frontmatter 精确字段以及 name/description 一致性。它最终从有效 `MemoryEntry` 重新
生成规范化索引，因此任意坏行、缺失文件或元数据不匹配都不会进入模型上下文。普通请求
不包含主题正文；需要细节时模型使用 `read_file` 按索引读取。

### 8. 退出时的隔离记忆整理

`ChatSession` 只记录本进程中新成功提交的 `SessionCommit`，恢复进来的旧历史不会重复
进入整理输入。用户在同一进程中切换多个会话后，每个新回合仍保留自己的 session ID、
turn ID 和消息 UTC 时间。

正常 `/exit`、`/quit`、EOF 或空闲 Ctrl+C 时，`TerminalUI` 调用幂等的
`finalize_memory()`：

~~~text
没有新提交
    → SKIPPED，不调用模型

有新提交
    → 重新 MemoryStore.load()，尊重运行期间人工修改
    → MemoryUpdater.analyze(current, new_commits)
    → 专用 system prompt
    → tools=()、thinking_enabled=False
    → 最多等待 30 秒
    → 只接受一个 JSON object
    → MemoryStore.apply(plan)
~~~

模型可返回 `create`、`update`、`delete` 或空操作。合并不增加第五种动作，而是“更新保留
条目 + 删除重复条目”。重复判断完全交给模型，程序不做向量、Embedding、关键词或
相似度算法。

程序仍保持硬边界：创建不能覆盖已有或未索引文件；同路径更新只能修改正文，不能改变
name、description 或 type；删除只能针对当前有效索引中的条目。若要改元数据或类型，
模型必须创建替代文件再删除旧文件。

应用变更时先完整校验最终集合并准备所有主题临时文件，再替换主题文件、原子替换
`MEMORY.md`，最后删除不再被索引引用的旧文件。即使中途失败，旧索引仍只会指向完整
文件；可能遗留的未索引文件不会进入启动快照。

整理超时、模型流异常、Thinking/工具事件、额外文本、非法 JSON 或写入失败都转换为
`TIMEOUT` 或 `FAILED` 报告，不影响已经完成的 JSONL 会话提交。强制终止、进程崩溃和
断电不会触发这条正常退出管线。

### ⚠️ 真实故障复盘：路径契约只写在代码里

> [!IMPORTANT]
> 如果结构化输出需要满足本地领域模型，不能只在解析器中实现校验；同一份
> 契约必须明确告诉模型。否则模型会返回语义正确、但无法被程序接受的数据。

故障现象是：用户明确要求“平时用中文与我交流，记住了”，`/exit` 后却显示
“项目记忆整理失败”，且 `.ycode/memory/` 没有生成主题文件。

最初只用构造的最小对话调用 DeepSeek，模型返回：

~~~json
{"operations":[]}
~~~

这只能证明空操作路径正常，没有走到真正出错的 `create` 分支。改用用户明确授权的
实际相关回合后，捕获到原始响应：

~~~json
{
  "operations": [
    {
      "action": "create",
      "path": "user_language",
      "entry": {
        "path": "user_language",
        "name": "交流语言偏好",
        "description": "用户要求使用中文交流",
        "type": "user_preference",
        "body": "用户明确要求平时使用中文交流。所有后续回复均应使用中文。"
      }
    }
  ]
}
~~~

问题由此确定：`MemoryEntry` 要求路径是单层、小写 kebab-case Markdown 文件，并按
记忆类型使用 `user-`、`feedback-`、`project-` 或 `reference-` 前缀。
`user_language` 不带类型前缀和 `.md` 后缀，因此 `parse_memory_update()` 最终抛出
`记忆操作字段无效`。`ChatSession._finalize_memory()` 当时把所有普通异常统一转换成
“项目记忆整理失败”，所以终端没有暴露路径校验这个真正原因。

根因不是校验过严，而是更新 Prompt 只给了 `"path":"..."` 占位符，没有把代码中的
路径契约告诉模型。修复后的 Prompt 明确规定：

- 文件名必须是单层 ASCII 小写 kebab-case Markdown。
- 四类记忆必须使用各自的固定前缀。
- `create` 和 `update` 的 `path` 必须与 `entry.path` 完全一致。
- 明确把 `user_language` 和目录路径列为反例。

使用同一回合重新请求后，DeepSeek 返回：

~~~json
{"operations":[{"action":"create","path":"user-chinese-language.md","entry":{"path":"user-chinese-language.md","name":"中文交流","description":"用户偏好使用中文交流","type":"user_preference","body":"用户明确要求使用中文交流。所有后续回复应使用中文。"}}]}
~~~

该响应以 `end_turn` 正常结束，并成功解析为一条 `create` 操作。修复后记忆相关
回归测试为 `27 passed`，格式、静态、编译和 `git diff --check` 也均通过。

> [!WARNING]
> “真实 API 可以返回合法 JSON”不等于“会返回合法领域对象”。调试结构化模型
> 输出时，必须触发实际失败的操作分支，保留原始响应，再逐层区分“流异常、
> JSON 语法、结构契约、领域校验和文件应用”。

### 9. 关键职责边界

~~~text
ProjectContextLoader：只负责启动快照和指令展开
SessionManager：只负责磁盘会话，不知道 Provider、权限、工具或 TUI
ContextManager：只负责 Token、摘要和恢复候选
MemoryUpdater：只让模型产生结构化操作计划
MemoryStore：只校验和应用本地记忆
ChatSession：编排写前提交、恢复切换、运行期新增回合和退出整理
TerminalUI：决定哪些退出属于正常退出并展示结果
~~~

这种拆分的核心收益不是类更多，而是失败可以局部隔离：指令错误阻止启动，记忆读取错误
只警告，会话写入错误回滚当前回合，恢复失败保留原活动会话，退出整理失败仍正常关闭。

### 面试表述

YCode 把 JSONL 作为会话事实来源，用最后写入的 `turn_commit` 建立回合原子边界；恢复时
通过字节偏移状态机跳过坏行并截断不完整工具链。ContextManager 采用无副作用候选完成
超限恢复，检查点落盘后才切换活动状态。项目记忆只向普通请求注入经过校验的索引，退出
时再用无工具、关闭 Thinking 的隔离请求分析本进程新增回合，由 MemoryStore 在路径和
文件层执行最终安全约束。

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
request = self._request(
    model_request,
    native_supplements=native_supplements,
)
stream = await self.client.messages.create(**request)
~~~

`_request()` 负责把 `AgentModelRequest` 转成 Anthropic 请求结构。`system` 和 `tools`
只在非空时加入请求。`await create()` 等待请求与响应流建立，不等待完整回答；随后使用
`async for` 逐个读取 SDK Event。原生 system message 的兼容降级也只发生在这一步，
不会在已经开始读取响应后重试。

### 2. Anthropic 原始事件映射

Provider 在单次请求内维护私有内容块状态，并按 index 关联事件。

| Anthropic 原始事件 | Provider 行为 | 公共事件 |
|---|---|---|
| message_start | 标记消息开始并读取输入、缓存用量 | 无 |
| text_delta | 读取文本 | TextDelta |
| thinking_delta | 累计并输出 Thinking | ThinkingDelta |
| signature_delta | 只在 Provider 内累计 | 无 |
| input_json_delta | 累计工具参数碎片 | ToolCallDelta |
| thinking block stop | 构造完整 ThinkingBlock | ThinkingComplete |
| tool block start | 保存 ID 和名称 | ToolCallStart |
| tool block stop | 解析完整参数 | ToolCallComplete |
| message_delta | 保存停止原因并更新输出用量 | 无 |
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
- READ、WRITE 或 UNKNOWN 分类。
- 统一的 `ToolArguments` 参数适配器。
- 适配器提供的冻结 JSON Schema。
- 是否延迟向模型暴露完整 Schema。
- 超时时使用的稳定错误码。

`PydanticToolArguments` 服务内建工具，`JsonSchemaToolArguments` 服务 MCP 远端工具。
两者都同时提供“发送给模型的 Schema”和“执行前运行时校验”，因此
Registry、Executor 和权限层不需要区分参数是来自 Pydantic 还是远端 JSON Schema。

### 2. 基础内建工具与 Anthropic Skill 工具

| 工具 | 分类 | 用途 |
|---|---|---|
| read_file | READ | 分页读取工作区 UTF-8 文本 |
| glob | READ | 按 POSIX Glob 查找文件 |
| grep | READ | 使用 Python 正则逐行搜索内容 |
| write_file | WRITE | 创建或显式覆盖文本文件 |
| edit_file | WRITE | 使用原文唯一匹配替换文件内容 |
| run_command | WRITE | 在工作区目录中执行 PowerShell |
| load_skill | READ | 按需加载并运行项目 Skill |
| install_skill | WRITE | 从受支持的公开 HTTPS 来源安装单个项目 Skill |

前六个基础内建工具始终注册；`load_skill` 和 `install_skill` 只在 Anthropic 路径装配。
启用 MCP 时还会按需注册 `tool_search`。这些本地工具都有可靠分类。MCP Server 的安全
annotations 不被信任，所有
`MCPToolWrapper` 固定使用 `UNKNOWN`：它在所有权限模式下默认询问，并按
非读取工具串行调度。

### 3. Registry、Executor 与 Scheduler

三者分工：

~~~text
ToolRegistry
    登记工具、拒绝重名、按名称查找、导出当前允许的 ToolDefinition
        ↓
ToolExecutor
    查找 → allowed_access 复核 → ToolArguments 参数校验 → 超时 → 调用工具
        ↓
ToolScheduler
    合并 PermissionEngine 的拒绝结果，按访问分类安排允许的调用
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

YCode 的工具通过 Protocol 结构化满足统一接口，ToolDefinition 使用统一的
ToolArguments 适配 Pydantic 模型或 JSON Schema。Registry 负责登记和可见性过滤，
PermissionEngine 在调度前集中决定能否执行，
Scheduler 负责连续读取并发及非读取屏障，Executor 负责最终参数校验、超时和错误归一
化。工具拒绝或失败都会形成结构化结果回灌模型，而不是让 Agent 直接崩溃。

## 工具权限安全系统

权限系统的核心不是把安全判断散落到六个工具里，而是在 `AgentLoop` 与
`ToolScheduler` 之间设置统一的执行前入口：

~~~text
模型返回 ToolCallBlock
    ↓
PermissionEngine.prepare() 规范化并执行硬检查
    ↓ 硬检查通过
tool.before_execute Hook
    ├── ALLOW / ASK / DENY：替代普通策略
    └── 无决定：PermissionEngine.evaluate_policy()
    ├── ALLOW：允许进入待执行批次
    ├── DENY：生成预计算错误结果
    └── ASK：发出 ToolApprovalRequested，严格等待用户
    ↓
整批权限判断完成
    ↓
ToolScheduler 合并拒绝结果并执行允许项
    ↓
按原始位置回填 ToolResultBlock
~~~

这个位置有两个好处：

- 工具获得允许之前不会进入 Scheduler 或 Executor，因此没有提前副作用。
- 拒绝仍然是普通工具结果，模型可以继续选择安全替代方案。

### 1. 配置、会话与三档模式

项目配置从当前工作区向上查找最近的 `.ycode/security.yaml`，启动时加载一次，并结合
ToolRegistry 校验工具名和参数名。当前项目开发配置是：

~~~yaml
mode: allow
rules: []
~~~

三档默认行为：

| 模式 | 未命中规则的可靠分类工具 |
|---|---|
| strict | READ、WRITE 都询问 |
| default | READ 允许，WRITE 询问 |
| allow | READ、WRITE 都允许，包括 run_command |

`UNKNOWN` 在三档模式下都询问。`/permission strict|default|allow` 只切换当前会话，
`/permission clear` 只清除本会话授权；它们不请求模型、不进入历史、也不修改配置。
永久规则只能手工写入项目配置。

### 2. 两阶段判定顺序与不可覆盖边界

`PermissionEngine.evaluate()` 仍是无 Hook 调用方的兼容入口，内部组合 `prepare()` 与
`evaluate_policy()`。主 AgentLoop 为了插入 Hook，显式使用两阶段接口：

~~~text
prepare()
工具查找与 ToolArguments 参数校验
    ↓
真实路径规范化、审批摘要和 session_key
    ↓
run_command 的 PowerShell 危险命令检查
    ↓
当前任务模式 allowed_access（包括 plan-only）
    ↓
硬拒绝？直接生成工具错误，不触发 before Hook
    ↓ 否
tool.before_execute Hook
    ├── DENY：直接拒绝并停止后续 before 规则
    ├── ASK：只允许本次审批，不写入会话授权
    ├── ALLOW：跳过普通权限策略
    └── 无决定：进入 evaluate_policy()
    ↓
evaluate_policy()
项目 DENY 规则
    ↓
plan-only MCP 白名单工具强制 ASK
    ↓
本会话允许
    ↓
项目 ALLOW / ASK 规则按声明顺序首次命中
    ↓
strict / default / allow 默认值
~~~

路径通过 `WorkspacePathResolver` 解析真实目标：工作区内的符号链接和 Junction 可以
使用，链接到工作区外、损坏或无法解析时拒绝。新写入目标检查真实父目录，所以不能靠
链接或 `..` 绕过沙箱。

`run_command` 先把原命令经 stdin 交给固定 PowerShell AST 解析脚本；解析进程只输出
命令、参数和管道结构，不执行待检查命令。大范围删除、磁盘破坏、远程下载后执行、
动态或编码执行、关机与启动破坏、高破坏性 Git、工作区外权限接管等类别会硬拒绝。
解析失败也拒绝。

危险命令、路径沙箱和 plan-only 访问边界不能被 Hook allow、allow 模式、项目 allow
规则或本会话允许覆盖。项目 DENY 属于普通策略，因此显式 Hook allow 可以替代它；这与
前面的硬拒绝边界不同。

### 3. 阻塞审批与会话授权

ASK 时，`AgentTurnStream` 只创建一个待审批 Future：

~~~text
AgentLoop yield ToolApprovalRequested
    ↓
TerminalUI 停止普通输入监听，显示工具、原因和安全摘要
    ↓
用户选择拒绝 / 本次允许 / 本会话允许
    ↓
ChatSession.submit_approval()
    ↓
AgentTurnStream 唤醒 AgentLoop
    ↓
才开始检查下一项
~~~

一批工具必须全部审完才进入 Scheduler。Ctrl+C 会清空待审批槽并取消整个回合，当前及
后续工具都不会启动。

本会话允许不是只按工具名匹配，而是使用工具特定的安全键。例如 `run_command` 使用
完整命令和真实 cwd，`write_file` 使用真实路径和 overwrite。读取分页参数、搜索结果
上限和文件正文不会进入授权键；关键参数变化后会重新询问。UNKNOWN 使用完整规范化
参数。授权只存内存，退出进程即消失。

### 4. 与提示词及 MCP 工具的边界

当前权限模式通过请求级 `<tool_state>` 动态补充发送给模型，同一用户任务的工具轮次
复用它。项目规则、危险命令细节和会话授权不会进入稳定 System Prompt、动态补充或
对话历史，因此切换权限模式不会破坏稳定提示词缓存。

安全层只依赖统一的 `ToolDefinition`、`ToolArguments` 和 `ToolAccess`，不理解
MCP 传输或 JSON-RPC。当前 MCP 工具已由 `MCPToolWrapper` 包装成普通 Tool，
固定标记为 `UNKNOWN` 并复用同一权限入口。

plan-only 默认既不列出 MCP 名称，也不暴露 Schema。只有
`.ycode/security.yaml` 中 `plan_only.allow_mcp_tools` 精确列出的 `mcp_*` 工具
才能被搜索和调用；该白名单不会把工具变成 READ，每次调用仍强制人工确认，
且不提供“本会话允许”。这里的“强制”是无 Hook 决定时的普通策略：显式 Hook allow
可以跳过这次 ASK 和项目规则，但不能让未进入白名单的 MCP 通过 `prepare()` 硬检查。

### 面试表述

YCode 在 AgentLoop 与 Scheduler 之间把权限拆成硬预检和普通策略：`prepare()` 先做参数、
真实路径、危险命令与任务模式检查，硬检查通过后才触发 before Hook；Hook 没有决定时再
由 `evaluate_policy()` 检查项目规则、会话授权和权限模式。ASK 通过 AgentEvent 和
AgentTurn 单一 Future 严格阻塞，拒绝作为结构化 ToolResult 回填。这样 Hook 可以调整
普通策略，同时不能越过真正的安全边界。

## Hook 系统：生命周期扩展、权限干预与临时提醒

Hook 系统位于 `ycode.hooks`，只在 Anthropic 主 Agent 路径装配。它从当前目录向上发现
最近的 `.ycode/hooks.yaml`，用“事件 + 可选条件 + 一个动作”描述项目自动化规则。
OpenAI PlainChatRunner 和隔离 Skill AgentLoop 都不会创建或触发 HookRuntime。

### 1. 配置加载与规则模型

配置顶层只能包含 `hooks` 列表。每条 `HookRule` 的主要字段是：

| 字段 | 含义 |
|---|---|
| `id` | 配置内唯一的小写 kebab-case 规则 ID |
| `enabled` | 固定启用标记，默认 `true` |
| `event` | 必填生命周期事件 |
| `conditions` | 可选的 `all` 或 `any` 条件组 |
| `action` | 一个 Shell、HTTP、Reminder 或 Agent 占位动作 |
| `permission` | 仅 `tool.before_execute` 可用的固定决定 |
| `once` | 是否只消费第一次匹配，默认 `false` |
| `async` | 是否后台执行，仅 Shell/HTTP 可用 |
| `timeout_seconds` | 动作超时，默认 30 秒 |

`HookRule` 本身冻结；可变状态放在 `RuntimeHookRule.executed`。加载时 `executed` 总是
`false`，不从 YAML 读取，也不持久化。事件和条件匹配后、动作启动前立即设为 `true`：

~~~text
enabled == false                 → 永远跳过，executed 不变
once == true 且 executed == true → 后续跳过
条件未命中                       → executed 不变
条件命中                         → 先 executed = true，再启动动作
动作失败、超时或取消             → 不恢复 executed
once == false                    → executed 为 true 也可再次触发
~~~

文件级 YAML/顶层错误会禁用整份配置；单条规则错误或重复 ID 只跳过该条，重复 ID 保留
第一条。`HookDiagnostic` 保存配置路径、规则序号、可识别 ID 和字段错误，启动时集中展示。
只有已启用的 Shell/HTTP 才产生一次外部操作风险提示。

当前仓库的 `.ycode/hooks.yaml` 是可加载示例：只有启动占位通知启用；高风险命令拦截、
工具失败 Reminder 和异步 Shell 示例都处于禁用状态。当前 `.ycode/config.example.yaml`
没有 Hook 节点。Hook 必须放在独立 `hooks.yaml`，不能直接成为 `config.yaml` 的顶层字段。

### 2. 事件、上下文与条件

第一期事件分四级并补充系统事件：

| 层级 | 事件 |
|---|---|
| 会话 | `session.start`、`session.end` |
| 用户任务 | `turn.start`、`turn.end` |
| 模型请求 | `message.before_send`、`message.after_receive` |
| 工具 | `tool.before_execute`、`tool.after_execute` |
| 系统 | `context.compacted`、`agent.error` |

`HookContextFactory` 为所有事件加入 `event.name`、`project.path` 和 `session.id`，再按事件
加入 `turn`、`message`、`tool`、`file`、`context` 或 `error`。before 工具事件使用
`PermissionEngine.prepare()` 得到的规范化参数；存在字符串 `path` 时额外提供
`file.path`。消息上下文只暴露角色和完整 TextBlock 文本，不暴露 Thinking 或供应商内部
事件。

条件通过点路径读取上下文，也支持数组索引，例如：

~~~yaml
conditions:
  all:
    tool.name: {exact: write_file}
    tool.arguments.path: {glob: "*.py"}
~~~

一条规则只能选择 `all` 或 `any`，不支持嵌套逻辑表达式。操作符为 `exact`、大小写敏感
Glob、正则搜索和包装单个正向匹配器的 `not`。字段不存在时始终不匹配，包括 `not`；
JSON null 与字段缺失不是同一个内部状态；不过当前 `exact` 不接受 null，其他正向操作符
也只处理字符串，所以两者在可配置条件中都不会形成匹配。

### 3. 模板与四类动作

模板只识别 `{{ field.path }}`，做一次文本替换。缺失字段变成空串；对象、数组、布尔值和
数字转成稳定 JSON 文本；替换值里即使再次出现 `{{ ... }}` 也不会二次解析。

四类动作的当前语义：

| 动作 | 当前行为 |
|---|---|
| `shell` | 使用平台默认 Shell、项目根 cwd 执行，捕获输出，可同步或异步 |
| `http` | 支持 GET/POST/PUT/PATCH/DELETE，模板化 URL、头和文本/JSON body |
| `reminder` | 生成请求级 `<system-reminder>`，只供模型下一次请求使用 |
| `agent` | 不启动子 Agent，只产生“子 Agent Hook 尚未实现：<rule-id>”终端通知 |

同步 `tool.before_execute` Shell 只要有非空 stdout，就会尝试把整段 stdout 解析为严格的
权限 JSON：

~~~json
{
  "permissionDecision": "deny",
  "permissionDecisionReason": "blocked by project hook"
}
~~~

额外字段、非法 JSON 或非零退出会让动态决定失败，再回退到本规则的固定 `permission`；
没有固定决定时等于不干预。普通 Shell/HTTP 输出只进入有界日志，不会自动显示在终端或
进入模型上下文。

当前没有通用 `print` 动作。若只是想打印任意模板内容，不能把 `shell: echo` 当成终端
输出；`agent` 也只有固定占位文字。轻量 `print` 动作已经记录在 Hook task 的后续待办，
尚未进入 Spec、实现或验收范围。

### 4. HookRuntime 分发与后台任务

`HookRuntime.dispatch()` 按 YAML 声明顺序遍历规则：先检查事件、enabled、once 和条件，
再消费 executed 并执行动作。同步动作串行完成；异步 Shell/HTTP 使用 asyncio Task 后台
运行，立即把控制权还给 Agent。动作异常、超时和取消统一转成 HookActionResult 并记录
有界日志，不触发新的 `agent.error`。

Reminder 进入运行时队列，由 `take_reminders()` 一次性取出并清空。Agent 占位消息进入
`HookDispatchResult.notices`：Agent Loop 中转换成 `HookNoticeEvent`，`session.start` 合并
到启动提示，`session.end` 在关闭阶段直接输出。

关闭顺序是：

~~~text
ChatSession.close()
    ↓ session.end
HookRuntime.close()
    ↓ 最多等待后台任务 3 秒
    ↓ 取消剩余任务并关闭自有 HTTP Client
AgentLoop.close()
    ↓ MCP / Provider 等资源
~~~

### 5. 权限决定的汇总与短路

单条规则的动态 Shell 决定优先于固定 `permission`。多条 before 规则按声明顺序执行，
总优先级固定为：

~~~text
deny > ask > allow > 无决定
~~~

遇到 deny 会立即返回，不再执行后续 before 规则；ask 不会被后续 allow 降级。AgentLoop
把 Hook 决定转换成现有 `PermissionDecision`，并设置 `allow_session=false`：

- allow：跳过项目规则、会话授权和权限模式。
- ask：最多发出一次现有审批，只允许本次，不产生会话授权。
- deny：生成结构化 permission_denied 工具结果，把原因反馈给模型。
- 无决定：继续 `evaluate_policy()` 的原有流程。

Hook 位于 `prepare()` 之后，所以无效参数、越界路径、危险 PowerShell 和 plan-only 访问
限制仍不可绕过。被硬检查、普通权限或 Hook 拒绝的调用没有真正进入 ToolExecutor，也就
不触发 `tool.after_execute`；真实工具返回成功或错误都会触发 after 事件。

### 6. Agent Loop 和会话生命周期

一个用户任务生成一个 turn ID，内部每次模型请求分别触发消息事件：

~~~text
turn.start
    ↓
每次请求：context.compacted（如果本轮自动压缩成功）
    ↓ message.before_send
    ↓ 消费 Reminder 并发送模型请求
    ↓ message.after_receive（完整消息组装后）
    ↓ 有工具时：prepare → tool.before_execute → 权限/审批
    ↓ 真正执行后：tool.after_execute
    ↓ 下一次模型请求
    ↓
turn.end(completed / cancelled / error / limit_reached)
~~~

导致任务失败时先触发 `agent.error`，再触发 `turn.end(status=error)`。流式 TextDelta、
ThinkingDelta 和单个内容块不会触发消息 Hook。自动压缩由 AgentLoop 触发
`context.compacted`；手动 `/compact` 在新检查点成功保存并激活后由 ChatSession 触发。

System Reminder 的关键边界是：

- before_send 产生的 Reminder 进入当前请求。
- after_receive 或 tool.after_execute 产生的 Reminder 进入下一次请求。
- Reminder 是 `SystemSupplement(SYSTEM_REMINDER)`，不是用户消息。
- 使用后队列清空，不进入 ChatSession.history 或 JSONL。
- `session.end` 配置 Reminder 会在加载阶段被判为非法规则。

### 7. 当前验证与边界

自动化已经覆盖配置降级、条件和模板、once/executed、Shell 权限输出、HTTP、Reminder、
运行时权限优先级、Agent Loop deny/ask/Reminder/状态集成，以及 Windows ConPTY 中真实的
ask、deny、模型调整和 session.end 收尾。

当前明确只做功能性实验：没有配置热加载、持久化执行状态、后台重试、生产级 Shell
沙箱、日志审计、压力/性能/长稳、复杂故障注入、多平台矩阵或真实付费 API 验证。

### 面试表述

YCode 用项目级 YAML 把 Hook 规则映射到会话、任务、模型请求、工具和压缩生命周期。
HookRuntime 维护会话内 once/executed 状态、同步或后台动作、一次性 System Reminder 和
权限汇总。工具 before Hook 被放在不可绕过的 `prepare()` 硬检查之后、普通权限策略之前，
因此既能让 Agent 根据 deny 原因自我调整，也不能越过参数、路径、危险命令和 plan-only
边界。Hook 错误只进入日志，不污染 Agent 错误事件或会话历史。

## 内置命令框架

内置命令不再散落在 `ChatSession` 和 `TerminalUI` 的条件分支里。Anthropic 启动时显式
调用 `build_command_runtime()`，得到共享同一份元数据的 `CommandRegistry` 和
`CommandDispatcher`。没有全局单例、装饰器自动注册或 YAML 动态命令。

### 1. 定义、注册和解析

每条 `CommandDefinition` 是不可变对象，包含：

~~~text
name / aliases / description / usage / argument_hint
kind / hidden / async handler
~~~

`CommandRegistry` 用一个大小写无关索引同时保存规范名称和别名。注册前先完成名称格式、
定义完整性、内部重复和已有索引冲突检查，全部通过后才一次性写入，因此失败不会留下
半注册状态。隐藏命令可以直接解析，但不会出现在帮助和补全中。

`CommandParser` 的规则保持简单：去除输入两端空白，以 `/` 开头才是命令；第一个空白前
转为小写命令名，后面的完整文本作为参数并保留原始大小写。`/` 是保留前缀，目前不支持
`//` 转义，也不解析引号、管道或重定向。

### 2. 分流与 UIController

用户回车后的主链路是：

~~~text
TerminalUI.run()
    ↓
CommandDispatcher.try_dispatch(text, controller)
    ├── 非斜杠输入 → False → send_user_message() → Agent
    ├── 未知命令 → 显示原输入和 /help 引导
    └── 已注册命令 → handler(invocation, UIController)
~~~

命令处理器只依赖 `UIController` Protocol。具体终端如何显示系统消息、切换模式、查询
MCP、压缩上下文、恢复会话或请求退出，都由 `TerminalUI` 适配到现有 `ChatSession`
能力。这样命令核心不导入 Rich 或 prompt_toolkit，也不会复制会话状态。

错误边界分三层：参数错误显示该命令用法；预期业务错误只显示安全摘要；未知异常统一
显示“命令执行失败”。`CancelledError` 不吞掉，继续交给现有取消管线。

### 3. 三类命令的实际含义

`CommandKind` 保留三类元数据：

- `LOCAL`：只读取或显示本地信息，例如 `/help`、`/mcp`。
- `STATE`：改变或维护会话状态，例如 `/plan`、`/compact`、`/resume`。
- `AI`：把预设提示词送进普通 Agent 对话。

分类主要用于表达和路由，不自动决定权限、是否访问模型或是否刷新状态。特别是
`/compact` 虽然内部调用模型生成摘要，仍属于状态命令；`/mcp` 只查询当前状态，不会
调用模型。可用项目 Skill 会动态注册为生产 `AI` 命令，提交 `/<skill-name>` 后进入
Skill 调用链。

AI 命令支持分离两份文本：

~~~text
display_text = 用户输入的原始 /command
model_text   = handler 展开的预设提示词
~~~

UI 展示 `display_text`，Agent 请求和成功提交的会话历史使用 `model_text`，不会把原始
命令再保存为第二条消息。

### 4. 当前命令、帮助与补全

静态生产命令由同一注册工厂定义，项目 Skill 命令由同一 Registry 动态维护：

| 命令 | 类型 | 作用 |
|---|---|---|
| `/help [command]` | LOCAL | 从注册元数据生成列表或详细帮助 |
| `/exit`、`/quit` | LOCAL | 进入统一正常退出与记忆整理 |
| `/plan` | STATE | 切换到 plan-only |
| `/agent` | STATE | 切回 agent |
| `/mcp` | LOCAL | 显示当前 MCP 状态快照 |
| `/compact` | STATE | 执行可取消的隔离上下文压缩 |
| `/permission [...]` | STATE | 查询、切换权限模式或清除临时授权 |
| `/resume <session-id>` | STATE | 原子恢复指定会话 |
| `/skills [show/deactivate/reload]` | LOCAL/STATE | 查看、停用或重新扫描项目 Skill |
| `/clear` | STATE | 建立空会话并清除历史、摘要、Skill 和临时授权 |
| `/<skill-name> [arguments]` | AI | 显式运行当前可用项目 Skill |

`/help`、解析、实际分发和 `CommandCompleter` 都读取同一个 Registry，避免多份手写命令
清单漂移。补全只在光标位于第一个命令词末尾时工作；单匹配直接替换，多匹配用
`CompletionsMenu` 展示，参数和隐藏命令不补全。

### 面试表述

YCode 用不可变命令定义和集中注册中心统一名称、别名、帮助与补全；Dispatcher 在普通
对话前完成斜杠命令分流，Handler 只依赖 UIController，因此业务命令不耦合终端框架。
命令分类是描述性元数据，不把“是否调用模型”误做成强制策略；显示文本和模型文本分离，
也为未来的 AI 预设命令保留了清晰事务边界。

## 启动性能与按需加载

启动优化包含两种不同的“延迟”，不要混淆：

1. Provider 延迟导入：`create_provider()` 根据已解析的 `active` 协议，在分支内部局部
   导入对应实现。只激活 Anthropic 时，不导入 OpenAI Provider 和 OpenAI SDK。
2. MCP 后台启动：应用不再等待 Server 握手和 `tools/list` 完成，先进入 UI；连接成功后
   注册的工具只对后续新 Agent 回合生效。

MCP 配置的 `enabled` 决定是否参与加载。禁用项不会解析秘密、创建 `McpConnection`、
启动子进程或建立 HTTP 连接，只在 `/mcp` 中保留 `disabled` 状态。应用仍只支持一个
`active` Provider；同时激活多个 Provider、自动故障切换和路由不在当前范围。

`McpManager.start_background()` 幂等创建并持有 `_start_task`，立即返回；兼容入口
`start()` 等待同一个任务。各启用 Server 仍在 TaskGroup 内并发连接，单个失败被隔离。
UI 首屏只显示“后台连接 N”，完成时不插入异步提示，用户通过 `/mcp` 主动查看
`starting → ready/unavailable`。

`startup_timeout_seconds` 省略时默认 5 秒，YAML 显式值优先。当前示例和项目配置显式
写出的 10 秒仍保持 10 秒，并用注释标明默认值。退出时 Manager 先取消未完成的启动
任务；处于 STARTING/RECONNECTING 的 Connection 再取消 owner task，因此不等待剩余
完整启动超时。

### 面试表述

YCode 通过协议分支局部导入消除未激活 SDK 的冷启动成本，并把 MCP 连接改成由 Manager
持有的后台生命周期任务。UI 不等待远端服务，状态用快照按需查询；退出时反向取消后台
任务和连接 owner，既改善首屏响应，也保留明确的资源所有权。

## MCP 客户端、连接与延迟工具加载

MCP 实现位于 `ycode.mcp`，目标不是另外建立一套 Agent 工具系统，而是把
远端 MCP 工具适配成现有 `Tool` 接口。适配完成后，模型请求、参数校验、
权限审批、调度、结果回填和取消都复用 YCode 原有链路。

### 1. 支持范围和整体链路

当前支持：

- 本地子进程 `stdio` 传输。
- 远端 `streamable_http` 传输，包括普通 JSON 和请求级 SSE 响应。
- MCP `2026-07-28` 与 `2025-11-25` 自动协商。
- 多 Server 并发后台启动、独立失败和状态汇总。
- 启动时工具发现、连接与目录复用。
- 任务级延迟 Schema 暴露。
- 远端调用前的本地参数校验和权限审批。
- 启动摘要和本地 `/mcp` 状态查询。

一条完整链路是：

~~~text
.ycode/config.yaml + .env
    ↓
load_config() / load_mcp_servers()
    ↓
McpManager 只为 enabled Server 创建 McpConnection
    ↓
start_background() 后 UI 立即可输入
    ↓
后台 TaskGroup 并发连接
    ↓
stdio_client 或 streamable_http_client
    ↓
Client(mode="auto") 完成协议握手
    ↓
tools/list 分页发现
    ↓
McpToolDescriptor → MCPToolWrapper → ToolRegistry
    ↓
首轮只暴露 mcp_* 名称
    ↓
tool_search 激活当前任务需要的工具
    ↓
下一模型轮次暴露完整 Schema
    ↓
PermissionEngine 审批
    ↓
ToolExecutor 校验参数
    ↓
tools/call → CallToolResult → ToolResultBlock
~~~

目前这条链路只装配到 Anthropic Agent。OpenAI 活动时使用 PlainChatRunner，不读取
MCP Server、不注册 ToolSearch，也不建立 MCP 连接。

### 2. YAML、`.env` 与敏感值

MCP Server 在 `.ycode/config.yaml` 顶层声明。stdio 示例：

~~~yaml
mcp_servers:
  - name: local_tools
    enabled: true
    transport: stdio
    command: python
    args: ["-m", "your_mcp_server"]
    env:
      API_TOKEN: ${MCP_API_TOKEN}
    startup_timeout_seconds: 10  # 默认 5 秒；此处显式覆盖
    tool_timeout_seconds: 60
~~~

Streamable HTTP 示例：

~~~yaml
mcp_servers:
  - name: remote_tools
    enabled: true
    transport: streamable_http
    url: https://mcp.example.com/mcp
    headers:
      Authorization: Bearer ${MCP_API_TOKEN}
    startup_timeout_seconds: 10  # 默认 5 秒；此处显式覆盖
    tool_timeout_seconds: 60
~~~

对应 `.env`：

~~~dotenv
ANTHROPIC_API_KEY=replace-with-real-key
MCP_API_TOKEN=replace-with-real-token
~~~

`load_project_dotenv()` 只读项目根目录的 `.env`，不会将其写入 `os.environ`，也不会
递归展开 `.env` 内部引用。`EnvironmentResolver` 的优先级是：

~~~text
系统环境变量 > 项目 .env
~~~

`${VARIABLE}` 可以用于活动 Anthropic Provider 的 API Key、stdio 的显式 `env` 值和
HTTP `headers` 值。只有显式写在 Server `env` 里的变量才会传给子进程，不会把
整份 `.env` 无条件传播给外部程序。

`enabled` 默认为 `true`。显式设为 `false` 时，该条目不解析秘密、不连接、
不发现也不注册工具，但会在 `/mcp` 中显示为 `disabled`。修改开关后必须重启
YCode，当前不支持热加载。`startup_timeout_seconds` 省略时为 5 秒，显式配置值优先。

配置错误采用两层处理：

- 顶层 YAML、`.env` 或 `mcp_servers` 结构损坏：阻止应用启动。
- 单个 Server 字段错误、重名或缺少环境变量：记录 `McpConfigIssue`，其他 Server
  继续启动。

API Key、stdio env 和 HTTP Header 值使用 `SecretStr` 保存，同时登记进
`SecretRedactor`。远端结果、结构化元信息和 stdio stderr 在输出边界会再做脱敏。

### 3. 两种传输与协议自动兼容

`McpTransportFactory` 根据 Pydantic 鉴别联合配置选择 transport：

~~~python
if isinstance(config, StdioMcpServerConfig):
    parameters = StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env=expanded_env,
    )
    return stdio_client(parameters, errlog=redacting_stderr)

return streamable_http_client(
    config.url,
    http_client=cached_http_client,
)
~~~

stdio 的 stdout 只是 MCP 协议通道，stderr 由有界的 `RedactingStderrSink` 持续排空，
避免子进程因 stderr 管道写满而阻塞。Streamable HTTP 使用缓存的
`httpx2.AsyncClient`，可配置 Header，且区分 connect/read/write/pool 超时。

两种传输之上都进入官方 SDK：

~~~python
client = await stack.enter_async_context(
    Client(transport_factory.create(), mode="auto")
)
~~~

`mode="auto"` 表示：

~~~text
先使用 2026-07-28 server/discover 探测
    ├── 有共同新版本：采用协商结果
    └── Server 是旧版：回退到 2025-11-25 initialize 握手
~~~

用户不需要在 YAML 里配置协议版本。协商完成后的 `protocol_version` 记录在
`McpDiscoveryResult`。

YCode 不自行手写 JSON-RPC 读取循环。官方 SDK 负责生成 JSON-RPC 2.0 `id`、将
等待中请求登记到 pending 映射，并在响应到达时按 `id` 唤醒正确的等待者。
因此并发请求即使乱序回包也不会串包，迟到、重复和未知 ID 也由协议层隔离。

远端 Streamable HTTP Server 可以直接通过 URL 连接，例如 Context7 不需要 `npx`。
`npx` 只是某些本地 stdio Server 的启动方式，不是 MCP 协议要求。

### 4. 启动发现、名称映射与 Registry

`McpManager` 为每个启用的 Server 建立独立 `McpConnection`。`run_app()` 调用
`start_background()` 后不等待，UI 首屏先出现；Manager 自己持有的启动任务再使用
`asyncio.TaskGroup` 并发连接，因此多个 Server 的超时不会简单累加。单个 Server
握手或发现失败时标记为 `unavailable`，不影响输入、内建工具和其他 Server。

连接成功后，`McpConnection._discover()` 循环调用分页 `tools/list`：

~~~python
while True:
    page = await client.list_tools(cursor=cursor)
    remote_tools.extend(page.tools)
    cursor = page.next_cursor
    if cursor is None:
        break
~~~

重复 cursor 会被当作协议异常，避免无限分页。每个远端工具保留描述和
`inputSchema`，并编译成 `JsonSchemaToolArguments`。只允许本地 `#...` `$ref`，
外部 URL 或文件引用直接拒绝，避免 Schema 校验触发隐式网络或文件访问。

远端名称通过 `normalize_tool_name()` 转为小写 snake_case，再加入 Server 前缀：

~~~text
远端：ReadFile
公开：mcp_filesystem_read_file

远端：resolve-library-id
公开：mcp_context7_resolve_library_id
~~~

统一格式为：

~~~text
mcp_<server_name>_<normalized_remote_name>
~~~

这个前缀同时解决多 Server 重名和权限规则命名问题。同一 Server 内两个名称
规范化后冲突时，两者都不注册；公开名与内建工具或其他 Server 冲突时，
Registry 拒绝覆盖。

`MCPToolWrapper` 保留公开名到原始 Server 工具名的反向映射。模型调用
`mcp_context7_xxx` 时，远端 `tools/call` 收到的仍是原始名称 `xxx`。

### 5. 四步延迟 Schema 暴露

这里有两层不同的延迟：Server 连接和目录发现现在在应用启动后后台进行；连接完成后，
完整 Schema 仍继续按任务延迟暴露。前者解决 UI 被远端 MCP 阻塞，后者解决工具太多导致
Schema 占用大量 Token。

四步机制：

1. `MCPToolWrapper` 注册时设置 `defer_loading=True`，Registry 仍保存完整定义。
2. 用户任务开始时，AgentLoop 建立 `ToolExposureSession`。首轮工具列表过滤掉
   MCP Schema，只在 `<tool_catalog>` supplement 稳定排序列出可搜索名称。
3. 模型需要某个工具时调用本地 `tool_search`。它只查 Registry 缓存，不连接
   Server，不重新 `tools/list`，只返回名称、最多 160 字描述和状态。
4. ToolSearch 将名称加入当前任务的 `discovered_tools`。AgentLoop 在下一模型轮次
   重新调用 `registry.definitions(...)`，这时才把该工具完整 Schema 放入请求。

这里的“下一轮”是指 ToolSearch 结果回填给模型后紧接着的下一次模型请求，
不是下一条用户消息。

AgentLoop 每轮确实会重新生成工具定义元组，但这是便宜的本地过滤，不是网络刷新：

~~~text
进程级固定目录：Registry + McpToolDescriptor
    任务级可见集：ToolExposureSession.discovered_tools
        请求级快照：advertised_names
~~~

`advertised_names` 是当前模型请求实际收到的工具快照。如果模型在同一批同时构造
ToolSearch 和隐藏 MCP 调用，后者仍会得到 `tool_not_discovered`，不会向远端产生
副作用。

用户任务无论成功、失败还是取消，`finally` 都会清空可见集。清空的只是
当前任务的“已发现”状态，不删除 Registry 工具、不重建目录、不关闭 MCP 连接。
所以不会随会话无限累计，也不需要 LRU。

对 Prompt Cache 而言，第一次激活新 Schema 时工具集必然改变；但只要当前可见集
不再变化，后续轮次的工具内容和顺序保持稳定，仍可继续利用缓存。

### 6. 调用、权限与结果转换

已激活工具进入普通 Agent 工具链：

~~~text
ToolCallBlock(mcp_server_tool, arguments)
    ↓
PermissionEngine.prepare()
    ↓ 硬检查通过
tool.before_execute Hook
    ↓ Hook 无决定时 evaluate_policy()
    ↓
人工允许或项目规则放行
    ↓
ToolScheduler（UNKNOWN 按非 READ 工具串行）
    ↓
ToolExecutor
    ↓
JsonSchemaToolArguments.validate()
    ↓
MCPToolWrapper.execute()
    ↓
McpConnection.call_tool(remote_name, arguments)
~~~

权限审批发生在 `tools/call` 之前。MCP annotations 无论声称只读、幂等或无破坏性，
都不会改变本地 `UNKNOWN` 分类。Agent 模式可以通过 `.ycode/security.yaml` 对
精确 `mcp_*` 名称配置 allow/deny/ask 规则。

`JsonSchemaToolArguments` 在网络调用前完成类型、必填字段、额外字段和其他
JSON Schema 约束校验。失败时返回 `invalid_arguments`，Server 不会收到调用。

`CallToolResult` 的转换规则：

- `TextContent`：按原始顺序输出文本。
- `structured_content`：冻结、脱敏，同时放入可读 JSON 文本和 metadata。
- 图片、音频：只输出类型与 MIME 摘要，不把 Base64 正文传给模型。
- Resource/ResourceLink：只输出 MIME 和 URI 摘要。
- `is_error=true`：保留工具失败语义并加入 `mcp_tool_error`。

稳定错误分类：

| 情况 | 错误码 |
|---|---|
| 本地 Schema 校验失败 | `invalid_arguments` |
| 工具未经 ToolSearch 发现 | `tool_not_discovered` |
| Server 当前不可用 | `mcp_unavailable` |
| 远端工具返回 error | `mcp_tool_error` |
| MCP/JSON-RPC 协议拒绝 | `mcp_protocol_error` |
| 连接或子进程中断 | `mcp_connection_error` |
| 单次工具调用超时 | `mcp_timeout` |
| 返回对象无法安全转换 | `mcp_invalid_result` |

工具错误通常作为 `ToolExecutionResult` 回填给模型，不会直接终止整个 Agent。

### 7. 连接缓存、断线恢复与关闭

MCP 存在两层进程级复用：

- `McpManager._connections`：每个启用 Server 只有一个 `McpConnection`。
- `McpTransportFactory`：stdio 复用同一子进程和 Client；HTTP 复用同一
  `AsyncClient` 和底层连接池。

`McpConnection` 用单一 owner task 持有 SDK Client 与 transport 的异步上下文，启动完成后
不退出上下文，而是等待 `_close_requested`。这个所有权模型避免了创建 Client
的任务和关闭 Client 的任务不一致。

断线规则特意不追求“当前调用透明重试”：

~~~text
当前 tools/call 断线
    → 返回 mcp_connection_error
    → 不自动重试，避免重复副作用

后续新调用
    → 看到 DISCONNECTED
    → 在 reconnect_lock 内重建 transport 和协议握手
    → 成功后只执行这次新调用
~~~

重连时不重新 `tools/list`，不修改启动时缓存的目录，也不改变当前任务的
可见集。Server 在运行期增删工具时，必须重启 YCode 才会刷新目录。

用户取消或工具超时时，当前 SDK 调用 task 会被取消并等待清理，迟到结果
不会进入 Agent 历史。关闭时先取消 inflight 调用，再退出 SDK 上下文，最后关闭
HTTP Client 或 stdio 子进程。如果 Manager 的后台启动仍未完成，先取消 Manager
`_start_task`；Connection 还在 STARTING 或 RECONNECTING 时再取消 owner task，不等待
剩余启动超时。Connection、Manager、AgentLoop 和 ChatSession 的关闭都是幂等的。

### 8. 状态与故障隔离

`McpConnectionState` 包含：

~~~text
disabled / invalid / starting / ready / disconnected
reconnecting / unavailable / closing / closed
~~~

`McpManager.snapshot()` 只组装脱敏的 `McpStatusReport`：Server 名、传输、状态、
有效工具数和稳定错误摘要。它不包含 URL、command、args、env 或 Header。

`/mcp` 由集中式命令框架注册为 LOCAL 命令：

~~~text
用户输入 /mcp
    ↓
CommandDispatcher → mcp_handler
    ↓
UIController.show_mcp_status()
    ↓
TerminalUI 读取 ChatSession.mcp_status 快照并渲染状态表
~~~

它不调用模型，不进入对话历史，也不触发重新发现。连接期间可观察 `starting`，完成后
观察 `ready` 或 `unavailable`。无论单个还是全部 MCP Server 失败，Anthropic Agent
仍可以使用六个基础内建工具和项目 Skill 工具启动。

### 9. 当前明确不支持的范围

- MCP Resources、Prompts 和 Completions。
- roots、sampling、elicitation 或代理模型调用。
- OAuth、浏览器登录、授权回调和 Token 自动刷新。
- 已废弃的 HTTP+SSE 传输。
- 运行期工具目录热更新、Server 开关热切换和配置热加载。
- 跨用户任务保留已发现工具，以及 LRU 淘汰。
- 将图片、音频或二进制正文直接传给模型。
- 信任 Server 提供的安全 annotations。
- OpenAI 路径的 MCP 工具调用。

### 面试表述

YCode 使用官方 MCP SDK 处理 stdio、Streamable HTTP、JSON-RPC ID 匹配和新旧
协议协商。McpManager 在后台并发启动多个独立连接，分页发现工具，并通过
MCPToolWrapper 将远端 JSON Schema 工具适配为统一 Tool。Registry 始终保留完整目录，
AgentLoop 使用任务级 ToolExposureSession 和本地 ToolSearch 按需暴露 Schema，减少
Token 占用且防止同批次绕过。所有 MCP 工具固定为 UNKNOWN，在远端调用前复用
本地参数校验和权限审批。连接和目录进程级复用，可见集任务级清空；断线时
当前调用不重试，只允许后续新调用重建连接，避免未知副作用被重复执行。禁用项完全
跳过连接流程，启动中退出则取消后台任务，不等待完整超时。

## ReAct Agent Loop

### 1. 一轮循环

AgentLoop 的一轮是：

~~~text
turn.start（每个用户任务一次）
    ↓
用稳定提示词、动态补充、消息和工具创建 AgentModelRequest
    ↓
message.before_send → 消费当前 Hook Reminder
    ↓
调用 AgentChatProvider.stream_agent()
    ↓
流式接收 StreamEvent
    ↓
ResponseAssembler 组装 Assistant ChatMessage
    ↓
message.after_receive
    ↓
累计本次请求的 TokenUsage
    ↓
检查 StopReason 与 ToolCallBlock
    ↓
有工具：PermissionEngine.prepare → tool.before_execute → 普通策略/审批
    ↓
Scheduler 合并拒绝结果并执行允许项
    ↓
真实执行完成后触发 tool.after_execute
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

YCode 的 AgentLoop 实现最小 ReAct：每轮创建新的 ResponseAssembler，在完整请求和工具
边界触发 Hook，完成一次模型请求后检查停止原因；工具先经过两阶段权限与 before Hook，
再交给 Scheduler，结构化结果按调用 ID 回填。无工具调用的 END_TURN 才形成最终回复。
循环默认 10 轮，并把正常完成、上限、取消和异常统一成四种 turn.end 状态。

## 模式、事务与取消

### 1. agent 与 plan-only

集中式命令框架注册两个模式命令，Handler 再通过 `UIController` 调用 ChatSession 的
状态能力：

~~~text
/plan   → plan-only
/agent  → agent
~~~

模式命令：

- 不调用 Provider。
- 不进入对话历史。
- 产生 ModeChangedEvent。
- 只在当前进程内保存。

plan-only 对普通工具有三层保护：

~~~text
第一层：Registry 只把 READ ToolDefinition 发给模型
第二层：PermissionEngine.prepare 在 Hook 前使用 allowed_access 硬拒绝 WRITE/UNKNOWN
第三层：Executor 执行前再次使用 allowed_access 复核
~~~

对 MCP 存在一个严格受控的例外：只有 `plan_only.allow_mcp_tools` 白名单中的
工具才进入可搜索名称，AgentLoop 为执行阶段临时增加 `UNKNOWN`，但
PermissionEngine 的普通策略仍对每次调用强制 ASK，不允许会话授权。这不是将 MCP 降级为
READ，而是一条显式白名单加逐次审批的例外通道。

最终计划输出后不会自动退出 plan-only。OpenAI 使用 PlainChatRunner 且不装配命令
运行时，继续走原有兼容路径；它不支持 plan-only。

### 2. 整轮事务

ChatSession 的事务边界是整个用户回合，而不是单次 Provider 请求：

~~~text
COMPLETED
    → 先将用户消息、ToolCall/ToolResult 和最终回复追加到 JSONL
    → 最后写入 turn_commit 并 flush
    → 再提交 ContextManager 和 ChatSession.history
    → 最后向 UI 发送终态事件

LIMIT_REACHED / CANCELLED / ERROR
    → 当前回合不写入 JSONL，也不进入内存历史
~~~

JSONL 是会话的事实来源，因此持久化失败时不能先把临时消息留在内存里。
“回合回滚”只覆盖会话记录和上下文状态；已经成功执行的写文件、编辑或命令
副作用不会自动撤销。

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

等待工具审批时，普通 Ctrl+C 监听会暂停，由审批 InputBox 独占输入；Ctrl+C 直接取消
待审批 Future 和整个 AgentTurn。这样既不会出现两个终端读取器竞争，也不会在取消后
继续检查或启动批次中的后续工具。

取消要求：

- 不再启动新的工具或下一轮模型请求。
- 已启动的读取任务被取消并等待清理。
- PowerShell 及其子进程树被终止。
- 当前回合历史不提交。
- Renderer 停止计时和 Rich Live。
- TUI 回到输入状态。

外层 asyncio 任务被取消时，清理完成后仍继续传播 CancelledError，不把它伪装成普通 Agent 取消。

空闲输入阶段不走上面的取消链，而是由普通 InputBox 的显式 `c-c` 绑定抛出
`KeyboardInterrupt`。TerminalUI 捕获后调用幂等的 `request_exit()`：先执行本次进程的
记忆整理，再由 `run_app()` 的 `finally` 关闭 Session、MCP 后台任务和 Provider。
prompt_toolkit 的基础绑定会忽略 `Ctrl+C`，因此这个显式绑定是安全退出行为的一部分，
不能只依赖 `load_key_bindings()`。

### 面试表述

YCode 把整个用户回合作为会话事务：只有 COMPLETED 才先落盘并再提交内存状态，
达到上限、用户取消和异常都丢弃临时消息。plan-only 同时限制模型可见工具，
并由 PermissionEngine.prepare 与 Executor 双重复核。活动操作中的 Ctrl+C 取消模型流、阻塞
审批、调度任务或 PowerShell 进程树并恢复输入；空闲输入中的 Ctrl+C 则进入统一安全
退出管线。

## 当前验证状态

截至 2026-08-14，Hook 实现完成后的最新工作区验证为：

~~~text
ruff format --check ycode tests：227 files already formatted
ruff check ycode tests：All checks passed
compileall -q ycode tests：通过
Hook 集成测试：4 passed
Hook Windows ConPTY 场景：1 passed, 22 deselected
非 E2E 全量：683 passed, 2 skipped, 1 failed
~~~

唯一非 E2E 失败仍是当前工作区
`.ycode/skills/frontend-design/SKILL.md` 返回 Windows `PermissionError: [WinError 5]`；
它也会导致对 `.` 执行 Ruff 时 Ruff 自身崩溃，所以本次格式和静态检查明确限定为实际
Python 源目录 `ycode tests`。完整 E2E 首轮中的既有审批 Ctrl+C 用例曾因 ConPTY 按键
传递波动失败，单独重跑为 `1 passed`。因此当前不能把全项目状态记录为全绿，但 Hook
定向单元、集成和真实终端主链路已有实际通过证据。

新增实现与验证覆盖：

- Anthropic 活动时不导入 OpenAI Provider 和 SDK。
- 内置命令注册、冲突检测、解析、帮助、隐藏命令、AI 双文本分流和 Tab 补全。
- `/help`、模式、权限、MCP、压缩、恢复和退出统一经过命令框架。
- 慢 MCP 后台连接时 UI 先进入输入状态，`/mcp` 可观察 starting 和 ready。
- 省略 MCP 启动超时时使用 5 秒，显式 10 秒仍优先。
- 后台启动中的 MCP 可在退出时取消并完成资源清理。
- 用户在真实空闲输入框中手动确认 `Ctrl+C` 可以直接退出。
- Hook deny 阻止工具副作用并把原因作为工具结果反馈模型。
- Hook ask 复用现有审批槽且不产生会话授权。
- 工具 after Reminder 只进入下一次模型请求，不进入会话历史。
- 真实终端验证启动风险提示、ask/deny、模型调整和 session.end 收尾。

记忆系统 E2E 使用本地假 SSE Provider，连续启动三个真实 YCode 进程，覆盖：

- 新会话 A 的普通回合和工具回合落盘。
- `--continue` 恢复 A 并继续追加。
- 新会话 B 与 `/resume <A>` 在运行期切换。
- 跨时长提醒只在恢复后的下一次普通请求注入。
- 退出时记忆更新可聚合本进程在多个会话中新提交的回合。

仍需人工完成：

- 使用固定任务观察真实模型的工具选择、修改前读取、模式遵守和输出风格。
- 使用真实 Anthropic API 验证首次缓存创建及后续缓存读取；步骤见
  `docs/manual-api-test.md`。
- 在 `.env` 填入真实 MCP API Key 后验证外部 Streamable HTTP Server，并使用
  `/mcp` 确认连接状态。

## Agent Skills：渐进加载、隔离执行与远程安装

Skill 系统位于 `ycode.skills`，只在 Anthropic Agent 路径装配。它把可复用 SOP 保存为
项目文件，而不是把每种流程写进 YCode 内核。当前只扫描：

~~~text
<project>/.ycode/skills/<name>/SKILL.md
~~~

`commit`、`review`、`test` 也是普通外部 Skill，没有专用 Python 分支。OpenAI 仍使用
`PlainChatRunner`，不会扫描 Skill、注册 Skill 工具或生成动态命令。

### 1. SKILL.md 与运行时模型

`SkillLoader` 严格读取 UTF-8、YAML frontmatter 和 Markdown 正文。标准字段包括
`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`；YCode
配置只能放在 `metadata` 的字符串字段中：

| 字段 | 含义 |
|---|---|
| `ycode-execution-mode` | `shared` 或 `isolated` |
| `ycode-model` | 隔离 Skill 使用的已有 Anthropic Provider 名称 |
| `ycode-context` | `current`、`summary`、`recent` 或 `none` |
| `ycode-recent-turns` | `recent` 策略携带的完整用户回合数 |
| `ycode-visible-tools` | 模型在 Skill 作用域内可见的工具白名单 |
| `ycode-argument-hint` | 动态 Slash Command 的参数提示 |

最小标准 Skill 默认是 `shared + current + 当前模型 + 继承工具 + 无预批准`。共享 Skill
不能指定模型，且只能使用 `current`；隔离 Skill 必须显式选择 `summary`、`recent` 或
`none`。`recent` 还必须提供正整数回合数。

主要数据对象的职责是：

- `SkillConfig`：验证执行模式、上下文、模型和工具集合的合法组合。
- `SkillSnapshot`：保存一次完整校验后的名称、正文、配置、路径和 SHA-256 指纹。
- `SkillCatalogEntry`：同时表示可用条目和带 error 的不可用条目；warning 可以与有效快照
  共存。
- `SkillCatalogState`：按目录名确定性排序的完整目录候选状态。
- `SkillTaskScope`：保存本回合开始前的共享状态、待提交状态、调用栈和任务级授权。
- `SkillTaskAuthorization`：保存本任务预批准工具及已经人工批准的 Skill 指纹。

指纹很重要：自动或嵌套调用获得的批准绑定到实际 `SKILL.md` 内容。文件发生变化后，旧
批准不能静默授权新正文。

### 2. 目录扫描与两级加载

启动时的链路是：

~~~text
run_app()
    ↓ 仅 Anthropic
SkillValidationEnvironment
    ↓ 当前工具、Anthropic Provider、内置命令名称
SkillCatalog.scan_candidate()
    ↓ 逐个读取直接子目录中的 SKILL.md
SkillCatalog.commit(candidate)
    ↓
SkillRuntime.refresh_catalog_prompt()
    ↓
PromptRuntimeContext：只注入名称和 description
~~~

扫描只处理 `.ycode/skills/` 的直接子目录，不递归发现嵌套 Skill，也不接受松散 Markdown
文件。单项损坏、依赖缺失或命令冲突会形成不可用条目，不阻止其他 Skill 和主程序启动。
规范化重名的双方都不可用，不按文件系统顺序覆盖。

目录状态使用“先构造候选、后提交”模式。扫描本身失败时旧目录不变；已有 Skill 在真正
调用前则通过 `reload_one()` 重新读取原路径。这样实现两个不同边界：

- 修改已有 `SKILL.md`：下一次调用立即读取新快照。
- 新增、删除或重命名目录：执行 `/skills reload` 后才改变目录、帮助、补全和动态命令。

未激活时，Prompt 只包含“名称 + 一句说明”。共享 Skill 激活后，完整正文才以
`<skill name="...">...</skill>` 形式进入会话级补充提示；资源目录不会自动注入。

### 3. 显式调用、自动调用与嵌套调用

自动调用通过始终可见的 READ 工具 `load_skill(name, arguments)` 进入。动态命令
`/<skill-name> [arguments]` 则由 `build_skill_command_definitions()` 生成，并走
`ChatSession.stream_skill()`。两条路径都会在执行前重读文件，但调用来源不同：

| 来源 | `SkillInvocationSource` | 带 `allowed-tools` 时是否额外审批 |
|---|---|---|
| 用户提交动态命令 | `EXPLICIT` | 不额外审批，命令提交本身视为本任务授权 |
| 主 Agent 调用 `load_skill` | `AUTOMATIC` | 必须审批 |
| Skill 内再次调用 `load_skill` | `NESTED` | 必须审批 |

参数不会替换 SOP。会话和模型收到稳定展开文本：

~~~text
Use the "<name>" skill for this task.

Invocation arguments:
<原始参数>
~~~

无参数时明确写 `No arguments were provided.`。终端仍显示原始 Slash Command，避免 UI
文本和模型文本互相污染。

`SkillRuntime.enter_call()` 用调用栈拒绝循环，并把最大嵌套深度限制为 3。隔离分支复制
父调用栈并共享任务授权对象，但不继承主分支的待提交共享状态。因此子 Agent 中激活的
共享 Skill 不会意外进入主会话。

### 4. 共享模式的事务边界

共享 Skill 不在 `invoke()` 时直接写入长期状态，而是先进入
`SkillTaskScope.pending_shared`：

~~~text
load_skill / 显式命令
    ↓ 调用时重读并校验
pending_shared + 临时 SOP + 本任务预批准
    ↓ Agent 回合继续
COMPLETED
    ↓ SessionManager 先写消息、skill_state、turn_commit
SkillRuntime.commit_task()
    ↓
active_shared 成为下一回合状态
~~~

若 Agent 达到上限、失败、取消或会话落盘失败，`discard_task()` 会丢弃待激活快照、调用栈
和任务级授权，并恢复已提交共享提示词。已发生的文件或命令副作用不自动回滚。

多个共享 Skill 按名称排序注入。工具白名单的处理不是简单交集：如果所有激活快照都声明
白名单，先取这些白名单的并集，再与当前模式基础工具取交集；只要其中一个 Skill 未声明
白名单，就继承基础工具集合。

### 5. 隔离模式与上下文策略

`IsolatedSkillRunner` 为每次隔离调用创建临时 Anthropic Provider、独立
`PromptRuntimeContext` 和临时 `AgentLoop`。它只注入当前 Skill 的完整 SOP，并按配置构造
历史：

- `summary`：用当前已提交摘要和全部已提交历史生成临时最新摘要。
- `recent=N`：只携带最近 N 个完整用户回合；工具调用和结果不会被拆散。
- `none`：不携带旧历史。

当前任务始终传入。执行成功后只返回非空最终文本 `handoff`；Thinking、工具调用、工具
结果和内部消息都不进入主会话，也不产生可恢复子会话。显式隔离调用只把展开后的用户任务
和最终交接作为一个完整主会话回合提交；自动或嵌套隔离调用把交接作为 `load_skill` 的工具
结果交给直接父 Agent。

隔离 Skill 可以引用现有命名 Anthropic Provider，不能定义 API Key、base URL 或新
Provider；共享 Skill 始终使用当前会话模型。取消会传给活动隔离 AgentTurn，Runner 最终
关闭临时循环和 Provider 资源。

### 6. 工具可见性、预批准与安全边界

Skill 对工具有两个独立维度：

~~~text
ycode-visible-tools
    → 模型是否看得到工具，只能收窄

allowed-tools
    → 本 Skill 任务中是否免普通人工询问，不增加可见工具
~~~

Loader 将 `Read`、`Write`、`Edit`、`Bash`、`PowerShell`、`Glob`、`Grep`、
`ToolSearch` 映射为 YCode 工具名。`Bash(git:*)` 这类参数表达式当前不执行：它产生
warning，但不使 Skill 不可用，也不授予 `run_command` 预批准；Git 命令仍可通过普通
`run_command` 权限流程执行。因此“bash 中 git 的 Skill 默认不可用”不是设计结论，准确
说法是“参数级 git 预授权不生效”。

预批准只把原本的 ASK 转为 ALLOW，不能覆盖：

- plan-only 的访问分类限制；
- 项目安全配置的 DENY；
- 工作区路径边界；
- PowerShell 命令安全检查；
- ToolExecutor 的最终访问分类复核。

自动或嵌套激活若声明普通 `allowed-tools`，PermissionEngine 会展示 Skill 名称和预批准
工具，并要求单次审批。批准绑定当前指纹且只活到本任务结束。历史上已激活、但本任务没有
再次调用的共享 Skill 不贡献预批准。

### 7. 管理命令与会话恢复

`/skills` 显示可用、激活和不可用状态；`show` 显示执行模式、上下文、模型、预批准及
诊断；`deactivate` 停用共享 Skill；`reload` 事务式刷新目录和动态命令。

共享 Skill 状态以 `SkillStateRecord` 写入会话 JSONL。它可以和本回合消息一起在
`turn_commit` 前写入，也可以在停用或 reload 自动移除时作为覆盖当前已提交回合的独立
状态记录追加。恢复会话时只读取名称，再按当前磁盘重新加载：有效共享 Skill 恢复，删除、
改名、失效或隔离 Skill 跳过并告警。旧 SOP、隔离内部历史和任务预批准都不持久化。

`/clear` 创建新的空会话，清除历史、上下文摘要、活动共享 Skill 和临时权限，并把模式
重置为 agent；项目 Skill 目录、MCP 连接、项目记忆和权限配置模式保留。

### 8. install_skill 的主动调用设计

`install_skill` 的工具描述明确告诉模型：用户提供以下 URL 并要求安装时应直接调用工具，
不要先用文本再次询问；真正调用会自动触发 PermissionEngine 的人工审批。这解决了“工具
存在但模型不主动使用”的提示歧义。

参数统一为 `source_url`，支持四种来源：

| 来源 | 识别和构造方式 |
|---|---|
| 直接 ZIP | 下载后安全解压唯一顶层 Skill 目录 |
| `skills.sh/<owner>/<repo>/<skill>` | 查询 GitHub 递归 tree，定位父目录名与 slug 匹配的唯一 `SKILL.md`，再下载该目录 |
| GitHub `tree/<ref>/<path>` | 通过 Contents API 从最长候选 ref 开始解析，递归下载明确目录 |
| 原始 `SKILL.md` URL | 读取 frontmatter name，只创建 `<name>/SKILL.md`，不猜相邻资源 |

普通 HTML 页面不会被当成 skills.sh 来源；若 URL 不是前三类且末尾不是 `SKILL.md`，才按
ZIP 处理。skills.sh 和 GitHub tree 会保留 `scripts/`、`references/`、`assets/` 等随附
资源；原始文件来源不会扩展抓取范围。

### 9. 安装事务、限制与临时缓存清理

安装采用临时构造后原子落位：

~~~text
.ycode/skills/.install-*/content/<name>/...
    ↓ 完整下载、解压、名称和结构检查
os.replace(<temporary-skill-dir>, .ycode/skills/<name>)
    ↓ Loader 判断 available / unavailable
刷新 Catalog 与动态 Slash Command
    ↓
finally 删除整个 .install-* 临时目录
~~~

因此安装成功、失败和取消都会清理下载 ZIP、GitHub API 响应构造物及其他临时缓存文件。
如果原子落位后刷新目录失败，目标 Skill 目录也会删除，避免出现“文件已安装但运行时没
刷新”的半提交状态。同名目标存在时直接拒绝覆盖。

功能性安全检查包括：

- 输入、重定向、API 地址和文件下载地址逐次验证 HTTPS；拒绝 URL 凭据、localhost 和
  字面量非公网 IP。
- 整次来源解析和下载共享 30 MB 累计预算。
- ZIP 同时检查声明解压量和实际写入量不超过 30 MB。
- ZIP 拒绝绝对路径、`..`、少于两层的根文件、symlink 和重解析点。
- GitHub 目录拒绝 symlink、submodule、不安全名称和缺少下载地址的条目。
- 顶层目录名必须等于 frontmatter `name`，且名称满足标准约束。

当前按批准范围只做功能性实现，并未做 DNS 解析结果审计、DNS rebinding 防护、恶意公网
样本库、压缩炸弹攻防矩阵或多平台安全验证。域名是否最终解析到私网地址不在本期功能性
校验内，不能把现有 URL 检查描述成生产级 SSRF 防护。

### 10. 当前实现与验证事实

实现完成时的功能性质量检查记录为：

~~~text
ruff format --check .：通过
ruff check .：All checks passed
compileall -q ycode tests：通过
pytest -q：655 passed, 2 skipped
~~~

2026-08-10 整理本笔记时，再次运行 Skill 定向测试：

~~~text
pytest -q tests/unit/skills tests/unit/tools/test_skill_tools.py \
    tests/integration/test_skill_install.py
结果：62 passed, 1 failed
~~~

失败发生在仓库示例扫描用例：工作区后来出现的
`.ycode/skills/frontend-design/SKILL.md` 在当前执行环境返回 Windows
`PermissionError: [WinError 5]`。其余定向用例通过。这个结果说明目录扫描会实际触碰当前
项目中的每个直接 Skill 目录，也说明最新验证结论必须以工作区实时状态为准，不能沿用之前
的全绿数字。

现有自动化覆盖 Loader、Catalog、Runtime、隔离上下文、隔离 Runner、动态命令、工具与
权限、四种安装来源及安装清理。批准文档中列出的
`tests/integration/test_skill_agent_flow.py` 和 `tests/integration/test_skill_sessions.py`
当前并不存在；真实 Skill 会话链路主要由相关单元测试、`test_app.py`、
`test_terminal_chat.py` 及现有集成测试分散覆盖。因此学习笔记以代码和实际测试文件为
事实来源，不把 checklist 中尚未落地的文件名当作已实现测试。

### 面试表述

YCode 的 Skill 系统以项目 `SKILL.md` 为扩展边界：启动只披露名称和说明，调用时重读并
生成不可变快照。共享 Skill 在整轮会话事务成功后才持久激活；隔离 Skill 使用临时
Anthropic Agent，只向父上下文返回最终交接。工具可见性和任务预批准彼此独立，且不能
绕过 plan-only、项目拒绝、路径和执行器安全边界。安装器识别 ZIP、skills.sh、GitHub
tree 和原始 SKILL.md，在临时目录完整构造后原子落位，并在成功、失败或取消时清理缓存。
