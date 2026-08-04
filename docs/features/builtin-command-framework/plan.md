# YCode 内置命令框架 Plan

> 状态：已批准

## 架构概览

```text
Anthropic App 装配
├─ CommandRegistry
│  └─ 内置命令定义与显式注册
├─ CommandParser
├─ CommandDispatcher
└─ ChatSession + TerminalUI
                │
                ├─ InputBox
                │  └─ CommandCompleter → 只读取 Registry 元数据
                │
用户回车 ────────┤
                │
                ├─ 普通输入 → 现有 Agent 对话流程
                │
                └─ 斜杠输入 → CommandDispatcher
                                ├─ 查询 Registry
                                ├─ 未知命令 → UIController 显示帮助引导
                                └─ Handler(UIController, Invocation)
                                    ├─ 本地/状态操作 → ChatSession 能力
                                    └─ AI 对话操作 → 现有 Agent 流程
```

### 命令核心层

新增独立的 `ycode.commands` 包，负责：

- 不可变命令定义和调用模型。
- 名称、别名验证与冲突检测。
- 斜杠输入解析。
- 命令查询、稳定枚举和可补全候选生成。
- 异步命令分发。
- 内置命令的显式注册与处理函数。

核心层不导入 Rich 或 prompt_toolkit，不使用全局单例、装饰器注册或导入副作用。

### UI 控制边界

`UIController` 是命令处理器唯一依赖的运行接口，由 `TerminalUI` 实现。它负责把命令
操作协调到现有会话与渲染流程，包括：

- 显示用户输入、系统消息和结构化状态。
- 执行模式、权限、压缩和恢复操作。
- 提交“显示文本与模型文本不同”的 AI 用户消息。
- 刷新输入状态。
- 请求正常退出并执行记忆整理。

命令处理器不直接访问 `Console`、`InputBox` 或响应渲染器。

### 会话层调整

`ChatSession` 继续拥有历史事务、模式、权限、上下文压缩、会话恢复和记忆整理。现有
硬编码命令中的业务逻辑抽成可复用的会话操作，供 `UIController` 调用。

Anthropic 输入由新分流器提前拦截，不再依赖 `ChatSession.stream_reply()` 内的命令
判断。为了不调整 OpenAI，本期保留未装配命令框架时的现有兼容入口，并让它复用相同
会话操作。

AI 对话型命令调用扩展后的普通回复入口：

```text
display_text = 原始斜杠输入
model_text   = 展开的预设提示词
```

`UserMessageEvent` 使用 `display_text`，Agent 请求、成功提交和 JSONL 历史使用
`model_text`。

### TUI 集成

`TerminalUI` 在每次回车后先尝试命令分发；分发器返回“不是命令”时才进入原有 Agent
消费流程。命令和 Agent 操作共用现有取消监听、事件渲染及退出收尾能力。

`InputBox` 接收可选补全器。Anthropic 装配命令框架时使用 prompt_toolkit 适配器；未
装配时保持原输入行为。帮助提示也只在启用命令框架时切换为 `/help for commands`。

### OpenAI 隔离

OpenAI 不创建命令注册表、解析器、分发器或补全器，继续使用当前 UI 和会话路径。本
功能不新增或修改 OpenAI 专用实现与测试。

## 核心数据结构与接口

### 命令定义

```python
class CommandKind(StrEnum):
    LOCAL = "local"
    STATE = "state"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    argument_hint: str
    kind: CommandKind
    hidden: bool
    handler: CommandHandler
```

约束：

- `name` 和 `aliases` 不包含 `/`，注册时统一转为小写。
- 名称采用字母开头、后续允许字母、数字和连字符的格式。
- 描述、用法和处理器不能为空；无参数命令允许 `argument_hint=""`。
- 定义对象不可变，注册后不能原地修改元数据或处理器。

统一处理器签名：

```python
type CommandHandler = Callable[
    [CommandInvocation, UIController],
    Awaitable[None],
]
```

分类不驱动权限、Provider 或业务服务选择。所有生产命令都通过处理器执行；分发器只
根据分类协调原始输入由自己展示还是由 AI 消息入口展示，AI 命令的处理器调用 UI 控制
接口提供的 AI 消息入口。

### 命令调用

```python
@dataclass(frozen=True, slots=True)
class CommandInvocation:
    raw_input: str
    name: str
    arguments: str
```

- `raw_input` 保存去除外层空白后的原始斜杠输入，用于终端展示。
- `name` 是去掉 `/` 并转为小写的实际输入名称，可能是别名。
- `arguments` 去除命令分隔空白，但保留参数自身的大小写和内部空白。
- 单独 `/` 产生名称为空的命令调用，由分发器按未知命令处理。
- 非 `/` 输入由解析器返回 `None`。

### 注册中心

```python
class CommandRegistry:
    def register(self, definition: CommandDefinition) -> None: ...
    def resolve(self, name: str) -> CommandDefinition | None: ...
    def visible_definitions(self) -> tuple[CommandDefinition, ...]: ...
    def completion_entries(self) -> tuple[CommandCompletionEntry, ...]: ...
```

注册过程先完整校验规范名称和全部别名，再一次性写入：

- 同一条定义内部的重复名称或别名也视为冲突。
- 规范名称、别名和交叉占用共用一个大小写无关索引。
- 发生冲突时注册中心保持原状。
- 定义顺序用于 `/help` 的稳定展示。
- 补全条目展开规范名称和公开别名，并携带所属命令描述。
- 隐藏定义仍可由 `resolve()` 找到，但不进入可见定义和补全条目。

### 解析与分发

```python
class CommandParser:
    def parse(self, text: str) -> CommandInvocation | None: ...


class CommandDispatcher:
    async def try_dispatch(
        self,
        text: str,
        controller: UIController,
    ) -> bool: ...
```

`try_dispatch()` 返回：

- `False`：不是斜杠输入，调用方应进入普通 Agent 流程。
- `True`：是斜杠输入，无论成功、未知、参数错误还是处理失败，都已被命令系统消费。

错误边界：

- `CommandUsageError`：处理器发现参数不合法，分发器显示错误及该命令用法。
- `CommandExecutionError`：预期业务失败，携带可安全展示的消息。
- 未预期异常：分发器只显示通用失败消息，不输出异常详情。
- `asyncio.CancelledError`：继续向上传播，由现有取消流程处理。

### `UIController`

```python
class UIController(Protocol):
    async def show_user_input(self, text: str) -> None: ...
    async def show_system_message(self, message: str) -> None: ...

    async def send_user_message(
        self,
        display_text: str,
        model_text: str,
    ) -> None: ...

    async def set_mode(self, mode: AgentMode) -> None: ...
    async def show_mcp_status(self) -> None: ...
    async def compact_context(self) -> None: ...

    async def show_permission_status(self) -> None: ...
    async def set_permission_mode(self, mode: PermissionMode) -> None: ...
    async def clear_permission_grants(self) -> None: ...

    async def resume_session(self, session_id: str) -> None: ...
    async def refresh_status(self) -> None: ...
    async def request_exit(self) -> None: ...
```

第一期只定义已有生产命令实际需要的方法；Token 查询等未来命令需要时再扩展。

`TerminalUI` 实现该协议。业务失败由实现转换为 `CommandExecutionError`，结构化 MCP、
压缩和恢复结果继续复用现有渲染函数。

### 命令运行时

```python
@dataclass(frozen=True, slots=True)
class CommandRuntime:
    registry: CommandRegistry
    dispatcher: CommandDispatcher
```

`create_builtin_command_runtime()` 显式创建注册中心、解析器、分发器并按固定顺序注册
内置命令。Anthropic 的 `ChatSession` 持有可选运行时供 UI 使用；OpenAI 的值为
`None`。

### `ChatSession` 调整

现有命令业务拆成可复用操作，供 `TerminalUI` 的控制接口实现调用。普通回复入口增加
独立展示文本：

```python
async def stream_reply(
    model_text: str,
    *,
    display_text: str | None = None,
) -> AsyncIterator[AgentEvent]: ...
```

- `display_text is None` 时与当前行为完全一致。
- `UserMessageEvent` 使用 `display_text`。
- Agent、上下文事务、会话持久化和记忆整理使用 `model_text`。
- AI 对话命令调用时传入两个不同值。

## 模块设计与文件组织

```text
ycode/
├─ commands/
│  ├─ __init__.py
│  ├─ contracts.py          # 定义、调用、分类、补全条目、UIController
│  ├─ errors.py             # 定义错误、冲突、用法和安全执行错误
│  ├─ registry.py           # 注册、冲突检测、查询、稳定枚举
│  ├─ parser.py             # 斜杠输入解析
│  ├─ dispatcher.py         # 未知命令、错误边界和异步分发
│  └─ builtin.py            # 内置处理器、帮助生成、显式运行时工厂
│
├─ session/
│  └─ chat.py               # 提取命令业务操作、展示/模型文本分离
│
├─ ui/
│  ├─ command_completion.py # prompt_toolkit Completer 适配
│  ├─ input_box.py          # 可选补全器和可选帮助提示
│  ├─ terminal.py           # 输入分流、UIController 实现、事件消费
│  └─ styles.py             # 必要的候选列表样式
│
└─ app.py                   # 仅在 Anthropic 分支创建命令运行时
```

### `ycode.commands.contracts`

集中保存框架公共契约：

- `CommandKind`
- `CommandInvocation`
- `CommandDefinition`
- `CommandCompletionEntry`
- `CommandHandler`
- `UIController`
- `CommandRuntime`

该模块只依赖已有的模式和权限枚举，不依赖会话、Provider 或终端实现。

### `ycode.commands.registry`

使用一个保持注册顺序的定义列表和一个大小写无关的名称索引：

```text
规范名称 ─┐
          ├─ normalized name → CommandDefinition
公开别名 ─┘
```

`register()` 先在临时集合中完成格式、内部重复和已有占用检查，全部通过后再同时更新
列表与索引，保证失败注册没有部分状态。

帮助读取注册顺序；补全条目按最终文本稳定排序，使候选显示不受别名注册细节影响。

### `ycode.commands.parser`

解析器不访问注册中心，只负责把文本转换为调用对象：

```text
"  /resume Session-AbC  "
→ raw_input="/resume Session-AbC"
→ name="resume"
→ arguments="Session-AbC"
```

`/ help`、`/` 等输入仍返回命令调用，但名称为空，由分发器统一产生未知命令提示。解析器
不理解引号、管道或参数类型。

### `ycode.commands.dispatcher`

处理顺序：

1. 调用解析器；非命令返回 `False`。
2. 用调用名称查询注册中心。
3. 未找到时显示原始输入及统一帮助引导，返回 `True`。
4. 本地或状态命令先显示原始输入，再调用处理器。
5. AI 命令由处理器调用 `send_user_message()`，同时传入原始输入和展开提示词。
6. 把用法错误、预期业务错误和未知异常转换为安全 UI 消息。
7. 取消异常不截获。

### `ycode.commands.builtin`

`create_builtin_command_runtime()` 按固定顺序构建以下定义：

| 规范名称 | 别名 | 分类 | 参数 |
|---|---|---|---|
| `help` | 无 | 本地 | 可选命令名或别名 |
| `exit` | `quit` | 状态 | 无 |
| `plan` | 无 | 状态 | 无 |
| `agent` | 无 | 状态 | 无 |
| `mcp` | 无 | 本地 | 无 |
| `compact` | 无 | 状态 | 无 |
| `permission` | 无 | 状态 | 可选模式或 `clear` |
| `resume` | 无 | 状态 | 一个会话 ID |

处理器只做参数校验、调用相应 `UIController` 方法以及必要的状态刷新。`/help` 处理器
持有同一个注册中心引用，从元数据实时生成列表和详情。

### `ycode.session.chat`

把现有条件分支中的业务操作提取为公开会话能力：

- 模式查询与切换。
- 权限状态查询、模式切换和临时授权清理。
- 可取消的手动上下文压缩事件流。
- 现有会话恢复。
- MCP 状态读取。
- 现有退出记忆整理。

原有事务顺序不变。Anthropic 的命令处理器通过控制接口调用这些能力；未装配运行时的
兼容路径继续调用相同能力，避免复制业务实现。

`stream_reply()` 使用独立的 `model_text` 和可选 `display_text`，但不改变 Runner、
上下文管理器或会话编码格式。

### `ycode.ui.command_completion`

实现 prompt_toolkit 的 `Completer`：

- 仅当光标前文本是单个 `/前缀` 且尚未出现空白时返回候选。
- 使用注册中心生成的规范名称和别名。
- 候选替换整个当前命令词。
- 描述作为候选附加说明。
- 不读取会话状态、不运行异步任务。

prompt_toolkit 负责单匹配补全和多匹配菜单；项目只提供确定性候选。

### `ycode.ui.input_box`

增加两个可选构造参数：

```python
completer: Completer | None
help_hint: str
```

- Anthropic 命令模式传入命令补全器和 `/help for commands`。
- 未启用命令框架时继续使用当前提示和无补全行为。
- `format_hint()` 接收提示文本，并继续优先保留右侧模式和权限。
- 候选菜单使用 prompt_toolkit 的非全屏浮层，不改变四行输入布局。

### `ycode.ui.terminal`

`TerminalUI` 实现 `UIController`，并把当前内嵌逻辑整理为可复用方法：

- 渲染用户输入。
- 消费 Agent 或命令事件。
- 运行可取消操作并监听 `Ctrl+C`。
- 处理工具审批。
- 执行正常退出和记忆整理。

主循环在空输入检查后：

```text
存在 CommandRuntime
→ dispatcher.try_dispatch()
   → True：回到下一次输入或按退出标记结束
   → False：执行普通 Agent 回合

不存在 CommandRuntime
→ 保持现有输入处理路径
```

### `ycode.app`

仅在 Anthropic 装配分支调用 `create_builtin_command_runtime()`，并将可选运行时交给
Anthropic 会话。OpenAI 会话不创建任何命令对象。

### 测试文件

```text
tests/unit/commands/
├─ test_contracts.py
├─ test_registry.py
├─ test_parser.py
├─ test_dispatcher.py
└─ test_builtin.py

tests/unit/ui/
├─ test_command_completion.py
├─ test_input_box.py          # 修改
└─ test_terminal.py           # 修改

tests/unit/session/test_chat.py   # 修改
tests/unit/test_app.py            # 只增加 Anthropic 装配检查
tests/e2e/test_terminal_chat.py   # 增加真实命令与补全流程
README.md                         # 说明命令、帮助和补全
```

不新增依赖，也不修改 OpenAI 专用测试。

## 模块交互与关键调用链

### Anthropic 启动

```text
加载配置与 Provider
→ 装配现有 Prompt / Tools / MCP / Security / Context / Session / Memory
→ create_builtin_command_runtime()
   → 创建 Registry
   → 注册 help 及现有命令
   → 创建 Parser 和 Dispatcher
→ ChatSession 保存可选 CommandRuntime
→ TerminalUI 读取运行时
   → 创建 CommandCompleter
   → InputBox 使用 /help for commands
```

任一命令定义无效或发生冲突时，启动立即失败，不进入交互循环。

### 普通用户消息

```text
用户回车
→ CommandDispatcher.parse()
→ 返回 None
→ TerminalUI 调用 ChatSession.stream_reply(model_text)
→ 现有 Agent / 工具 / 审批 / 上下文 / 持久化流程
```

普通消息的事件、历史和事务行为不变。

### 未知命令

```text
用户输入 /unknown value
→ Parser 生成 CommandInvocation
→ Registry 未找到定义
→ UIController.show_user_input("/unknown value")
→ UIController.show_system_message("未知命令……使用 /help")
→ Dispatcher 返回已消费
→ 回到输入状态
```

不创建 Agent 回合，不调用 Provider，不修改会话历史。

### `/help`

```text
/help
→ Help Handler 读取 Registry.visible_definitions()
→ 按注册顺序生成名称、别名、描述
→ UIController 显示系统消息

/help compact
→ 按规范名称或别名解析可见定义
→ 输出完整描述、usage 和 argument_hint
```

参数超过一个命令词、目标不存在或目标隐藏时，分别进入用法错误或未知命令行为。帮助
输出不缓存，始终来自当前注册元数据。

### 本地状态命令

以 `/mcp` 为例：

```text
/mcp
→ Dispatcher 显示原始用户输入
→ Handler 校验无参数
→ UIController.show_mcp_status()
→ TerminalUI 读取 ChatSession.mcp_status
→ 使用现有 MCP 状态渲染器输出
```

MCP 状态缺失时控制器抛出安全业务错误，由分发器显示；不会调用普通 Agent。

### 模式与权限命令

```text
/plan
→ Handler 校验无参数
→ UIController.set_mode(PLAN_ONLY)
→ ChatSession.change_mode()
→ TerminalUI 渲染 ModeChangedEvent
→ UIController.refresh_status()
→ 下一次 InputBox 读取新模式

/permission strict
→ Handler 对参数做大小写无关匹配
→ UIController.set_permission_mode(STRICT)
→ ChatSession 更新 PermissionSession
→ TerminalUI 渲染事件
→ 刷新下一次输入状态
```

`/agent`、权限查询和 `clear` 使用相同路径。参数错误由处理器抛出
`CommandUsageError`，不调用会话操作。

### 手动压缩

```text
/compact
→ Dispatcher 显示原始用户输入
→ Handler 校验无参数
→ UIController.compact_context()
→ TerminalUI 消费 ChatSession.stream_compaction()
→ ContextManager 生成候选摘要
→ 可选 SessionManager 检查点写入
→ 成功后激活摘要和历史
→ TerminalUI 渲染压缩结果
→ Handler 请求状态刷新
```

TerminalUI 在操作期间复用现有 `wait_for_interrupt()`；`Ctrl+C` 调用
`ChatSession.cancel_active_turn()`，取消活动压缩任务。失败或取消继续沿用当前回滚
边界。

### 会话恢复

```text
/resume <session-id>
→ Handler 将完整剩余文本作为不透明 session-id，保留大小写和内部空格
→ UIController.resume_session(id)
→ ChatSession.restore(id)
→ 可选恢复压缩与检查点写入
→ 成功后切换历史、清理临时权限并重置模式提醒
→ TerminalUI 渲染 SessionRestoredEvent
→ Handler 刷新模式和权限状态
```

恢复失败由控制器转换为安全业务错误；切换前的会话和 UI 状态保持不变。

### AI 对话型命令

测试中注册隐藏命令：

```text
/review src
→ Parser 保留 raw_input 和 arguments
→ Handler 根据 arguments 生成 preset_prompt
→ UIController.send_user_message(
      display_text="/review src",
      model_text=preset_prompt,
  )
→ ChatSession.stream_reply(
      model_text=preset_prompt,
      display_text="/review src",
  )
→ UserMessageEvent 展示 /review src
→ Runner、上下文事务和 SessionManager 使用 preset_prompt
```

若 Agent 失败或被取消，展开提示词不进入已提交历史；成功时只提交展开提示词。

### Tab 补全

```text
用户输入 /co 并按 Tab
→ CommandCompleter 检查光标前没有空白
→ Registry.completion_entries()
→ 匹配 /compact
→ prompt_toolkit 替换当前命令词

用户输入 /a 并按 Tab
→ 匹配 /agent 及其他公开候选
→ prompt_toolkit 显示多候选菜单
```

输入 `/resume ` 后不提供参数候选。补全过程只读取内存元数据。

### 正常退出

```text
/exit 或 /quit
→ Registry 解析到同一 exit 定义
→ Handler 校验无参数
→ UIController.request_exit()
→ TerminalUI 执行 finalize_memory()
→ 设置退出标记
→ 当前输入循环结束
→ run_app finally 关闭 Session / Runner / Provider / MCP / Context
```

`EOF` 和空闲 `Ctrl+C` 继续直接进入同一个正常退出收尾方法。

### OpenAI 兼容路径

```text
OpenAI App 装配
→ 不创建 CommandRuntime
→ InputBox 保持原帮助提示且无命令补全
→ TerminalUI 不调用新 Dispatcher
→ /exit、/quit 和 ChatSession 现有兼容判断保持当前行为
```

`stream_reply()` 新增参数具有兼容默认值，OpenAI 调用方仍传入一个文本值，因此展示
内容、模型请求和历史内容相同。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 注册方式 | 应用启动时显式创建并注册 | 避免全局状态、导入副作用和测试污染 |
| 名称格式 | 定义中不带 `/`；统一小写；字母开头，后续允许字母、数字、连字符 | 覆盖现有命令并保持输入规则简单 |
| 冲突索引 | 规范名称与别名共用一个索引 | 保证任意输入只解析到一条定义 |
| 注册原子性 | 全部校验完成后一次写入 | 冲突失败不会留下半注册状态 |
| 帮助排序 | 使用显式注册顺序 | 可以人为控制高频命令展示顺序，且结果确定 |
| 补全排序 | 按补全文本排序 | 多候选菜单易扫描，别名加入后仍稳定 |
| 处理器模型 | 统一异步处理器 | 同时覆盖即时帮助、压缩、恢复和未来 AI 回合 |
| 命令分类 | 仅协调输入展示，不推导权限或模型策略 | 避免把重叠概念变成复杂策略系统 |
| UI 边界 | 命令只依赖 `UIController` | 隔离 Rich、prompt_toolkit 和终端生命周期 |
| 业务状态 | 继续由 `ChatSession` 及现有服务持有 | 命令框架只负责入口，不复制会话状态 |
| 本地命令展示 | 分发器通过 `show_user_input()` 展示原始命令 | 保持现有滚动区行为，避免每个处理器重复实现 |
| AI 命令展示 | 同时传入 `display_text` 和 `model_text` | 用户看到简洁命令，模型和恢复历史保留真实语义 |
| 错误边界 | 用法错误、预期业务错误、未知异常分层处理 | 提供可操作反馈并避免泄露内部异常 |
| 取消 | `CancelledError` 穿透分发器 | 复用现有 TerminalUI 与 ChatSession 取消机制 |
| Tab 行为 | prompt_toolkit 同步 Completer + 浮动候选菜单 | 单匹配插入、多匹配展示，不阻塞输入 |
| 隐藏命令 | 可解析但不可发现 | 支持内部测试和未来受控入口 |
| OpenAI 隔离 | 可选 `CommandRuntime`，仅 Anthropic 装配 | 不改变 OpenAI 当前路径 |
| 依赖 | 只使用现有 prompt_toolkit | 无需新增运行依赖 |

补全菜单使用 `FloatContainer` 包装现有四行 `HSplit`，并加入 `CompletionsMenu`。基础
输入区尺寸和顺序不变；Tab 触发同步补全，单一候选立即插入，多候选显示浮层，应用
结束时由 `erase_when_done` 清理。

硬编码内置命令的注册错误属于开发错误。Anthropic 装配时若发生此类错误，转换为安全
的启动配置错误；不带内部堆栈进入普通终端输出。

## 验证策略

### 命令核心

运行新增命令单元测试，验证：

- 定义字段、名称规范和不可变性。
- 大小写、别名和交叉冲突。
- 失败注册原子性。
- 普通输入、命令输入、空名称和参数保真。
- 未知命令、用法错误、安全异常和取消传播。
- 隐藏定义的解析、帮助与补全差异。
- AI 测试命令的展示文本与模型文本分离。

### 内置命令与会话

使用本地 Fake Provider 和 UIController 替身验证：

- `/help` 内容完全来自注册中心。
- `/quit` 与 `/exit` 使用同一处理器。
- 模式、权限、MCP、压缩和恢复保持现有行为。
- `/compact` 的成功、无需压缩、失败、存储失败和取消。
- `/resume` 的成功与失败原子性。
- 正常退出继续触发记忆整理。
- 命令不进入普通历史，AI 测试命令只提交展开提示词。

### TUI

使用 prompt_toolkit 管道输入和 DummyOutput 验证：

- `/help for commands` 的宽屏、窄屏降级。
- 单匹配直接补全。
- 多匹配显示候选菜单。
- 隐藏命令和参数不补全。
- 命令完成、失败、取消后回到输入状态。
- 模式与权限变化反映在下一次输入提示。
- 候选菜单在提交和退出后清理。

### 应用与端到端

- Anthropic 装配能够取得完整命令运行时。
- OpenAI 不创建命令运行时，不增加 OpenAI 测试场景。
- Windows PTY 中完成：
  - `/help`
  - Tab 单匹配与多匹配
  - 未知命令
  - `/plan` → `/agent`
  - `/permission`
  - `/mcp`
  - `/compact`
  - `/resume`
  - `/quit`
- 使用本地模拟服务，不调用真实 API。

### 仓库检查

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands
.venv\Scripts\python.exe -m pytest -q tests/unit/ui tests/unit/session/test_chat.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_app.py
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

之后按 `checklist.md` 执行真实 PTY 验收。

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1–F2 | 命令契约、注册中心和原子冲突检测 |
| F3 | 独立解析器与命令调用模型 |
| F4–F5 | 分发器、未知命令和输入路由 |
| F6 | `UIController` 与 `TerminalUI` 实现 |
| F7 | 命令分类元数据和统一处理器 |
| F8 | AI 处理器、双文本回复入口和测试命令 |
| F9 | 注册中心驱动的帮助处理器 |
| F10 | 补全条目、prompt_toolkit 适配和候选菜单 |
| F11 | 可选帮助提示与窄宽度布局 |
| F12–F13 | 内置注册工厂、会话操作和终端控制器 |
| F14 | 隐藏定义的查询、帮助与补全规则 |
| F15 | 输入循环、活动操作和现有取消机制 |
| F16 | 分发错误层与控制器安全业务错误 |
| N1 | Anthropic 可选运行时装配与 OpenAI 兼容路径 |
| N2–N6 | 确定性注册、同步补全、显式装配和统一元数据 |
| N7–N9 | 安全错误、替身测试及现有事务复用 |
| N10 | 无全局注册、插件发现或动态命令配置 |
