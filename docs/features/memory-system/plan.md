# YCode 记忆系统 Plan

> 状态：已批准

## 架构概览

```text
CLI / App 装配
├─ ProjectContextLoader
│  ├─ 读取并展开 YCODE.md
│  └─ 调用 MemoryStore 读取并校验 memory/MEMORY.md
│       ↓
│  PromptRuntimeContext（两个独立会话级系统补充）
│
├─ SessionManager
│  └─ .ycode/sessions/*.jsonl
│     创建 / 列表 / 恢复 / 删除 / 追加 / 修复
│
├─ ChatSession
│  ├─ 活动历史、模式、权限、斜杠命令
│  ├─ 调用 SessionManager 完成写前提交与 /resume
│  ├─ 调用 ContextManager 完成恢复压缩
│  └─ 累积本次运行新增回合
│
├─ MemoryStore
│  └─ .ycode/memory/ 索引、主题解析和安全写入
│
├─ MemoryUpdater
│  └─ 退出时通过隔离 Anthropic 请求产生变更计划
│
└─ AgentLoop / ContextManager / Tools / MCP / Provider
   保持现有职责
```

`SessionManager` 只管理磁盘会话，不接触 Provider、工具、权限或 TUI。`ChatSession`
仍是活动对话协调者，负责保证“JSONL 落盘 → 上下文提交 → 最终事件”的顺序。
`ContextManager` 继续拥有会话压缩摘要，并新增无副作用的恢复预检与成功切换后的状态
替换能力。`MemoryUpdater` 只生成结构化变更计划，`MemoryStore` 负责校验和应用。

项目指令和记忆索引共用启动发现及系统补充注入流程，但分别使用不同上下文类型。
应用工作区统一为活动配置确定的项目根目录，使工具、`YCODE.md`、会话和记忆使用同一
安全边界。OpenAI 继续走现有纯聊天路径，不装配这些新组件。

## 核心数据结构

### 项目上下文

```python
@dataclass(frozen=True, slots=True)
class ProjectContextSnapshot:
    supplements: tuple[SystemSupplement, ...]
    warnings: tuple[ProjectContextWarning, ...]
```

`ProjectContextLoader.load()` 在启动时生成一次快照：

- `PROJECT_INSTRUCTIONS`：展开后的 `YCODE.md`。
- `PROJECT_MEMORY`：校验后的 `MEMORY.md` 索引。
- 指令错误抛出启动异常；记忆错误进入 `warnings`。

现有会话压缩摘要改用独立的 `<conversation_memory>` 标签，避免与项目记忆混淆。

### 带时间的回合消息

```python
@dataclass(frozen=True, slots=True)
class TurnMessage:
    message: ChatMessage
    created_at: datetime
```

`created_at` 必须为 UTC。`AgentTurnResult` 保存 `tuple[TurnMessage, ...]`，同时提供只读
`messages` 属性兼容只需要消息内容的调用方。用户提交、Assistant 完整消息和工具结果
产生时分别记录时间。

### JSONL 记录

```python
@dataclass(frozen=True, slots=True)
class SessionMessageRecord:
    version: int
    session_id: str
    turn_id: str
    timestamp: datetime
    message: ChatMessage

@dataclass(frozen=True, slots=True)
class TurnCommitRecord:
    version: int
    session_id: str
    turn_id: str
    timestamp: datetime
    message_count: int

@dataclass(frozen=True, slots=True)
class ContextCheckpointRecord:
    version: int
    session_id: str
    covered_turn_id: str
    timestamp: datetime
    memory: ConversationMemory
    retained_history: tuple[ChatMessage, ...]
```

序列化时为三种记录分别加入 `type`：`message`、`turn_commit` 和
`context_checkpoint`。所有记录使用同一版本字段和 UTC ISO 8601 时间。检查点保存
摘要及压缩后仍需保留的消息，使恢复不必重新推导摘要覆盖到当前回合的哪个中间消息。

### 会话模型

```python
@dataclass(frozen=True, slots=True)
class SessionDescriptor:
    session_id: str
    created_at: datetime

@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    history: tuple[ChatMessage, ...]
    memory: ConversationMemory | None
    last_turn_id: str | None
    last_active_at: datetime
    warnings: tuple[SessionWarning, ...]
```

### 记忆模型

```python
class MemoryType(StrEnum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"

@dataclass(frozen=True, slots=True)
class MemoryEntry:
    path: str
    name: str
    description: str
    type: MemoryType
    body: str

@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    index_content: str
    entries: tuple[MemoryEntry, ...]
    warnings: tuple[MemoryWarning, ...]

@dataclass(frozen=True, slots=True)
class MemoryOperation:
    action: Literal["create", "update", "delete"]
    path: str
    entry: MemoryEntry | None

@dataclass(frozen=True, slots=True)
class MemoryUpdatePlan:
    operations: tuple[MemoryOperation, ...]
```

模型表达合并时使用“更新保留项 + 删除重复项”，不增加单独的 merge 文件操作。

## 核心接口

### `SessionManager`

```python
begin_new() -> None
commit_turn(messages, context_checkpoint=None) -> SessionCommit
load(session_id) -> SessionSnapshot
load_latest() -> SessionSnapshot
activate(snapshot) -> None
list_sessions() -> tuple[SessionDescriptor, ...]
delete(session_id) -> None
```

- 新会话在第一个成功回合提交时才生成 ID 和文件。
- `load()` 校验并按需修复文件，但不改变活动会话。
- 只有上下文恢复预检也成功后才调用 `activate()`。
- `commit_turn()` 按“消息 → 可选检查点 → `turn_commit` → flush”写入，提交边界最后写。
- 文件操作通过异步包装执行，避免较长恢复扫描阻塞 TUI。

### 上下文恢复

```python
prepare_restore(history, memory) -> RestoreContextResult
activate_restore(result) -> None
reset_runtime_state() -> None
```

`prepare_restore()` 不修改当前 `ContextManager`；必要时只压缩一次并返回候选提交。
候选检查点成功落盘且目标会话激活后，才替换上下文状态。

### 项目记忆

```python
MemoryStore.load() -> MemorySnapshot
MemoryStore.apply(plan) -> MemorySnapshot

MemoryUpdater.analyze(
    current_memory,
    new_conversations,
) -> MemoryUpdatePlan
```

`MemoryUpdater` 只解析隔离模型返回的结构化操作；`MemoryStore` 负责路径、类别、
front matter、索引一致性和文件替换。

## 模块设计与文件组织

```text
ycode/
├── prompt/
│   ├── models.py            # 新增项目指令、项目记忆补充类型
│   ├── runtime.py           # 会话补充快照、模式状态重置
│   └── project.py           # YCODE.md 展开与项目上下文快照
│
├── session/
│   ├── models.py            # JSONL 记录、会话描述、恢复结果与警告
│   ├── codec.py             # ChatMessage/内容块与 JSON 的双向转换
│   ├── manager.py           # SessionManager、文件 CRUD、重放与修复
│   └── chat.py              # 写前提交、--continue、/resume、状态切换
│
├── memory/
│   ├── __init__.py
│   ├── models.py            # 记忆条目、类型、快照、变更计划
│   ├── store.py             # 索引/front matter 校验与安全应用
│   ├── updater.py           # 退出分析、响应解析
│   └── resources/
│       ├── __init__.py
│       └── update.md        # 隔离记忆整理 Prompt
│
├── context/
│   ├── models.py            # 恢复候选与检查点结果
│   └── manager.py           # 无副作用恢复预检、状态激活与重置
│
├── agent/
│   ├── contracts.py         # TurnMessage、带时间的 AgentTurnResult
│   ├── events.py            # 会话恢复成功/修复提示事件
│   ├── loop.py              # 在消息完整产生时记录 UTC 时间
│   └── plain.py             # 保持统一结果契约，不接入持久化
│
├── cli.py                   # --continue
├── app.py                   # 使用 config.project_root 装配新组件
└── ui/terminal.py           # /resume 结果、恢复警告、退出整理提示
```

`prompt/project.py` 使用现有安全路径解析器展开 `YCODE.md`，并调用
`MemoryStore.load()` 获取有效索引，自身不解析主题记忆。`session/codec.py` 是唯一理解
JSONL 消息格式的模块。`session/manager.py` 是唯一读写 `.ycode/sessions/` 的模块，恢复
时记录字节偏移以便准确截断。`memory/store.py` 使用 PyYAML 安全解析 front matter，
并使用同一项目根路径边界验证索引与文件。

`TerminalUI` 在正常退出路径调用会话的记忆整理入口并展示结果；资源关闭仍由应用
装配层兜底。项目根 `.gitignore` 增加 `.ycode/sessions/` 和 `.ycode/memory/`。

## 模块交互

### 启动

```text
加载配置
→ 使用 config.project_root 作为工作区
→ MemoryStore 读取并校验记忆索引
→ ProjectContextLoader 展开 YCODE.md
→ 两份快照写入 PromptRuntimeContext
→ SessionManager.begin_new()
   或 --continue → load_latest()
→ ContextManager.prepare_restore()
→ 必要时压缩并保存检查点
→ 成功后激活会话
→ TerminalUI 展示启动与修复提示
```

项目指令加载失败直接终止；记忆条目错误只形成启动提示。OpenAI 路径不装配新组件，
使用 `--continue` 时报告当前 Provider 不支持。

### 正常回合提交

```text
ChatSession 收到用户输入
→ AgentLoop 生成带 UTC 时间的 TurnMessage
→ Agent 正常结束
→ 取得 ContextCommit 和可选压缩检查点
→ SessionManager.commit_turn()
   写 message 记录
   写可选 checkpoint
   最后写 turn_commit
   flush
→ ContextManager.commit()
→ ChatSession 替换内存历史
→ 发出 FinalResponseEvent
```

任何存档写入失败都发生在内存提交和最终完成事件之前。自动上下文压缩若改变摘要，
则将压缩后的 `memory + retained_history` 与当前回合一起保存。

### 手动压缩

```text
/compact
→ ContextManager 生成候选压缩结果，不立即修改状态
→ SessionManager 追加并刷新检查点
→ ContextManager 激活候选状态
→ ChatSession 更新历史
```

检查点写入失败时，原历史和原摘要保持不变。

### `/resume <session-id>`

```text
SessionManager.load(id)
→ 跳过坏 JSON 行
→ 重放完整 turn_commit
→ 校验工具调用链
→ 必要时截断损坏尾部
→ 返回 SessionSnapshot（尚未激活）
→ ContextManager.prepare_restore()
→ 超限时只压缩一次
→ 必要时向目标 JSONL 追加检查点
→ SessionManager.activate()
→ ContextManager.activate_restore()
→ ChatSession 替换历史并重置模式/权限临时授权
→ PromptRuntimeContext 重置模式提醒状态
→ 设置一次性时间跨度提醒
→ 发出 SessionResumedEvent
```

任一步失败都不切换当前活动会话；对目标损坏文件已经完成的安全修复保留。

### 退出记忆整理

```text
每个成功提交回合
→ ChatSession 记录 session_id、回合时间和本轮消息
→ 用户在本次运行中可以多次 /resume

正常退出
→ 没有新增回合：直接结束
→ MemoryStore 重新读取退出时的当前记忆
→ MemoryUpdater 分析全部新增回合和当前记忆
→ 30 秒内返回结构化变更
→ MemoryStore 校验并应用
→ TerminalUI 显示成功或非致命提示
→ 关闭 Runner、Provider、MCP 和上下文资源
```

退出时重新读取记忆文件只用于避免覆盖运行期间的人工修改；普通模型请求仍使用启动时
快照，不构成热加载。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 项目工作区 | 使用 `config.project_root` | 保证从子目录启动时工具、指令、会话和记忆共享同一边界 |
| 指令编码 | UTF-8，按原位置递归替换 `@include` 行 | 结果确定，保留模块顺序 |
| 安全路径 | 复用真实路径与工作区边界校验 | 同时拦截 `..`、绝对路径和符号链接逃逸 |
| 系统补充类型 | 新增 `PROJECT_INSTRUCTIONS`、`PROJECT_MEMORY`；压缩摘要使用 `<conversation_memory>` | 明确区分三种来源 |
| 系统内容顺序 | 内置 Prompt → 会话压缩摘要 → 项目指令 → 记忆索引 → 环境/工具/模式 → 边界提醒 | 保持稳定前缀和确定性顺序 |
| JSONL 编码 | UTF-8、每条记录单行 JSON、显式 `type` 和 `version` | 便于追加、跳过坏行和格式演进 |
| 时间 | 消息产生时记录 UTC，写为 ISO 8601 `Z` 格式 | 支持准确恢复、活跃时间和记忆整理 |
| 回合 ID | 会话内递增的固定宽度编号 | 无额外索引也能检测顺序、缺失和重复 |
| 写入强度 | `turn_commit` 最后写并立即 `flush`，不执行每行 `fsync` | 满足进程崩溃恢复边界且避免额外延迟 |
| 消息编码 | 显式编码每种内容块，不序列化 Python 类内部状态 | JSONL 格式稳定且 Provider 无关 |
| 恢复算法 | 状态机重放；只有合法 `turn_commit` 才推进安全截断偏移 | 准确丢弃半回合和不完整工具链 |
| 坏 JSON 行 | 记录警告并跳过；若破坏当前回合结构，则在上一安全边界停止 | 同时满足坏行继续和结构可信性 |
| 上下文检查点 | 保存 `memory + retained_history + covered_turn_id` | 正确恢复回合中间产生的压缩结果 |
| 文件 I/O | 通过 `asyncio.to_thread` 执行扫描和写入 | 不引入异步文件依赖，也不阻塞事件循环 |
| 记忆索引 | 从有效主题 front matter 重新生成注入文本 | 不把坏条目或任意索引正文传给模型 |
| 记忆响应 | 模型返回单个 JSON 对象和操作列表；空列表表示无需更新 | 易于严格校验，不让模型直接使用文件工具 |
| 记忆更新 | 新文件先写、索引原子替换、最后删除旧文件 | 索引不指向尚未完成的新文件 |
| 记忆重命名 | metadata 或类别变化使用创建替代项加删除旧项；同路径更新只改正文 | 避免中断时旧索引与新 front matter 失配 |
| 退出输入 | 退出时重新读取当前记忆，对话只取本次运行新增成功回合 | 尊重人工修改并避免重复处理历史 |
| 整理安全 | 专用 Prompt、无工具、关闭 Thinking、严格响应校验 | 降低提示注入和越权文件操作风险 |
| 并发范围 | 单进程单写者，不实现跨进程锁 | 多进程并发不在本期范围 |
| OpenAI | 不装配持久化、项目上下文和记忆组件 | 遵守当前 Provider 阶段边界 |

错误策略：指令加载错误转换为启动配置错误；会话写入、恢复和修复使用不包含正文的
安全错误码；`/resume` 失败复用现有错误事件通道，成功使用新的恢复事件；记忆加载产生
可继续的警告；退出整理返回报告，不抛出导致应用异常退出。`--continue` 找不到任何
会话时明确失败，不自动创建新会话。

## Spec 覆盖

| Spec | 设计归属 |
|------|----------|
| F1–F4 | `config.project_root`、`prompt/project.py`、`PromptRuntimeContext` |
| F5–F6 | `SessionManager`、会话 ID 生成、单 JSONL 文件 |
| F7–F8 | `TurnMessage`、会话记录模型、codec、写前提交 |
| F9 | CLI、`ChatSession` 命令分发、`SessionManager.load*()` |
| F10–F11 | JSONL 状态机重放、警告和安全偏移截断 |
| F12 | `ContextManager.prepare_restore()`、上下文检查点 |
| F13–F14 | 一次性提醒、会话与 Prompt 运行状态重置 |
| F15–F18 | `ycode/memory/models.py`、`store.py`、项目上下文注入 |
| F19–F22 | `MemoryUpdater`、运行期新增回合收集、退出整理和报告 |
