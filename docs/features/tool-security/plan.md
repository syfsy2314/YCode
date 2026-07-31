# YCode 工具权限安全系统 Plan

## 架构概览

采用统一且轻量的执行前权限层，不把安全逻辑分散到各个工具：

```text
项目 security.yaml
        ↓
安全配置加载与校验
        ↓
PermissionSession
├── 当前权限模式
└── 本会话允许记录

模型产生 ToolCall
        ↓
PermissionEngine
├── plan-only 检查
├── 路径解析与安全参数规范化
├── PowerShell 危险命令检查
├── 会话规则
├── 项目规则
└── 权限模式默认值
        ↓
ALLOW ───────────────┐
DENY → 结构化结果    │
ASK → 审批事件 → TUI │
                     ↓
            ToolScheduler
                     ↓
             ToolExecutor
                     ↓
                实际工具
```

`SecurityConfigLoader` 发现和校验项目安全配置；`PermissionSession` 保存当前模式和会话
允许；`PermissionEngine` 执行硬检查、规范化和普通规则匹配；
`PowerShellSafetyChecker` 只负责 `run_command` 的危险语法分析。Agent 使用现有事件流
发起阻塞式审批，TerminalUI 完成输入后才能继续。Scheduler 只处理已经完成权限判断的
批次，Executor 保留最终参数、模式和路径校验。

当前工具的安全参数规范化集中在权限模块，不建立策略插件或 MCP 专用类型。未来 MCP
工具由统一适配器包装成普通工具后复用相同入口。`ToolAccess` 增加 `UNKNOWN`，其默认
权限行为是询问，Scheduler 将其视为非读取调用串行安排。

安全配置和权限组件只装配到 Anthropic Agent 路径；OpenAI PlainChatRunner 保持原样。

## 核心数据结构

### PermissionMode

```python
class PermissionMode(StrEnum):
    STRICT = "strict"
    DEFAULT = "default"
    ALLOW = "allow"
```

### PermissionAction 与 ApprovalChoice

```python
class PermissionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalChoice(StrEnum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
```

### 安全配置模型

```python
class ArgumentMatcher:
    exact: str | int | bool | None
    glob: str | None


class SecurityRule:
    id: str
    action: PermissionAction
    tool: str
    arguments: Mapping[str, ArgumentMatcher]


class SecurityConfig:
    mode: PermissionMode = PermissionMode.DEFAULT
    rules: tuple[SecurityRule, ...] = ()
```

`ArgumentMatcher` 必须且只能设置 `exact` 或 `glob`，Glob 只接受字符串。规则 ID 在单份
配置中唯一；工具名和参数名在加载时与当前 Registry 对照。

### PermissionSubject 与 PermissionDecision

```python
@dataclass(frozen=True)
class PermissionSubject:
    call: ToolCallBlock
    normalized_arguments: FrozenJsonObject
    session_key: FrozenJsonObject
    approval_summary: str


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    subject: PermissionSubject
    reason_code: str
    rule_id: str = ""
```

- `normalized_arguments` 用于项目规则匹配。
- `session_key` 使用工具特定的安全关键参数。
- `approval_summary` 是经过限制和脱敏的终端展示文本。
- `reason_code` 区分黑名单、plan-only、会话授权、项目规则、权限模式和内部失败。

### PermissionSession

```python
class PermissionSession:
    mode: PermissionMode

    def set_mode(self, mode: PermissionMode) -> None: ...
    def allows(self, session_key: FrozenJsonObject) -> bool: ...
    def grant(self, session_key: FrozenJsonObject) -> None: ...
    def clear(self) -> None: ...
```

会话授权使用冻结结构化键去重，不保存写入或编辑正文。`clear()` 只清除会话授权，不改变
当前权限模式。

### PermissionEngine

```python
class PermissionEngine:
    async def evaluate(
        self,
        call: ToolCallBlock,
        allowed_access: frozenset[ToolAccess],
        session: PermissionSession,
    ) -> PermissionDecision: ...
```

内部顺序固定为：

1. 查找工具并使用其参数模型校验参数。
2. 解析真实路径并生成规范化参数、会话键和审批摘要。
3. 检查 plan-only 允许的访问分类。
4. 对 `run_command` 执行危险命令检查。
5. 检查会话授权。
6. 顺序匹配项目规则。
7. 使用权限模式产生默认决策。

### PowerShellSafetyChecker

```python
class PowerShellSafetyChecker:
    async def inspect(
        self,
        command: str,
        workspace: Path,
    ) -> CommandSafetyResult: ...
```

结果表示安全，或携带稳定危险类别和用户可见原因。解析失败与检查器异常均返回硬拒绝。

### ToolApprovalRequested

```python
@dataclass(frozen=True)
class ToolApprovalRequested:
    round_number: int
    position: int
    subject: PermissionSubject
    reason_code: str
```

每个 AgentTurn 同一时刻最多存在一个待审批请求。AgentTurn 增加提交当前审批选择的
入口，ChatSession 只把 TerminalUI 的选择转交当前 AgentTurn。没有待审批请求、重复
提交或状态异常时拒绝继续执行。

## 模块设计

### 安全配置

`ycode/security/config.py` 从当前目录向上发现最近的 `.ycode/security.yaml`，使用
安全 YAML 加载和强类型校验。配置缺失时返回 `default` 模式和空规则。加载过程结合
Registry 校验工具名和参数名。

路径规则在 Windows 下按文件系统语义忽略大小写，其他字符串保持大小写敏感。配置只在
启动时读取，解析或校验失败时阻止应用启动。

### 权限模型与会话状态

`ycode/security/models.py` 保存权限枚举、规则、匹配条件、权限主题、决策和会话状态。
会话状态由 AgentLoop、ChatSession 和 TerminalUI 共享，退出进程后自然释放。

### 权限引擎

`ycode/security/engine.py` 使用 Registry 中的参数模型校验工具参数，并为当前六个工具
生成以下安全键：

- `read_file`：真实路径。
- `write_file`：真实路径和覆盖标记。
- `edit_file`：真实路径。
- `glob`：搜索模式。
- `grep`：搜索表达式、真实起始路径、文件模式和大小写选项。
- `run_command`：去除首尾空白后的完整命令和真实工作目录。

路径参数通过现有 WorkspacePathResolver 获取真实目标和工作区相对 POSIX 路径。未知
分类工具使用完整规范化参数作为会话键。项目规则使用声明顺序进行精确或 Glob 全匹配。

审批摘要限制为 2 KiB，文件正文超过限制时显示截断标记。`run_command` 按 Spec 完整
显示命令。引擎内部异常转换为稳定安全拒绝，不把异常细节发送给模型。

### PowerShell 安全检查

`ycode/security/powershell.py` 使用短生命周期的
`powershell.exe -NoProfile -NonInteractive` 进程调用 PowerShell 自带语法解析器。
待检查命令通过标准输入传递给固定解析脚本，不拼接进解析器命令行，也不会执行。

解析器只输出命令、参数、管道和语法错误的 JSON 描述。Python 侧统一处理别名、大小写
和危险类别。解析进程使用短超时和隐藏窗口；启动失败、超时、非法输出或语法错误一律
硬拒绝。内置危险规则不从项目配置读取。

### Agent 审批流程

AgentLoop 在每次模型工具响应后按调用顺序执行权限判断：

```text
检查当前位置
    ↓
ASK
    ↓
设置唯一待审批状态
    ↓
yield ToolApprovalRequested
    ↓
异步生成器暂停，等待 TerminalUI 完成输入
    ↓
读取选择并处理当前位置
    ↓
才检查下一个位置
```

等待期间不检查后续工具、不启动工具、不发起模型请求。选择 `ALLOW_SESSION` 后将
`session_key` 写入 PermissionSession；选择拒绝后生成预先计算的
ToolExecutionResult。相同调用再次出现时重新询问。

一批调用全部完成权限判断后才进入 Scheduler。ToolScheduler 接收“位置到预生成拒绝
结果”的映射：拒绝位置直接产生完成记录且不调用 Executor，允许位置沿用读取并发和
写入屏障，最终记录仍按模型原始位置排序回填。

### Session 与终端

ChatSession 持有可选 PermissionSession，识别 `/permission`、权限模式切换和
`/permission clear`，并提供向当前 AgentTurn 提交审批选择的入口。

TerminalUI 和 InputBox：

- 在启动页和输入提示中同时显示任务模式与权限模式。
- 收到审批事件后停止普通事件推进并显示三选一审批界面。
- 等待审批时只接受拒绝、本次允许、本会话允许或 Ctrl+C。
- Ctrl+C 取消审批和整个当前 Agent 回合。
- 同一时刻只运行普通中断监听或审批输入应用，避免两个输入监听器竞争。

OpenAI PlainChatRunner 不装配权限会话，保持现有 UI 和命令行为。

### 提示词与应用装配

Anthropic Agent 启动时加载项目安全配置，并创建权限会话、权限引擎和命令检查器。
当前权限模式不进入 `build_builtin_prompt()`；AgentLoop 在每个普通用户任务调用
PromptRuntimeContext 生成动态上下文时，增加请求级 `tool_state` system 补充。

模式切换后的下一任务自然获得新补充，同一任务的工具轮次复用相同补充。项目规则和
会话授权不进入提示词或 ChatSession 历史。

## 模块交互

### 启动流程

```text
发现 Provider 配置和工作区
    ↓
创建 WorkspacePathResolver 与 ToolRegistry
    ↓
发现并加载项目 security.yaml
    ↓
创建 PermissionSession
    ↓
创建 PowerShellSafetyChecker 与 PermissionEngine
    ↓
注入 AgentLoop 和 ChatSession
    ↓
TerminalUI 显示启动权限模式
```

安全配置需要 Registry 才能校验工具名和参数名，因此在内置工具注册后加载。

### 单次工具批次

```text
Anthropic 返回 ToolCallBlock
    ↓
AgentLoop 按位置逐个调用 PermissionEngine
    ├─ DENY：保存预生成错误结果
    ├─ ALLOW：标记允许
    └─ ASK：产生 ToolApprovalRequested 并暂停
                    ↓
              TerminalUI 等待选择
                    ↓
              ChatSession 提交选择
                    ↓
              AgentLoop 原位置恢复
    ↓
全部调用完成权限判断
    ↓
ToolScheduler 执行允许项、合并拒绝结果
    ↓
按原始位置生成 ToolResultBlock
```

### 权限命令

```text
用户输入 /permission ...
    ↓
ChatSession 直接处理
    ↓
修改或查询 PermissionSession
    ↓
产生权限模式事件
    ↓
TerminalUI 更新显示
```

权限命令不调用模型、不进入历史、不修改项目配置。

## 文件组织

```text
ycode/
├── security/
│   ├── __init__.py
│   ├── models.py
│   ├── config.py
│   ├── engine.py
│   └── powershell.py
├── tools/
│   ├── contracts.py
│   └── scheduler.py
├── agent/
│   ├── contracts.py
│   ├── events.py
│   └── loop.py
├── session/
│   └── chat.py
├── ui/
│   ├── header.py
│   ├── input_box.py
│   └── terminal.py
└── app.py

tests/
├── unit/security/
├── unit/agent/
├── unit/session/
├── unit/ui/
├── integration/
└── e2e/
```

OpenAI 相关实现文件不修改，只运行现有回归测试。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 权限入口 | AgentLoop 调度前集中检查 | 阻止副作用并支持阻塞式 HITL，不把审批逻辑放进工具 |
| 参数校验 | 权限检查前校验，Executor 执行前再次校验 | 使用同一工具参数模型，保持默认安全失败 |
| 项目配置 | 独立 `.ycode/security.yaml` | 不与 Provider 和 API Key 配置耦合 |
| 规则匹配 | 有序首次命中，精确或 Glob 全匹配 | 行为简单、确定且容易测试 |
| 路径判断 | 使用现有 Resolver 解析真实目标 | 允许沙箱内链接并阻止越界链接 |
| 命令检查 | 每次命令启动短生命周期 PowerShell 解析进程 | 使用官方语法解析能力，不维护常驻进程 |
| 命令传递 | 通过 stdin 交给固定解析脚本 | 避免待检查文本被当作解析器命令执行 |
| HITL 通信 | AgentEvent 和当前 AgentTurn 单一响应槽 | 复用现有事件流，天然阻塞，不引入队列 |
| 批次执行 | 全部审批完成后再调用 Scheduler | 满足完成输入后才能进入下一步 |
| 会话授权 | 冻结结构化安全键存内存 | 不持久化正文，匹配确定，退出即清除 |
| 未知工具 | 默认 ASK，按非读取工具串行调度 | 为统一工具适配保留保守行为 |
| 权限提示词 | 请求级动态 `tool_state` 补充 | 权限切换不破坏稳定 System Prompt 缓存 |
| OpenAI | 不装配权限组件 | 保持当前纯聊天路径，不扩大 Provider 范围 |

PowerShell 检查器不尝试证明命令绝对安全，只对 Spec 定义的危险类别做硬拒绝；其余命令
继续经过项目规则、权限模式和 HITL。

## Spec 覆盖

- F1、F5、F6：权限模型、PermissionEngine 和规则匹配。
- F2：现有 WorkspacePathResolver、plan-only 与权限引擎的硬检查顺序。
- F3：PowerShellSafetyChecker。
- F4：安全配置发现、加载和 Registry 联合校验。
- F7：审批事件、AgentTurn 响应槽和 TerminalUI 审批输入。
- F8：PermissionSession 与工具特定安全键。
- F9：ChatSession 权限命令和 UI 状态展示。
- F10：AgentLoop 动态补充和结构化拒绝结果。
- N1–N4：双重校验、默认安全失败、预览限制和阻塞式审批。
- N5–N7：现有 Scheduler/Executor 复用、UNKNOWN 分类和分层自动化测试。
