# YCode 工具系统与 Agent Loop Checklist

> 状态：已批准（含 Agent 回合单标题展示修订）

## 使用说明

- Checklist 获得批准后才开始修改实现代码。
- 只有实际执行验证并观察到通过结果后，才能勾选对应项目。
- 任一必选项未通过时，不将本功能报告为完成。
- 文件和命令测试使用临时工作区；模型测试使用占位 Key 和本机模拟服务。
- 不读取或修改用户级 `.ycode/config.yaml`，不连接真实模型 API，不创建 Git commit。
- 最终报告必须列出实际执行命令、通过数量、失败项目和无法验证的项目。
- 本次增量修订只做功能性验收，不执行压力、性能、长时稳定性或复杂故障注入等生产级验证。

## 范围与架构

- [x] 工具、Agent、Session、Provider 和 UI 的依赖方向与已批准 Plan 一致。
- [x] 工具系统、AgentLoop 和 AgentEvent 不导入 Anthropic/OpenAI SDK 类型。
- [x] Provider 仍然只负责单次模型请求，不执行工具或控制 Agent 循环。
- [x] ChatSession 只管理已提交历史、当前模式和整轮事务，不解析供应商事件。
- [x] 没有引入 Agent 框架、插件系统、子代理、权限审批或复杂提示系统。
- [x] 没有实现 Bash、POSIX Shell、macOS/Linux 命令后端或额外工具。
- [x] OpenAIProvider 没有增加 tools、system prompt 或 AgentLoop 能力。
- [x] 除 `pathspec` 外没有为本功能增加新的运行时依赖。

## 工具契约与注册（AC1）

- [x] 六个工具均提供唯一名称、描述、Pydantic 参数模型、JSON Schema、访问分类和异步执行能力。
- [x] 具体参数模型直接继承 Pydantic `BaseModel`，没有多余的共同参数基类。
- [x] 工具类通过泛型 Tool Protocol 结构化满足接口，不要求继承 BaseTool。
- [x] 参数模型是 JSON Schema 和运行时校验的唯一事实来源。
- [x] Schema 和结构化结果在核心层不可原地修改，交给 Provider 前可恢复成普通 JSON。
- [x] 注册中心拒绝重复名称并保持固定注册顺序。
- [x] 按名称能取得正确工具；未知名称产生 `unknown_tool` 结构化结果。
- [x] 普通模式可发现六个工具，plan-only 只发现三个 READ 工具。

## 工作区边界（AC2）

- [x] 合法的工作区相对路径可以访问。
- [x] 解析后仍在工作区内的绝对路径可以访问。
- [x] 工作区外绝对路径被拒绝。
- [x] 使用 `..` 越界的路径被拒绝。
- [ ] 通过符号链接越界的现有目标被拒绝。
- [x] Windows Junction 越界目标被拒绝。
- [x] 新写入目标先解析真实父目录，不能通过父目录链接越界。
- [x] 边界判断使用规范路径和 `commonpath`，不存在字符串前缀误判。
- [x] 所有返回模型和 UI 的文件路径统一为工作区相对 POSIX `/`。
- [x] 命令工作目录必须是工作区内的现有目录。

## read_file（AC3）

- [x] 能读取 UTF-8 和 UTF-8 BOM 文本。
- [x] 非 UTF-8、含 NUL 二进制、目录和不存在文件返回明确失败。
- [x] `offset`、`limit` 的默认值、最小值和最大值均正确校验。
- [x] 输出包含正确的行号、路径、实际范围、总行数和截断状态。
- [x] 单次最多返回 2,000 行或 100 KiB。
- [x] 字节截断不会产生无效 UTF-8。

## write_file（AC4）

- [x] 能在父目录已存在时创建 UTF-8 无 BOM 新文件。
- [x] 父目录不存在时失败且不自动创建目录。
- [x] 已有文件在 `overwrite=False` 时保持不变。
- [x] 已有文件只有显式 `overwrite=True` 时才完整替换。
- [x] 目录目标和越界目标被拒绝。
- [x] 新建冲突、替换失败、超时和取消不会留下部分文件。
- [x] 同目录临时文件在成功和失败路径中均得到正确提交或清理。

## edit_file（AC5）

- [x] `old_text` 恰好匹配一次时只替换该处。
- [x] 零次匹配返回 `match_not_found` 且文件不变。
- [x] 多次匹配返回 `multiple_matches` 和匹配数量且文件不变。
- [x] `old_text == new_text` 返回 `no_change` 且文件不变。
- [x] 编辑使用字面匹配而不是正则。
- [x] 参数中的逻辑 `\n` 能匹配 CRLF 文件。
- [x] 成功编辑保持原 UTF-8 BOM 状态和换行风格。
- [x] 编辑通过原子替换提交，失败、超时和取消时原文件可用。

## run_command（AC6）

- [x] 公开工具名为 `run_command`，执行后端固定为 PowerShell。
- [x] PowerShell 使用 `-NoProfile`、`-NonInteractive` 和进程 `cwd` 启动。
- [x] 实现不使用 `shell=True` 或拼接 `Set-Location`。
- [x] 默认在工作区根目录执行，可在合法工作区子目录执行。
- [x] 非法、越界、不存在或文件类型的 cwd 被拒绝。
- [x] 结果包含退出码、stdout、stderr、耗时和截断状态。
- [x] 非零退出码产生工具失败结果，但不会直接终止 Agent。
- [x] stdout 与 stderr 合计最多保留 100 KiB，截断后仍继续排空管道。
- [x] 默认 120 秒超时不能由模型覆盖。
- [x] 超时和取消后 PowerShell 及其子进程树均已终止。

## glob（AC7）

- [x] 支持工作区相对 POSIX `*`、`?` 和 `**`。
- [x] 只返回普通文件，不返回目录。
- [x] 返回路径使用 `/` 并稳定排序。
- [x] 工作区根 `.gitignore` 规则生效。
- [x] `.git/` 始终排除，未忽略的其他点目录仍可搜索。
- [x] 默认最多 200 条，参数硬上限 1,000 条。
- [x] 达到结果上限时明确标记截断。

## grep（AC8）

- [x] 使用 Python 正则逐行搜索，不支持跨行匹配。
- [x] 支持文件或目录搜索路径、文件模式、大小写和结果数量。
- [x] 无效正则返回 `invalid_regex`，不会导致 Agent 崩溃。
- [x] 结果包含相对路径、行号和匹配行，并按路径、行号稳定排序。
- [x] 根 `.gitignore` 和 `.git/` 排除规则生效。
- [x] 非 UTF-8 与二进制文件被跳过并在元信息中计数。
- [x] 默认最多 100 条，参数硬上限 500 条。
- [x] 达到结果上限时明确标记截断。

## 统一执行与结构化结果（AC9、AC10）

- [x] Executor 按查找、权限、参数校验、超时、执行的固定顺序工作。
- [x] 缺少字段、错误类型、越界数值和额外字段均产生 `invalid_arguments`。
- [x] Pydantic 错误只返回整理后的字段路径和安全消息。
- [x] 工具超时产生结构化失败，不使应用崩溃。
- [x] 普通意外异常转换为 `internal_error`，不返回 traceback。
- [x] `CancelledError` 完成资源清理后继续传播到 Agent 取消路径。
- [x] 每个 ToolExecutionResult 都包含主要内容、错误标记和冻结元信息。
- [x] 每个结果通过原 ToolCall ID 转成正确 ToolResultBlock。
- [x] 同一响应的多个 ToolResultBlock 按模型调用原顺序回填。
- [x] 模型能看到失败原因并在下一轮调整调用。
- [x] 完整工具结果进入 AgentEvent，UI 只显示安全摘要。

## 调度与写入屏障（AC14）

- [x] 连续 READ 工具确实并发执行。
- [x] WRITE 等待此前 READ 批次全部完成。
- [x] 多个 WRITE 工具不会重叠。
- [x] WRITE 后面的 READ 不会提前启动。
- [x] 并发读取完成事件按实际完成时间输出。
- [x] 回填模型的结果仍按原调用顺序排列。
- [x] 取消后不再启动尚未开始的工具。
- [x] 每个已开始工具都有完成、失败或取消终态事件。

## AgentLoop 与终止状态（AC11、AC12）

- [x] 一轮严格执行“Provider 响应—组装—判断—工具执行—结果回填”。
- [x] 每次 Provider 调用使用新的 ResponseAssembler。
- [x] 第一轮 `END_TURN + 无工具调用` 可以直接形成最终回复。
- [x] 多个工具轮后 `END_TURN + 无工具调用` 正常结束。
- [x] `TOOL_USE + 有工具调用` 执行工具并进入下一轮。
- [x] `TOOL_USE + 无工具调用` 和 `END_TURN + 有工具调用` 均以异常结束。
- [x] `MAX_TOKENS`、`STOP_SEQUENCE`、`CONTENT_FILTER` 和 `UNKNOWN` 均映射为异常。
- [x] 工具失败作为可观察结果继续循环，系统错误才终止 Agent。
- [x] 默认最大轮数为 10，并能在构造时注入其他值。
- [x] 第 10 个工具轮执行后不发起第 11 次请求，并产生 LIMIT_REACHED。
- [x] 每次用户对话最多产生一个 FinalResponseEvent。
- [x] Agent 对外只暴露 COMPLETED、LIMIT_REACHED、CANCELLED、ERROR 四种终止结果。

## 会话事务与模式（AC13、AC17）

- [x] Session 持有不可变历史快照、当前模式和唯一活动 Turn。
- [x] 本轮用户消息、中间 Assistant、ToolResult 和最终回复先进入临时上下文。
- [x] 只有 COMPLETED 才一次性提交本轮全部消息。
- [x] Provider 错误、组装错误、上限和取消不提交残缺历史。
- [x] 失败或取消不会破坏此前成功历史。
- [x] 已完成文件或命令副作用不会被错误地宣称已回滚。
- [x] `/plan` 和 `/agent` 精确、大小写不敏感地切换模式。
- [x] 模式命令不发送 Provider，也不进入对话历史。
- [x] `/plan xxx` 作为普通用户消息。
- [x] plan-only 请求只包含 READ 工具。
- [x] plan-only 执行边界再次拒绝 WRITE 调用。
- [x] plan-only 最终计划输出后不会自动退出模式。
- [x] OpenAI 纯聊天路径保持 AGENT，意外 `/plan` 不调用 Provider。

## AgentEvent 与 UI（AC15、AC16、AC18）

- [x] 事件流可观察用户消息、Thinking、过程文本、工具开始、工具结果和最终回复。
- [x] 模式变化、工具取消、Agent 取消、上限和错误均有明确事件。
- [x] AgentEvent 不包含供应商 SDK 类型。
- [x] StreamEnd 只在 Provider/Agent 内部使用，不被 TerminalUI 消费。
- [x] 中间 Provider 请求结束不会让 UI 提前完成。
- [x] 工具调用轮文本实时显示为过程文本，不混入最终 Markdown。
- [x] 最后一轮文本在 FinalResponseEvent 后完成 Markdown 渲染。
- [x] 工具摘要显示名称、安全参数、成功/失败、截断和必要元信息。
- [x] UI 不显示写入内容、完整命令输出、环境变量或异常对象。
- [x] 输入区右下角持续显示 `mode: agent` 或 `mode: plan-only`。
- [x] 宽终端同时显示帮助和模式，窄终端优先保留可识别模式。
- [x] 模式切换后立即更新状态并显示一次确认。
- [x] 最终、上限、取消和错误后计时、Rich Live 与输入状态均正确恢复。

### 工具状态单行收敛增量验收

- [x] 同一 `call_id` 的开始、审批等待和终态始终共用一个 UI 位置。
- [x] 工具成功、失败、审批拒绝或取消后，UI 只保留该调用的最终结果，不保留开始或等待状态。
- [x] 一轮内多个工具调用各保留一条最终结果，并按首次出现顺序展示。
- [x] 缺少先行开始事件时，终态事件仍能建立一条最终结果。
- [x] AgentEvent 的发送、工具审批、调度和结果回灌行为未改变。
- [x] 定向 UI 单元测试通过。
- [x] 现有六工具 Windows PTY 场景通过，最终回复可见且交互正常结束。

### 工具状态按轮就地展示增量验收

- [x] 工具状态归属于发起该调用的 `round_number`，不再作为 Renderer 全局末尾列表。
- [x] 每轮的工具状态显示在本轮 Thinking 和模型文本之后、下一轮内容之前。
- [x] 工具完成后仍在所属轮次原位覆盖为最终结果，同一调用不产生额外行。
- [x] 同一轮的多个工具仍按首次出现顺序显示，不同轮次的工具不混合。
- [x] TerminalUI 向 Renderer 传递开始、审批、完成和取消事件的正确 `round_number`。
- [x] AgentEvent、审批、调度和工具结果回灌行为未改变。
- [x] 定向 UI 单元测试和现有六工具 Windows PTY 场景通过。

### Agent 回合单 YCode 标题增量验收

- [x] 一次用户发起的 Agent 回合只显示一个 `● YCode` 标题。
- [x] UI 不显示 `round N` 或为 AgentLoop 内部后续轮次创建新的 YCode 块。
- [x] 后续模型文本在前一轮工具最终结果之后继续显示。
- [x] 最终轮 Markdown、工具结果原位覆盖、计时和终态恢复行为未改变。
- [x] Renderer 定向单元测试和现有六工具 Windows PTY 场景通过。
- [ ] 最终终端展示效果交由用户手动验收。

## 系统提示与 Provider（AC19、AC20、AC21）

- [x] 普通模式 system prompt 包含工作区、PowerShell、工具使用和失败调整说明。
- [x] plan-only prompt 包含只调查和输出实施计划的约束。
- [x] Prompt 不包含复杂模板、供应商对象、API Key 或用户环境变量。
- [x] Anthropic 请求顶层包含 Registry 提供的 system 和工具定义。
- [x] Anthropic 工具顺序、名称、描述和 input_schema 与 ToolDefinition 一致。
- [x] plan-only Anthropic 请求只包含三个读取工具。
- [x] 不传 system/tools 时 Anthropic 请求保持旧纯聊天结构。
- [x] Anthropic 流式工具 JSON 分片继续正确形成 ToolCallBlock。
- [x] ToolCall 和 ToolResult 历史继续正确转换为 `tool_use`、`tool_result`。
- [x] OpenAI 请求中没有新增 `tools` 或 Agent system prompt。
- [x] OpenAI 单元、SSE、Session/UI 和真实 PTY 纯聊天回归通过。

## 取消、资源、安全与有界性（AC22、AC23）

- [x] Provider 流期间可以取消，取消后不启动工具或下一轮请求。
- [x] 并发 READ 批次可以取消并等待全部已启动任务结束。
- [x] 等待 WRITE 屏障时可以取消，尚未开始的写入和后续工具不启动。
- [x] 文件原子写入期间取消不会留下临时文件或部分目标。
- [x] PowerShell 期间取消会终止完整进程树。
- [x] 用户取消产生 AgentCancelledEvent，临时历史不提交并恢复终端输入。
- [x] 外层任务取消完成必要清理后继续传播 CancelledError。
- [x] 模型轮数、工具时间、文件内容、搜索数量和命令输出硬上限均生效。
- [x] 所有截断均在结果和 UI 中明确可见。
- [x] 错误与事件不包含 API Key、认证头、完整环境变量或 traceback。
- [x] 错误不会无条件回显大段文件内容、编辑参数或命令输出。
- [x] 测试完成后不存在残留 PowerShell 子进程、临时文件或异步任务。

## 局部自动化验证

- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_contracts.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_paths.py tests/unit/tools/test_text_files.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_file_tools.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_search_tools.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_command.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_registry.py tests/unit/tools/test_executor.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/tools/test_scheduler.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_contracts.py tests/unit/agent/test_prompt.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_plain.py tests/unit/session/test_chat.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py tests/unit/ui/test_renderer.py tests/unit/ui/test_terminal.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/test_cli.py -q`

## 集成与真实交互（AC24）

- [x] Anthropic 本机 SSE 完成至少两个工具轮和最终回复。
- [x] 每轮请求历史、工具结果 ID、工具定义和 system prompt 均正确。
- [x] OpenAI 本机 SSE 纯聊天回归通过且请求无工具字段。
- [x] 真实 Windows PTY 临时工作区完成 read、glob、grep、write、edit 和 run_command。
- [x] 真实 PTY 中工具失败后模型能够调整并继续。
- [x] 真实 PTY 中 READ 并发和 WRITE 屏障顺序可观察且正确。
- [x] 真实 PTY 中 `/plan`、`/agent`、模式状态和写工具拦截正确。
- [x] 真实 PTY 中十轮上限不产生最终回复且恢复输入。
- [x] 真实 PTY 中活动命令 Ctrl+C 后进程树结束、历史回滚并恢复输入。
- [x] 真实 PTY 中最终 Markdown、错误恢复、退出和终端布局正常。
- [x] `.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py tests/integration/test_openai_stream.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q`

## 完整质量门禁

- [x] `.venv\Scripts\python.exe -m ruff format --check .`
- [x] `.venv\Scripts\python.exe -m ruff check .`
- [x] `.venv\Scripts\python.exe -m pytest -q`
- [x] `.venv\Scripts\python.exe -m compileall -q ycode tests`
- [x] `.venv\Scripts\python.exe -m ycode --help`
- [x] `.venv\Scripts\ycode.exe --help`
- [x] `git diff --check`
- [x] 最终实现范围与已批准 Spec、Plan、Tasks 一致。
- [x] 最终报告列出所有命令的实际结果和任何未通过项目。

## 验收结果

- 完整 pytest：237 passed，1 skipped。
- Windows PTY：14 passed。
- Ruff format、Ruff check、compileall、两个 CLI help：全部通过。
- 未通过或未验证项目：文件符号链接越界用例因当前 Windows 环境无创建权限而跳过；Junction 越界用例已通过。
