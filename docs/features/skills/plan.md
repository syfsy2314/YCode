# YCode Agent Skills Plan

> 状态：修订已批准（2026-08-10）

## 架构概览

采用“统一协调器 + 专职组件”的结构：

- `SkillCatalog` 负责扫描、解析、校验和事务式 reload，保存可用与不可用 Skill 的确定性目录。
- `SkillRuntime` 负责共享 Skill 激活状态、调用栈、嵌套深度、工具可见性和任务级预批准生命周期。
- `IsolatedSkillRunner` 复用现有 Anthropic Agent 能力执行隔离 Skill，根据 `summary`、`recent`、`none` 构造临时上下文，只返回最终交接结果。
- `SkillSourceResolver` 负责识别直接 ZIP、skills.sh、GitHub tree 和原始 `SKILL.md`
  来源，并把它们统一解析为单 Skill 暂存目录。
- `SkillInstaller` 负责统一的公开 HTTPS 校验、容量限制、来源构造、Skill 校验和原子安装。
- `load_skill`、`install_skill` 作为薄工具层调用上述服务，不自行维护业务状态。
- 动态 Slash Command 扩展现有 `CommandRegistry`，由 Skill 目录生成命令视图，内置命令始终优先。
- `ChatSession` 继续作为对话事务边界，协调显式 Skill 调用、`/skills`、`/clear`、恢复和持久化，但不承担 Skill 文件解析或安装细节。
- Prompt 层把未激活 Skill 的名称与说明作为简要目录注入；共享 Skill 快照作为独立的会话补充参与每轮 Prompt，不进入可压缩历史。
- 权限层在现有 `PermissionEngine` 之前叠加任务级预批准判断，最终仍受 plan-only、安全拒绝、工作区和执行器检查约束。
- 核心 Skill 模型保持 Provider 无关；隔离执行与模型配置解析仅接入 Anthropic 路径，不修改 OpenAI Provider。

主要调用链：

```text
启动
  → SkillCatalog.scan()
  → 更新动态命令和 Skill 简要目录
  → ChatSession 等待任务

显式命令或 load_skill
  → 调用时重读并校验
  → SkillRuntime 建立调用作用域
  ├─ shared：更新会话激活快照 → 主 Agent 继续执行
  └─ isolated：构造临时上下文 → IsolatedSkillRunner → 返回最终交接结果
  → 清除本任务预批准与调用栈

reload / install / restore
  → 先生成完整候选状态
  → 校验成功后一次性替换目录、命令视图和激活状态
```

该划分使文件校验、运行状态、隔离 Agent、安装安全和会话事务分别保持单一职责，避免把大量 Skill 分支直接放入 `ChatSession.stream_reply()`。

## 核心数据结构与接口

### 枚举

```python
class SkillExecutionMode(StrEnum):
    SHARED = "shared"
    ISOLATED = "isolated"


class SkillContextKind(StrEnum):
    CURRENT = "current"
    SUMMARY = "summary"
    RECENT = "recent"
    NONE = "none"


class SkillInvocationSource(StrEnum):
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"
    NESTED = "nested"


class SkillProblemSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
```

### SkillConfig

```python
@dataclass(frozen=True, slots=True)
class SkillConfig:
    execution_mode: SkillExecutionMode
    model_name: str | None
    context_kind: SkillContextKind
    recent_turns: int | None
    visible_tools: frozenset[str] | None
    allowed_tools: frozenset[str]
    argument_hint: str
```

- `visible_tools=None` 表示继承当前模式原本可见工具；非空集合表示显式白名单。
- 共享模式固定使用 `CURRENT`，不得设置模型或最近回合数。
- 隔离模式必须使用 `SUMMARY`、`RECENT` 或 `NONE`。
- 工具名称在解析阶段完成标准名称到 YCode 名称的映射，运行期只处理规范化后的名称。

### SkillSnapshot

```python
@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    metadata: Mapping[str, str]
    root: Path
    source_path: Path
    instructions: str
    config: SkillConfig
    fingerprint: str
```

`SkillSnapshot` 是一次完整校验后的不可变快照。共享激活和隔离执行都使用快照，避免磁盘文件在回合中途改变行为。

### SkillCatalogEntry

```python
@dataclass(frozen=True, slots=True)
class SkillProblem:
    code: str
    message: str
    severity: SkillProblemSeverity


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    directory_name: str
    source_path: Path
    snapshot: SkillSnapshot | None
    problems: tuple[SkillProblem, ...]

    @property
    def available(self) -> bool: ...
```

存在 error 级问题的 Skill 不可用；只有 warning 的 Skill 仍可用。所有目录条目都保留，
供 `/skills` 和 `/skills show` 展示原因或降级警告。

### 调用期模型

```python
@dataclass(frozen=True, slots=True)
class SkillInvocation:
    name: str
    arguments: str | None
    source: SkillInvocationSource


@dataclass(frozen=True, slots=True)
class SkillCallFrame:
    snapshot: SkillSnapshot
    visible_tools: frozenset[str] | None


@dataclass(slots=True)
class SkillTaskScope:
    mode: AgentMode
    active_before_turn: Mapping[str, SkillSnapshot]
    pending_shared: dict[str, SkillSnapshot]
    call_stack: list[SkillCallFrame]
    preapproved_tools: set[str]


@dataclass(frozen=True, slots=True)
class SkillCallResult:
    name: str
    execution_mode: SkillExecutionMode
    activated: bool
    final_handoff: str | None
```

`SkillInvocation.arguments=None` 明确表示没有参数；非 `None` 时保持原始大小写和内部空格。

每个普通 Agent 任务拥有独立 `SkillTaskScope`：

- `call_stack` 检测循环和第四层调用。
- `preapproved_tools` 只收集本任务实际调用链贡献的工具。
- `pending_shared` 让新激活 SOP 在当前回合后续模型轮次立即生效。
- 任务成功后提交共享状态；失败或取消时丢弃候选状态并清除预批准。

共享模式的 `SkillCallResult` 返回激活结果；隔离模式额外返回最终交接文本。

### 发现与加载接口

```python
class SkillLoader:
    def load(
        self,
        source_path: Path,
        environment: SkillValidationEnvironment,
    ) -> SkillCatalogEntry: ...


class SkillCatalog:
    @property
    def entries(self) -> tuple[SkillCatalogEntry, ...]: ...

    def get_available(self, name: str) -> SkillSnapshot | None: ...
    def scan_candidate(self) -> SkillCatalogState: ...
    def commit(self, candidate: SkillCatalogState) -> None: ...
    def reload_one(self, name: str) -> SkillCatalogEntry: ...
```

`SkillLoader` 负责读取 frontmatter、校验标准字段、解析 YCode 扩展、映射工具名称，并生成快照或诊断。

全量 reload 先建立完整候选目录，成功后一次替换。`reload_one()` 只用于调用时重读已有目录，不发现新增、删除或重命名。条目和可用索引均按规范化名称稳定排序。

### 运行接口

```python
class SkillRuntime:
    def begin_task(self, mode: AgentMode) -> SkillTaskScope: ...

    async def invoke(
        self,
        invocation: SkillInvocation,
        scope: SkillTaskScope,
    ) -> SkillCallResult: ...

    async def commit_task(self, scope: SkillTaskScope) -> None: ...
    def discard_task(self, scope: SkillTaskScope) -> None: ...
    async def deactivate(self, name: str) -> None: ...
    async def restore(self, names: Sequence[str]) -> tuple[str, ...]: ...
```

`invoke()` 统一处理显式、自动和嵌套调用，包括调用时重读、审批、循环检查、工具策略及共享/隔离分派。

`commit_task()` 遵守现有“先落盘、后提交内存”边界；取消、失败或会话写入失败时不更新正式激活状态。

### 隔离执行接口

```python
@dataclass(frozen=True, slots=True)
class IsolatedSkillContext:
    history: tuple[ChatMessage, ...]
    summary: ConversationMemory | None
    user_task: ChatMessage


class IsolatedSkillRunner:
    async def run(
        self,
        snapshot: SkillSnapshot,
        context: IsolatedSkillContext,
        mode: AgentMode,
        parent_scope: SkillTaskScope,
    ) -> SkillCallResult: ...

    def cancel(self) -> None: ...
```

隔离运行器创建临时 Anthropic `AgentLoop`，但不创建 `SessionManager`，因此隔离消息不会写入会话文件。子 Agent 与父任务共享取消信号和调用链，拥有独立消息历史及工具曝光状态。

### 会话持久化模型

新增版本化记录：

```python
@dataclass(frozen=True, slots=True)
class SkillStateRecord:
    session_id: str
    covered_turn_id: str
    timestamp: datetime
    active_skill_names: tuple[str, ...]
```

`SessionSnapshot` 增加：

```python
active_skill_names: tuple[str, ...]
```

只持久化稳定排序的共享 Skill 名称，不保存 SOP、隔离历史、调用栈或预批准权限。恢复时始终重新读取当前磁盘文件。

## 模块设计

### `ycode/skills/models.py`

集中定义 Skill 枚举、快照、目录条目、调用请求、任务作用域和诊断模型，不包含文件或网络操作。

为使 Skill 状态与已提交回合关联，持久化记录补充 `covered_turn_id`：

```python
@dataclass(frozen=True, slots=True)
class SkillStateRecord:
    session_id: str
    covered_turn_id: str
    timestamp: datetime
    active_skill_names: tuple[str, ...]
```

任务内激活记录与对应 `TurnCommitRecord` 一起写入；独立停用操作引用当前最后一个已提交回合。加载器只应用已提交回合对应的状态。

### `ycode/skills/loader.py`

负责单个 `SKILL.md`：

- 严格识别文件开头的 YAML frontmatter。
- 校验标准字段类型、长度、名称规则和父目录一致性。
- 要求 `metadata` 的键和值均为字符串。
- 解析 YCode 扩展和字段组合。
- 解析空格分隔的 `allowed-tools`；不支持的参数级表达式生成 warning 并且不授权，可识别的
  普通工具名称继续参与预批准。
- 映射标准工具名称并校验工具、模型及内置命令冲突。
- 计算内容指纹并生成不可变快照。
- 将读取或校验错误转换为安全、稳定的 `SkillProblem`。

本期采用 Spec 要求的严格校验，不采用 Agent Skills 实现指南中可选的宽松修复策略。

### `ycode/skills/catalog.py`

只扫描项目根目录的 `.ycode/skills/*/SKILL.md`：

- 使用排序后的直接子目录，不递归搜索其他位置。
- 先解析所有候选，再统一检测规范化名称冲突。
- 冲突双方都标记不可用。
- 全量 reload 构造完整 `SkillCatalogState` 后一次替换。
- 调用时重读只更新指定的既有条目；失败时返回错误，但保留正式目录和有效激活快照。
- 提供稳定的模型简要目录、管理命令视图和动态命令定义。

### `ycode/skills/runtime.py`

作为共享状态与单次任务状态的协调器：

- 正式共享状态保存已提交的 `SkillSnapshot`。
- 每次 Agent 任务创建 `SkillTaskScope`。
- 调用前通过目录定位已有 Skill，再从磁盘重读。
- 自动或嵌套调用带 `allowed-tools` 的 Skill 时发起审批。
- 显式 Slash Command 直接记录本任务预批准。
- 检测调用栈循环和最大深度。
- 计算当前轮次可见工具与预批准集合。
- 共享激活先进入任务候选状态；任务成功并持久化后才替换正式状态。
- 失败、取消或存储失败时丢弃任务候选及预批准。
- reload、停用、恢复均使用候选状态后一次提交。

多个共享 Skill 的工具白名单按并集计算；任一 Skill 未声明白名单时，该 Skill 贡献当前模式的原始可见集合。

### Prompt 与 `AgentLoop`

`SupplementKind` 增加 Skill 目录和 Skill 指令类型。所有共享 SOP 聚合成一个稳定排序的补充，避免现有 `PromptRuntimeContext` 按类型唯一存储时相互覆盖。

`AgentLoop` 在每个模型轮次前重新取得当前 Skill 目录、正式及本任务新激活的共享 SOP、当前工具可见集合和本任务预批准集合。因此 `load_skill` 在某轮执行后，新 SOP 和工具策略能在同一 Agent 任务的下一轮立即生效。上下文压缩只处理消息历史，不处理这些运行时补充。

### `ycode/skills/context.py`

提供隔离上下文构造：

- `summary` 使用现有 `ConversationCompactor`，基于当前摘要和全部已提交历史生成临时最新摘要，但不提交到主 `ContextManager`。
- `recent` 从已提交历史尾部提取最近 N 个完整用户回合；工具调用和对应结果作为所属回合整体保留。
- `none` 不传入先前历史。
- 三种策略都追加当前触发任务的原始内容。

回合边界以普通用户文本消息开始；只包含工具结果的 user-role 消息仍属于前一个回合。

### `ycode/skills/isolated.py`

`IsolatedSkillRunner` 创建临时 Anthropic `AgentLoop`：

- 当前模型或 `ycode-model` 指定的已有 Anthropic 配置创建独立 Provider。
- 复用工具注册表、调度器、安全配置和工作区解析器。
- 使用独立 Prompt 运行状态、工具曝光和消息历史。
- 继承父任务模式、调用栈与取消信号。
- 不接入主会话 `SessionManager`、主 `ContextManager` 或项目记忆更新。
- 只提取正常完成时的最终 Assistant 文本作为交接结果。
- 无论成功、失败或取消，都关闭临时 Provider 并清除任务授权。

命名 Provider 的解析复用配置加载逻辑，仅校验被引用的已有 Anthropic 条目；不修改 OpenAI Provider。

### `load_skill`、`install_skill` 与权限

- `load_skill` 为读取类工具，在 agent 和 plan-only 中始终可见。
- `install_skill` 为写入类工具，在 plan-only 中不暴露，并始终要求人工审批。
- `ToolContext` 增加当前 `SkillTaskScope` 引用，使嵌套调用沿用同一调用链。

权限顺序为：

```text
模式与工具可见性
  → 工作区及命令安全检查
  → security.yaml 明确拒绝
  → Skill 任务级预批准
  → 普通权限模式审批
```

预批准只能把原本需要询问的决定变为允许，不能覆盖任何拒绝。`install_skill` 的强制审批不能被全局 allow 或 Skill 预批准跳过。

### `ycode/skills/installer.py`

安装工具参数命名为 `source_url`。安装器先按 URL 形态选择来源解析器：

- `skills.sh/<owner>/<repo>/<skill>`：解析明确的仓库与 Skill slug，通过公开 GitHub
  元数据定位唯一匹配的 Skill 目录，再下载该目录文件；不调用 skills.sh 搜索 API。
- `github.com/<owner>/<repo>/tree/<ref>/<path>`：解析公开仓库、ref 和目录路径。ref 含斜杠
  时，通过公开 GitHub ref 元数据从最长候选开始解析，剩余部分作为目录路径。
- 路径以 `SKILL.md` 结尾的其他公开 HTTPS URL：作为原始单文件来源，以 frontmatter
  `name` 创建暂存顶层目录，只写入该文件。
- 其他公开 HTTPS URL：作为直接 ZIP 来源，继续应用唯一顶层目录规则。

GitHub 目录下载使用公开、无认证的 GitHub 元数据和文件地址，只递归目标 Skill 目录。
目录条目若为 symlink 或 submodule 则拒绝；skills.sh 来源若找不到唯一匹配目录也拒绝。
不执行 Git、不运行 `npx`，也不下载或安装整个仓库。

所有解析器共用可注入的异步 HTTP 客户端和以下安全边界：

- 拒绝非 HTTPS、URL 凭据及非公开目标地址。
- 对用户 URL、每次重定向、API 地址和解析出的文件下载地址重复验证。
- 流式下载并按本次安装累计字节数，超过 30 MB 时立即取消。
- 在临时目录逐项检查 ZIP，不调用无条件批量解压。
- 拒绝绝对路径、`..`、多个顶层目录、符号链接和重解析点。
- 同时限制 ZIP 声明大小与实际写入总量。
- GitHub 文件路径和 API 返回结构也执行相同的相对路径、链接和累计容量校验。
- 完整校验 `SKILL.md` 后，将暂存目录放到 `.ycode/skills/` 同一文件系统，再以原子重命名安装。
- 目标已存在时拒绝；失败或取消时清理暂存内容。
- 安装完成后事务式刷新目录和动态命令，但不激活 Skill。

### 命令与 UI

`CommandRegistry` 增加原子替换动态定义的能力，保持同一个 Registry 实例，使现有帮助和补全对象无需重建。

新增 `/skills`、`/skills show <name>`、`/skills deactivate <name>`、`/skills reload`、`/clear` 及每个可用 Skill 对应的动态 `/<name>`。

动态 Skill 命令使用 `CommandKind.AI`，但通过新增的 `UIController.invoke_skill()` 进入 `ChatSession`，从而区分共享与隔离执行。终端先显示原始命令；主会话只保存展开后的任务。

### 会话与 `/clear`

`SessionManager.commit_turn()` 接受候选共享 Skill 名称，并把 `SkillStateRecord` 放入同一次可回滚追加中。磁盘成功后，`ChatSession` 才提交历史、上下文和 Skill 正式状态。

`/clear` 由 `ChatSession.clear()` 统一执行：

- 活动任务期间拒绝运行。
- 调用 `SessionManager.begin_new()`。
- 清空历史、摘要、共享 Skill 和临时授权。
- 模式重置为 agent，并重置 Prompt 模式状态。
- 保留目录、MCP、项目记忆和权限配置模式。

恢复会话时，先构造历史、摘要和 Skill 恢复候选；全部必要存储检查成功后再一次替换当前状态。失效 Skill 只产生告警并从候选激活集合移除。

### 启动装配

Anthropic 启动路径按以下顺序装配：

```text
内置命令与基础工具
  → MCP 与 Provider 配置视图
  → SkillLoader / SkillCatalog 初次扫描
  → load_skill / install_skill
  → 安全引擎
  → SkillRuntime / IsolatedSkillRunner
  → AgentLoop / ChatSession
  → 动态命令与终端补全
```

MCP 工具完成发现后触发一次依赖重校验，使引用 MCP 工具的 Skill 状态与实际注册表一致。OpenAI 路径保持现状，不扫描、不注册或执行 Skill。

## 模块交互

### 启动与发现

```text
run_app
  → 装配基础工具、MCP 配置和内置命令
  → SkillCatalog.scan_candidate()
  → 解析全部 Skill，并统一处理名称和命令冲突
  → 生成 Skill 简要目录及动态命令候选
  → 一次提交 Catalog、Prompt 目录和 CommandRegistry
  → 显示单个不可用 Skill 的启动告警
```

单个 Skill 无效属于正常扫描结果，不阻止目录提交；只有目录读取等扫描过程整体失败时保留旧状态。首次启动没有旧状态时，扫描整体失败只禁用 Skill 功能，不阻止普通 Anthropic 对话。

### 显式共享 Skill

用户提交 `/commit Fix parser whitespace handling` 时：

```text
CommandDispatcher
  → 显示原始 Slash Command
  → SkillRuntime 调用时重读 commit/SKILL.md
  → 创建 SkillTaskScope
  → 显式授权本次 allowed-tools
  → 将共享快照放入 pending_shared
  → 构造展开后的用户任务
  → 主 AgentLoop 执行
  → 会话回合与 SkillStateRecord 一起落盘
  → 提交历史、上下文和正式共享状态
```

展开后的任务采用稳定结构：

```text
Use the "commit" skill for this task.

Invocation arguments:
Fix parser whitespace handling
```

无参数时写为 `No arguments were provided.`。持久化的是展开后的用户任务，不保存第二份原始 Slash Command。SOP 作为系统补充发送，不拼接进用户参数。

### Agent 自动调用

```text
主 Agent 调用 load_skill(name, arguments)
  → 确认名称属于启动目录
  → 调用时重读并校验
  → 检查循环和深度
  → 若 allowed-tools 非空，发出审批事件
     ├─ 拒绝：返回工具错误，父 Agent 继续
     └─ 同意：记录本任务预批准
  → shared：加入 pending_shared，返回激活结果
  → 下一模型轮次重新计算 SOP 和工具集合
```

读取或校验失败时只返回稳定工具错误，不改变目录快照、激活状态或任务授权。

### 隔离 Skill

显式隔离调用直接由 `ChatSession` 启动临时运行器；自动隔离调用由 `load_skill` 工具等待临时运行器完成。

```text
SkillRuntime
  → 构造 summary / recent / none 上下文
  → 选择当前或已命名 Anthropic Provider
  → IsolatedSkillRunner.run()
  → 临时 AgentLoop 执行
  → 提取最终 Assistant 文本
  → 关闭临时 Provider 和资源
  → 返回 final_handoff
```

- 显式调用在主会话提交“展开后的用户任务 + 最终交接回复”。
- 自动或嵌套调用只向主 Agent 返回包含最终交接内容的 `load_skill` 工具结果。
- 隔离 Agent 的 Thinking、工具调用、工具结果和中间消息均不进入主历史。
- 隔离执行失败或取消时不提交主会话回合，也不创建可恢复子会话。

### 嵌套调用

每次进入 Skill 都压入一个调用帧。调用前若名称已在栈中则返回循环错误；栈已有三层时拒绝第四层。每层根据自己的配置计算可见工具。进入隔离子 Skill 时使用独立工具视图，返回后恢复父层视图。获批的 `allowed-tools` 加入当前顶层任务授权集合，任务结束统一清除。隔离子 Skill 的最终交接只返回直接父 Agent。

### 工具可见性与权限

每个模型轮次按以下顺序计算：

```text
当前模式的基础可见工具
  → 当前 Agent 所在 Skill 帧的可见白名单
  → 已激活共享 Skill 白名单的并集
  → plan-only 读取限制
  → 生成实际 ToolDefinition 列表
```

执行工具时：

```text
调用是否在本轮广告集合
  → 底层安全检查是否拒绝
  → 是否属于本任务已获批工具
  → 普通权限模式是否需要询问
  → ToolExecutor
```

未参与当前任务的历史共享 Skill 仍影响 SOP 和工具可见性，但不贡献预批准。

### 任务提交与失败

成功任务：

```text
AgentTurnResult(COMPLETED)
  → SessionManager.commit_turn(messages, checkpoint, active_skill_names)
  → 单次可回滚写入消息、检查点、Skill 状态和 TurnCommit
  → ContextManager.commit()
  → SkillRuntime.commit_task()
  → 更新 ChatSession.history
```

失败、取消、达到轮数上限或存储失败时调用 `SkillRuntime.discard_task()`，丢弃 `pending_shared` 并清空调用栈与预批准，不改变正式共享状态。

### reload、停用和恢复

`/skills reload` 先构造新目录、动态命令和激活状态候选，将失效或删除 Skill 从候选激活状态移除；必要时先持久化新激活名称，再一次替换 Catalog、Runtime、Prompt 和 CommandRegistry。

`/skills deactivate <name>` 先把新状态写入当前会话，再更新内存；没有活动持久化会话时只更新当前空会话状态。

恢复流程先由 `SessionManager` 和 `ContextManager` 构造历史与摘要候选，再按 `active_skill_names` 重读磁盘，跳过失效项并收集告警，最后一次替换历史、摘要、模式和共享 Skill。

### `/clear`

```text
确认当前没有活动任务
  → SessionManager.begin_new()
  → 清空 ChatSession.history
  → ContextManager 清空摘要和运行状态
  → SkillRuntime 清空共享状态与临时授权
  → PermissionSession 清空临时授权
  → 模式设为 agent
  → PromptRuntimeContext 重置模式
```

不调用模型、不写入命令历史，也不删除旧会话文件。

### 远程安装

```text
install_skill(source_url)
  → 强制审批
  → 验证公开 HTTPS 来源 URL
  → 识别 ZIP / skills.sh / GitHub tree / 原始 SKILL.md
  → 来源解析器受限下载并构造临时单 Skill 目录
  → 统一执行路径、链接、容量和来源结构检查
  → 校验唯一 Skill
  → 预构造新 Catalog 和动态命令候选
  → 原子重命名到 .ycode/skills/<name>
  → 提交 Catalog、Prompt 和 CommandRegistry
```

同名目标、来源无法识别或唯一定位、下载错误、取消、ZIP 校验失败、GitHub 目录含链接或
候选目录构造失败时，正式目录和运行状态均不变化。安装后即使 Skill 因工具、模型或兼容性
依赖而不可用，也作为不可用目录项提交。

## 文件组织

```text
ycode/
├── skills/
│   ├── __init__.py
│   ├── models.py          # 静态模型、诊断、调用与任务状态
│   ├── loader.py          # SKILL.md 解析和单项校验
│   ├── catalog.py         # 扫描、冲突处理和事务式目录
│   ├── runtime.py         # 激活、嵌套、工具策略和生命周期
│   ├── context.py         # summary/recent/none 上下文构造
│   ├── isolated.py        # 临时 Anthropic Agent
│   ├── installer.py       # 多来源 HTTPS Skill 安全安装
│   └── commands.py        # 管理命令和动态 Skill 命令
├── tools/builtin/
│   ├── load_skill.py
│   └── install_skill.py
├── agent/
│   ├── contracts.py       # Skill 感知的可选运行接口
│   └── loop.py            # 每轮刷新 SOP、工具和授权
├── commands/
│   ├── contracts.py       # UIController Skill 操作
│   ├── registry.py        # 原子替换动态命令
│   └── builtin.py         # /skills 和 /clear
├── config/
│   └── loader.py          # 解析被 Skill 引用的 Anthropic 配置
├── prompt/
│   ├── models.py          # Skill 补充类型
│   └── runtime.py         # Skill 目录和 SOP 聚合
├── security/
│   └── engine.py          # 预批准和强制审批规则
├── session/
│   ├── models.py          # SkillStateRecord、恢复快照
│   ├── codec.py           # Skill 状态记录编解码
│   ├── manager.py         # 事务写入与恢复
│   └── chat.py            # 显式调用、管理、clear
├── tools/
│   ├── contracts.py       # 当前 Skill 任务作用域
│   └── registry.py        # 注册两个 Skill 工具
├── ui/
│   └── terminal.py        # Skill 命令桥接与状态输出
├── mcp/
│   └── manager.py         # MCP 就绪后触发依赖重校验
└── app.py                 # Anthropic 路径整体装配

.ycode/skills/
├── commit/SKILL.md
├── review/SKILL.md
└── test/SKILL.md
```

测试文件：

```text
tests/
├── unit/skills/
│   ├── test_models.py
│   ├── test_loader.py
│   ├── test_catalog.py
│   ├── test_runtime.py
│   ├── test_context.py
│   ├── test_isolated.py
│   ├── test_installer.py
│   └── test_commands.py
├── unit/tools/
│   └── test_skill_tools.py
├── integration/
│   ├── test_skill_agent_flow.py
│   ├── test_skill_sessions.py
│   └── test_skill_install.py
└── e2e/
    └── test_terminal_chat.py
```

现有 Agent、命令、Prompt、权限、会话、UI 和应用装配测试同步扩展，不建立 OpenAI Skill 测试。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 标准解析 | `yaml.safe_load` 加显式字段校验 | 复用现有依赖，同时严格控制类型与扩展字段 |
| 解析兼容性 | 不自动修复损坏 YAML | 避免静默改变外部 Skill 的含义 |
| 参数级授权 | warning 并安全降级 | 保持标准 Skill 可运行，同时不扩大权限 |
| 快照标识 | 对完整文件字节计算 SHA-256 | 稳定识别热更新，不依赖文件时间 |
| 激活内容 | 系统补充携带 Markdown 正文及必要元数据 | SOP 与任务参数分离，随附资源仍按需读取 |
| 目录顺序 | 按规范化名称排序 | 保证 Prompt、命令、帮助和工具集合稳定 |
| 动态命令 | 原地原子替换动态定义 | 保持现有补全器持有的 Registry 引用有效 |
| 共享状态 | 成功回合落盘后提交 | 延续现有会话事务边界 |
| 会话格式 | 保留现有格式版本，增加可选记录类型 | 旧会话自然恢复为空 Skill 状态 |
| 隔离历史 | 临时内存状态，不创建 SessionManager | 防止内部消息被恢复或写入主历史 |
| 摘要策略 | 临时调用现有 ConversationCompactor | 保持摘要语义一致，但不改变主上下文 |
| 最近回合 | 按完整用户回合分组 | 不拆散工具调用与结果 |
| 命名模型 | 延迟校验已有 Anthropic 配置 | 未被引用的配置仍保持现有懒校验行为 |
| 网络客户端 | 复用 `httpx2`，通过协议注入测试替身 | 不增加依赖，支持取消和流式限额 |
| 来源解析 | 小型 URL 分类器与来源专用解析函数 | 只覆盖四种已确认来源，不引入市场或通用网页抓取框架 |
| GitHub 目录 | 公开元数据解析 ref 并递归目标目录 | 不执行 Git、不安装整个仓库，保留随附资源 |
| skills.sh | 从明确详情页提取 owner/repo/slug 后定位唯一目录 | 不使用搜索、推荐或需要认证的目录 API |
| 原始 SKILL.md | 用 frontmatter name 构造只含单文件的目录 | 不猜测未明确提供的相邻资源 |
| ZIP 解压 | 使用标准库逐项复制 | 能在写入前后执行路径、链接和容量检查 |
| 原子安装 | 在目标父目录暂存后原子重命名 | 保证同一文件系统内不会出现半安装目录 |
| MCP 依赖 | MCP 注册完成后事务式重校验 | 避免启动连接尚未完成时永久误判工具缺失 |
| 示例 Skill | 完全走外部目录和通用路径 | 证明不存在名称专用逻辑 |

### 任务作用域细化

每个 Agent 分支拥有自己的 `SkillTaskScope` 和 `pending_shared`。隔离分支复制调用栈，并共享顶层任务的预批准生命周期；隔离分支内激活的共享 Skill 只在该分支持续，结束后丢弃。只有主 Agent 分支的 `pending_shared` 可以写入主会话。

调用栈元素使用包含对应快照的 `SkillCallFrame`，保证每层使用调用时快照和自己的工具策略。这避免隔离 Skill 嵌套加载的共享 SOP 或工具状态泄漏进主会话，同时满足每层独立应用执行配置的要求。

### 实施与验证边界

- 只实现 Spec 明确要求的功能和失败行为，不增加面向未来的抽象。
- 只验证主要成功路径和 Spec 明确列出的核心失败路径。
- 四类来源共同验证 HTTPS、容量、路径越界、链接、单 Skill 结构和原子安装；GitHub 与
  skills.sh 使用 HTTP 替身覆盖目录定位、资源保留和歧义拒绝。
- 使用临时目录、HTTP 替身、假 Provider 和单一真实 PTY 场景。
- 取消只验证受控任务能够停止并清理状态。
- 不做压力测试、性能基准、长时间运行、大规模并发、复杂故障注入、DNS 重绑定攻防、恶意 ZIP 样本库、全面安全审计、多平台矩阵或真实付费 API。
- 不增加重试框架、遥测、缓存、后台任务治理或未确认的可靠性工程。
- 最终验证执行现有格式检查、静态检查、编译、完整测试及 Skill 核心端到端流程。

## 设计自检

- F1–F4 由 Loader、Catalog 和 Prompt 渐进加载覆盖。
- F5–F7 由 Runtime、隔离上下文及 Anthropic Provider 选择覆盖。
- F8–F15 由两个工具、动态命令、权限策略、分支作用域和嵌套流程覆盖。
- F16–F19 由命令、会话状态、`/clear` 和恢复事务覆盖。
- F20–F22 由安装器、目录候选和动态命令冲突处理覆盖。
- F23 由三个普通项目 Skill 覆盖。
- 未新增 OpenAI 适配、市场、版本管理、文件监视或参数级授权。
- `SkillStateRecord.covered_turn_id`、诊断严重级别和隔离分支状态已在接口与模块设计中保持一致。
- 模块依赖以 `SkillRuntime` 为协调边界，Loader、Catalog、Installer 和 Isolated Runner 不互相反向持有，未形成循环依赖。
