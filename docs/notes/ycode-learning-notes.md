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

`run_app()` 是组合根：它读取配置、创建 Provider，并根据协议选择对话运行器。Anthropic
路径还会装配内建工具、MCP、权限系统和 AgentLoop；OpenAI 仍保持纯聊天。

简化后的核心逻辑：

~~~python
config = load_config(path)
provider = create_provider(config.active_provider)
workspace = Path(start_dir or Path.cwd()).resolve()
manager = None

if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
    resolver = WorkspacePathResolver(workspace)
    registry = create_builtin_registry(...)

    if config.mcp.servers or config.mcp.issues:
        manager = McpManager(config.mcp, registry, config.redactor)
        await manager.start()
        if any(server.enabled for server in config.mcp.servers):
            registry.register(ToolSearchTool(registry))

    security_result = load_security_config(workspace, registry)
    permission_session = PermissionSession(security_result.config.mode)
    permission_engine = PermissionEngine(
        registry, resolver, security_result.config, PowerShellSafetyChecker(workspace)
    )
    runner = AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, ToolExecutor(registry)),
        build_builtin_prompt(),
        PromptRuntimeContext(),
        EnvironmentCollector(workspace),
        ToolContext(workspace),
        permission_engine=permission_engine,
        permission_session=permission_session,
        plan_only_mcp_tools=frozenset(
            security_result.config.plan_only.allow_mcp_tools
        ),
        resource_manager=manager,
    )
else:
    runner = PlainChatRunner(provider)
    permission_session = None

session = ChatSession(runner, permission_session, manager)
~~~

Anthropic 的对象关系：

~~~text
TerminalUI
    └── ChatSession
            └── AgentLoop
                    ├── AnthropicProvider
                    ├── ToolRegistry
                    │       ├── 六个内建工具
                    │       ├── ToolSearchTool（配置 MCP 时）
                    │       └── MCPToolWrapper...
                    ├── McpManager
                    │       └── McpConnection...
                    ├── ToolScheduler
                    │       └── ToolExecutor
                    ├── PermissionEngine
                    │       ├── PermissionSession
                    │       └── PowerShellSafetyChecker
                    ├── PromptBundle
                    ├── PromptRuntimeContext
                    ├── EnvironmentCollector
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

- Anthropic 进入 AgentLoop，能够使用内建工具和 MCP 工具并进行多轮 ReAct
  循环。
- OpenAI 只通过 PlainChatRunner 发起一次模型请求，没有工具定义、Agent system prompt 或 plan-only 能力。

退出时，`finally` 调用 `session.close()`。Session 再关闭 Runner，AgentLoop 先通过
`resource_manager` 关闭 MCP 连接、HTTP Client 和 stdio 子进程，最后关闭 Provider。

### 面试表述

YCode 通过 `pyproject.toml` 注册 CLI 入口。`cli.main()` 负责参数解析和启动 asyncio
事件循环，`run_app()` 是组合根：它加载 Provider 与 MCP 配置，在 Anthropic
路径先完成 MCP 连接和工具注册，再装配权限系统与 AgentLoop；OpenAI 保持
PlainChatRunner。资源关闭沿 Session、Runner 传递，MCP 和 Provider 都会被释放。

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

### 6. ChatSession

ChatSession 负责：

- 保存已经提交的对话历史。
- 保存当前 AgentMode。
- 保存可选的 PermissionSession。
- 保证同一时间只有一个活动 AgentTurn。
- 处理 `/plan`、`/agent` 和 `/permission`。
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
    elif isinstance(event, ToolApprovalRequested):
        # 暂停普通 Ctrl+C 监听，进入三选一审批输入
        ...
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

Anthropic 的 InputBox 右下角同时显示任务模式和权限模式。审批输入只有“拒绝、本次
允许、本会话允许”三个选择；进入审批前会暂停普通 Ctrl+C 监听，避免两个输入应用竞争
同一终端设备。OpenAI 没有装配权限会话，保持原有输入和命令行为。

### 8. 职责边界

| 组件 | 职责 |
|---|---|
| AnthropicProvider / OpenAIProvider | 单次模型请求和供应商协议转换 |
| PromptBuilder / PromptBundle | 加载、校验和稳定排列内置提示词章节 |
| PromptRuntimeContext | 管理模式提醒和会话级动态补充 |
| EnvironmentCollector | 采集请求级环境与 Git 摘要 |
| ResponseAssembler | 把单次 StreamEvent 流组装成 Assistant 消息 |
| AgentLoop | 多轮判断、工具执行、结果回填和 Agent 终止 |
| PermissionEngine | 工具执行前的硬边界、规则、会话授权和模式判定 |
| PermissionSession | 当前权限模式和内存中的本会话授权 |
| PowerShellSafetyChecker | 使用 PowerShell AST 识别已定义的危险命令 |
| PlainChatRunner | 把单次纯聊天包装成 AgentTurn |
| ToolRegistry | 登记工具并提供当前可用定义 |
| ToolExecutor | 执行前再次查找、校验访问分类和参数，处理超时及工具错误 |
| ToolScheduler | 合并预先拒绝结果，并保持读取并发和非读取屏障 |
| ChatSession | 历史、任务模式、权限模式、活动回合和事务提交 |
| TerminalUI | 消费 AgentEvent 并控制输入、取消与展示 |
| Renderer | 多轮内容、工具摘要、计时和最终 Markdown |

可以记成：

~~~text
Provider 负责翻译一次响应
Prompt System 负责决定发什么系统上下文
Assembler 负责拼装一次响应
AgentLoop 负责多轮行动
Tool 系统负责执行本地能力
ChatSession 负责事务和模式
TerminalUI 负责交互与展示
~~~

### 面试表述

YCode 使用两层供应商无关事件隔离职责：Provider 将 SDK SSE 转换为单次请求级 StreamEvent，AgentLoop 或 PlainChatRunner 再转换为整轮对话级 AgentEvent。ResponseAssembler 负责单次响应完整性，AgentLoop 负责 ReAct 循环和工具回填，ChatSession 只在 COMPLETED 后事务式提交整轮历史，TerminalUI 完全不感知 StreamEnd。

## 提示词系统

提示词系统的核心目标是把“长期稳定、适合缓存的内容”和“每轮可能变化的上下文”
分开，同时保持动态内容的 system 语义。

### 1. 五类请求内容

`AgentModelRequest` 明确区分四个字段，当前用户输入已经包含在真实消息中：

| 内容 | 字段 | 生命周期 |
|---|---|---|
| 内置全局指令 | `system_prompt` | 应用启动后稳定 |
| 环境、模式、工具状态、记忆 | `supplements` | 请求级或会话级 |
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

- `kind`：environment、task mode、tool state、memory 或 reminder。
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
指令，其余任务只发送精简提醒，不维护“第几轮”的计数。

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

同一用户任务中的多次工具轮次只扩展 `working_messages`，继续复用相同的
`system_prompt`、`supplements` 和工具定义。这保证环境与模式提醒不会在一次 ReAct
循环中重复生成。

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
分离。AgentLoop 每个用户任务生成一次动态上下文并在工具轮次复用；AnthropicProvider
只负责缓存断点、system message 兼容降级和 usage 解析，因此提示词策略没有泄漏到
供应商协议层。

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

### 2. 六个内建工具

| 工具 | 分类 | 用途 |
|---|---|---|
| read_file | READ | 分页读取工作区 UTF-8 文本 |
| glob | READ | 按 POSIX Glob 查找文件 |
| grep | READ | 使用 Python 正则逐行搜索内容 |
| write_file | WRITE | 创建或显式覆盖文本文件 |
| edit_file | WRITE | 使用原文唯一匹配替换文件内容 |
| run_command | WRITE | 在工作区目录中执行 PowerShell |

六个内建工具都有可靠分类。MCP Server 的安全 annotations 不被信任，所有
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
PermissionEngine 规范化并判定
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

### 2. 固定判定顺序与不可覆盖边界

`PermissionEngine.evaluate()` 的核心顺序以当前实现为准：

~~~text
工具查找与 ToolArguments 参数校验
    ↓
真实路径规范化、审批摘要和 session_key
    ↓
run_command 的 PowerShell 危险命令检查
    ↓
当前任务模式 allowed_access（包括 plan-only）
    ↓
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

危险命令、路径沙箱和 plan-only 访问边界不能被 allow 模式、项目 allow 规则或本会话
允许覆盖。这是纵深防御中最重要的一层。

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
且不提供“本会话允许”。项目 DENY 规则仍然可以在此之前直接拒绝。

### 面试表述

YCode 在 AgentLoop 与 Scheduler 之间设置统一 PermissionEngine：先做参数和真实路径
规范化，再执行不可覆盖的危险命令与任务模式检查，之后才看会话授权、项目规则和权限
模式。ASK 通过 AgentEvent 和 AgentTurn 单一 Future 严格阻塞，整批审完后 Scheduler
才执行；拒绝作为结构化 ToolResult 回填。这样既保留 ReAct 自我修正，也保证工具在
获准前没有副作用。

## MCP 客户端、连接与延迟工具加载

MCP 实现位于 `ycode.mcp`，目标不是另外建立一套 Agent 工具系统，而是把
远端 MCP 工具适配成现有 `Tool` 接口。适配完成后，模型请求、参数校验、
权限审批、调度、结果回填和取消都复用 YCode 原有链路。

### 1. 支持范围和整体链路

当前支持：

- 本地子进程 `stdio` 传输。
- 远端 `streamable_http` 传输，包括普通 JSON 和请求级 SSE 响应。
- MCP `2026-07-28` 与 `2025-11-25` 自动协商。
- 多 Server 并发启动、独立失败和状态汇总。
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
McpManager 并发启动每个 McpConnection
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
    startup_timeout_seconds: 10
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
    startup_timeout_seconds: 10
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
YCode，当前不支持热加载。

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

`McpManager` 为每个启用的 Server 建立独立 `McpConnection`，使用
`asyncio.TaskGroup` 并发启动，因此多个 Server 的超时不会简单累加。单个 Server
握手或发现失败时标记为 `unavailable`，不影响内建工具和其他 Server。

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

延迟加载解决的是“工具太多导致 Schema 占用大量 Token”，不是延迟连接 Server。
Server 在 YCode 启动时已经连接和发现，延迟的只是完整 Schema 进入模型请求。

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
PermissionEngine.evaluate()
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
HTTP Client 或 stdio 子进程。Connection、Manager、AgentLoop 和 ChatSession 的关闭
都是幂等的。

### 8. 状态与故障隔离

`McpConnectionState` 包含：

~~~text
disabled / invalid / starting / ready / disconnected
reconnecting / unavailable / closing / closed
~~~

`McpManager.snapshot()` 只组装脱敏的 `McpStatusReport`：Server 名、传输、状态、
有效工具数和稳定错误摘要。它不包含 URL、command、args、env 或 Header。

`/mcp` 是 ChatSession 的本地命令：

~~~text
用户输入 /mcp
    ↓
ChatSession 调用 McpStatusProvider.snapshot()
    ↓
产生 McpStatusEvent
    ↓
TerminalUI 渲染状态表
~~~

它不调用模型，不进入对话历史，也不触发重新发现。无论单个还是全部 MCP
Server 失败，Anthropic Agent 仍可以使用六个内建工具启动。

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
协议协商。McpManager 并发启动多个独立连接，启动时分页发现工具，并通过
MCPToolWrapper 将远端 JSON Schema 工具适配为统一 Tool。Registry 始终保留完整目录，
AgentLoop 使用任务级 ToolExposureSession 和本地 ToolSearch 按需暴露 Schema，减少
Token 占用且防止同批次绕过。所有 MCP 工具固定为 UNKNOWN，在远端调用前复用
本地参数校验和权限审批。连接和目录进程级复用，可见集任务级清空；断线时
当前调用不重试，只允许后续新调用重建连接，避免未知副作用被重复执行。

## ReAct Agent Loop

### 1. 一轮循环

AgentLoop 的一轮是：

~~~text
用稳定提示词、动态补充、消息和工具创建 AgentModelRequest
    ↓
调用 AgentChatProvider.stream_agent()
    ↓
流式接收 StreamEvent
    ↓
ResponseAssembler 组装 Assistant ChatMessage
    ↓
累计本次请求的 TokenUsage
    ↓
检查 StopReason 与 ToolCallBlock
    ↓
有工具：PermissionEngine 按位置判定，必要时阻塞审批
    ↓
Scheduler 合并拒绝结果并执行允许项
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

plan-only 对普通工具有三层保护：

~~~text
第一层：Registry 只把 READ ToolDefinition 发给模型
第二层：PermissionEngine 在调度前使用 allowed_access 硬拒绝 WRITE/UNKNOWN
第三层：Executor 执行前再次使用 allowed_access 复核
~~~

对 MCP 存在一个严格受控的例外：只有 `plan_only.allow_mcp_tools` 白名单中的
工具才进入可搜索名称，AgentLoop 为执行阶段临时增加 `UNKNOWN`，但
PermissionEngine 仍对每次调用强制 ASK，不允许会话授权。这不是将 MCP 降级为
READ，而是一条显式白名单加逐次审批的例外通道。

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

### 面试表述

YCode 把整个用户回合作为会话事务：只有 COMPLETED 才提交历史，达到上限、用户取消
和异常都丢弃临时消息。plan-only 同时限制模型可见工具，并由 PermissionEngine 与
Executor 双重复核。Ctrl+C 可以取消模型流、阻塞审批、调度任务或 PowerShell 进程树，
清理后恢复输入。

## 当前验证状态

以 2026-08-02 当前工作区的实际重跑结果为准：

~~~text
MCP 专项单元与集成测试：76 passed
Ruff format：通过
Ruff check：通过
compileall：通过

完整 pytest -x：收集 419 项，2 passed 后在第 1 个失败处停止
失败：tests/e2e/test_terminal_chat.py::test_windows_terminal_anthropic_thinking
独立重跑：1 failed
~~~

当前失败是 Windows ConPTY 场景在 15 秒内未等到 `Send a message...` 提示，
捕获输出只有终端控制序列。它发生在发送模型请求和 MCP 调用之前，与本次
笔记修改无关；但在它修复或确认为环境问题之前，不能声称当前全量测试通过。

MCP 专项的 76 项通过用例覆盖：

- `.env` 解析、系统环境优先级、Server 配置隔离和秘密脱敏。
- stdio 真实子进程发现、调用、stderr 排空、超时、取消和连接复用。
- Streamable HTTP 普通 JSON、请求级 SSE、Header、超时和并发回包。
- `2026-07-28` 直接协商和 `2025-11-25` 自动回退。
- JSON-RPC 并发响应按请求匹配。
- 分页发现、名称映射、Schema 校验、结果转换和错误分类。
- ToolSearch、下一轮 Schema 暴露、同批次防绕过和任务级清空。
- 真实 MCP Agent 链路：ToolSearch → 审批 → 远端调用 → 最终回复。

仍需人工完成：

- 使用固定任务观察真实模型的工具选择、修改前读取、模式遵守和输出风格。
- 使用真实 Anthropic API 验证首次缓存创建及后续缓存读取；步骤见
  `docs/manual-api-test.md`。
- 在 `.env` 填入真实 MCP API Key 后验证外部 Streamable HTTP Server，并使用
  `/mcp` 确认连接状态。
