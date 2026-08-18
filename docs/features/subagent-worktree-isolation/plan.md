# 子 Agent Worktree 隔离技术设计

## 文档状态

- 对应 Spec：`docs/features/subagent-worktree-isolation/spec.md`
- 当前状态：已批准（2026-08-18）
- 实现范围：一次性交付已批准 Spec 的全部能力
- Provider 范围：仅 Anthropic；核心 Worktree 模块保持 Provider 无关
- 验证范围：功能性单元测试、集成测试和真实终端端到端测试，不做生产级验证

## 1. 设计目标与约束

本设计在现有子 Agent 系统上增加一个项目级 Worktree 管理层。主 Agent 和普通子 Agent 继续使用主仓库；只有定义式角色明确声明 `isolation: worktree` 时，系统才在任务启动前创建独立 Worktree，并为该任务装配一套工作区相关运行时。

核心约束如下：

1. 不调用 `os.chdir()`，也不依赖进程级当前目录。所有 Git、文件、搜索、命令、环境和 Hook 操作都显式接收工作区路径。
2. Worktree 生命周期先于子 Agent 生命周期：关键初始化全部成功后任务才进入 `running`，任一终态都先关闭子运行时，再检查并处置 Worktree。
3. Git 与磁盘记录共同构成受管身份。任何身份、占用、状态或 Hooks 结论不确定时保留现场。
4. 主 Agent、普通子 Agent、Fork、Skill、MCP、Session 和 OpenAI 的既有行为默认不变；隔离角色只获得 Spec 明确要求的例外。
5. 不自动提交、合并、变基、拣选、拉取、推送或访问网络。
6. 只做本机进程之间的功能性互斥和故障恢复，不引入分布式锁、持久化任务队列、后台维护服务或生产级竞态工程。

## 2. 总体架构

```text
Main ChatSession / Main AgentLoop
        │
        │ run_subagent(role=isolation: worktree)
        ▼
SubagentManager
        │  取得稳定 session_id + 分配 task_id
        ▼
WorktreeManager
├── WorktreeNaming        安全名称与可逆分支映射
├── WorktreeStore         磁盘记录与本地短时互斥
├── GitWorktreeClient     显式 -C 路径的 Git 操作
├── WorktreeInitializer   Hooks、复制、Glob、目录链接
└── WorktreeAccessGuard   活动目录访问保护
        │
        │ WorktreeLease
        ▼
SubagentWorkspaceFactory
├── 独立 Registry / Resolver / TextFileService
├── 独立 PermissionEngine / PermissionSession
├── 独立 ContextManager / 临时资源
├── Worktree 项目指令 + 主仓库只读 Memory
├── 独立 HookRuntime
└── 共享 Provider、MCP 连接及无路径状态工具
        │
        ▼
SubagentRunner → AgentLoop → 任一终态
        │
        ▼
WorktreeManager.finalize()
├── 无 commit、无工作区变更 → 删除 Worktree、分支和记录
└── 有变更或无法可靠判断      → retained，并返回 Git 摘要
```

`WorktreeManager` 是项目级唯一编排入口，`SubagentManager` 只负责把任务生命周期与 Worktree 生命周期连接起来。隔离运行时由 `SubagentWorkspaceFactory` 创建，避免把 Git、路径、权限、Hooks 和上下文装配继续堆入 `app.py` 或 `SubagentRunner`。

## 3. 配置与角色模型

### 3.1 Worktree 配置

在 `AppConfig` 中增加：

```python
class WorktreeConfig(BaseModel):
    copy_files: tuple[str, ...] = ()
    ignored_file_globs: tuple[str, ...] = ()
    link_directories: tuple[str, ...] = ()
    cleanup_ttl_hours: int = 24


class AppConfig(BaseModel):
    ...
    worktrees: WorktreeConfig = Field(default_factory=WorktreeConfig)
```

对应配置示例：

```yaml
worktrees:
  copy_files:
    - settings.local.json
  ignored_file_globs:
    - fixtures/local/**/*.json
  link_directories:
    - node_modules
  cleanup_ttl_hours: 24
```

三个路径列表均以主仓库为根，禁止绝对路径、空段、`.`、`..`、反斜杠越界语义和重复项。Glob 只允许仓库相对模式；配置阶段拒绝明显非法形式，初始化阶段再对每个真实源与目标执行规范化校验。TTL 使用严格正整数小时。

不内置任何框架文件名，也不把 `.ycode/config.yaml` 复制到子 Worktree。

### 3.2 工具与角色隔离字段

```python
class SubagentIsolation(StrEnum):
    NONE = "none"
    WORKTREE = "worktree"


class SubagentRoleConfig:
    ...
    isolation: SubagentIsolation = SubagentIsolation.NONE


class RunSubagentArguments:
    ...
    isolation: SubagentIsolation | None = None
```

角色 Loader 将 `isolation` 加入严格字段集合。省略时为 `none`；未知值或非字符串值产生角色级诊断并禁用该角色。`run_subagent` Schema 同样暴露可选 `isolation`。管理器按“工具参数 → 角色定义 → `none`”解析最终隔离模式，因此工具可以为单次定义式任务或 Fork 请求 Worktree，也可以显式用 `none` 覆盖角色默认值。

## 4. Worktree 身份与持久化模型

### 4.1 名称和分支

系统名称固定采用两段：

```text
agents/<role-slug>-<task-suffix>
```

- `role-slug` 由已验证角色名生成；无角色 Fork 使用固定 `fork`：转小写、把非法字符折叠为单个 `-`、去除不安全首尾字符并截断。
- `task-suffix` 只来自 YCode 生成的任务 ID，不读取任务正文或模型输出。
- 最终名称统一经过 `WorktreeName.parse()` 再校验段数、段长、总长、Windows 保留名、段尾点和连续 `--`。
- 冲突时只替换系统生成的任务后缀，最多重试固定次数；不会接管已有目录或分支。
- 分支名为 `ycode/<flat-name>`，其中逻辑 `/` 唯一映射为 `--`。逻辑段本身禁止 `--`，因此映射可逆且无碰撞。

### 4.2 目录布局

```text
.ycode/worktrees/
├── agents/
│   └── <role-task>/          # Git linked worktree
└── .state/
    ├── records/
    │   └── agents/
    │       └── <role-task>.json
    └── mutation.lock         # 仅保护短时管理记录变更
```

记录目录与 Worktree 数据目录分离。扫描只读取 `.state/records/agents/` 中符合受管命名规则的文件，不递归遍历主仓库或其他目录。

### 4.3 核心记录

```python
class WorktreeLifecycle(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    RETAINED = "retained"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class WorktreeOwner:
    session_id: str
    task_id: str
    process_id: int
    process_instance_id: str


@dataclass(frozen=True)
class WorktreeRecord:
    version: int
    name: str
    path: str
    branch: str
    base_head: str
    current_head: str | None
    lifecycle: WorktreeLifecycle
    created_at: datetime
    last_activity_at: datetime
    initialization_complete: bool
    initialization_warnings: tuple[str, ...]
    owner: WorktreeOwner
    last_status: WorktreeStatusSnapshot | None
```

所有磁盘字段按严格 Schema 解码。读取后根据合法名称重新计算预期目录和分支，再与记录值比较；磁盘元数据不能自行指定任意路径。记录采用同目录临时文件、刷新后原子替换。项目级 `mutation.lock` 只覆盖“校验当前状态—修改 Git/记录”的短临界区，使用排他创建和 PID 存活判断处理本地残留；它不是分布式锁。

`process_instance_id` 是每次 YCode 启动生成的随机值。所有权以 `session_id + task_id` 为主，PID 和进程实例只用于保守判断占用者是否仍可能存活。

### 4.4 任务结果模型

`SubagentTaskView` 增加可选 `worktree` 摘要；普通子 Agent 与 Fork 为 `None`。摘要包含：

- 名称、绝对路径、分支、创建基准和当前 HEAD；
- 最终处置是 `cleaned` 或 `retained`；
- staged、modified、untracked 的分类摘要；
- 相对基准的新 commit 列表；
- 相对基准的 diff stat；
- upstream、未推送 commit 和删除阻止原因；
- 初始化告警和状态检查告警。

完整 diff 不进入任务结果。即使自动清理后记录已经删除，当前进程内的任务结果仍保留最终摘要。

## 5. Git 操作层

### 5.1 `GitWorktreeClient`

所有 Git 子进程都使用参数数组和显式 `git -C <root>`，不修改进程 `cwd`，不拼接未经验证的命令文本。主要操作如下：

- 仓库和初始 commit 检查：`rev-parse --show-toplevel`、`rev-parse HEAD`；
- 唯一性检查：验证目标路径、`show-ref --verify refs/heads/<branch>` 和受管记录；
- 创建：`git worktree add --lock --reason <owner> -b <branch> <path> <base>`；
- 枚举与身份确认：`git worktree list --porcelain -z`；
- 工作区状态：`git status --porcelain=v2 --branch -z`；
- 新 commit：只查询 `<base>..HEAD`；
- 汇总：`git diff --stat <base>`，不输出完整 diff；
- 删除：通过安全判定后 `worktree unlock`、`worktree remove`，再强制删除已确认安全的临时分支；
- 推送判断：只比较本地 upstream 和远端跟踪引用，不执行 fetch。

`--porcelain -z` 输出由独立解析器处理，业务层不解析面向用户的 Git 文本。Git 命令失败转换为稳定错误码和脱敏摘要。

### 5.2 变更和推送判定

`WorktreeStatusSnapshot` 同时保存：

1. `HEAD` 是否不同于创建基准以及 `<base>..HEAD` 的 commit；
2. porcelain 状态中的 staged、modified、untracked 项；
3. upstream 是否存在；
4. 新 commit 中不被 upstream 包含的 commit；
5. diff stat 和无法确认项。

删除决策固定为：

```text
active / identity unknown / Git check failed → 拒绝
dirty                                   → 拒绝
没有新 commit                           → 允许，不要求 upstream
有新 commit 且无 upstream                → 拒绝
有新 commit 且存在 upstream：
    仍有 commit 不被 upstream 包含       → 拒绝
    全部已被本地 upstream 包含           → 允许
```

该结论只代表本地远端跟踪引用的可达性，不声称远端当前状态已经重新验证。

### 5.3 纯文件系统 HEAD 恢复

`LinkedWorktreeHeadReader` 专门实现 F11，不调用 Git：

1. 读取 Worktree 根目录 `.git` 指针文件；
2. 要求其目标位于主仓库 Git common dir 的 `worktrees/` 下；
3. 验证私有 Git 目录中的反向 `gitdir`、`commondir` 和记录路径一致；
4. 读取私有 `HEAD`；若为符号引用，只允许从已验证的 common dir loose ref 或 `packed-refs` 解析；
5. 将分支引用、HEAD SHA、记录中的分支和 `current_head` 交叉核对。

快速恢复只接受同一 `session_id + task_id`、`initialization_complete=true` 的有效记录。目标存在但记录缺失、记录为半初始化状态、引用后端无法纯文件系统解析或任一字段不一致时，保持现场并报错。该路径不会认领其他任务或来源不明目录。

### 5.4 Git Hooks 一致性

初始化器先计算主仓库的有效 Hooks 目录：

- 未配置 `core.hooksPath`：分别解析主仓库与 linked worktree 的默认 hooks 路径，要求规范化后相同；
- 已配置 `core.hooksPath`：按 Git 的相对路径语义解析出主仓库有效绝对目录，并为隔离子 Agent 的 Git 进程注入等价的 `core.hooksPath` 环境覆盖。

`PowerShellCommandRunner` 增加不可变环境覆盖参数，合并到子进程环境；隔离子 Agent 的命令进程通过 `GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n` 获得绝对 Hooks 路径。YCode 自己的 `GitWorktreeClient` 使用同一覆盖。主 Agent 的 Runner 和 Git 配置不修改，因此不存在“切回后恢复 Hooks”的全局状态问题。

若主/子有效 Hooks 路径不能可靠解析为同一目录，初始化作为关键步骤失败。

设计依据为 Git 官方 [`git worktree`](https://git-scm.com/docs/git-worktree)、[`git status --porcelain`](https://git-scm.com/docs/git-status)、[`core.hooksPath`](https://git-scm.com/docs/git-config) 和 [`githooks`](https://git-scm.com/docs/githooks) 契约。

## 6. 环境初始化

`WorktreeInitializer` 在 Git Worktree 创建成功且任务尚未启动时执行：

1. 验证 Git Hooks 一致性；
2. 复制 `copy_files`；
3. 展开并复制 `ignored_file_globs`；
4. 创建 `link_directories`；
5. 汇总 best-effort 告警并把记录切换为 `active`。

### 6.1 显式文件复制

- 源和目标使用同一仓库相对路径；
- 源必须是主仓库中的普通文件，不能通过符号链接或 Junction 越界；
- 目标父目录逐层创建，但目标已存在时不覆盖；
- 缺失、类型错误或复制失败只记录相对路径和错误类别，不记录正文。

### 6.2 Gitignore Glob 复制

- 先校验 Glob 的词法边界，再在主仓库展开；
- 每个匹配必须是规范化后仍位于主仓库的普通文件；
- 使用本地 `git check-ignore` 确认确实被忽略，非忽略匹配不复制；
- 不跟随目录链接，不复制目录，不覆盖目标；
- 单项失败形成告警并继续处理其他匹配。

### 6.3 依赖目录链接

- 源必须是主仓库内已经存在的真实目录，目标为子 Worktree 的同一相对路径；
- Linux/macOS 使用目录符号链接；Windows 使用固定 PowerShell 脚本创建 Junction，路径通过独立参数/环境传入，不拼接到可执行脚本文本；
- 目标已存在、源缺失或链接失败只记录告警；
- 成功链接登记为子 Resolver 的受控挂载点，只有从 Worktree 内该逻辑入口访问并落在已登记源目录内时才允许解析。

### 6.4 关键失败回滚

Worktree 创建、记录持久化或 Hooks 校验失败时，管理器只回滚本次新建且身份完全匹配的 Worktree 和临时分支。回滚本身失败时写入 `interrupted` 记录并保留现场，错误中明确给出管理名称；不会扩大删除范围。

复制文件、忽略文件和依赖链接失败不触发回滚。

## 7. 路径与工具隔离

### 7.1 路径解析扩展

`WorkspacePathResolver` 增加两个可选能力：

```python
class WorkspacePathPolicy(Protocol):
    def check(self, lexical_path: Path, resolved_path: Path | None, operation: PathOperation) -> None: ...


@dataclass(frozen=True)
class WorkspaceMount:
    logical_root: Path
    physical_root: Path
    writable: bool
    command_cwd_allowed: bool
```

解析器先检查工作区内的词法候选，再解析真实路径并检查最终目标。这样即使活动 Worktree 内存在指向外部的 Junction，主 Agent 从 `.ycode/worktrees/...` 进入时也会先被拒绝。

受控挂载仅允许通过登记的逻辑入口访问：

- 依赖目录链接：实际存在的可写挂载，允许作为命令相对子目录；
- 主仓库 Memory：只读虚拟挂载 `.ycode/memory`，允许文件、Glob、Grep 读取，不允许写入、编辑或作为命令 `cwd`。

绝对物理路径不能绕过逻辑入口。现有越界符号链接和 Junction 仍默认拒绝。

### 7.2 活动 Worktree 访问保护

主仓库 Resolver 注入动态 `WorktreeAccessGuard`。普通路径不产生额外磁盘扫描；候选一旦进入 `.ycode/worktrees/agents/<name>`，Guard 就从磁盘读取并严格校验该名称对应的单条记录，在文件、搜索和命令 `cwd` 解析副作用发生前拒绝活动 Worktree。记录缺失、损坏或无法读取时拒绝整个候选受管子树，不把未知现场当作可访问目录。普通子 Agent 共享主 Resolver，因此得到相同保护；其他隔离子 Agent 则天然受各自工作区根限制。

Glob/Grep 从活动目录的父级开始搜索时，不能只校验搜索起点。路径策略同时向搜索工具提供动态排除子树，搜索实现必须在遍历或启动搜索进程前排除全部活动 Worktree；无法生成可靠排除集合时拒绝该次覆盖受管根的搜索。

`retained` 和 `interrupted` 目录不再被活动访问锁阻止，以便主 Agent 在用户授权后检查。删除仍必须经过独立的安全门。

主 Agent基础提示词增加两条稳定规则：

1. 不在 PowerShell 命令正文中显式访问任何活动 Worktree；
2. 检查或整合保留成果前先取得用户明确授权，不自动执行 merge、rebase 或 cherry-pick。

本期不扫描或证明任意 PowerShell 命令正文的完整文件系统效果，不宣称操作系统级沙箱。

### 7.3 隔离子 Agent 工具装配

`SubagentWorkspaceFactory` 对隔离任务创建：

- 新的 `WorkspacePathResolver`、`TextFileService`、内置文件/搜索/命令工具和 `ToolRegistry`；
- 新的 `ToolExecutor`、`ToolScheduler`、`PermissionEngine`、`PermissionSession` 和 `PowerShellSafetyChecker`；
- 新的 `ContextArtifactStore`、`ContextManager`、`PromptRuntimeContext`、`EnvironmentCollector` 和 `ToolContext`；
- 新的 `HookRuntime` 与 Hook 上下文；
- 带 Git Hooks 环境覆盖的 `PowerShellCommandRunner`。

路径相关内置工具全部重新创建。Provider、Provider 池和现有 MCP 连接继续共享；主注册表中非工作区内置工具以任务启动时快照注册到子 Registry，`tool_search` 针对子 Registry 重新创建。`run_subagent`、Skill 安装和 Skill 加载即使保留定义，也继续由现有子 Agent 硬策略拒绝，不能形成嵌套或运行时扩权。

未隔离子 Agent 继续走当前共享 Registry、Resolver、HookRuntime 和上下文装配路径，不因该工厂改变语义。

## 8. 子 Agent 上下文与 Hooks

### 8.1 项目上下文

隔离任务启动时执行一次新加载：

- 从 Worktree 根加载该分支版本的 `YCODE.md` 和 `@include` 文件；
- 使用新的 `MemoryStore(main_root)` 读取主仓库 Memory 快照；
- 在子 Resolver 上把 `.ycode/memory` 映射为主仓库只读 Memory，使既有 Memory 索引链接仍可由读取工具按需解析；
- 不把子 Agent 的上下文或结果交给 `MemoryUpdater`。

项目指令加载失败属于任务启动失败，不退回主仓库上下文。Memory 告警进入子上下文告警，但不泄露文件正文。

### 8.2 任务路径说明

定义式隔离任务的首条用户消息使用运行时模板：

```text
<worktree_mapping>
parent_workspace: ...
agent_workspace: ...
规则：相对路径以 agent_workspace 为根；任务中出现的 parent_workspace
绝对路径应映射到 agent_workspace 下的相同相对路径；不得直接访问父目录。
</worktree_mapping>

<original_task>
原始任务正文，保持逐字不变
</original_task>
```

映射由受信任运行时生成，不接受模型覆盖，也不预先改写任务正文中的路径。

### 8.3 YCode Hooks

隔离任务以主进程已验证的 `HookRule` 快照创建独立 `HookRuntime(project=worktree)`：

- `once` 命中、Reminder、后台 Hook 任务和关闭状态均不与主 Agent 或其他子 Agent共享；
- Shell Hook 的项目路径为 Worktree；
- AgentLoop 继续触发现有回合、消息、工具、上下文压缩和 Agent 错误事件；
- Session start/end 仍只由 `ChatSession` 的主 HookRuntime 触发。

子 Agent 结束时先关闭其 AgentLoop，再关闭独立 HookRuntime 和上下文资源，最后进行 Git 完成检查。普通子 Agent 保持现有共享 HookRuntime 及会话级 `once` 语义。

## 9. 任务创建、失败降级与完成

### 9.1 稳定会话身份

首次用户回合在启动 `AgentLoop` 前调用 `SessionManager.reserve_session_id(first_message)`：

- 已恢复或已提交会话返回 `active_session_id`；
- 新会话只生成并保留 `_pending_session_id`，不提前创建空会话文件；
- 成功提交继续使用该 ID，失败或取消后也不会改变同一内存会话的归属；
- `/clear` 清除预留 ID，`/resume <id>` 激活指定 ID。

`SubagentManager` 通过只读 provider 取得当前稳定会话 ID，并与任务 ID 一起写入 Worktree owner。

### 9.2 创建顺序

```text
校验角色、模式与并发上限
    ↓
分配 task_id，取得 session_id
    ↓
WorktreeManager.acquire()
├── 记录 creating
├── 从主 HEAD 创建并 Git lock
├── 关键 Hooks 校验
├── best-effort 初始化
└── 记录 active，返回 WorktreeLease
    ↓
登记 ManagedSubagentTask(running)
    ↓
创建隔离运行时并执行
```

同步和异步调用都要等待 `acquire()` 完成；异步任务不会先返回 ID 再在后台创建 Worktree。创建失败时不登记运行中任务。

Git `worktree --lock --reason` 作为活动期额外保护。它不代替 YCode owner 记录和路径访问门。任务进入 `retained` 时解锁；清理时由管理器在安全门通过后解锁并删除。

### 9.3 隔离失败后的显式共享降级

`run_subagent` 增加可选的、不面向普通调用的 `shared_fallback_token`：

1. 隔离创建因 Git 不可用、无初始 commit 或创建失败而终止时，管理器返回 `isolation_unavailable`、明确原因和随机一次性 token；
2. token 仅存内存，并绑定当前 session、角色或 Fork 身份、原始任务、模式、最终隔离模式和发放它的父 `turn_id`；
3. 主 Agent 必须结束当前回合并向用户询问是否允许该任务共享主仓库执行；
4. 用户明确同意后的新回合，主 Agent 用原参数和 token 重试；
5. 管理器要求 parent `turn_id` 已变化、绑定字段完全一致，然后消费 token 并仅对这一次调用使用 `isolation: none`；
6. 同回合使用、字段改变、重复使用、跨会话使用或进程恢复后使用均拒绝。

运行时不尝试用字符串规则理解用户自然语言，由主 Agent依据新一轮用户消息判断是否得到明确同意；“必须跨用户回合 + 一次性绑定 token”防止在首次失败的同一回合内自动静默降级。

### 9.4 终态处置

`SubagentManager._execute()` 在 `finally` 中保证所有 `completed / failed / cancelled / limit_reached` 路径都调用 Worktree 完成处理：

1. 关闭子 Agent 工作区资源；
2. 获取 HEAD、commit、porcelain 状态和 diff stat；
3. 无新 commit 且工作区干净：删除 Worktree、临时分支和管理记录；
4. 存在任一变更：更新为 `retained`，解除活动占用并保留目录和分支；
5. Git 或身份检查失败：fail-closed，保留目录并记录阻止原因；
6. 把最终摘要合并进同步结果、异步通知和 `/tasks` 详情。

失败、取消和轮次上限不改变上述判断。管理器不自动提交或整合成果。

## 10. 启动恢复、会话恢复与清理

### 10.1 启动位置检查

配置发现后、Provider 和运行时创建前，将实际 `start_dir`（默认进程启动目录）规范化并与主仓库受管根比较。若它位于有有效记录对应的 `.ycode/worktrees/agents/...` 内，Anthropic 路径拒绝启动并提示返回主仓库。损坏但看似受管的路径同样保守拒绝，不从子 Worktree 向上接管主配置。

### 10.2 启动协调

每次 Anthropic 启动只执行一次：

1. 读取受管记录，不扫描无关目录；
2. 对 `active` 记录检查 owner PID 是否仍可能存活；可能存活则保持占用并跳过；
3. owner 明确失效时把记录改为 `interrupted`，不恢复任务或 `/tasks`；
4. 对受管名称、记录和路径一致的过期 `retained/interrupted` 候选执行与手动 cleanup 相同的安全门；
5. 删除通过全部检查的候选，失败项保留并汇总启动告警。

活动任务不因 TTL 过期而清理。TTL 从最近一次生命周期/管理活动计算；任务进入终态时更新 `last_activity_at`，`/worktree status` 也更新该记录。没有定时器、后台扫描或文件监听。

### 10.3 会话恢复

`ChatSession.restore()` 仍只恢复会话历史、上下文和 Skill 状态。激活 session ID 后查询该会话对应的 `retained/interrupted` 记录，并把数量和名称作为恢复告警返回；不恢复子 Agent、结果或 `/tasks`。

`--continue` 继续调用现有最近会话恢复路径，`/resume <session-id>` 继续要求显式 ID，`/resume` 缺参仍报原用法错误。

## 11. Slash 命令与交互

新增单一顶层命令：

```text
/worktree list
/worktree status <name>
/worktree delete <name> [--force]
/worktree cleanup
```

解析器只接受上述固定形状，不提供 create、enter、exit、认领或导入。

### 11.1 查询

- `list` 主要读取持久化摘要，显示名称、生命周期、分支、owner、最后活动时间和上次删除阻止原因，不为每项执行重型 Git 检查。
- `status` 对单项执行身份、Git 状态、upstream 和未推送检查，显示 Spec 要求的详情并更新最后活动时间。

### 11.2 删除

- 普通 `delete` 调用统一安全门；任何活动占用、dirty、未推送或未知结论都作为命令错误展示，现场不变。
- `--force` 仍拒绝活动 Worktree和身份不明记录。对其余候选先生成包含未提交分类、新 commit、未推送 commit 和 diff stat 的风险预览。
- `TerminalUI` 显示预览后调用 `InputBox.read_confirmation()`，只接受“取消”或“确认丢弃”。只确认一次；取消后不产生 Git 或记录变更。
- 子 Agent、启动清理和 `/worktree cleanup` 没有 force 入口。

`UIController`、`TerminalUI` 和 `ChatSession` 增加对应的查询、准备删除、确认删除和清理方法；命令格式化集中在 `ycode.worktrees.formatting`，不让 UI 自行推导安全状态。

## 12. 模块组织与现有代码改动

```text
ycode/
├── worktrees/
│   ├── __init__.py
│   ├── models.py          # 配置外的领域模型与状态
│   ├── naming.py          # 名称校验和分支映射
│   ├── store.py           # JSON 记录与本地互斥
│   ├── git.py             # Git 命令、porcelain 解析、FS HEAD 读取
│   ├── initialize.py      # 文件、Glob、链接和 Hooks
│   ├── access.py          # 活动路径门与受控挂载
│   ├── manager.py         # 生命周期、安全删除、启动协调
│   ├── runtime.py         # 隔离子 Agent 运行时装配
│   └── formatting.py      # 结果和命令输出
├── config/models.py       # WorktreeConfig
├── subagents/
│   ├── models.py          # isolation、Worktree 摘要、fallback token 参数
│   ├── loader.py          # isolation frontmatter
│   ├── manager.py         # acquire/finalize 与 fallback 协议
│   ├── runner.py          # workspace assignment 和路径任务模板
│   └── formatting.py      # Worktree 结果展示
├── tools/
│   ├── paths.py           # policy、mount、operation-aware 解析
│   ├── command.py         # 子进程环境覆盖
│   └── builtin/...        # 按 operation 使用 Resolver
├── prompt/...             # 主 Agent Worktree 规则补充
├── session/
│   ├── manager.py         # session ID 预留
│   └── chat.py            # 恢复提示和 Worktree 命令入口
├── commands/
│   ├── builtin.py
│   └── contracts.py
├── ui/
│   ├── input_box.py       # 强制删除二次确认
│   └── terminal.py
└── app.py                 # 项目级 Manager 与运行时工厂装配
```

测试按现有结构放入 `tests/unit/worktrees/`、相关既有模块单测、`tests/integration/` 和 `tests/e2e/`。实现阶段可按仓库现状合并过细文件，但职责边界和行为不能改变。

## 13. 错误与安全策略

稳定错误至少区分：非法名称、来源不明、记录损坏、占用中、Git 不可用、无初始 commit、创建失败、Hooks 不一致、快速恢复失败、工作区 dirty、无 upstream、有未推送 commit、状态未知、删除失败和用户取消。

安全规则：

1. 所有记录路径先由名称重新推导，永不直接信任 JSON 中的绝对路径。
2. 删除前同时验证记录、规范化路径、Git worktree 列表、分支和 owner。
3. 自动操作只触及 `agents/...` 受管命名空间和 `ycode/` 临时分支。
4. 创建冲突只换新名称，不覆盖、删除或认领冲突对象。
5. 初始化输出不包含复制文件正文、环境变量、密钥或命令环境覆盖值。
6. Git 检查不访问网络；所有未知状态都阻止普通删除和自动清理。
7. PowerShell 命令正文不属于硬文件系统沙箱，依靠稳定提示规则和现有命令安全/权限流程约束。

## 14. 验证设计

### 14.1 单元测试

覆盖：

- 名称合法性、长度、Windows 保留名、`--` 禁止和可逆分支映射；
- Worktree 配置默认值、非法路径、Glob 和 TTL；
- 角色 `isolation` 默认、合法值和非法值；
- 记录严格解码、路径重算、原子替换和本地互斥；
- `.git` 指针、loose ref、packed ref 的纯文件系统 HEAD 读取及各种不匹配；
- porcelain v2、commit、diff stat、upstream 和未推送解析；
- 自动保留、普通删除、force 预览和 fail-closed 决策表；
- 显式复制、ignore 过滤、不覆盖、越界拒绝和告警脱敏；
- 目录链接和受控挂载；只读 Memory 的读/写/命令 cwd 边界；
- 主 Resolver 对活动 Worktree 的词法路径保护；
- session ID 预留以及 fallback token 的跨回合、同任务、一次性约束；
- 隔离与普通子 Agent 的 Registry、权限、上下文和 Hook 状态差异；
- 命令参数和强制删除确认取消。

### 14.2 集成测试

使用本地临时 Git 仓库和 Fake Provider 覆盖：

- 从主 HEAD 创建，主仓库 staged、modified、untracked 不进入 Worktree；
- 同步和异步定义式角色分别创建独立 Worktree，Fork 和 `none` 不创建；
- 两个隔离任务使用不同目录、分支、工具、权限、临时资源和 Hooks 状态；
- Worktree `YCODE.md`、主仓库只读 Memory、路径映射说明和 Git Hooks 生效；
- best-effort 告警不阻止启动，关键失败回滚；
- 任务各终态下的无变更清理、有变更保留和结果摘要；
- 创建冲突、有效快速恢复、损坏现场保留；快速恢复路径用替身断言未调用 Git；
- 普通删除的 clean/no-upstream 例外、dirty、无 upstream 新 commit、未推送和检查失败；
- 启动标记 interrupted、TTL 清理、单候选失败继续和无后台扫描；
- 两个 Session 不会占用同一 Worktree，恢复会话只提示遗留记录；
- 隔离失败不能同回合降级，用户后续确认后 token 只允许原任务共享执行一次；
- 未启用隔离时现有子 Agent、Hooks、Memory、MCP、Skill、Session 和命令回归。

### 14.3 真实终端端到端测试

使用现有 PTY 或等价交互终端与 Fake Provider 验证：

1. 主 Agent 同时创建两个隔离子 Agent，观察不同路径/分支、独立修改、通知和保留摘要；
2. 活动期间主 Agent 的文件工具和命令 `cwd` 被拒绝，进程 cwd 始终不变；
3. 用户授权后主 Agent 能检查保留成果，但系统不自动提交或整合；
4. `/worktree list/status/delete/cleanup` 的完整交互；
5. force 风险预览、确认和取消；
6. 异常退出残留在新进程启动后变为 interrupted，`--continue` 与 `/resume <id>` 契约不变；
7. 清理后目录和临时分支消失，主仓库未授权内容不变。

最终还执行项目规定的 Ruff 格式检查、Ruff 静态检查、Pytest、compileall 和功能 checklist。不会调用真实付费 API、真实远端服务或网络，不做压力、性能、长稳、大规模并发、复杂故障注入、多平台矩阵。

## 15. 技术决策汇总

| 决策点 | 选择 | 原因 |
|---|---|---|
| 生命周期编排 | 独立 `WorktreeManager` | 让任务调度与 Git/磁盘安全职责分离 |
| 隔离运行时 | `SubagentWorkspaceFactory` 每任务装配 | 保证路径、工具、权限、上下文和 Hooks 不串用 |
| 进程 cwd | 始终不修改 | 支持并发 Agent，避免全局状态竞争 |
| 创建命令 | `git worktree add --lock --reason` | 创建后立即具有 Git 层活动保护 |
| Git 枚举/状态 | `--porcelain -z` | 使用稳定、可机器解析格式 |
| 记录位置 | `.ycode/worktrees/.state/records` | 与实际工作目录分离且限制扫描范围 |
| 并发保护 | 本地短时 mutation lock + owner 记录 | 满足多进程功能性互斥，不扩展分布式工程 |
| 快速恢复 | 严格记录 + `.git` 纯文件系统 HEAD | 不运行 Git，也不认领来源不明现场 |
| Hooks | 默认路径一致校验；自定义路径用子进程配置覆盖 | 不修改共享 Git 配置或主 Agent 环境 |
| 初始化失败 | 关键项回滚，复制/链接告警 | 对应 Spec 的失败分级 |
| 共享 Memory | Resolver 只读虚拟挂载 | 保持按需读取且不把主 Memory 暴露为可写目录 |
| 依赖目录 | 实际 symlink/Junction + 登记挂载 | 满足运行需要并保持路径校验可解释 |
| 活动目录保护 | Resolver 词法与真实路径双检查 | 防止直接路径和链接路径绕过 |
| Shell 正文 | 提示规则，不声称 OS 沙箱 | 与现有 PowerShell 安全能力边界一致 |
| 隔离失败降级 | 跨用户回合的一次性绑定 token | 防止同回合静默降级，同时不引入新命令 |
| 首次会话归属 | 预留 ID，不提前写空会话 | Worktree 从第一回合起拥有稳定 session owner |
| 终态判断 | commit 与 porcelain 工作区状态共同决定 | 同时覆盖已提交和未提交成果 |
| 已推送判断 | 本地 upstream 可达性 | 无隐式网络访问 |
| 自动清理 | 仅启动一次及手动命令 | 控制负载，符合已批准范围 |
| 强制删除 | UI 风险预览 + 一次确认 | force 只由用户交互触发 |
| 整合 | 主 Agent 获授权后显式执行 | 管理器不自动改动主分支 |
| OpenAI | 不装配 Worktree 功能 | 遵守 Provider 范围 |

## 16. Spec 覆盖映射

| Spec | 主要设计位置 |
|---|---|
| F1–F4 | 3.2、9.1–9.3 |
| F5–F11 | 4.1–4.3、5.1、5.3、9.2 |
| F12–F17 | 3.1、5.4、6 |
| F18–F20 | 2、5.1、7 |
| F21–F24 | 7.1–7.3、8 |
| F25–F30 | 4.4、5.2、7.2、9.4 |
| F31–F35 | 4.2–4.3、9.1、10 |
| F36–F40 | 5.2、11、13 |
| F41–F44 | 4.2、10.2、11 |
| N1–N5 | 1、4–5、7、13、15 |
| N6–N10 | 6、9–10、13 |
| N11–N15 | 12、14–15 |

AC1–AC13 分别由上述功能映射和第 14 节验证设计覆盖。实现任务将在 `task.md` 中按依赖顺序拆分，并把每项验收标准落到具体测试与命令；最终实测结果记录在 `checklist.md`。

## 17. 范围边界

本设计不增加主 Agent Worktree 切换、手动创建/进入/退出、Fork 隔离、子 Agent 嵌套、自动提交或整合、远端 Git 操作、任务恢复、周期清理、文件监听、操作系统沙箱、分布式锁、持久化调度、OpenAI 适配或生产级可靠性验证。任何此类能力都需要新的 Spec，不在实现中顺带加入。
