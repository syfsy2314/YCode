# 子 Agent 系统技术设计

## 文档状态

- 对应 Spec：`docs/features/subagent-system/spec.md`
- 当前状态：已批准（2026-08-16）
- 实现范围：一次性交付已批准 Spec 的全部能力
- Provider 范围：仅 Anthropic
- 验证范围：功能性单元测试、集成测试和真实终端端到端测试，不做生产级验证

## 1. 设计目标与约束

本设计在保留现有主 Agent 行为的前提下，引入统一的子 Agent 运行时。子 Agent 复用现有 `AgentLoop`、工具执行基础设施、权限引擎、Hook 引擎和 Provider 连接，但拥有独立的消息、上下文、权限会话、读取状态、Reminder 和 Token 统计。

核心约束如下：

1. 定义式和 Fork 式共用一套任务、状态、取消、统计和结果模型。
2. 同步或异步在创建时确定，不允许运行中转换。
3. Fork 首次请求必须保持父请求的可缓存前缀，新增任务只能出现在该前缀之后。
4. Fork 可以看见父 Agent 的原始工具定义，但执行权必须在每次工具调用前重新检查。
5. 子 Agent 不允许嵌套创建子 Agent，也不能在运行时扩大自己的工具能力。
6. 本期不重写主执行循环，不迁移现有隔离 Skill 执行器，只做支撑子 Agent 所需的小范围重构。
7. 任务只在当前进程和当前会话中存在，不增加持久化队列、自动重试或跨进程调度。

## 2. 总体架构

```text
┌──────────────────────────── Main ChatSession ────────────────────────────┐
│                                                                          │
│  InputBox / Slash Commands                                               │
│        │                                                                 │
│        ▼                                                                 │
│  Main AgentLoop ── request snapshot ──► run_subagent tool                │
│        │                                      │                          │
│        │ drain notifications                  ▼                          │
│        ◄──────────────────────────── SubagentManager                     │
│                                               │                          │
│                         ┌─────────────────────┴─────────────────────┐    │
│                         ▼                                           ▼    │
│                 Defined SubagentRunner                       Fork Runner  │
│                 fresh request/history                 inherited request  │
│                         │                                           │    │
│                         └────────────── AgentLoop ──────────────────┘    │
│                                           │                              │
│                       SubagentToolPolicy → Permission → Hook             │
│                                           │                              │
│                            shared registry / filesystem / clients        │
└──────────────────────────────────────────────────────────────────────────┘
```

新增 `ycode.subagents` 包承载角色发现、执行策略、Provider 选择、子循环运行和任务管理。每个会话创建一个 `SubagentManager`，同步和异步任务都由它登记、计数和取消。

每个子任务仍使用现有 `AgentLoop`：

- 定义式任务创建空白消息历史、独立上下文和独立权限会话，使用基础系统指令并叠加角色正文。
- Fork 任务从产生 `run_subagent` 调用的父模型请求快照开始，沿用模型、工具 Schema、系统指令、动态补充和历史消息，将任务放入新增的 continuation 区域。
- 同步任务由工具调用等待统一结果；异步任务创建内部 `asyncio.Task` 后立即返回任务 ID。
- 终态异步任务向管理器提交一次通知，主 `AgentLoop` 只在下一次模型请求边界提取通知。

## 3. 核心数据结构与接口

以下名称是实现时采用的目标接口；字段可根据仓库既有类型习惯调整表示形式，但语义不得改变。

### 3.1 配置

```python
@dataclass(frozen=True)
class SubagentConfig:
    max_concurrent: int = 4
    async_allowed_tools: tuple[str, ...] = (
        "read_file",
        "glob",
        "grep",
        "tool_search",
        "write_file",
        "edit_file",
        "run_command",
    )
```

`max_concurrent` 必须为正整数。异步白名单中的名称在启动时对照最终工具注册表校验；外部或 MCP 工具只有显式加入该列表后才可在异步子 Agent 中执行。

### 3.2 枚举与状态

```python
class SubagentCreationMode(StrEnum):
    DEFINED = "defined"
    FORK = "fork"


class SubagentRunMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class SubagentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"
```

状态只能从 `running` 进入一个终态，终态不可再次变化。达到轮次限制使用独立的 `limit_reached`，不混入普通失败。

### 3.3 角色模型

```python
@dataclass(frozen=True)
class SubagentRoleConfig:
    name: str
    description: str
    prompt: str
    model: str | None
    allowed_tools: frozenset[str] | None
    denied_tools: frozenset[str]
    max_rounds: int
    permission: PermissionMode


@dataclass(frozen=True)
class SubagentRoleSnapshot:
    config: SubagentRoleConfig
    source: str
    builtin: bool


@dataclass(frozen=True)
class SubagentRoleProblem:
    source: str
    code: str
    message: str


@dataclass(frozen=True)
class SubagentRoleCatalogEntry:
    role: SubagentRoleSnapshot | None
    problems: tuple[SubagentRoleProblem, ...]
```

角色在启动时解析并生成不可变快照，运行中的任务不受文件变化影响。角色名使用统一的去空白、小写规范化规则；规范化后的 Frontmatter `name` 必须与规范化后的文件 stem 相同。未知字段、未知模型、未知工具、名单重叠、非法轮次或重复名称均生成问题记录，并只禁用相关项目角色。内置角色先注册，项目角色不能覆盖它们。

### 3.4 工具调用与请求快照

```python
@dataclass(frozen=True)
class RunSubagentArguments:
    task: str
    role: str | None = None
    mode: SubagentRunMode | None = None


@dataclass(frozen=True)
class SubagentInvocation:
    task: str
    role: SubagentRoleSnapshot | None
    creation_mode: SubagentCreationMode
    run_mode: SubagentRunMode
    owner_turn_id: str


@dataclass(frozen=True)
class AgentRequestSnapshot:
    turn_id: str
    request: AgentModelRequest
    mode: AgentMode
    permission_mode: PermissionMode
    effective_tool_names: frozenset[str]


@dataclass
class AgentToolScope:
    turn_id: str
    current_snapshot: AgentRequestSnapshot | None = None
```

主 `AgentLoop` 在每次真正发送模型请求前保存不可变快照。模型随后返回 `run_subagent` 工具调用时，工具取得的就是产生该调用的请求，而不是包含尚未完成 assistant 工具调用的后续历史。

`AgentModelRequest` 新增：

```python
continuation_messages: tuple[AgentMessage, ...] = ()
```

普通请求保持为空。Fork 将父请求的原字段原样保留，只把强制任务消息和后续子 Agent 对话放入 `continuation_messages`。

### 3.5 任务与结果

```python
@dataclass(frozen=True)
class SubagentTaskView:
    task_id: str
    status: SubagentStatus
    creation_mode: SubagentCreationMode
    role: str | None
    task: str
    result: str | None
    usage: TokenUsage
    started_at: datetime
    finished_at: datetime | None
    error: SubagentError | None


@dataclass
class ManagedSubagentTask:
    view: SubagentTaskView
    run_mode: SubagentRunMode
    owner_turn_id: str
    runtime_task: asyncio.Task[SubagentTaskView] | None
    notification_pending: bool


@dataclass(frozen=True)
class SubagentError:
    code: str
    message: str
```

`SubagentTaskView` 是同步工具结果、异步详情和异步通知的统一来源。序列化字段固定为 `task_id`、`status`、`creation_mode`、`role`、`result`、`usage`、`started_at`、`finished_at` 和 `error`；任务详情可以额外展示原始任务和运行模式。

模型结果只负责正文文本。运行时使用最后一条 assistant 文本作为 `result`，不要求模型输出 JSON，不机械截断结果。

### 3.6 扩展接口

```python
class AgentNotificationSource(Protocol):
    def take_pending(self) -> tuple[AgentRuntimeNotification, ...]: ...


class AgentToolPolicy(Protocol):
    def evaluate(self, tool_call: ToolCall) -> ToolPolicyDecision: ...


class OwnedTurnController(Protocol):
    async def cancel_owned(self, turn_id: str) -> None: ...
```

`AgentRuntimeNotification` 表示已经格式化但尚未注入的运行时通知。`AgentToolPolicy` 是权限引擎之前的硬边界。`OwnedTurnController` 使会话取消逻辑不依赖管理器具体实现。

## 4. 为现有执行循环做的小范围重构

本功能需要调整现有启动和请求组装路径，但不重写 `AgentLoop`。

### 4.1 AgentLoop 运行选项

把新增行为集中到一个可选配置对象，避免继续扩张构造函数参数：

```python
@dataclass(frozen=True)
class AgentLoopOptions:
    tool_policy: AgentToolPolicy | None = None
    notification_source: AgentNotificationSource | None = None
    tool_scope: AgentToolScope | None = None
    hook_scope_id: str = "main"
    task_metadata: Mapping[str, object] | None = None
```

主 Agent 仅配置通知源和工具作用域；子 Agent 配置工具策略、独立作用域和任务元数据。不启用子 Agent 时，这些选项为空，原流程保持不变。

### 4.2 内部请求状态

`AgentLoop` 将当前回合的固定补充、Hook Reminder、运行时通知和请求快照作为回合内部状态维护：

1. 在请求边界提取待处理异步通知。
2. 将新通知追加到当前回合固定补充；后续轮次继续携带，但不会进入未来回合。
3. 获取当前 Hook scope 的 Reminder。
4. 完成上下文准备并构建最终 `AgentModelRequest`。
5. 在发给 Provider 前保存 `AgentRequestSnapshot`。

通知提取发生在上下文准备和请求发送之前，不会修改正在流式传输的请求。

### 4.3 Seeded turn

为子 Agent 增加 `start_seeded_turn(...)` 或等价内部入口，允许执行循环从已经构造的请求种子开始，而不是只能接收交互式用户文本。该入口仍复用现有流式事件、工具调度、上下文管理、错误处理和取消机制。

定义式使用空历史种子；Fork 使用父请求快照和 continuation 任务消息。子 Agent 不进入 UI 输入循环。

### 4.4 请求复制与上下文处理

增加集中式请求复制辅助函数，确保以下路径都保留 `continuation_messages`：

- 请求更新与 Provider 调用；
- Token 估算；
- 上下文压缩与外部化；
- 测试 Fake Provider 记录；
- Fork 快照复制。

首次 Fork 请求若因新增任务导致上下文超限，直接以明确错误结束，不能通过改写父前缀来挽救。首次请求成功后，后续上下文管理只能压缩子 Agent 自己的 continuation 区域，不能改写继承前缀。

### 4.5 Hook Reminder 作用域

`HookRuntime` 的 Reminder 从全局列表改为按 `scope_id` 存储：

```python
dispatch(event, context, scope_id=...)
take_reminders(scope_id) -> tuple[Reminder, ...]
```

Hook 规则、执行器和会话级 `once` 命中状态仍由同一个 `HookRuntime` 共享。父 Agent 和每个子任务使用不同 scope；任务结束后清理对应 Reminder。现有事件类型不增加，子任务只补充 `task_id`、创建模式、角色和运行模式上下文字段，并跳过 session start/end。

## 5. 模块设计

### 5.1 `ycode.subagents.models`

定义配置、枚举、角色、调用、任务、错误、通知和序列化模型。该模块不导入 Anthropic SDK 类型，保证核心任务模型与 Provider 解耦。

### 5.2 `ycode.subagents.loader`

职责：

- 读取 `.ycode/agents/*.md`，只扫描项目根目录的直接子文件；
- 分离 YAML Frontmatter 和 Markdown 正文；
- 校验必填字段、字段集合、数据类型、名称、轮次和权限值；
- 对照 Provider 名称和工具注册表校验引用；
- 返回合法快照或结构化问题，不因单文件失败抛出启动级异常。

实现使用安全 YAML 解析，不支持角色继承、热重载或额外搜索路径。

### 5.3 `ycode.subagents.catalog`

先加载内置 `explore`、`plan`，再合并项目角色。对规范化后的重复项目名称，将所有冲突项标记为不可用；内置冲突始终由内置角色胜出且项目项不可用。目录向工具提供稳定的运行时名称查询，不把名称写入工具 Schema 枚举。

### 5.4 `ycode.subagents.policy`

`SubagentToolPolicy` 在现有权限引擎和 Hook 之前执行，按以下顺序收窄：

1. 全局硬拒绝：`run_subagent` 及会安装、加载或激活运行时能力的 Skill 工具；
2. 创建模式基础边界：Fork 来源为父快照的有效工具集，定义式来源为定义式基础工具集；
3. 角色限制：`allowed-tools` 收窄后再应用 `denied-tools`，内置 `explore`、`plan` 使用固定只读集合；
4. 异步白名单：异步任务继续与全局异步集合取交集；
5. plan-only 只读硬上限；
6. 交给现有 PermissionEngine、命令安全、工作区检查和 Hook 权限流程。

工具在 Fork Schema 中可见不代表允许执行。策略拒绝作为普通工具错误结果返回子模型，使它可以换用其他方案；策略阶段已经拒绝的调用不触发 `tool.before`/`tool.after`。如果后续权限或 Hook 得到 `ASK`，子 Agent 将其转换为拒绝结果，不展示审批 UI。

权限初值：

- Fork 复制父 Agent 当前权限模式的值，但创建空白权限会话。
- 定义式取父当前权限模式与角色声明中更严格者，角色不能提权。
- 父 Agent 为 plan-only 时，无论角色或 Fork 均应用不可覆盖的只读上限。

### 5.5 `ycode.subagents.providers`

`SubagentProviderPool` 负责 Provider 所有权：

- Fork 借用产生调用的父 Provider；
- 定义式未指定模型时借用当前活动 Anthropic Provider；
- 定义式指定模型时按已有 Anthropic Provider 配置延迟创建并在会话内复用；
- 借用实例不由子循环关闭；池创建的命名实例只在应用退出时统一关闭；
- 不为 Fork 重复建立同配置连接。

本期不增加 OpenAI 分支。Provider 创建错误只使对应任务失败，不终止主会话。

### 5.6 `ycode.subagents.runner`

`SubagentRunner` 将一次调用转换为独立 `AgentLoop`：

1. 创建独立消息、上下文、权限会话、读取状态、Reminder scope 和 Token 累加器；
2. 选择 Provider、角色系统指令、请求种子、工具策略和最大轮次；
3. 注入任务后运行到终态；
4. 聚合输入、输出、缓存创建和缓存读取 Token；
5. 根据最终事件生成统一结果。

定义式任务使用角色快照中的 `max_rounds`，省略配置时为 10；Fork 没有角色配置，固定使用相同的默认值 10。

完成判定：

- 无工具调用且有正常文本：`completed`；
- 同时有文本和工具调用：执行工具并继续；
- 无文本且无工具调用：`failed / empty_result`；
- 达到最大轮次：`limit_reached`，保留最后一段可用文本；
- Provider、内容过滤或输出上限等异常停止：`failed`；
- 任务取消：`cancelled`。

Fork 强制指令由资源文件提供，首条 continuation 用户消息包含任务，并要求禁止嵌套、禁止交互确认、直接工作，以及按“结论、证据、风险/待办”返回约 1000 汉字以内正文。

### 5.7 `ycode.subagents.manager`

`SubagentManager` 是会话级唯一任务管理器：

- 校验调用模式和角色；
- 为同步、异步任务生成 ID 并登记 `running`；
- 合并计算并发数，默认上限 4；达到上限立即返回错误，不排队；
- 同步调用等待 `SubagentRunner`，异步调用创建 `asyncio.Task`；
- 将终态快照保存到内存并立即释放并发名额；
- 只为异步任务登记一次完成通知；
- 支持按 ID/唯一前缀查询、终止和列表；
- 支持按 `owner_turn_id` 取消当前父回合创建的任务；
- 支持会话清空、恢复和退出时取消全部并清空记录。

管理器采用两阶段绑定：先创建可供工具注册引用的管理器对象，待工具注册表、角色目录、Provider 池和 Runner 完成后再绑定运行依赖。绑定前调用返回明确的未就绪错误，不引入全局单例。

### 5.8 `ycode.subagents.formatting`

集中处理：

- `run_subagent` 同步和异步返回文本；
- `/tasks` 列表和详情；
- 任务终止提示；
- 异步完成通知的结构化 System supplement；
- Token 分类及时间显示。

所有输出从 `SubagentTaskView` 生成，避免三条展示路径产生不一致状态字段。

### 5.9 `ycode.tools.builtin.run_subagent`

注册稳定工具 Schema：

- `task`：必填非空字符串；
- `role`：可选字符串，不使用动态枚举；
- `mode`：可选 `sync | async`。

工具访问级别标记为 `READ`，表示“创建任务”本身不等同于写文件；真正的子工具调用仍逐次经过子 Agent 策略、权限和 Hook。主 Agent 对该工具的现有安全或 Hook 规则仍可拒绝或请求批准。

指定角色时默认同步；不指定角色时固定 Fork 异步，显式 `sync` 返回参数错误。

### 5.10 内置角色资源

- `explore.md`：代码和项目证据探索，只读工具集合。
- `plan.md`：根据任务与代码现状形成计划，只读工具集合。
- `fork.md`：Fork 强制工作规范，不作为可选角色出现在目录中。

## 6. Anthropic Fork 请求与 Prompt Cache

### 6.1 线序列化顺序

Anthropic 请求固定按以下逻辑顺序生成：

```text
tools
→ stable system_prompt
→ messages
→ system supplements
→ continuation_messages
```

普通主请求的 `continuation_messages` 为空，不改变现有语义。Fork 首次请求必须逐项复制父快照中的 model、tools 及顺序、stable system prompt、messages 和 supplements；强制任务消息只追加到 continuation。

### 6.2 Cache breakpoint

父 Anthropic 请求在最后一个可复用的历史消息或 system supplement 上设置 conversation cache breakpoint。Fork 保留完全相同的 breakpoint 和此前内容，从而使父请求已经写入的缓存前缀可被首次 Fork 请求读取。

稳定顶层 system 和工具已有缓存行为继续保留。缓存读取 Token 是可观测结果，不作为任务成功条件：长度、TTL 或 Provider 规则不满足时，Fork 仍可正常执行。

### 6.3 后续轮次

Fork 后续的 assistant、tool result 和用户补充均追加在 continuation 中。上下文压缩只能处理 continuation，不能重新排序、重新序列化或摘要父前缀。首轮新增任务已超上下文时返回失败，避免用压缩父历史破坏首次缓存匹配。

## 7. 关键调用流程

### 7.1 主 Agent 请求边界

```text
开始/继续父回合
  → 提取待注入异步通知
  → 追加到当前回合固定 supplements
  → 提取 main Hook scope reminders
  → 上下文准备
  → 构造最终 AgentModelRequest
  → 保存 AgentRequestSnapshot(turn_id)
  → Provider 请求
  → 模型可能调用 run_subagent
```

通知一经提取即标记已投递；如果当前没有后续模型请求，则保持待处理，直到下一次用户消息触发请求。通知不会主动创建回合。

### 7.2 定义式同步

```text
run_subagent(task, role, sync/default)
  → 目录解析角色快照
  → 管理器检查并发并登记 running
  → 计算父权限与角色权限的严格交集
  → Runner 创建独立运行状态与 AgentLoop
  → 空历史 + 基础指令 + 角色正文 + 任务
  → 工具策略 → 权限 → Hook → 工具执行，循环到终态
  → 管理器保存结果并释放名额
  → 工具直接返回统一结果
```

同步任务不产生异步通知，也不会因运行时间自动转为异步。

### 7.3 定义式异步

```text
run_subagent(task, role, async)
  → 校验、登记并启动 asyncio.Task
  → 立即返回 task_id + running
  → 后台沿定义式执行路径运行
  → 进入终态并保存结果
  → 按完成时间登记一次通知
  → 主 Agent 在下一个请求边界提取
```

### 7.4 Fork 异步

```text
run_subagent(task, no role, async/default)
  → 读取产生本次调用的 AgentRequestSnapshot
  → 保留父 model/tools/system/messages/supplements/cache breakpoint
  → 创建独立权限、上下文、读取和统计状态
  → continuation 追加 fork 强制指令与 task
  → 立即返回 task_id + running
  → 后台复用父 Provider 执行
```

父响应中尚未完成的 assistant `run_subagent` 调用不在快照内，因此不会成为 Fork 前缀的一部分。

### 7.5 工具调用检查

```text
子模型产生 tool_use
  → SubagentToolPolicy 硬边界
      → 拒绝：生成 tool error result，继续模型循环
      → 允许：进入现有 PermissionEngine / 工作区 / 命令安全
  → 权限 DENY：生成拒绝结果
  → 权限 ASK：自动转换为拒绝结果
  → Hook permission
      → DENY/ASK：生成拒绝结果
      → ALLOW：执行工具并触发既有 tool events
```

`run_subagent` 即使因 Fork 缓存要求仍在 Schema 中，也会在第一层硬拒绝。

### 7.6 取消与会话生命周期

每个父回合生成稳定 `turn_id`。同步和异步任务都记录创建它的 `owner_turn_id`。

- ESC 或 Ctrl+C：取消当前 `AgentTurnStream`，再调用 `cancel_owned(current_turn_id)`，清除该回合创建且仍在运行的全部子任务。
- `/tasks stop <id>`：只取消指定运行任务。
- 取消新父回合：不会影响此前已经正常结束父回合遗留的异步任务。
- clear、restore、exit：先取消当前会话所有子任务并等待基本收尾，再清空任务和通知记录。

取消不回滚子 Agent 已经写入文件系统的变更。

## 8. 启动与关闭路径

### 8.1 启动顺序

内部装配顺序调整为：

1. 加载应用配置、Provider 配置和项目路径；
2. 创建基础工具注册表；
3. 创建尚未绑定 Runner 的会话级 `SubagentManager`；
4. 注册 `run_subagent`、Skill 和 tool search 等工具；
5. 创建现有安全、权限和 Hook 基础设施；
6. 用最终工具表和 Provider 配置加载内置及项目角色；
7. 创建 `SubagentProviderPool` 和 `SubagentRunner`，完成管理器绑定；
8. 创建主 `AgentLoop`、`ChatSession` 和命令处理器；
9. 恢复会话并触发既有 session start；
10. 启动现有 MCP 后台连接和 UI。

用户可见启动流程不增加新的交互步骤。角色错误作为隔离的诊断信息保留，不阻止应用启动。

### 8.2 关闭顺序

1. 停止并等待当前会话子任务；
2. 执行既有 session end 与 Hook 收尾；
3. 关闭 MCP；
4. 关闭 Provider 池创建的命名子 Provider；
5. 关闭主 Provider 和上下文资源。

借用的主 Provider 只由原所有者关闭一次。

## 9. 斜杠命令与 UI

新增命令：

- `/tasks`：列出当前会话全部任务，包含 ID、状态、创建模式、角色、运行时长和总 Token；
- `/tasks <task-id>`：按完整 ID 或唯一前缀显示参数、结果/错误、分类 Token 和起止时间；
- `/tasks stop <task-id>`：取消唯一匹配的运行任务。

命令直接读取管理器，不写入模型历史，也不触发模型请求。空列表、无匹配、多匹配和终态任务均返回明确文本。

`InputBox.wait_for_interrupt()` 扩展为同时识别 ESC 和 Ctrl+C。二者语义一致：取消当前父回合以及该回合拥有的全部运行中子 Agent；不存在活动回合时不执行任务级取消。异步任务完成可以显示非阻塞提示，但提示本身不代表通知已经注入模型。

## 10. 文件组织

### 10.1 新增文件

| 文件 | 职责 |
|---|---|
| `ycode/subagents/__init__.py` | 导出稳定子 Agent API |
| `ycode/subagents/models.py` | 配置、角色、任务、状态、通知模型 |
| `ycode/subagents/loader.py` | 项目角色解析与严格校验 |
| `ycode/subagents/catalog.py` | 内置/项目角色合并与查询 |
| `ycode/subagents/policy.py` | 子工具执行前硬限制与权限收窄 |
| `ycode/subagents/providers.py` | Anthropic Provider 借用和会话池 |
| `ycode/subagents/runner.py` | 独立 AgentLoop 的跑到底执行 |
| `ycode/subagents/manager.py` | 任务登记、并发、通知、取消、查询 |
| `ycode/subagents/formatting.py` | 工具、命令和通知格式化 |
| `ycode/subagents/resources/explore.md` | 内置探索角色 |
| `ycode/subagents/resources/plan.md` | 内置计划角色 |
| `ycode/subagents/resources/fork.md` | Fork 强制工作指令 |
| `ycode/tools/builtin/run_subagent.py` | 统一子 Agent 工具 |
| `tests/unit/subagents/test_models.py` | 模型与状态测试 |
| `tests/unit/subagents/test_loader.py` | 角色解析、校验与隔离测试 |
| `tests/unit/subagents/test_catalog.py` | 内置优先、重复和查询测试 |
| `tests/unit/subagents/test_policy.py` | 多层工具限制和权限上限测试 |
| `tests/unit/subagents/test_providers.py` | Provider 借用、复用和关闭测试 |
| `tests/unit/subagents/test_runner.py` | 执行终态、轮次和用量测试 |
| `tests/unit/subagents/test_manager.py` | 并发、通知、取消和生命周期测试 |
| `tests/unit/subagents/test_formatting.py` | 统一输出测试 |
| `tests/unit/tools/test_run_subagent.py` | 工具参数和返回测试 |
| `tests/integration/test_subagent_flow.py` | 定义式、Fork、Hook、通知集成测试 |

测试文件的最终目录层级遵循仓库现有测试布局；若现有同类测试使用不同命名，只调整位置，不改变覆盖范围。

### 10.2 修改文件/模块

| 模块 | 修改内容 |
|---|---|
| `ycode/agent/contracts.py`、事件与导出 | continuation、运行选项、快照和任务元数据 |
| `ycode/agent/loop.py` | 请求边界、策略入口、seeded turn、子循环终止语义 |
| `ycode/core/provider.py` | 请求 continuation 字段及复制支持 |
| `ycode/context/*` | continuation 的估算、压缩和外部化 |
| `ycode/providers/anthropic.py` | 新消息顺序与 conversation cache breakpoint |
| `ycode/tools/contracts.py`、导出 | AgentToolScope 或所需上下文扩展 |
| `ycode/prompt/*` | 角色系统补充和运行时通知模型 |
| `ycode/hooks/runtime.py`、上下文 | scoped Reminder 和子任务元数据 |
| `ycode/config/models.py`、导出 | SubagentConfig 配置入口 |
| `ycode/commands/contracts.py`、builtin | `/tasks` 命令族 |
| `ycode/session/chat.py` | owner turn 取消与会话任务清理 |
| `ycode/ui/input_box.py`、terminal | ESC/Ctrl+C 一致取消及非阻塞提示 |
| `ycode/app.py` | 两阶段装配、启动与关闭顺序 |
| 相关 Fake Provider 和既有测试 | continuation、通知与取消适配 |

### 10.3 明确不修改

- OpenAI Provider；
- 工具 Scheduler 和 Executor 的核心调度模型；
- 现有安全引擎的规则语义；
- MCP 协议与连接实现；
- Skill 系统及现有隔离 Skill Runner；
- 会话持久化 codec 和任务持久化格式。

## 11. 测试设计

### 11.1 单元测试

覆盖：

- 角色合法/非法 Frontmatter、未知字段、名称规范化、重复、内置冲突、未知模型/工具；
- 定义式与 Fork 参数推导，Fork 同步拒绝；
- 全局禁止、角色白黑名单、异步白名单、plan-only 和父权限上限；
- `ASK` 自动拒绝，策略拒绝可返回模型继续；
- 每种执行终态、最后文本选择、最大轮次和分类 Token；
- 同步/异步统一登记、默认并发 4、超限不排队、终态释放名额；
- 通知按完成时间、一次提取、同步无通知；
- owner turn 取消、任务停止、会话清理；
- Hook once 共享与 Reminder scope 隔离；
- Provider 借用、命名模型复用及所有权关闭；
- Anthropic 序列化顺序、cache breakpoint 和 continuation 保留。

### 11.2 集成测试

使用本地 Fake Provider 和真实工具注册/Hook/上下文组件覆盖：

- 定义式同步从空历史执行工具并返回；
- 定义式异步立即返回并在后续请求注入通知；
- Fork 首次请求前缀与父快照一致，不含未完成工具调用；
- Fork 继承写工具可见性，但嵌套和越权在执行前拒绝；
- 满足受控缓存条件时记录 cache read Token；
- 多子任务状态、Token、Reminder 和权限记录互不串扰；
- clear、restore 和退出的任务清理。

### 11.3 真实终端端到端测试

在现有 PTY/交互测试框架中验证：

- 定义式同步、定义式异步和 Fork 异步完整主流程；
- 异步结果提示、下一请求通知和 `/tasks` 列表/详情/停止；
- ESC 与 Ctrl+C 对当前父回合及其同步/异步子任务的级联取消；
- 前一正常父回合遗留任务不被新回合取消；
- clear 和退出收尾。

不调用真实付费 API，不做压力、性能、长稳、大规模并发、多平台矩阵或复杂故障注入。

## 12. 技术决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 子执行循环 | 复用 `AgentLoop` | 保持工具、Hook、上下文和事件语义一致 |
| 任务管理 | 每会话单一 Manager | 统一同步/异步状态、并发、取消和通知 |
| 后台实现 | 进程内 `asyncio.Task` | 满足当前会话异步需求，不引入持久化队列 |
| Fork 来源 | 实际已发送请求快照 | 精确对应产生工具调用的输入历史 |
| Fork 新消息 | `continuation_messages` | 保持父 messages/supplements 前缀不变 |
| Anthropic 缓存 | conversation cache breakpoint | 让父对话前缀可被首次 Fork 请求读取 |
| 上下文超限 | 首次不压父前缀，后续只压 continuation | 避免破坏首次缓存匹配和历史语义 |
| 工具安全 | 可见性与执行权分离 | Fork 保留 Schema，同时执行前强制限制 |
| `run_subagent` 访问级别 | `READ` | 创建任务本身不代表获得子工具写权限 |
| 定义式权限 | 父模式与角色模式取更严格者 | 防止角色配置提升父权限 |
| plan-only | 所有子任务只读硬上限 | 保持父任务行为边界 |
| 子任务 `ASK` | 自动拒绝并回传工具结果 | 子任务不具备用户交互审批通道 |
| Provider | 借用当前实例 + 命名实例会话池 | 共享连接并明确关闭所有权 |
| 上下文 | 每任务独立实例 | 防止压缩、读取缓存和 Token 串扰 |
| Hook | 共享 Runtime，Reminder 分 scope | 共享规则/once，同时隔离父子提示 |
| 通知载体 | 一次性结构化 system supplement | 不伪造用户消息，不主动发起回合 |
| 通知时机 | 下一模型请求边界提取 | 不打断流式请求，保持顺序确定 |
| 取消归属 | `owner_turn_id` | 精确级联当前父回合创建的任务 |
| 并发超限 | 立即失败、不排队 | 与 Spec 一致并控制实现复杂度 |
| 角色加载 | 启动时快照 | 行为确定，不增加热重载状态 |
| 任务保存 | 会话内存 | 不扩展跨进程持久化范围 |
| 结果格式 | 运行时结构 + 模型文本正文 | 稳定外层字段，不要求模型严格 JSON |
| 过程回传 | 不向父模型流式注入中间事件 | 本期只需要最终结果和完成通知 |
| 装配 | Manager 两阶段绑定 | 解决工具注册、角色校验和 Runner 的依赖顺序 |
| 现有隔离 Skill | 不迁移 | 避免把子 Agent 功能扩成无关重构 |
| Provider 适配 | 只改 Anthropic | 遵守本期范围，OpenAI 留在范围外 |

## 13. Spec 覆盖映射

| Spec | 主要设计位置 |
|---|---|
| F1–F5 | 3.4、5.9、7.2–7.4 |
| F6–F13 | 3.3、5.2、5.3、5.10 |
| F14–F18 | 3.4、4.4、6、7.4 |
| F19–F24 | 3.5、5.6 |
| F25–F28 | 2、5.4–5.6 |
| F29–F37 | 5.4、7.5 |
| F38–F44 | 5.7、7.2、7.3 |
| F45–F49 | 4.2、5.7、7.1 |
| F50–F54 | 4.5、5.4、5.6 |
| F55–F57 | 5.8、9 |
| F58–F61 | 5.7、7.6、8、9 |
| N1–N5 | 1、4、6、10.3、12 |
| N6–N9 | 5.7、7、8、9 |
| N10–N12 | 11 |
| N13 | 全部新增模块与实现约束 |

AC1–AC16 分别由上述功能映射和第 11 节测试设计覆盖。实现阶段将在 `task.md` 中把每项验收标准拆成具体任务与验证命令，并在 `checklist.md` 中逐项记录实际结果。

## 14. 范围边界

本设计不包含角色热重载、更多内置角色、子 Agent 嵌套、同步/异步转换、任务排队、自动重试、任务持久化、写冲突处理、独立工作树、OpenAI 适配、生产监控或生产级并发可靠性工程。任何此类能力都需要新的 Spec，不在本次实现中顺带加入。
