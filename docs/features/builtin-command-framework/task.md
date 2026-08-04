# YCode 内置命令框架 Tasks

> 状态：已批准

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `ycode/commands/__init__.py` | 导出命令框架公共接口 |
| 新建 | `ycode/commands/contracts.py` | 命令定义、调用、分类、补全和 UI 控制契约 |
| 新建 | `ycode/commands/errors.py` | 定义、冲突、用法和安全执行错误 |
| 新建 | `ycode/commands/registry.py` | 命令注册、冲突检测、查询和稳定枚举 |
| 新建 | `ycode/commands/parser.py` | 斜杠输入解析 |
| 新建 | `ycode/commands/dispatcher.py` | 异步分发、未知命令和错误边界 |
| 新建 | `ycode/commands/builtin.py` | 内置命令处理器和显式运行时工厂 |
| 修改 | `ycode/session/chat.py` | 命令运行时、双文本回复入口和可复用会话操作 |
| 新建 | `ycode/ui/command_completion.py` | prompt_toolkit 命令补全适配 |
| 修改 | `ycode/ui/input_box.py` | 可选补全器、帮助提示和候选菜单 |
| 修改 | `ycode/ui/styles.py` | 候选菜单的最小样式 |
| 修改 | `ycode/ui/terminal.py` | 输入分流和 `UIController` 实现 |
| 修改 | `ycode/app.py` | 仅为 Anthropic 装配命令运行时 |
| 新建 | `tests/unit/commands/test_contracts.py` | 命令契约测试 |
| 新建 | `tests/unit/commands/test_registry.py` | 注册和冲突测试 |
| 新建 | `tests/unit/commands/test_parser.py` | 输入解析测试 |
| 新建 | `tests/unit/commands/test_dispatcher.py` | 分发和错误边界测试 |
| 新建 | `tests/unit/commands/test_builtin.py` | 内置命令元数据、帮助和处理器测试 |
| 新建 | `tests/unit/ui/test_command_completion.py` | 补全候选适配测试 |
| 修改 | `tests/unit/ui/test_input_box.py` | 帮助提示、Tab 和候选菜单测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | 命令分流及 UI 控制器测试 |
| 修改 | `tests/unit/session/test_chat.py` | 双文本历史和可复用会话操作测试 |
| 修改 | `tests/unit/test_app.py` | Anthropic 命令运行时装配测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | Windows PTY 内置命令完整流程 |
| 修改 | `README.md` | 帮助、补全和现有命令说明 |

## T1：定义命令核心契约与错误

**文件：** `ycode/commands/contracts.py`、`ycode/commands/errors.py`、
`tests/unit/commands/test_contracts.py`  
**依赖：** 无

**步骤：**

1. 定义 `CommandKind` 的 `local`、`state` 和 `ai` 三个值。
2. 定义不可变的 `CommandInvocation`、`CommandDefinition` 和
   `CommandCompletionEntry`。
3. 定义异步 `CommandHandler` 和 `UIController` Protocol，包含 Plan 批准的全部方法。
4. 定义不可变 `CommandRuntime`，使用前向类型避免契约模块依赖具体实现。
5. 定义命令定义错误、冲突错误、用法错误和安全执行错误；安全错误只保存可展示摘要。
6. 测试不可变性、字段归一化边界、枚举值、Protocol 形状和错误消息校验。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands/test_contracts.py
```

期望契约测试全部通过，命令模块不导入 Rich 或 prompt_toolkit。

## T2：实现原子命令注册中心

**文件：** `ycode/commands/registry.py`、`tests/unit/commands/test_registry.py`  
**依赖：** T1

**步骤：**

1. 实现命令名称的小写归一化和字母开头、字母数字连字符格式校验。
2. 使用同一个索引保存规范名称与别名，并保留定义注册顺序。
3. 在写入前检查定义内部重复、已有规范名称、已有别名和交叉冲突。
4. 保证任一校验失败时定义列表和名称索引都不发生变化。
5. 实现大小写无关 `resolve()`、注册顺序 `visible_definitions()` 和按文本排序的
   `completion_entries()`。
6. 验证隐藏命令可直接解析，但不会进入可见定义或补全条目。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands/test_registry.py
```

期望名称、别名、大小写、原子失败、稳定排序和隐藏规则测试全部通过。

## T3：实现斜杠命令解析器

**文件：** `ycode/commands/parser.py`、`tests/unit/commands/test_parser.py`  
**依赖：** T1

**步骤：**

1. 对输入去除两端空白，非 `/` 前缀返回 `None`。
2. 按第一个空白字符拆分命令词和参数，不解析引号、管道或嵌套语法。
3. 去除命令词的 `/` 并转小写；参数只去除分隔空白，保留大小写和内部空白。
4. 让 `/`、`/ help` 和 `//value` 继续产生斜杠调用，交由分发器按未知命令处理。
5. 覆盖大小写、空白字符、外层空白、参数保真和普通输入测试。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands/test_parser.py
```

期望解析器测试全部通过。

## T4：实现命令分发与安全错误边界

**文件：** `ycode/commands/dispatcher.py`、
`tests/unit/commands/test_dispatcher.py`  
**依赖：** T1、T2、T3

**步骤：**

1. 实现 `try_dispatch()` 的非命令 `False` 与全部斜杠输入 `True` 语义。
2. 未知命令先通过控制器显示原始输入，再显示统一 `/help` 引导。
3. 本地和状态命令在执行处理器前显示原始输入；AI 命令把展示交给处理器的
   `send_user_message()`。
4. 将 `CommandUsageError` 转为参数错误加命令用法，将 `CommandExecutionError` 转为
   安全业务消息。
5. 将未预期异常转换为不含异常详情的通用消息，并让 `CancelledError` 原样传播。
6. 使用完整 UIController 替身验证调用顺序、未知命令、隐藏命令直达、错误隔离和取消。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands/test_dispatcher.py
```

期望分流、展示、错误和取消测试全部通过。

## T5：注册内置命令并生成帮助

**文件：** `ycode/commands/builtin.py`、`ycode/commands/__init__.py`、
`tests/unit/commands/test_builtin.py`  
**依赖：** T1、T2、T3、T4

**步骤：**

1. 实现无参数、可选单参数和必需单参数的局部校验辅助函数。
2. 实现 `/help` 列表与详情处理器，内容只读取当前注册中心元数据。
3. 实现 `/exit` 与 `/quit`、`/plan`、`/agent`、`/mcp`、`/compact`、
   `/permission` 和 `/resume` 处理器。
4. 权限参数采用大小写无关匹配；`/resume` 将完整剩余文本作为不透明会话 ID，保留
   原始大小写和内部空格，仅拒绝空 ID。
5. 状态处理器在成功操作后显式调用 `refresh_status()`；退出处理器不刷新下一输入。
6. 按批准顺序构建 `CommandRuntime`，导出框架公共接口。
7. 测试元数据完整性、唯一别名、固定顺序、帮助详情、隐藏帮助、参数错误和控制器调用。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands/test_builtin.py
```

期望八个生产命令、`quit` 别名、帮助和参数行为测试全部通过。

## T6：分离会话展示文本与模型文本

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T1

**步骤：**

1. 将 `stream_reply()` 的主参数改为 `model_text`，增加可选 `display_text`。
2. 使用 `model_text` 创建传给 Runner 的用户消息，使用展示文本创建
   `UserMessageEvent`。
3. 保持未传 `display_text` 时请求、事件和历史完全一致。
4. 测试显示原始斜杠命令而 Provider 收到展开提示词的情况。
5. 测试成功时只提交展开提示词，Provider 失败和取消时不提交任何一方。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py
```

期望双文本测试和现有会话测试全部通过。

## T7：提取模式与权限会话操作

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T6

**步骤：**

1. 提取模式切换操作，返回现有 `ModeChangedEvent`，并保留运行器能力校验。
2. 提取权限状态、权限模式切换和会话授权清理操作，返回现有权限事件。
3. 让未装配命令框架时的旧斜杠兼容分支调用这些操作。
4. 保持非法模式、权限不可用、清理数量和不进入历史的当前行为。
5. 增加直接调用新会话操作的测试，并让原命令测试继续通过。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k "mode or permission"
```

期望模式与权限的新旧入口测试全部通过。

## T8：提取可取消压缩与恢复兼容操作

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T6、T7

**步骤：**

1. 将手动压缩分支提取为公开异步事件流，继续维护活动压缩任务和完成事件。
2. 让旧 `/compact` 兼容分支转发该事件流，不复制摘要、检查点或回滚逻辑。
3. 保持 `restore()` 为公开原子恢复操作，并让旧 `/resume` 分支只负责兼容解析和错误转换。
4. 验证压缩成功、无需压缩、摘要失败、存储失败和取消仍保持当前状态边界。
5. 验证恢复成功重置状态、失败保持当前会话，并保证会话 ID 的大小写和内部空格保真。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k "compact or resume"
```

期望压缩和恢复测试全部通过。

## T9：为 Anthropic 装配可选命令运行时

**文件：** `ycode/session/chat.py`、`ycode/app.py`、`tests/unit/test_app.py`  
**依赖：** T5、T8

**步骤：**

1. 为 `ChatSession` 增加只读可选 `command_runtime`，默认值为 `None`。
2. 在 Anthropic 应用分支创建内置命令运行时，并随现有依赖交给会话。
3. 将内置定义或冲突错误转换为不含内部堆栈的启动配置错误。
4. OpenAI 分支不创建或访问命令运行时，保持现有装配参数和行为。
5. 只增加 Anthropic 装配断言，不新增或修改 OpenAI 测试场景。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_app.py
```

期望应用装配测试全部通过，Anthropic 会话包含八个生产命令。

## T10：实现 prompt_toolkit 命令补全适配

**文件：** `ycode/ui/command_completion.py`、
`tests/unit/ui/test_command_completion.py`  
**依赖：** T2

**步骤：**

1. 实现同步 `CommandCompleter`，只读取注册中心的补全条目。
2. 只在光标前文本是无空白的 `/前缀` 时生成候选。
3. 候选文本包含 `/`，替换当前完整命令词，并把命令描述作为附加说明。
4. 规范名称和公开别名按文本稳定排序；隐藏命令不出现。
5. 参数区、普通文本、光标位于命令词中间和无匹配时返回空候选。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_command_completion.py
```

期望补全适配测试全部通过，测试期间不调用任何处理器。

## T11：接入输入框帮助提示与候选菜单

**文件：** `ycode/ui/input_box.py`、`ycode/ui/styles.py`、
`tests/unit/ui/test_input_box.py`  
**依赖：** T10

**步骤：**

1. 为 `InputBox` 增加可选 `Completer` 和可选帮助提示，默认保持当前行为。
2. 让 `format_hint()` 使用传入提示，并继续优先显示右侧模式和权限信息。
3. 使用 `FloatContainer` 和 `CompletionsMenu` 包装现有四行输入布局，不改变基础顺序。
4. 绑定 Tab：单匹配完成文本，多匹配打开浮动候选菜单；禁止输入时自动补全。
5. 为候选菜单增加与现有低对比度风格一致的最小样式。
6. 更新布局断言，验证默认提示、命令提示、宽窄降级、单匹配、多匹配和退出清理。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_input_box.py tests/unit/ui/test_command_completion.py
```

期望输入框和补全测试全部通过。

## T12：提取 TerminalUI 的通用消息消费能力

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T6、T11

**步骤：**

1. 将现有 Agent 事件消费、Renderer、工具审批和中断监听提取为可复用私有流程。
2. 实现 `show_user_input()` 和 `show_system_message()`，保持现有用户消息样式。
3. 实现 `send_user_message(display_text, model_text)`，调用双文本会话入口并复用完整事件流。
4. 保持普通 Agent 的 Thinking、工具状态、审批、取消、完成和失败渲染顺序。
5. 测试普通消息和双文本消息均只创建一个 Agent 回合，取消后可以继续输入。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py -k "conversation or message or cancel"
```

期望消息消费、双文本和取消测试全部通过。

## T13：接入命令分发、帮助和未知命令

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T5、T9、T12

**步骤：**

1. 会话包含命令运行时时，为默认输入框创建命令补全器并设置 `/help for commands`。
2. 主循环在空输入后先调用分发器，只有返回 `False` 时才发送普通 Agent 消息。
3. 验证 `/help`、详细帮助、未知命令和单独 `/` 都回到输入状态且不调用 Provider。
4. 使用隐藏测试 AI 命令验证原始输入展示和展开提示词提交。
5. 未配置命令运行时时保持现有输入框、`/exit`、`/quit` 和普通输入路径。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py -k "help or unknown or command or prompt"
```

期望帮助、未知命令、AI 分流和兼容路径测试全部通过。

## T14：实现模式与权限 UI 控制方法

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T7、T13

**步骤：**

1. 实现模式设置并复用 `ModeChangedEvent` 输出。
2. 实现权限状态查询、权限模式设置和临时授权清理，复用现有权限事件输出。
3. 实现 `refresh_status()`，确保下一次输入框读取最新模式和权限状态。
4. 验证参数错误不调用会话操作，成功与查询均不调用 Provider 或进入历史。
5. 验证 `/plan`、`/agent` 和 `/permission` 后的下一输入提示状态。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py -k "mode or permission"
```

期望模式、权限和提示刷新测试全部通过。

## T15：实现 MCP 状态命令控制方法

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T13

**步骤：**

1. 实现 `show_mcp_status()`，读取会话快照并复用现有 MCP 表格渲染。
2. 状态缺失时抛出不含内部详情的 `CommandExecutionError`。
3. 验证 `/mcp` 不调用普通 Agent、不进入历史且保留现有状态摘要内容。
4. 验证额外参数显示 `/mcp` 用法而不是发送给模型。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py -k mcp
```

期望 MCP 可用、不可用和参数错误测试全部通过。

## T16：实现可取消的压缩命令控制方法

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T8、T13

**步骤：**

1. 实现 `compact_context()`，消费公开压缩事件流并复用现有结果渲染。
2. 在压缩期间复用 `wait_for_interrupt()` 和 `ChatSession.cancel_active_turn()`。
3. 成功完成后刷新状态；失败、无需压缩和取消时返回下一次输入。
4. 验证压缩命令不进入普通历史，检查点写入失败仍保持原历史。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py tests/unit/session/test_chat.py -k compact
```

期望压缩成功、失败、无需压缩、存储失败和取消测试全部通过。

## T17：实现恢复与正常退出控制方法

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T8、T13

**步骤：**

1. 实现 `resume_session()`，渲染成功事件并把恢复错误转换为安全业务错误。
2. 成功恢复后刷新模式与权限；失败时保持当前会话和状态。
3. 实现退出标记和 `request_exit()`，幂等调用现有记忆整理后结束输入循环。
4. 让 `/exit` 与 `/quit` 使用同一处理器和退出收尾，EOF 与空闲 `Ctrl+C` 继续复用收尾。
5. 验证恢复参数错误、成功、失败、退出有无新增回合和别名行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py tests/unit/session/test_chat.py -k "resume or restore or exit or quit or memory"
```

期望恢复、退出和记忆整理测试全部通过。

## T18：执行命令框架集成回归

**文件：** `tests/unit/commands/`、`tests/unit/ui/`、
`tests/unit/session/test_chat.py`、`tests/unit/test_app.py`  
**依赖：** T1–T17

**步骤：**

1. 运行全部命令核心、UI、会话和应用装配测试。
2. 补齐跨模块遗漏：同一元数据驱动帮助、补全和分发，隐藏命令一致，别名一致。
3. 验证命令失败、Agent 失败、取消和会话存储失败均不会污染历史。
4. 检查 `ycode.commands` 不导入 Rich 或 prompt_toolkit，且不存在全局注册实例。
5. 检查本功能没有新增 OpenAI 配置、Provider 分支或测试场景。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands tests/unit/ui tests/unit/session/test_chat.py tests/unit/test_app.py
```

期望相关单元测试全部通过。

## T19：更新公开命令文档

**文件：** `README.md`  
**依赖：** T18

**步骤：**

1. 说明 `/help`、命令名称与别名补全及未知命令行为。
2. 列出第一期生产命令、`/quit` 别名和各命令参数入口。
3. 说明命令框架当前只在 Anthropic 路径启用，OpenAI 不在本期范围。
4. 检查文档没有声称支持参数补全、自定义命令或尚未实现的业务命令。

**验证：**

```powershell
rg -n "/help|/quit|/compact|/resume|Anthropic|Tab" README.md
```

期望公开文档包含已实现能力且没有超出 Spec 的说明。

## T20：完成 Windows PTY 命令框架场景

**文件：** `tests/e2e/test_terminal_chat.py`  
**依赖：** T18

**步骤：**

1. 使用本地 Anthropic SSE 模拟服务新增 `builtin_command_framework` PTY 场景。
2. 依次验证 `/help`、单匹配 Tab、多匹配候选和未知命令。
3. 验证模式、权限和 MCP 状态命令。
4. 准备已提交历史后验证 `/compact`，再验证 `/resume` 与 `/quit` 正常收尾。
5. 确认命令文本未作为普通历史发送、候选菜单退出后无残留、测试未访问真实 API。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k builtin_command_framework
```

期望真实 Windows PTY 命令框架场景通过。

## T21：执行完整质量检查

**文件：** 全部本功能文件  
**依赖：** T19、T20

**步骤：**

1. 运行格式检查并修正本功能引入的格式问题。
2. 运行静态检查并修正本功能引入的问题。
3. 运行编译检查。
4. 运行完整测试套件，修复所有与本功能相关的回归。
5. 确认工作区原有未提交改动未被覆盖或清理。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条命令全部通过。

## 执行顺序

```text
T1 ─┬→ T2 ─┬→ T4 → T5 ───────────────┐
    └→ T3 ─┘                          │
                                     ├→ T9 ─────────────┐
T1 → T6 → T7 → T8 ───────────────────┘                 │
                                                       ├→ T12 → T13 ─┬→ T14 ─┐
T2 → T10 → T11 ────────────────────────────────────────┘             ├→ T15 ─┤
                                                                     ├→ T16 ─┤
                                                                     └→ T17 ─┘
                                                                              ↓
                                                      T18 ─┬→ T19 ─┐
                                                           └→ T20 ─┴→ T21
```

T2 与 T3 可在 T1 后并行；T6–T8 与 T2–T5 可独立推进；T10–T11 可在注册中心稳定后
独立推进。T14–T17 在 T13 后按文件顺序执行，不并行修改 `terminal.py`；T19 与 T20
可以在 T18 后并行准备。
