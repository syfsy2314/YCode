# YCode 工具系统与 Agent Loop Tasks

> 状态：已批准

## 实施约束

- 只实现已批准 `spec.md` 和 `plan.md` 中的能力。
- 严格按任务依赖顺序实施；每个任务完成后先运行局部验证，失败时在当前任务内修复。
- 核心工具、Agent 状态与事件不得导入 Anthropic/OpenAI SDK 类型。
- Anthropic 是本次 Agent 实现目标；OpenAI 只保留纯聊天路径并执行回归测试。
- 不实现权限审批、子代理、插件、复杂提示、跨平台 Shell 或额外工具。
- 不连接真实模型服务，不读取或改写用户级 `.ycode/config.yaml`。
- 涉及文件副作用的测试一律使用 Pytest 临时目录。
- 不创建 Git commit。

## 文件变化概览

| 操作 | 文件或目录 | 职责 |
| --- | --- | --- |
| 修改 | `pyproject.toml` | 增加 `pathspec` 直接依赖 |
| 新建 | `ycode/tools/` | 工具契约、路径、文本、命令、注册、执行、调度和六个内建工具 |
| 新建 | `ycode/agent/` | Agent 契约、事件、提示、循环和纯聊天运行器 |
| 修改 | `ycode/core/provider.py`、`ycode/core/__init__.py` | AgentChatProvider 及公共导出 |
| 修改 | `ycode/providers/anthropic.py` | system/tools 请求字段转换 |
| 不修改 | `ycode/providers/openai.py` | 保留现有纯聊天 Provider |
| 重写 | `ycode/session/chat.py` | AgentEvent 流、模式和整轮事务 |
| 修改 | `ycode/session/__init__.py` | 导出新会话契约 |
| 修改 | `ycode/ui/input_box.py` | 右下角模式状态 |
| 重写 | `ycode/ui/renderer.py`、`ycode/ui/terminal.py` | 多轮 AgentEvent 展示和取消恢复 |
| 修改 | `ycode/app.py` | Provider、工具、Agent 与纯聊天运行器装配 |
| 新建 | `tests/unit/tools/`、`tests/unit/agent/` | 工具与 Agent 单元测试 |
| 修改/新建 | 现有 Session、Provider、UI、App 测试 | 新边界及 OpenAI 回归 |
| 修改 | `tests/support/`、`tests/integration/` | 可编排本机模型响应及请求检查 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 真实 Windows PTY Agent 场景 |

## 实现任务

### TS1：建立工具基础契约和依赖

**文件：**
`pyproject.toml`、`ycode/tools/__init__.py`、`ycode/tools/contracts.py`、
`ycode/tools/errors.py`、`tests/unit/tools/test_contracts.py`

**依赖：** 无

**步骤：**

1. 增加 `pathspec` 直接依赖，不增加 `jsonschema`。
2. 定义 `ToolAccess`、泛型 `ToolDefinition`、`Tool` Protocol、`ToolContext`、
   `ToolExecutionResult` 和 `ToolExecutionRecord`。
3. `ToolDefinition` 从具体 Pydantic 参数模型生成并冻结 JSON Schema。
4. 定义受控 `ToolError`，只携带稳定错误码、安全消息和冻结元信息。
5. 验证 Tool Protocol 可由普通类结构化满足，无需继承基类。
6. 验证 Schema、结果和元信息不会被原地修改。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_contracts.py -q
.venv\Scripts\python.exe -m ruff check ycode/tools tests/unit/tools
```

### TS2：实现工作区路径和文本文件服务

**文件：**
`ycode/tools/paths.py`、`ycode/tools/text_files.py`、
`tests/unit/tools/test_paths.py`、`tests/unit/tools/test_text_files.py`

**依赖：** TS1

**步骤：**

1. 实现工作区根目录规范化、现有文件/目录解析、新写入目标解析和显示路径转换。
2. 使用 `commonpath`、`normcase` 和真实路径拒绝绝对路径、`..`、符号链接及 Junction
   越界。
3. 对新目标先解析真实父目录，并要求父目录存在。
4. 实现 UTF-8、UTF-8 BOM、NUL 二进制检测、换行识别和逻辑 `\n` 规范化。
5. 实现同目录临时文件写入、刷新、关闭、原子提交和失败清理。
6. 新建文件拒绝并发覆盖；明确覆盖和编辑使用 `os.replace()`。
7. 覆盖混合换行、只读目标、目录目标、替换失败、超时和取消清理。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_paths.py tests/unit/tools/test_text_files.py -q
```

### TS3：实现读取、写入和精确编辑工具

**文件：**
`ycode/tools/builtin/__init__.py`、`ycode/tools/builtin/read_file.py`、
`ycode/tools/builtin/write_file.py`、`ycode/tools/builtin/edit_file.py`、
`tests/unit/tools/test_file_tools.py`

**依赖：** TS1、TS2

**步骤：**

1. 为三个工具分别定义直接继承 Pydantic `BaseModel` 的参数模型，并拒绝额外字段。
2. 实现 `read_file` 的带行号分页、2,000 行和 100 KiB 双重上限及截断元信息。
3. 实现 `write_file` 的新建、显式覆盖、父目录要求、UTF-8 无 BOM 和原子提交。
4. 实现 `edit_file` 的逻辑换行字面唯一匹配、零/多匹配错误和 no-change 错误。
5. 编辑成功时保持 BOM 与换行风格；任何失败都不得修改目标。
6. 将可预期失败转换为稳定 `ToolError`，不包含文件全文。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_file_tools.py -q
```

### TS4：实现文件发现、忽略规则、Glob 和 Grep

**文件：**
`ycode/tools/ignore.py`、`ycode/tools/builtin/glob.py`、
`ycode/tools/builtin/grep.py`、`tests/unit/tools/test_search_tools.py`

**依赖：** TS1、TS2

**步骤：**

1. 使用 `pathspec` 读取工作区根 `.gitignore`，始终排除 `.git/`。
2. 遍历时不默认排除其他点目录，并确保解析后的候选文件仍在工作区内。
3. 实现工作区相对 POSIX Glob、普通文件过滤、稳定排序、默认数量和硬上限。
4. 实现逐行 Python 正则搜索、路径范围、文件模式、大小写和稳定排序。
5. 无效正则返回受控失败；二进制和非 UTF-8 文件跳过并计数。
6. 搜索按行处理，达到结果上限后显式标记截断，不把文件全集读入结果内存。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_search_tools.py -q
```

### TS5：实现 PowerShell 命令后端和 run_command

**文件：**
`ycode/tools/command.py`、`ycode/tools/builtin/run_command.py`、
`tests/unit/tools/test_command.py`

**依赖：** TS1、TS2

**步骤：**

1. 定义 `CommandRunner` Protocol 和不可变 `CommandResult`。
2. 使用 `asyncio.create_subprocess_exec()` 实现固定的 `PowerShellCommandRunner`。
3. 使用进程 `cwd` 参数设置工作目录，不使用 `shell=True` 或拼接 `Set-Location`。
4. 并发排空 stdout/stderr，合计只保留 100 KiB，达到上限后继续排空。
5. 返回退出码、两路输出、耗时和截断状态。
6. 超时或取消时调用 `taskkill /PID ... /T /F`，等待进程树及管道清理。
7. 实现 `run_command` 参数、工作区目录校验、120 秒固定超时元数据和非零退出结果。
8. 使用真实本机 PowerShell 测试成功、stderr、非零退出、超量输出、超时和子进程终止。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_command.py -q
```

### TS6：实现注册中心、内建工厂和统一执行器

**文件：**
`ycode/tools/registry.py`、`ycode/tools/executor.py`、`ycode/tools/__init__.py`、
`tests/unit/tools/test_registry.py`、`tests/unit/tools/test_executor.py`

**依赖：** TS3、TS4、TS5

**步骤：**

1. 实现显式注册、重复名称拒绝、按名查找和按访问分类过滤定义。
2. 实现 `create_builtin_registry(...)`，按固定顺序装配六个工具及依赖。
3. Executor 按“查找、权限、thaw、Pydantic 校验、超时、执行”顺序工作。
4. 将未知工具、访问拒绝、无效参数、受控失败、超时和普通异常转换为结构化结果。
5. 整理 Pydantic 错误字段路径，不回显完整参数或 traceback。
6. `CancelledError` 在工具清理后继续传播，`KeyboardInterrupt/SystemExit` 不包装。
7. 验证普通模式六个定义、plan-only 三个读取定义及执行边界二次拦截。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_registry.py tests/unit/tools/test_executor.py -q
```

### TS7：实现读取并发与写入屏障调度

**文件：**
`ycode/tools/scheduler.py`、`tests/unit/tools/test_scheduler.py`

**依赖：** TS6

**步骤：**

1. 按模型原始位置把连续 READ 调用切成并发批次。
2. WRITE 等待此前读取全部结束并单独串行执行，阻止后续读取提前。
3. 工具开始事件按启动时产生，完成事件按实际完成时间产生。
4. 最终记录按原始 `position` 排序，保持 ToolCall 与 ToolResult 对应关系。
5. 取消并等待所有已启动任务，不再启动队列中剩余调用。
6. 为每个已经开始的调用产生完成、失败或取消终态。
7. 使用可控虚拟工具验证并发重叠、屏障时序、失败隔离、稳定回填和取消。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_scheduler.py -q
```

### TS8：建立 Agent 契约、事件、提示和可取消 Turn

**文件：**
`ycode/agent/__init__.py`、`ycode/agent/contracts.py`、`ycode/agent/events.py`、
`ycode/agent/prompt.py`、`tests/unit/agent/test_contracts.py`、
`tests/unit/agent/test_prompt.py`

**依赖：** TS1、TS6

**步骤：**

1. 定义 `AgentMode`、四种 `AgentTermination`、`AgentTurnResult`。
2. 定义完整 AgentEvent 冻结数据类和联合类型，不引用供应商 SDK。
3. 定义 `AgentTurn` 与 `ConversationRunner` Protocol。
4. 实现 Turn 的结果生命周期和 `cancel()` 信号；完整消费前结果不可用。
5. 实现最小 `SystemPromptBuilder`，包含工作区、PowerShell、允许工具和当前模式。
6. plan-only 提示只要求调查和输出计划，不增加复杂模板。
7. 验证事件不可变、字段合法、终态互斥且最终回复最多一次。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_contracts.py tests/unit/agent/test_prompt.py -q
```

### TS9：扩展 Anthropic Provider 请求接口

**文件：**
`ycode/core/provider.py`、`ycode/core/__init__.py`、
`ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`、
`tests/integration/test_anthropic_stream.py`

**依赖：** TS1、TS6

**步骤：**

1. 保留 `ChatProvider`，增加带默认关键字参数的 `AgentChatProvider` Protocol。
2. 扩展 Anthropic `stream_chat()` 接收 `system_prompt` 和工具定义。
3. 只在非空时增加 Anthropic 顶层 `system`、`tools` 请求字段。
4. 将冻结的供应商无关 Schema 转成普通 Anthropic `input_schema`。
5. 不修改现有消息历史、工具调用分片、工具结果、停止原因和错误映射。
6. 验证无工具调用时原请求结构保持兼容。
7. 验证工具定义顺序、Schema、system、分片工具调用和 ToolResult 历史往返。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q
.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q
```

### TS10：实现 ReAct AgentLoop

**文件：**
`ycode/agent/loop.py`、`tests/support/fake_provider.py`、
`tests/unit/agent/test_loop.py`

**依赖：** TS7、TS8、TS9

**步骤：**

1. 每次 Provider 调用创建独立 `ResponseAssembler`，并实时转换 Thinking/Text 事件。
2. 实现 StopReason 与 ToolCallBlock 的完整组合校验。
3. 对 `TOOL_USE + 工具调用` 执行 Scheduler，按原序创建 ToolResultBlock 用户消息。
4. 工具失败作为结果继续循环，系统异常以 AgentErrorEvent 终止。
5. 对 `END_TURN + 无工具调用` 产生唯一 FinalResponseEvent。
6. 实现默认 10 轮和构造注入；第 10 个工具轮执行后以 LIMIT_REACHED 结束。
7. 普通模式提供全部工具，plan-only 只提供 READ 工具并保留执行侧拦截。
8. 取消 Provider、读取批次、写屏障和工具后等待清理，产生完整取消事件。
9. 验证最终结果只包含本轮消息，过程中不修改 Session 历史。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q
```

### TS11：实现 PlainChatRunner 与 ChatSession 事务

**文件：**
`ycode/agent/plain.py`、`ycode/session/chat.py`、`ycode/session/__init__.py`、
`tests/unit/agent/test_plain.py`、`tests/unit/session/test_chat.py`

**依赖：** TS8、TS10

**步骤：**

1. PlainChatRunner 包装现有单次 ChatProvider 流，不传工具、system 或进入循环。
2. ChatSession 改为依赖 ConversationRunner，持有历史、模式、活动 Turn 和关闭状态。
3. Session 产生 UserMessageEvent，并把当前模式快照交给运行器。
4. 正常完成时一次性提交本轮消息；错误、取消和上限不提交。
5. 暂存终态事件，先结束 Turn 和处理事务，再向 UI 转发。
6. 实现 `/plan`、`/agent` 精确命令和 ModeChangedEvent，不写入历史。
7. OpenAI 纯聊天路径保持 AGENT；意外 `/plan` 只返回不支持提示且不调用 Provider。
8. 实现并发回合拒绝、`cancel_active_turn()` 和幂等 `close()`。
9. 验证 Provider/组装错误、消费者取消、模式命令和历史快照一致性。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_plain.py tests/unit/session/test_chat.py -q
```

### TS12：迁移 InputBox、Renderer 和 TerminalUI

**文件：**
`ycode/ui/input_box.py`、`ycode/ui/renderer.py`、`ycode/ui/terminal.py`、
`tests/unit/ui/test_input_box.py`、`tests/unit/ui/test_renderer.py`、
`tests/unit/ui/test_terminal.py`

**依赖：** TS11

**步骤：**

1. InputBox 接收当前模式，在右下角显示完整或降级模式文本。
2. 使用纯布局函数保证宽终端同时显示帮助和模式，窄终端优先保留模式。
3. Renderer 按轮保存 Thinking、过程文本、工具摘要和最终文本。
4. 流式文本先按纯文本显示；只有 FinalResponseEvent 对应轮转换为 Markdown。
5. 为六个工具生成安全、截断的开始与完成摘要，不显示写入内容或完整结果。
6. TerminalUI 只消费 AgentEvent，不导入或判断 StreamEnd。
7. 正确处理最终、上限、取消、错误和模式事件，所有路径停止计时与 Rich Live。
8. 等待输入时 Ctrl+C/EOF 退出；活动回合 Ctrl+C 取消本轮并恢复输入。
9. 验证多轮过程不混入最终 Markdown，并发工具完成顺序按事件展示。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py tests/unit/ui/test_renderer.py tests/unit/ui/test_terminal.py -q
```

### TS13：完成应用装配和测试基础设施

**文件：**
`ycode/app.py`、`tests/support/fake_provider.py`、`tests/support/sse_server.py`、
`tests/unit/test_app.py`、`tests/unit/test_cli.py`

**依赖：** TS6、TS10、TS11、TS12

**步骤：**

1. 应用根据已加载的协议配置装配 Anthropic AgentLoop 或 OpenAI PlainChatRunner。
2. 为 Anthropic 装配工作区、路径/文本服务、PowerShell Runner、六工具 Registry、
   Executor、Scheduler 和默认 10 轮 AgentLoop。
3. 不通过 Provider 实例类型判断能力，不向 OpenAI 注入工具组件。
4. 保持 Provider、Session 和 UI 的关闭所有权清楚且异常时也能清理。
5. 扩展本机 SSE 测试服务，支持按请求序列返回多轮响应并记录请求体。
6. 扩展 FakeProvider，支持取消、异常、停止原因和工具轮脚本。
7. 回归 CLI 参数、配置发现、ProviderFactory 注入和应用关闭。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/test_cli.py -q
```

### TS14：完成集成测试与真实 Windows PTY 验收

**文件：**
`tests/integration/test_anthropic_stream.py`、
`tests/integration/test_openai_stream.py`、
`tests/e2e/test_terminal_chat.py`

**依赖：** TS9–TS13

**步骤：**

1. 用本机 Anthropic SSE 脚本执行“工具调用—结果回填—继续调用—最终回复”。
2. 检查每轮请求历史、工具定义、system、ToolCall/ToolResult 对应和最终停止。
3. 验证 plan-only 只发送读取工具，并在伪造写调用时被执行边界拒绝。
4. 运行 OpenAI 单元外的 SSE 和完整 UI 纯聊天回归，确认请求无 tools/system。
5. 在真实 Windows PTY 临时工作区中覆盖读文件、Glob、Grep、写文件、精确编辑和命令。
6. 覆盖工具失败后模型调整、并发读取、写屏障、十轮上限和 Provider 异常。
7. 活动命令期间发送 Ctrl+C，验证进程树结束、取消事件可见、历史不提交并恢复输入。
8. 覆盖 `/plan`、`/agent`、右下角模式、窄终端、最终 Markdown 和正常退出。
9. 测试只连接本机模拟服务，不使用真实 API Key 或外部网络。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py tests/integration/test_openai_stream.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q
```

### TS15：完整质量门禁与范围检查

**文件：** 全部受影响代码、测试和功能文档

**依赖：** TS1–TS14

**步骤：**

1. 运行 Ruff 格式检查、静态检查、完整测试和编译检查。
2. 运行两个 CLI 入口的帮助命令。
3. 搜索确认 TerminalUI 不再消费 StreamEnd。
4. 搜索确认工具与 Agent 层不导入供应商 SDK。
5. 搜索确认 OpenAIProvider 未获得 tools/system/AgentLoop 实现。
6. 检查临时文件、PowerShell 子进程、Rich Live 和异步任务均无残留。
7. 检查错误和测试输出不包含 API Key、完整环境变量、traceback 或大段工具参数。
8. 按后续批准的 `checklist.md` 逐项记录实际结果。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

## 执行顺序

```text
TS1 → TS2
       ├──→ TS3 ─┐
       ├──→ TS4 ─┼──→ TS6 → TS7 ─┐
       └──→ TS5 ─┘                │
                         TS6 → TS8 ├──→ TS10 → TS11 → TS12 → TS13 → TS14 → TS15
                         TS6 → TS9 ┘
```

任务按编号及依赖执行。局部验证未通过时不得开始依赖任务。

## Spec 与 Plan 覆盖

| 能力 | 任务 |
| --- | --- |
| 工具契约、Pydantic Schema、结构化结果 | TS1、TS6 |
| 工作区边界、编码、原子写入 | TS2–TS3 |
| read/write/edit | TS3 |
| glob/grep、`.gitignore` | TS4 |
| PowerShell、输出上限、进程树清理 | TS5 |
| 注册、权限、超时和安全错误 | TS6 |
| 读取并发与写入屏障 | TS7 |
| Agent 状态、事件和最小提示 | TS8 |
| Anthropic tools/system 请求 | TS9 |
| ReAct 循环、停止原因、轮数和取消 | TS10 |
| 会话事务、模式、OpenAI 纯聊天 | TS11 |
| TUI 多轮展示和模式状态 | TS12 |
| 应用装配与本机模拟服务 | TS13 |
| Anthropic/OpenAI 集成与真实 PTY | TS14 |
| 完整质量门禁与范围检查 | TS15 |

已批准 Spec 与 Plan 中的每项能力均有对应实现任务和验证方式，任务依赖无循环。
