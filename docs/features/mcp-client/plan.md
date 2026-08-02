# YCode MCP 客户端与延迟工具加载 Plan

## 架构概览

本功能采用“YCode 管理生命周期和安全边界，官方 MCP SDK 负责协议通信”的分层设计。

```text
.env + .ycode/config.yaml
              │
              ▼
配置解析、变量展开与敏感值登记
              │
              ▼
McpManager ─────── 每个 Server 一个 McpConnection
     │                         │
     │                         ├── stdio transport
     │                         └── Streamable HTTP transport
     │
     ▼
MCPToolWrapper ───── 注册到现有 ToolRegistry
     │
     ▼
ToolSearch + 当前任务 ToolExposureSession
     │
     ▼
AgentLoop → PermissionEngine → ToolScheduler → ToolExecutor → MCP Server
```

官方 `mcp` Python SDK 2.x 负责 JSON-RPC ID 分配、异步响应关联、传输消息解析、
协议协商、请求取消和基础连接生命周期。YCode 不重复实现 JSON-RPC dispatcher，而是在
SDK 上方实现多 Server 故障隔离、固定工具目录、统一 Tool 适配、权限控制、延迟 Schema
暴露和状态展示。

每个 Server 使用独立的 `Client(mode="auto")`。SDK 优先执行 2026-07-28 的
`server/discover`，旧 Server 不支持时自动回退到传统 `initialize` 握手。当前设计不使用
`ClientSessionGroup`，因为它固定使用传统握手，无法满足优先使用 2026-07-28 的要求。

## 核心数据结构

### 配置与环境变量

#### `EnvironmentResolver`

```python
class EnvironmentResolver:
    def resolve(self, variable_name: str) -> str | None: ...
    def interpolate(self, value: str) -> SecretStr: ...
```

`EnvironmentResolver` 保存系统环境和项目 `.env` 的只读快照。解析优先级固定为：

```text
系统环境变量 > 项目 .env > 未定义
```

`interpolate()` 支持一个字符串中的一个或多个 `${VARIABLE}`，因此 HTTP Header 可以
使用 `Bearer ${TOKEN}`。`.env` 使用 `interpolate=False` 读取，其内部值不递归展开；
解析过程不修改 `os.environ`。

#### `SecretRedactor`

```python
class SecretRedactor:
    def add(self, value: SecretStr | str) -> None: ...
    def redact_text(self, value: str) -> str: ...
    def redact_json(self, value: FrozenJson) -> FrozenJson: ...
```

它登记活动 Anthropic API Key、展开后的 stdio 环境变量值和 HTTP Header 值。所有非空
敏感值在错误、状态、stderr、MCP 文本或结构化结果中统一替换为 `[REDACTED]`。

#### MCP Server 配置

```python
class StdioMcpServerConfig(BaseModel):
    name: str
    enabled: bool = True
    transport: Literal["stdio"]
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, SecretStr] = {}
    startup_timeout_seconds: float = 10.0
    tool_timeout_seconds: float = 60.0


class HttpMcpServerConfig(BaseModel):
    name: str
    enabled: bool = True
    transport: Literal["streamable_http"]
    url: str
    headers: Mapping[str, SecretStr] = {}
    startup_timeout_seconds: float = 10.0
    tool_timeout_seconds: float = 60.0


type McpServerConfig = StdioMcpServerConfig | HttpMcpServerConfig
```

Server 名称匹配 `^[a-z][a-z0-9_]*$`。`command` 非空且直接作为可执行程序启动，不经
PowerShell 拼接；URL 只允许 HTTP/HTTPS；超时必须大于零；环境变量名必须合法；Header
名称和值不能包含换行。

#### 配置加载结果

```python
@dataclass(frozen=True)
class McpConfigIssue:
    entry_index: int
    server_name: str | None
    code: str
    message: str


@dataclass(frozen=True)
class McpConfigSet:
    servers: tuple[McpServerConfig, ...]
    issues: tuple[McpConfigIssue, ...]


@dataclass(frozen=True)
class LoadedAppConfig:
    app: AppConfig
    active_provider: ProviderConfig
    project_root: Path
    mcp: McpConfigSet
    redactor: SecretRedactor
```

顶层 YAML、`.env` 或活动 Provider 错误继续抛出 `ConfigError`。`mcp_servers` 的每个
条目独立解析，条目错误转成 `McpConfigIssue`。名称重复时所有同名条目均无效，避免
配置顺序决定结果。

`enabled: false` 的条目仍校验名称、传输、URL/command 和超时等非敏感结构，但不展开
env/Header 引用，不创建连接、不发现或注册工具。它以 `disabled` 状态进入状态目录，
不计入启动成功或失败。

### 工具参数契约

#### `ToolArguments`

```python
class ToolArguments[ArgumentsT](Protocol):
    @property
    def input_schema(self) -> FrozenJsonObject: ...

    @property
    def field_names(self) -> frozenset[str]: ...

    def validate(self, raw: Mapping[str, FrozenJson]) -> ArgumentsT: ...

    def to_mapping(self, value: ArgumentsT) -> FrozenJsonObject: ...
```

`ArgumentsT` 是 Python 3.12 的泛型类型参数，表示校验后的实际参数类型。内建工具返回
对应的 Pydantic `BaseModel` 子类，MCP 工具返回 `FrozenJsonObject`。

具体实现：

```python
class PydanticToolArguments[ArgumentsT: BaseModel]:
    model: type[ArgumentsT]


class JsonSchemaToolArguments:
    schema: FrozenJsonObject
    validator: jsonschema.protocols.Validator
```

两种实现把底层错误统一转换为 `ToolArgumentValidationError`，其中包含稳定的字段路径、
错误类型和简短消息。

#### `ToolDefinition` 与 `Tool`

```python
@dataclass(frozen=True)
class ToolDefinition[ArgumentsT]:
    name: str
    description: str
    access: ToolAccess
    arguments: ToolArguments[ArgumentsT]
    defer_loading: bool = False
    timeout_error_code: str = "timeout"

    @property
    def input_schema(self) -> FrozenJsonObject: ...


class Tool[ArgumentsT](Protocol):
    definition: ToolDefinition[ArgumentsT]
    timeout_seconds: float

    async def execute(
        self,
        arguments: ArgumentsT,
        context: ToolContext,
    ) -> ToolExecutionResult: ...
```

六个内建工具改用 `PydanticToolArguments`，其参数对象和行为不变。`ToolExecutor` 和
`PermissionEngine` 都通过统一的 `arguments.validate()` 校验，安全配置通过
`field_names` 检查顶层参数名，不再直接访问 `arguments_model.model_fields`。

`ToolContext` 增加可选的任务级暴露状态：

```python
@dataclass(frozen=True)
class ToolContext:
    workspace: Path
    exposure: ToolExposureSession | None = None
```

类型引用通过 `TYPE_CHECKING` 和前向注解处理，避免 `contracts.py` 与 `exposure.py` 的
运行时循环依赖。六个现有工具只读取 `workspace`，行为不变；ToolSearch 要求
`exposure` 非空。AgentLoop 在每条用户任务开始时以基础 Context 创建携带本任务状态的
副本，并把该副本传给 Scheduler。

### MCP 工具描述与包装

```python
@dataclass(frozen=True)
class McpToolDescriptor:
    public_name: str
    server_name: str
    remote_name: str
    description: str
    arguments: JsonSchemaToolArguments


class MCPToolWrapper:
    definition: ToolDefinition[FrozenJsonObject]
    timeout_seconds: float

    async def execute(
        self,
        arguments: FrozenJsonObject,
        context: ToolContext,
    ) -> ToolExecutionResult: ...
```

Wrapper 固定设置：

- `access=ToolAccess.UNKNOWN`
- `defer_loading=True`
- `timeout_error_code="mcp_timeout"`

公开名称只在 YCode 内使用，发送 `tools/call` 时使用 `remote_name`。远端 annotations、
只读或幂等声明不改变本地访问分类。

### 连接与状态

```python
class McpConnectionState(StrEnum):
    DISABLED = "disabled"
    INVALID = "invalid"
    STARTING = "starting"
    READY = "ready"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    UNAVAILABLE = "unavailable"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class McpErrorSummary:
    code: str
    message: str


@dataclass(frozen=True)
class McpServerStatus:
    name: str
    transport: str
    state: McpConnectionState
    tool_count: int
    last_error: McpErrorSummary | None


@dataclass(frozen=True)
class McpStatusReport:
    servers: tuple[McpServerStatus, ...]
    security_warnings: tuple[SecurityConfigWarning, ...]

    @property
    def ready_count(self) -> int: ...

    @property
    def failed_count(self) -> int: ...

    @property
    def disabled_count(self) -> int: ...
```

`McpStatusReport` 始终按配置顺序保存 Server，不包含 command、args、URL、Header、env
或原始异常对象。

```python
class McpStatusProvider(Protocol):
    def snapshot(self) -> McpStatusReport: ...
```

会话事件只携带已经脱敏的快照：

```python
@dataclass(frozen=True)
class McpStatusEvent:
    report: McpStatusReport
```

### 发现结果与单 Server 连接

```python
@dataclass(frozen=True)
class McpDiscoveryResult:
    server_name: str
    protocol_version: str
    tools: tuple[McpToolDescriptor, ...]
    issues: tuple[McpErrorSummary, ...]


class McpConnection:
    async def start(self) -> McpDiscoveryResult: ...

    async def call_tool(
        self,
        remote_name: str,
        arguments: FrozenJsonObject,
    ) -> CallToolResult: ...

    def snapshot(self) -> McpServerStatus: ...

    async def close(self) -> None: ...
```

每个连接由一个长期运行的所有权任务进入和退出 SDK Client 上下文，调用任务作为受跟踪
子任务运行。所有权任务串行处理建立连接、替换连接和关闭资源，避免重连与关闭竞争。

### 任务级工具暴露

```python
@dataclass(frozen=True)
class ToolSearchMatch:
    name: str
    description: str
    status: Literal["loaded", "already_loaded", "not_found"]


class ToolExposureSession:
    searchable_names: tuple[str, ...]
    discovered_tools: set[str]

    def activate(self, names: Sequence[str]) -> tuple[ToolSearchMatch, ...]: ...

    def exposed_names(self) -> frozenset[str]: ...

    def clear(self) -> None: ...
```

每条用户任务创建一个空实例。它只保存本任务可见状态，不修改 Registry、Wrapper、工具
目录或连接。

```python
class ToolSearchArguments(BaseModel):
    tool_names: tuple[str, ...]


class ToolSearchTool:
    definition: ToolDefinition[ToolSearchArguments]

    async def execute(
        self,
        arguments: ToolSearchArguments,
        context: ToolContext,
    ) -> ToolExecutionResult: ...
```

公开名为 `tool_search`，分类为 READ 且不延迟。它只接受 reminder 已提供的精确工具名，
查询本地 Registry 并激活当前任务状态，不发起任何 MCP 请求。

### 安全配置扩展

```python
class PlanOnlySecurityConfig(BaseModel):
    allow_mcp_tools: tuple[str, ...] = ()


class SecurityConfig(BaseModel):
    mode: PermissionMode = PermissionMode.DEFAULT
    plan_only: PlanOnlySecurityConfig = PlanOnlySecurityConfig()
    rules: tuple[SecurityRule, ...] = ()


@dataclass(frozen=True)
class SecurityConfigWarning:
    code: str
    tool_name: str
    message: str


@dataclass(frozen=True)
class SecurityConfigLoadResult:
    config: SecurityConfig
    warnings: tuple[SecurityConfigWarning, ...]
```

`PermissionDecision` 增加 `allow_session: bool = True`。plan-only MCP 决策固定为 ASK 且
`allow_session=False`。

## 模块设计

### `ycode/config/discovery.py`

**职责：** 保留配置搜索，并根据实际配置路径确定项目根。

规则：

- 自动或显式使用 `.ycode/config.yaml` 时，项目根是 `.ycode` 的父目录。
- 显式使用其他 YAML 文件时，项目根是文件所在目录。
- 项目根只决定 `.env`；Agent 工作区继续使用当前 `start_dir`。

### `ycode/config/environment.py`

**职责：** 严格读取 UTF-8 `.env`、提供环境变量解析和敏感值脱敏。

缺少 `.env` 是正常情况；无法读取、编码错误或语法错误抛出 `ConfigError`。只在活动
Anthropic 路径读取 `.env`，OpenAI 保持现有环境变量行为。

### `ycode/config/mcp.py`

**职责：** 对 `mcp_servers` 逐项校验，展开已启用 Server 的敏感字段并隔离条目错误。

`mcp_servers` 不是列表属于顶层错误。列表内非映射、缺少字段、非法 transport、URL、
timeout、名称或环境变量引用只形成对应 `McpConfigIssue`。禁用条目不解析敏感变量。

### `ycode/tools/arguments.py`

**职责：** 提供 Pydantic 和 JSON Schema 两种参数适配器，以及统一验证错误。

JSON Schema 发现阶段完成以下工作：

1. `inputSchema` 必须是 JSON object。
2. 根据 `$schema` 选择 Validator；未声明时使用 JSON Schema 2020-12。
3. 调用 `check_schema()`。
4. 编译并缓存 Validator。
5. 允许 fragment 形式的本地 `$ref`。
6. 禁止外部 URL 和文件 `$ref` 触发任何资源读取。
7. 参数错误最多返回前 20 项。

无效 Schema 只排除该工具；SDK 无法解析整个协议响应时按 Server 发现失败处理。

### `ycode/mcp/naming.py`

**职责：** 把远端名称规范化为稳定公开名，并检测冲突。

步骤固定为：

```text
camelCase/PascalCase 边界插入下划线
→ 连字符、点和其他非 ASCII 字母数字字符改为下划线
→ 小写
→ 合并连续下划线
→ 去除首尾下划线
→ 添加 mcp_<server>_ 前缀
```

规范化为空时排除工具。同一 Server 中规范化冲突的双方全部排除。全部 Server 发现完成
后按配置顺序和公开名进行全局冲突检查，再注册到 Registry。最终名称仍通过现有工具名
规则。

### `ycode/mcp/connection.py`

**职责：** 管理单个 Server 的 SDK Client、传输、调用、状态、重连和关闭。

stdio 资源关系：

```text
StdioServerParameters → stdio_client → Client → 一个长期子进程
```

只把显式配置的 env 传给 SDK。stderr 由有界、脱敏的 sink 持续排空，不直接原样写到
终端。

Streamable HTTP 资源关系：

```text
httpx2.AsyncClient → streamable_http_client → Client
```

Header、连接/读写超时配置在 `httpx2.AsyncClient`；HTTP Client 在 YCode 生命周期内保持
打开并复用连接池。不使用废弃 SSE transport，不为新版协议虚构 MCP session；旧协议
session 由 SDK 管理。

Client 构造不注册 roots、sampling 或 elicitation 回调，因此声明的相关客户端能力为空。
日志、进度和工具变化通知由 SDK 安全解析；YCode 不据此更新工具目录。

### `ycode/mcp/manager.py`

**职责：** 创建所有状态项，并发启动已启用连接，适配和注册工具，汇总状态并幂等关闭。

`start()` 使用 `asyncio.TaskGroup` 并发启动所有有效且已启用 Server。每个 Server 的完整
连接、协商和分页发现受自身 `startup_timeout_seconds` 限制。分页读取直到
`next_cursor is None`，重复 cursor 使该 Server 发现失败，避免无限循环。

一页请求失败不注册不完整目录。各任务结果回到配置顺序后再做名称处理和注册，因此异步
完成顺序不影响 Registry 和模型工具顺序。

单个 Server 失败转成 `UNAVAILABLE`，不从 `start()` 抛出全局错误。全部 Server 失败时
仍返回状态报告并继续使用内建工具。

`close()` 原子进入关闭状态、拒绝新调用和重连、取消并等待在途调用、并发关闭所有 Client
和 HTTP Client，并保留最终脱敏状态快照。重复调用等待同一关闭过程，不重复释放资源。

### `ycode/mcp/tool.py`

**职责：** 包装远端工具、调用所属连接并把 `CallToolResult` 转换成
`ToolExecutionResult`。

结果转换规则：

- `TextContent` 按远端顺序合并。
- `structuredContent` 格式化为可读 JSON，并保存在
  `metadata.structured_content`。
- 图片和音频只输出类型与 MIME 摘要。
- Resource、ResourceLink 和其他未支持内容只输出安全的类型、URI/MIME 摘要。
- 不对 SDK 内容对象整体执行 `model_dump()`，防止 Base64 或正文意外进入结果。
- 文本和结构化结果同时存在时均保留，使用固定分隔格式。
- 最终文本和结构化元数据经过 `SecretRedactor`。

稳定错误分类：

| 情况 | 错误码 |
|---|---|
| 本地参数不符合 Schema | `invalid_arguments` |
| `CallToolResult.is_error` | `mcp_tool_error` |
| JSON-RPC/MCP 协议错误 | `mcp_protocol_error` |
| 连接或子进程中断 | `mcp_connection_error` |
| 工具调用超时 | `mcp_timeout` |
| 返回结构不能安全转换 | `mcp_invalid_result` |

### `ycode/tools/exposure.py` 与 `builtin/tool_search.py`

**职责：** 管理当前用户任务的延迟工具可见集合并提供本地 ToolSearch。

Agent 模式的 `searchable_names` 是所有有效延迟工具。plan-only 则取有效延迟工具与
`plan_only.allow_mcp_tools` 的交集。名称稳定排序。

ToolSearch 对输入去重并排序，返回 `loaded`、`already_loaded` 或 `not_found`。不存在和
当前模式不可搜索使用相同的 `not_found`，不暴露被模式隐藏的目录。描述折叠换行并最多
保留 160 个字符，结果不包含 Schema。

### `ycode/tools/registry.py`

**职责：** 保持完整、稳定注册目录，并按本轮暴露集合生成模型定义。

```python
def definitions(
    self,
    allowed_access: frozenset[ToolAccess] | None = None,
    *,
    exposed_deferred: frozenset[str] = frozenset(),
) -> tuple[ToolDefinition[Any], ...]: ...
```

非延迟工具继续按 `allowed_access` 过滤。延迟工具只有在 `exposed_deferred` 中才返回。
plan-only 白名单中的 MCP 工具由 `ToolExposureSession` 明确传入，即使分类仍为 UNKNOWN
也可以暴露。返回顺序始终使用 Registry 注册顺序。

### `ycode/agent/loop.py`

**职责：** 创建任务级暴露状态、生成 MCP reminder、每轮刷新定义，并在权限前执行
隐藏工具防绕过检查。

每轮模型请求前执行：

```python
definitions = registry.definitions(
    allowed_access,
    exposed_deferred=exposure.exposed_names(),
)
advertised_names = frozenset(item.name for item in definitions)
```

收到模型工具批次后，使用该请求开始时的 `advertised_names` 快照检查所有延迟工具。
未在快照中的调用预先生成 `tool_not_discovered`，跳过权限审批并交给 Scheduler 作为拒绝
结果。即使同批次 ToolSearch 已修改发现集合，也必须等下一次模型请求才生效。

任务完成、失败、取消或达到轮数限制时，在 `finally` 中清空并丢弃暴露状态。

### `ycode/prompt/runtime.py`

**职责：** 增加 `TOOL_CATALOG` request supplement。

存在可搜索 MCP 工具时列出稳定排序的名称和使用 ToolSearch 的最小说明，不包含描述或
Schema。该内容在整个用户任务中保持不变；工具激活后也不从 reminder 删除。没有可搜索
工具时不增加该 supplement。

### `ycode/security/config.py`

**职责：** 加载 plan-only MCP 白名单，并区分暂时不可用 MCP 引用与普通配置错误。

- 已注册工具正常校验工具名和参数字段。
- 未注册 `mcp_*` 工具保留规则并产生警告；因没有 Schema，暂不检查参数字段。
- 未注册的非 MCP 工具继续阻止启动。
- 已注册 MCP 工具引用未知顶层参数字段时阻止启动。
- plan-only 白名单只接受完整、精确的 `mcp_*` 名称。
- 白名单引用暂时不可用工具时产生警告。
- 非法名称和重复白名单项阻止启动。

### `ycode/security/engine.py`

**职责：** 在现有硬安全判定中加入 plan-only MCP 特例。

```python
async def evaluate(
    self,
    call: ToolCallBlock,
    session: PermissionSession,
    *,
    allowed_access: frozenset[ToolAccess],
    plan_only: bool,
) -> PermissionDecision: ...
```

只有已注册、已通过 ToolSearch 暴露、位于本地 plan-only 白名单且分类为 UNKNOWN 的延迟
工具可以绕过普通 `allowed_access={READ}` 的立即拒绝。项目 DENY 规则仍可拒绝；项目
ALLOW、会话授权和 `/permission allow` 均降为本次 ASK。每次调用都重新审批并保持串行。

### `ycode/session/chat.py` 与 UI

**职责：** `ChatSession` 接受可选 `McpStatusProvider`，精确、大小写不敏感地识别
`/mcp`。

```text
/mcp       → 状态命令
/MCP       → 状态命令
/mcp xxx   → 普通用户消息
```

命令产生 `UserMessageEvent` 和 `McpStatusEvent`，不创建 AgentTurn、不调用 Provider、
不修改历史、不连接或发现 Server。没有状态提供者时返回 `mcp_unavailable`，仍不调用
模型。

UI 按配置顺序显示 Server、Transport、State、Tools 和 Recent error。启动时只在配置中
存在 MCP 条目时显示“可用/失败/未启用”统计及脱敏警告。InputBox 根据
`PermissionDecision.allow_session` 决定显示两个还是三个审批选项。

### `ycode/app.py`

**职责：** 按依赖顺序装配组件，并保证任何中途异常都关闭已创建资源。

Anthropic 路径顺序：

```text
发现配置和项目根
→ 读取 .env 并加载活动 Provider/MCP 配置
→ 创建 Provider
→ 创建六个内建工具 Registry
→ 存在 MCP 条目时注册 ToolSearch 并创建 McpManager
→ 并发发现和注册 MCP 工具
→ 加载 security.yaml 与 MCP 警告
→ 创建 PermissionEngine、Executor、Scheduler
→ 创建 AgentLoop、ChatSession、TerminalUI
→ 显示启动摘要并运行
```

如果未配置 MCP，不创建 Manager、不注册 ToolSearch、不增加 reminder 或启动摘要，现有
六工具行为不变。配置存在但全部禁用时创建状态 Manager 和 ToolSearch，但不建立连接。

OpenAI 路径继续使用 PlainChatRunner，不加载或启动 MCP，不新增 OpenAI 工具转换和验收
逻辑。

## 模块交互

### 启动与发现

```text
run_app
  ├── discover_config / resolve_project_root
  ├── load_config
  │     ├── load .env（Anthropic）
  │     ├── validate active provider
  │     └── validate each MCP entry
  ├── create_builtin_registry
  ├── register ToolSearch（存在 MCP 条目）
  ├── McpManager.start
  │     ├── 并发 McpConnection.start
  │     ├── Client(mode="auto")
  │     ├── 分页 list_tools
  │     ├── Schema 编译和名称规范化
  │     └── 稳定顺序注册 MCPToolWrapper
  ├── load_security_config
  └── AgentLoop / ChatSession / TerminalUI
```

### 延迟发现与调用

```text
用户任务开始
→ 新建 ToolExposureSession
→ 生成固定 MCP 名称 reminder
→ 首轮只发送内建工具和 ToolSearch
→ 模型调用 tool_search
→ 本地 Registry 查询并激活名称
→ ToolResult 只返回名称、短描述和状态
→ 下一模型轮重新生成 definitions
→ 新激活工具的完整 Schema 进入工具列表
→ 模型调用 mcp_* 工具
→ advertised_names 快照检查
→ JSON Schema 参数校验
→ 权限审批
→ UNKNOWN 串行调度
→ McpConnection.call_tool
→ 结果转换、脱敏并返回模型
→ 任务终止后清空暴露状态
```

### 同批次防绕过

```text
模型同批返回：
  1. tool_search(mcp_remote_search_lookup)
  2. mcp_remote_search_lookup(...)

AgentLoop 使用本次模型请求开始时的 advertised_names：
  1. ToolSearch 正常执行并激活工具
  2. MCP 调用返回 tool_not_discovered，不进入权限和远端调用

下一模型请求才包含该 Schema。
```

### 断线与重连

```text
READY 连接发送 tools/call
→ 连接中断且结果不确定
→ 当前调用返回 mcp_connection_error
→ 不重连、不重发当前调用
→ 状态变为 DISCONNECTED

下一次独立工具调用
→ 发现 DISCONNECTED
→ 新建 Client(mode="auto") 并协商
→ 重连成功后只发送这次新调用
→ 不重新 list_tools，不修改 Registry
```

如果连接在空闲期间已经失效但客户端无法预知，首次使用旧连接的调用仍可能失败。该调用
不能自动转移到新连接，以避免重复副作用。重连失败后保持 DISCONNECTED，下一次新的
调用可以再触发一次重连。

### 取消与关闭

每个在途调用保存 SDK task 和 completion future。用户取消或 Executor 超时时，取消对应
SDK task、等待取消传播，并保证 future 只有一个终态。迟到结果不进入 Agent 历史。

退出顺序：

```text
ChatSession 取消并等待当前 AgentTurn
→ AgentLoop.close
→ McpManager 拒绝新调用
→ 取消并等待在途 MCP 调用
→ 并发退出 Client 上下文
→ 关闭 HTTP Client / stdio 子进程
→ Provider.close
```

## 文件组织

```text
ycode/
├── config/
│   ├── discovery.py
│   ├── environment.py
│   ├── loader.py
│   ├── mcp.py
│   └── models.py
├── mcp/
│   ├── __init__.py
│   ├── models.py
│   ├── naming.py
│   ├── connection.py
│   ├── manager.py
│   └── tool.py
├── tools/
│   ├── arguments.py
│   ├── contracts.py
│   ├── exposure.py
│   ├── executor.py
│   ├── registry.py
│   └── builtin/
│       └── tool_search.py
├── security/
│   ├── models.py
│   ├── config.py
│   └── engine.py
├── agent/
│   ├── events.py
│   └── loop.py
├── prompt/
│   ├── models.py
│   └── runtime.py
├── session/
│   └── chat.py
├── ui/
│   ├── mcp_status.py
│   ├── input_box.py
│   └── terminal.py
└── app.py

tests/
├── support/
│   ├── mcp_stdio_server.py
│   └── mcp_http_server.py
├── unit/
│   ├── config/
│   ├── mcp/
│   ├── tools/
│   ├── security/
│   ├── agent/
│   ├── session/
│   └── ui/
├── integration/
│   ├── test_mcp_stdio.py
│   ├── test_mcp_http.py
│   ├── test_mcp_protocol_fallback.py
│   └── test_mcp_agent_flow.py
└── e2e/
    └── test_terminal_chat.py

.env.example
.ycode/config.example.yaml
README.md
pyproject.toml
```

## 依赖

`pyproject.toml` 新增：

```toml
"mcp>=2,<3"
"httpx2>=2.5.0"
"jsonschema>=4.20.0"
"python-dotenv>=1.0.0"
```

YCode 会直接导入 `httpx2` 和 `jsonschema`，因此即使它们也是 SDK 依赖，也在本项目中
显式声明。不安装 `mcp[cli]`，运行时不需要 MCP CLI、Typer 等能力。

## 验证设计

### 单元测试

- `.env` 语法、系统环境优先、不修改 `os.environ` 和敏感值脱敏。
- `enabled: false` 跳过变量解析、连接和注册。
- MCP 配置逐项失败隔离、非法字段和重复名称。
- JSON Schema 编译、本地 `$ref`、外部 `$ref` 禁止和参数错误。
- Pydantic 内建工具参数行为不回归。
- 名称规范化、冲突双方排除和稳定顺序。
- 文本、结构化 JSON、图片/音频摘要及 Base64 排除。
- ToolSearch 激活、重复激活、不可见名称和跨任务清空。
- 同批次搜索并调用返回 `tool_not_discovered`。
- plan-only 默认不可搜索、白名单可搜索、每次强制审批且无会话授权选项。
- `/mcp` 不创建 AgentTurn、不调用 Provider、不写历史。
- Manager 状态变化、Server 隔离、调用取消和重复关闭。

### 集成测试

- stdio 子进程连接、分页发现、调用、取消和关闭。
- Streamable HTTP 普通 JSON 与请求级 SSE。
- 2026-07-28 直接连接和 2025-11-25 自动回退。
- 多 Server 并发启动及单 Server 失败。
- 重复分页 cursor 和损坏响应。
- 并发 JSON-RPC 响应按 ID 匹配，迟到和未知响应不污染调用。
- 当前调用断线失败，下一独立调用重连且不重新发现。
- ToolSearch → 下一轮 Schema → 审批 → MCP 调用的完整 Agent 流程。

### Windows 真实交互测试

使用现有 Anthropic 测试 Server 和本地 stdio MCP Server，在真实 PTY 中覆盖：

```text
启动 → MCP 摘要 → /mcp → 用户任务 → ToolSearch → 下一轮 Schema
→ 本次审批 → MCP 调用 → 最终回答 → /exit 后子进程退出
```

2026-07-28 与 2025-11-25 各至少有一个完整自动化端到端场景。当前阶段不新增 OpenAI
MCP 或工具调用测试。

最终验证命令：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
```

## Spec 覆盖

| 需求 | 设计归属 |
|---|---|
| F1 配置与 `.env` | `config/environment.py`、`config/mcp.py` |
| F2 两种传输 | `mcp/connection.py` |
| F3 协议自动兼容 | SDK `Client(mode="auto")` |
| F4 启动发现与固定目录 | `mcp/manager.py` |
| F5 名称适配 | `mcp/naming.py` |
| F6 统一工具适配 | `tools/arguments.py`、`mcp/tool.py` |
| F7 延迟 Schema 暴露 | `tools/exposure.py`、`agent/loop.py` |
| F8 任务级可见状态 | `ToolExposureSession` |
| F9 权限与 plan-only | `security/config.py`、`security/engine.py` |
| F10 超时与取消 | `mcp/connection.py`、`tools/executor.py` |
| F11 连接复用与恢复 | `mcp/connection.py` |
| F12 降级启动 | `mcp/manager.py`、`app.py` |
| F13 状态查询与关闭 | `mcp/manager.py`、`session/chat.py`、UI |

F1 至 F13 均有明确组件归属。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| JSON-RPC 实现 | 官方 MCP SDK 2.x | 复用协议协商、ID 匹配、SSE 和取消实现 |
| 多 Server | 每个 Server 独立 Client | 支持新版优先和旧版回退，并隔离故障 |
| Server 开关 | `enabled` 默认 true | 保持配置直观，禁用时完全不激活 |
| 工具目录 | 启动时固定在内存 | 满足确定性、缓存和不热更新要求 |
| 延迟状态 | 每个用户任务独立 | 防止会话累计，无需 LRU |
| ToolSearch | 精确名称、本地查询 | 确定性强，不意外批量激活，不产生网络请求 |
| 参数模型 | Pydantic/JSON Schema 统一适配器 | 保留内建强类型，同时完整保留远端 Schema |
| `$ref` | 只允许本地引用 | 避免隐式网络请求、SSRF 和本地文件读取 |
| MCP 权限 | 固定 UNKNOWN | 不信任远端安全 annotations |
| plan-only | 本地白名单且每次 ASK | 允许受控调查但不放松审批边界 |
| 断线 | 当前调用失败，后续调用重连 | 避免结果不确定的副作用被重复执行 |
| `.env` | 系统环境优先，不修改进程环境 | 限制秘密传播和子进程继承范围 |
| OpenAI | 当前阶段不接入 | 遵循已批准范围并避免未验证的供应商适配 |

## 官方参考

- [MCP Python SDK：Client transports](https://py.sdk.modelcontextprotocol.io/client/transports/)
- [MCP Python SDK：Protocol versions](https://py.sdk.modelcontextprotocol.io/protocol-versions/)
- [MCP Python SDK：Session groups](https://py.sdk.modelcontextprotocol.io/client/session-groups/)
- [MCP Python SDK：Client callbacks](https://py.sdk.modelcontextprotocol.io/client/callbacks/)
- [MCP Python SDK pyproject](https://github.com/modelcontextprotocol/python-sdk/blob/main/pyproject.toml)
