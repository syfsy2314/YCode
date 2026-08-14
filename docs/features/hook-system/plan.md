# YCode Hook 系统 Plan

## 架构概览

Hook 系统采用独立运行时，并由 Agent Loop 与会话生命周期在明确节点直接调用：

```text
.ycode/hooks.yaml
        ↓
HookConfigLoader
├─ 安全 YAML 解析
├─ 逐条校验与降级
└─ 启动诊断
        ↓
HookRuntime
├─ 事件分发与条件匹配
├─ enabled / once / executed
├─ 模板渲染与动作执行
├─ 权限决定汇总
├─ System Reminder 队列
└─ 后台任务管理
        ↓
AgentLoop / ChatSession / Application
```

新增独立的 `ycode/hooks/` 包，集中管理配置、事件上下文、条件、模板、动作、权限结果、
运行时状态和资源收尾。AgentLoop 只在关键时序点调用 `HookRuntime.dispatch()` 并处理返回
结果，不直接解释 YAML 或执行具体动作。

应用会话创建一个 HookRuntime。主交互 AgentLoop 与 ChatSession 共享该实例：

- AgentLoop 负责用户任务、模型消息、自动上下文压缩、工具执行和 Agent 错误事件。
- ChatSession 与应用装配负责会话起止和手动上下文压缩事件。
- HookRuntime 保存一次性 Reminder、规则运行状态和异步任务。

核心事件采用直接嵌入调用，不建设通用事件总线。这样可以明确权限拦截和提示词注入的
先后关系，也便于验证每个 Agent Loop 节点。

## 核心数据结构

### HookEventName

```python
class HookEventName(StrEnum):
    SESSION_START = "session.start"
    SESSION_END = "session.end"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    MESSAGE_BEFORE_SEND = "message.before_send"
    MESSAGE_AFTER_RECEIVE = "message.after_receive"
    TOOL_BEFORE_EXECUTE = "tool.before_execute"
    TOOL_AFTER_EXECUTE = "tool.after_execute"
    CONTEXT_COMPACTED = "context.compacted"
    AGENT_ERROR = "agent.error"
```

### HookMatcher 与 HookConditions

```python
class HookPositiveMatcher:
    exact: JsonScalar | None
    glob: str | None
    regex: str | None


class HookMatcher:
    exact: JsonScalar | None
    glob: str | None
    regex: str | None
    not_: HookPositiveMatcher | None


class HookConditions:
    all: Mapping[str, HookMatcher] | None
    any: Mapping[str, HookMatcher] | None
```

`HookPositiveMatcher` 必须且只能声明一个正向操作符。`HookMatcher` 必须且只能声明
`exact`、`glob`、`regex` 或 YAML 别名 `not` 之一；`not` 不能继续嵌套。条件组必须且
只能声明 `all` 或 `any`，映射键是点路径。

`exact` 支持字符串、数值和布尔值；不接受 `null`。`glob` 和 `regex` 只接受非空字符串。
正则表达式在加载配置时验证语法。

示例：

```yaml
conditions:
  all:
    tool.name:
      exact: run_command
    tool.arguments.command:
      regex: "(?i)deploy|publish"
```

### HookAction

```python
class ShellHookAction:
    type: Literal["shell"]
    command: str


class ReminderHookAction:
    type: Literal["reminder"]
    content: str


class HttpHookAction:
    type: Literal["http"]
    method: HttpMethod
    url: str
    headers: Mapping[str, str]
    body: str | None
    json: JsonValue | None


class AgentHookAction:
    type: Literal["agent"]
```

HTTP 的 `body` 和 `json` 最多配置一个。请求头、文本正文和 JSON 中的字符串叶子节点均
支持模板替换。第一期 `agent` 不要求额外字段，避免为尚未设计的子 Agent 接口提前固定
协议。

### HookRule

```python
class HookRule:
    id: str
    event: HookEventName
    action: HookAction
    enabled: bool = True
    conditions: HookConditions | None = None
    permission: HookPermissionDecision | None = None
    once: bool = False
    async_: bool = False
    timeout_seconds: float = 30.0
```

YAML 使用 `async`，Python 字段使用 `async_` 映射。`permission` 是规则的固定权限决定，
仅允许用于 `tool.before_execute`。

完整示例：

```yaml
hooks:
  - id: confirm-production-command
    enabled: true
    event: tool.before_execute
    conditions:
      all:
        tool.name:
          exact: run_command
        tool.arguments.command:
          regex: "(?i)production|prod"
    permission: ask
    once: false
    async: false
    timeout_seconds: 10
    action:
      type: shell
      command: "python .ycode/hooks/check_command.py"
```

### HookConfigLoadResult

```python
@dataclass(frozen=True)
class HookDiagnostic:
    code: str
    path: str
    rule_index: int | None
    rule_id: str
    message: str


@dataclass(frozen=True)
class HookConfigLoadResult:
    rules: tuple[HookRule, ...]
    diagnostics: tuple[HookDiagnostic, ...]
    external_action_warning: bool
```

```python
def discover_hook_config(start_dir: str | Path) -> Path | None: ...

def load_hook_config(start_dir: str | Path) -> HookConfigLoadResult: ...
```

### RuntimeHookRule

配置模型保持不可变，运行时包装对象保存每条规则的状态：

```python
@dataclass(slots=True)
class RuntimeHookRule:
    config: HookRule
    executed: bool = False
```

门禁顺序固定为：

```text
事件名不匹配            → 跳过
enabled == false        → 跳过
once && executed        → 跳过
条件不匹配              → 跳过
条件匹配                → executed = true → 启动动作
```

设置 `executed` 和启动异步任务之间不插入异步等待点。动作失败、异常、超时或取消不恢复
状态。`once: false` 时，`executed` 只记录已经触发过，不阻止后续执行。

禁用规则仍接受完整配置校验，确保以后改为 `enabled: true` 时不会激活残缺动作。

### HookEvent

Hook 不直接依赖 Agent 内部类型，而是接收冻结 JSON 上下文：

```python
@dataclass(frozen=True)
class HookEvent:
    name: HookEventName
    context: FrozenJsonObject
```

公共字段：

```text
event.name
project.path
session.id
```

事件专有字段：

| 事件 | 主要字段 |
|---|---|
| `session.start/end` | `session.id` |
| `turn.start` | `turn.id`、`message.role`、`message.content` |
| `turn.end` | `turn.id`、`turn.status`、可选 `error.code/message` |
| `message.before_send` | `turn.id`、`message.role`、`message.content` |
| `message.after_receive` | `turn.id`、`message.role`、`message.content` |
| `tool.before_execute` | `turn.id`、`tool.id/name/arguments`、可选 `file.path` |
| `tool.after_execute` | 执行前字段及 `tool.result.content/is_error/metadata` |
| `context.compacted` | `turn.id`、`context.before_tokens/after_tokens/manual` |
| `agent.error` | `turn.id`、`error.code/message` |

`tool.arguments` 使用权限硬检查产生的规范化参数。存在规范化 `path` 参数时，同时提供
`file.path`。每个用户任务生成一个 `turn.id`，内部模型工具循环复用该 ID。不适用的字段
不加入上下文。

新会话尚未持久化时使用应用会话内稳定的临时 ID；恢复会话时使用现有 SessionManager
的会话 ID。

### 匹配与模板接口

```python
def resolve_hook_path(context: FrozenJsonObject, path: str) -> object | Missing: ...

def matches_hook_conditions(
    conditions: HookConditions | None,
    context: FrozenJsonObject,
) -> bool: ...

def render_hook_template(
    template: str,
    context: FrozenJsonObject,
) -> str: ...
```

模板转换规则：

- 字符串原样使用。
- 布尔值和数值使用 JSON 标量表示。
- 对象和数组使用键顺序稳定的紧凑 JSON 文本。
- 缺失路径替换为空串。
- 单遍替换，不重新解释替换结果中的 `{{ ... }}`。

### 动作结果与权限决定

```python
class HookPermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class HookActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class HookActionResult:
    status: HookActionStatus
    permission: HookPermissionDecision | None = None
    reason: str = ""
    message: str = ""
```

Shell 动态输出模型：

```python
class ShellPermissionOutput:
    permissionDecision: HookPermissionDecision
    permissionDecisionReason: str = ""
```

只有同步 `tool.before_execute` Shell 尝试解析该结构。动态输出无效时记录失败摘要，权限
结果回退到规则的固定 `permission`。

### HookRuntime

```python
class HookRuntime:
    async def dispatch(self, event: HookEvent) -> HookDispatchResult: ...

    def take_reminders(self) -> tuple[SystemSupplement, ...]: ...

    async def close(self) -> None: ...
```

```python
@dataclass(frozen=True)
class HookDispatchResult:
    permission: HookPermissionDecision | None = None
    reason: str = ""
    notices: tuple[str, ...] = ()
```

- `dispatch()` 负责规则匹配、状态更新和动作调度。
- 普通事件的权限字段始终为空。
- `tool.before_execute` 汇总权限决定并在 `deny` 时短路。
- Reminder 动作成功后进入一次性队列。
- Agent 占位动作通过 `notices` 返回终端信息。
- `take_reminders()` 原子取出并清空当前 Reminder 队列。
- `close()` 等待后台任务最多 3 秒，随后取消剩余任务并关闭 HTTP 客户端。

### HookActionExecutor

```python
class HookActionExecutor(Protocol):
    async def execute(
        self,
        rule: HookRule,
        event: HookEvent,
    ) -> HookActionResult: ...
```

具体实现为 `ShellHookExecutor`、`HttpHookExecutor`、`ReminderHookExecutor` 和
`AgentPlaceholderExecutor`。异步 Shell/HTTP 由 HookRuntime 创建后台任务；执行器只
负责一次动作。

### PermissionPreparation

```python
@dataclass(frozen=True)
class PermissionPreparation:
    subject: PermissionSubject
    denial: PermissionDecision | None
```

```python
class PermissionEngine:
    async def prepare(
        self,
        call: ToolCallBlock,
        *,
        allowed_access: frozenset[ToolAccess],
        plan_only: bool,
    ) -> PermissionPreparation: ...

    def evaluate_policy(
        self,
        preparation: PermissionPreparation,
        session: PermissionSession,
        *,
        skill_scope: SkillTaskScope | None,
    ) -> PermissionDecision: ...
```

`prepare()` 完成不可绕过检查和参数规范化；失败时返回 `denial`。Hook 使用
`preparation.subject.normalized_arguments`。Hook 无决定时才调用 `evaluate_policy()`。

## 模块设计

### 配置与模型

`ycode/hooks/models.py` 定义严格 Pydantic 配置模型和冻结运行结果。动作使用 `type` 判别
联合，拒绝额外字段。模型层校验：

- 事件名、规则 ID、条件操作符和动作必填字段。
- `permission` 只能用于 `tool.before_execute`。
- 参与权限决定的动作不能异步。
- Reminder 不能异步，也不能用于 `session.end`。
- 只有 Shell 和 HTTP 可以设置 `async: true`。
- `timeout_seconds` 必须是正数。

`ycode/hooks/config.py` 从起始目录向上发现最近配置，使用 `yaml.safe_load()`。文件级错误
返回空规则和诊断；顶层有效时逐条校验。重复 ID 保留第一条，后续规则跳过。规则顺序与
YAML 声明一致。包含已启用 Shell 或 HTTP 动作时产生一次外部操作风险诊断。

### 上下文、匹配与模板

`ycode/hooks/context.py` 提供事件工厂，将 ChatMessage、ToolCallBlock、
PermissionSubject、ToolExecutionRecord 和 ContextCompactionReport 转换为冻结 JSON。
消息只暴露完整文本，不暴露 Thinking、签名或供应商块结构。

`ycode/hooks/matching.py` 使用专用 Missing 哨兵区分字段缺失与 JSON `null`：

- 对象按键访问，数组索引必须是非负十进制整数。
- `glob` 和 `regex` 只匹配字符串。
- `regex` 使用搜索语义；全匹配由 `^...$` 表达。
- `not` 只对已存在字段的正向结果取反。
- Hook Glob 默认大小写敏感，不隐式加入平台路径语义。

权限系统保持现有路径 Glob 的大小写处理。两者只复用适合共享的基础 Glob 辅助，不扩展
权限配置对外支持的操作符。

`ycode/hooks/template.py` 执行单遍 `{{ field.path }}` 替换。System Reminder 正文在模板
渲染后进行 XML 文本转义；事件名和规则 ID 使用已校验值。

### 动作执行层

`ycode/hooks/executors.py` 实现四类执行器。

Shell：

```text
渲染 command
  → 使用平台默认 Shell 创建独立子进程
  → cwd = 项目根目录
  → 捕获 stdout/stderr
  → 等待退出或规则超时
  → 必要时解析权限 JSON
```

Shell 不调用 `run_command`、PermissionEngine 或 HookRuntime，从结构上避免权限审批和
递归 Hook。输出只保留有界日志摘要。

HTTP 使用共享 `httpx.AsyncClient`，渲染 URL、请求头、body 或 JSON 字符串叶子节点。
2xx 返回成功，其他状态和请求异常返回失败。第一期不实现重试和生产级网络策略。

Reminder 执行器生成 `SystemSupplement(SupplementKind.SYSTEM_REMINDER, content)`。新增的
SupplementKind 值为 `system-reminder`，现有 `tagged_content` 因而生成准确的
`<system-reminder>...</system-reminder>`。

Agent 占位执行器返回 `子 Agent Hook 尚未实现：<rule-id>` 通知，不创建 Agent。

### HookRuntime

HookRuntime 按规则声明顺序串行处理同步动作，捕获每条规则的运行异常，并继续处理后续
规则。执行前权限汇总为：

```text
aggregate = none

依次处理命中规则：
  有有效动态决定 → 使用动态决定
  否则            → 使用固定 permission

  deny  → 立即返回并停止后续规则
  ask   → aggregate = ask
  allow → 仅当 aggregate 为空时设为 allow
  none  → 保持 aggregate
```

因此优先级固定为 `deny > ask > allow > none`。运行时管理 Reminder 列表和后台任务集合。
后台任务结束时移出集合并记录结果；异常不传播到 Agent 主流程。

### 权限引擎

`ycode/security/engine.py` 将现有 `evaluate()` 拆成：

```text
prepare()
├─ 查找工具和校验参数
├─ 参数及路径规范化
├─ PowerShell 危险命令检查
└─ 访问分类及 plan-only 限制

evaluate_policy()
├─ 项目 deny 规则
├─ Skill 特殊审批
├─ plan-only MCP 特殊审批
├─ 会话授权
├─ 项目 allow/ask 规则
└─ 权限模式
```

项目 `deny` 与其他项目规则一样属于普通权限策略。按照已确认的 Claude 式语义，Hook
明确返回 `allow` 时可以跳过项目 `deny`；参数校验、路径边界、危险命令和 plan-only
限制仍在 Hook 前执行，不能绕过。

Hook 决定处理：

- `allow`：使用同一个 PermissionSubject 构造允许结果，不运行 `evaluate_policy()`。
- `deny`：构造拒绝结果并回填模型。
- `ask`：构造 `allow_session=False` 的审批结果，只允许本次调用，不写会话授权。
- 无决定：调用 `evaluate_policy()`，保持现有权限规则和会话授权语义。

原有 `evaluate()` 保留为兼容入口，内部顺序调用 `prepare()` 和 `evaluate_policy()`，确保
未接入 Hook 的调用方及现有测试继续工作。

### Agent Loop

`ycode/agent/loop.py` 增加可选 HookRuntime 和每轮用户任务 ID。

模型请求时序：

```text
构造请求候选
  → ContextManager.prepare_request
  → 如成功压缩，dispatch(context.compacted)
  → dispatch(message.before_send)
  → take_reminders()
  → 将 Reminder 加入本次 supplements
  → Provider.stream_agent
  → 组装完整 assistant_message
  → dispatch(message.after_receive)
```

`message.before_send` 位于压缩之后，看到最终发送消息。当前事件产生的 Reminder 与此前
排队内容一起进入当前请求。Reminder 在上下文预检后追加，功能实验阶段不为它重新执行
一次 Token 压缩估算。

工具时序：

```text
PermissionEngine.prepare
  → 硬拒绝则生成拒绝结果
  → dispatch(tool.before_execute)
  → Hook 决定或 PermissionEngine.evaluate_policy
  → 必要时人工审批一次
  → ToolScheduler
  → 真实工具完成
  → dispatch(tool.after_execute)
```

Scheduler 当前会为预生成拒绝结果也产生 Started 和 Completed。AgentLoop 保留
`denied_results` 位置集合，仅对不在集合中的 Completed 触发 `tool.after_execute`，不修改
Scheduler 的现有公开事件和并发行为。

任务终态通过统一内部辅助收口：

```text
completed / cancelled / error / limit_reached
  → 错误时先 dispatch(agent.error)
  → dispatch(turn.end)
  → 完成 AgentTurnResult
  → 产生现有终态 AgentEvent
```

AgentLoop 在开始实际模型任务时触发 `turn.start`。Hook 通知使用新增的供应商无关
`HookNoticeEvent` 向 ChatSession 和 TerminalUI 传递，不进入会话历史。

### ChatSession 与终端

`ycode/session/chat.py` 负责：

- 应用装配完成后触发 `session.start`。
- 合并配置诊断与 session.start 通知到 `startup_warnings`。
- 手动 `/compact` 成功激活新历史后触发 `context.compacted`。
- 关闭 AgentLoop 前触发 `session.end`，再调用 HookRuntime 收尾。

AgentLoop 自动压缩和 ChatSession 手动压缩各自在成功激活结果后触发一次。

显式 Skill 的最终交接和内置命令不算普通 Agent 用户任务；只有真正进入 AgentLoop 的请求
触发 `turn.start/end`。

`ycode/ui/terminal.py` 渲染 HookNoticeEvent。配置诊断沿用启动 warning 展示。Shell/HTTP
普通日志不持续打印到终端；Agent 占位通知显示为：

```text
hook: 子 Agent Hook 尚未实现：<rule-id>
```

session.end 发生在 UI 输入循环退出后，其占位通知由关闭路径直接输出，其他结果只写日志。

### 应用装配

`ycode/app.py` 的 Anthropic 路径增加：

```text
加载 hooks.yaml
  → 创建执行器和 HookRuntime
  → 将配置诊断合入 startup_warnings
  → 注入主 AgentLoop 与 ChatSession
  → session.start
  → TerminalUI.run
  → ChatSession.close
      ├─ session.end
      ├─ HookRuntime.close
      └─ AgentLoop / Provider 关闭
```

隔离 Skill 子会话第一期不注入共享 HookRuntime，避免尚未定义的子 Agent/隔离上下文重复
触发任务和 Reminder。Hook 仅作用于主交互 AgentLoop。

OpenAI PlainChatRunner 不加载 Hook 配置、不创建运行时、不触发 Hook 事件。

### 日志

`ycode/hooks/logging.py` 使用 Python 标准 `logging` 记录配置诊断和运行结果，并统一限制
命令输出、HTTP 响应、消息和错误摘要长度。第一期不新增日志轮转、审计文件或 UI 面板。

每条运行日志包含事件名、规则 ID、动作类型和结果；Hook 运行异常在调用边界转换为日志，
不触发 `agent.error`。

## 模块交互

### Hook 强制人工审批

```text
模型调用 run_command
  → prepare：参数、路径、危险命令和 plan-only 检查通过
  → before_execute Shell 返回 ask
  → 构造 Hook 来源的 PermissionDecision
  → TerminalUI 显示一次审批
  → 用户允许本次
  → Scheduler 执行命令
  → after_execute
  → 工具结果回填模型
```

### Hook 自动允许

```text
模型调用 write_file
  → prepare 通过
  → before_execute 固定 permission=allow
  → 跳过项目 deny/allow/ask、会话授权和权限模式
  → Scheduler 执行
```

路径越界、危险命令、非法参数或 plan-only 禁止会在 Hook 前结束，不能被 `allow` 覆盖。

### Hook 拒绝并让 Agent 调整

```text
模型调用工具
  → before_execute 返回 deny + reason
  → 后续 before_execute 规则停止
  → 生成 permission_denied 工具结果
  → 不进入真实工具执行
  → 不触发 after_execute
  → 下一轮模型收到原因并调整策略
```

### Reminder 跨模型请求

```text
tool.after_execute
  → Reminder 动作入队
  → 组装工具结果
  → 下一轮 message.before_send
  → 当前 before_send Hook 也可继续入队
  → 一次性取出全部 Reminder
  → 加入当前 AgentModelRequest.supplements
  → Provider 请求
  → 队列已清空
```

### 应用关闭

```text
TerminalUI 结束输入循环
  → dispatch(session.end)
  → HookRuntime 停止接受普通新事件
  → 等待后台任务最多 3 秒
  → 取消未完成任务
  → 关闭 Hook HTTP 客户端
  → 关闭 AgentLoop、MCP、Provider 和 ContextManager
```

## 文件组织

```text
ycode/
├── hooks/
│   ├── __init__.py
│   ├── models.py
│   ├── config.py
│   ├── context.py
│   ├── matching.py
│   ├── template.py
│   ├── executors.py
│   ├── runtime.py
│   └── logging.py
├── security/
│   └── engine.py
├── prompt/
│   └── models.py
├── agent/
│   ├── events.py
│   └── loop.py
├── session/
│   └── chat.py
├── ui/
│   └── terminal.py
└── app.py

tests/
├── unit/
│   ├── hooks/
│   │   ├── test_models.py
│   │   ├── test_config.py
│   │   ├── test_matching.py
│   │   ├── test_template.py
│   │   ├── test_executors.py
│   │   └── test_runtime.py
│   ├── security/test_engine.py
│   ├── agent/test_loop.py
│   └── session/test_chat.py
├── integration/test_hook_agent_flow.py
└── e2e/test_terminal_chat.py
```

现有测试文件只追加相关场景，不重写无关测试结构。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 触发机制 | 明确节点直接调用 HookRuntime | 时序直观，不建设通用事件总线 |
| 规则启用 | YAML `enabled`，默认 `true` | 可保留暂时停用规则 |
| 单次状态 | 固定 `once` + 内存 `executed` | 满足确认语义，不引入状态机 |
| 配置容错 | 文件级禁用整份，规则级只跳过单条 | Hook 错误不阻止应用启动 |
| 配置模型 | 严格字段和动作判别联合 | 精确定位错误并拒绝残缺动作 |
| 事件上下文 | 供应商无关冻结 JSON | 适合条件、模板和未来扩展 |
| 条件逻辑 | 单层 `all` 或 `any` | 不引入表达式引擎 |
| 正则语义 | 搜索匹配 | 符合常见使用习惯 |
| 缺失字段 | 始终不匹配，包括 `not` | 避免意外触发 |
| 模板 | 单遍 `{{ path }}` 替换 | 行为确定，不执行代码 |
| Shell | 独立平台 Shell 子进程 | 对齐 Claude Hook，不递归权限流程 |
| HTTP | 共享 httpx.AsyncClient | 复用现有依赖和异步能力 |
| 权限拆分 | prepare 硬检查 + evaluate_policy | Hook 可插入且硬边界不可绕过 |
| 项目规则 | 属于普通权限策略 | 保持已确认的 Claude 式 allow 语义 |
| Hook ask | 只批准本次调用 | 后续调用仍重新运行 Hook |
| 权限优先级 | deny > ask > allow > none | 最严格决定获胜，deny 短路 |
| Reminder | 新增 system-reminder supplement | 复用现有请求补充并生成准确标签 |
| Reminder 生命周期 | HookRuntime 一次性队列 | 不进入历史或 session supplement |
| after_execute | 使用拒绝位置集合过滤 | 不改变 Scheduler 公开行为 |
| 异步任务 | HookRuntime 持有任务集合 | 统一退出收尾 |
| 日志 | 标准 logging + 有界摘要 | 满足功能实验，不建生产日志系统 |
| UI 通知 | HookNoticeEvent + 启动 warnings | 不写入模型上下文或历史 |
| 隔离 Skill | 第一期不接入 HookRuntime | 避免未定义的隔离上下文语义 |
| Provider | 只装配 Anthropic 主 Agent | 遵循当前开发范围 |

## Spec 覆盖

- F1–F2、F20–F21：配置模型、加载器、诊断和日志。
- F3–F4：AgentLoop、ChatSession 生命周期触发。
- F5–F8：上下文、匹配器和模板模块。
- F9–F15：四类执行器、权限输出和 Reminder 队列。
- F16：`enabled`、`once`、`executed` 运行时门禁。
- F17–F19：超时、异步任务、关闭收尾和错误隔离。
- N1–N10：运行时边界、严格模型、有界日志和容错策略。
- N11–N13：复用现有调度与会话流程、Anthropic 范围和代表性验证。

技术设计不修改 OpenAI Provider，也不为未来子 Agent 提前定义任务协议。
