# YCode 交互式对话 Plan

> 状态：已批准

## 架构概览

YCode 首期采用单进程、单事件循环的分层架构。CLI 负责启动和退出码；配置层负责发现、解析与校验 YAML；Provider 层封装 Anthropic 和 OpenAI 官方异步 SDK；会话层维护当前进程内的多轮历史；TUI 层负责输入、流式展示、计时和最终 Markdown 渲染。上层只依赖统一事件，不接触供应商专用的请求或 SSE 类型。

```text
命令行入口
    ↓
配置发现 → YAML 解析/校验 → 活动 Provider 配置
    ↓
ProviderFactory → AnthropicProvider / OpenAIProvider
    ↓
ChatSession（多轮历史与单轮事务）
    ↓
TerminalUI
    ├── prompt_toolkit：异步输入
    ├── Rich Live：当前流式区域与计时
    └── Rich Markdown：完成后的整体回答渲染
```

依赖方向保持单向：`ui → session → core.Provider`，具体 Provider 只实现 `core.Provider` 定义的协议。配置层和 UI 不互相依赖，应用装配由 `app.py` 完成。

## 核心数据结构与接口

### `ProviderProtocol`

字符串枚举，取值为 `anthropic` 或 `openai`，用于配置校验和 Provider 工厂路由。

### `ProviderConfig`

使用 Pydantic 模型表示经过完整校验、可以创建 Provider 的活动配置：

- `name: str`：非空且在配置文件中唯一。
- `protocol: ProviderProtocol`：供应商协议。
- `model: str`：模型标识。
- `base_url: str`：协议兼容的 API 基础地址。
- `api_key: SecretStr`：解析后的密钥，只保存在内存中并隐藏字符串表示。
- `thinking: bool = False`：仅 Anthropic 可启用。

未激活配置不会直接构造成 `ProviderConfig`。

### `ProviderEntry`

使用轻量模型表示 YAML `providers` 中尚未激活的原始条目：

- `name: str`：唯一且非空，用于和 `active` 匹配。
- 保留该条目的其余原始字段，但不校验字段是否齐全、协议是否合法、`thinking` 是否适用，也不解析 `api_key` 环境变量。

该模型只承担配置索引职责，不能直接交给 ProviderFactory。

### `AppConfig`

- `active: str`：活动配置名称。
- `providers: list[ProviderEntry]`：只完成名称级校验的全部供应商条目。
- `active_provider: ProviderConfig`：从活动条目的原始字段单独解析并完整校验得到的可用配置。

配置加载采用两阶段模型：先建立 `AppConfig` 的顶层结构和名称索引，再只把活动条目转换成 `ProviderConfig`。因此未激活条目无法绕过工厂边界进入运行流程。

### `ChatMessage`

- `role: Literal["user", "assistant"]`
- `content: str`

历史只保存用户原文和模型最终回答的原始 Markdown 字符串。Thinking 内容不进入公共会话历史，也不写入磁盘。

### `StreamEventKind`

统一流事件枚举：

- `THINKING_DELTA`：Claude 思考文本增量。
- `TEXT_DELTA`：最终回答文本增量。
- `COMPLETED`：本轮流正常结束。

错误不作为普通流事件吞掉，而是抛出统一的 `ProviderError`，使失败路径和正常完成路径保持明确。

### `StreamEvent`

- `kind: StreamEventKind`
- `text: str = ""`：增量文本；完成事件为空。

### `ChatProvider`

使用 Python `Protocol` 定义统一异步接口：

- `stream_chat(messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]`：根据完整会话上下文产生统一流事件。
- `close() -> Awaitable[None]`：释放官方 SDK 的异步网络客户端。

Provider 实现负责协议专用消息格式转换、认证、官方 SDK 调用、流事件映射和错误整理。它不负责终端展示或通用会话历史提交。

### `ProviderError`

- `code: str`：稳定的内部错误类别，如 `authentication`、`rate_limit`、`network`、`server`、`stream`。
- `user_message: str`：经过整理、可直接显示且不含密钥的提示。
- `retryable: bool`：表示用户重新提交是否可能成功。

原始异常可以通过异常链保留给测试或调试，但不得直接打印到终端。

### `ChatSession`

- `history: list[ChatMessage]`：仅包含已成功完成的轮次。
- `stream_reply(user_text: str) -> AsyncIterator[StreamEvent]`：构造“既有历史 + 当前用户消息”，调用 Provider 并转发统一事件。
- `close() -> Awaitable[None]`：转交 Provider 关闭资源。

每轮对话采用事务式提交：收到 `COMPLETED` 后，才把当前用户消息和累计完整回答一起追加到 `history`。Provider 抛错、流中断或用户取消时，本轮用户消息、部分回答和 Thinking 均不写入历史，既有历史保持不变。

### `ResponseTimer`

UI 内部计时对象，使用 `time.perf_counter()`：

- `start()`：记录起点并清除上一轮状态。
- `elapsed`：返回当前或已冻结的秒数。
- `stop()`：记录结束点并冻结本轮总耗时。

它不进入 Provider 或 ChatSession，避免把展示状态混入业务会话。

### Provider 与 ChatSession 的关系

两者都是 YCode 自己实现的层，但处于不同职责边界：

| 层 | 定位 | 负责 | 不负责 |
|---|---|---|---|
| Provider | 对外协议适配器 | 把通用消息转换成供应商请求，调用官方 SDK，把供应商流转换成统一事件，整理 API 错误 | 决定保存哪些历史、控制 TUI、渲染文本 |
| ChatSession | 应用会话协调器 | 保存已成功轮次，组合多轮上下文，调用 Provider，累计本轮回答，成功提交或失败回滚 | 了解 Anthropic/OpenAI 的请求格式、SSE 事件和认证细节 |

调用方向始终是 `TerminalUI → ChatSession → ChatProvider`，事件沿调用结果反向返回。Provider 不会反过来调用 ChatSession，也不知道谁在消费事件。

```text
ChatSession
    │  通用 ChatMessage 历史
    ▼
ChatProvider 接口
    │
    ├── AnthropicProvider → Anthropic SDK/API
    └── OpenAIProvider    → OpenAI SDK/API

统一 StreamEvent
    ▲
    └──────── Provider 返回给 ChatSession，再交给 TUI
```

因此，新增供应商时只需要增加 Provider 适配器；修改历史提交、失败回滚等会话策略时只修改 ChatSession。两层通过 `ChatProvider` 接口解耦，测试 ChatSession 时可以注入 `FakeProvider`，不需要真实网络。

## 模块设计

### CLI 与应用装配

#### `cli.py`

职责：

- 解析可选的 `--config PATH`。
- 调用异步应用入口。
- 将配置错误映射为非零退出码。
- 将正常退出、`/exit`、`/quit` 和 `Ctrl+C` 收敛为无堆栈的安全退出。

#### `app.py`

职责：

- 串联配置发现、配置加载、Provider 工厂、ChatSession 和 TerminalUI。
- 使用 `try/finally` 确保 Provider 客户端关闭。
- 不包含任何供应商协议分支或具体渲染逻辑。

### 配置层

#### `config/discovery.py`

- 显式传入 `--config` 时，解析该路径并直接验证文件存在，不向上搜索。
- 未显式传入时，从当前工作目录开始逐级访问父目录，返回遇到的第一个 `.ycode/config.yaml`。
- 到达文件系统根目录仍未找到时，抛出包含搜索起点和目标相对路径的 `ConfigError`。

#### `config/loader.py`

- 使用 `yaml.safe_load` 读取 YAML。
- 第一阶段校验顶层 `active`、`providers` 结构、每个条目的非空 `name`、名称唯一性以及活动名称存在性；其余字段原样保留。
- 找到活动条目后，只复制该条目的原始映射进入第二阶段，避免修改 YAML 解析结果和未激活条目。
- 第二阶段只对活动条目把完整形态为 `${ENV_VAR}` 的 `api_key` 解释为环境变量引用；变量缺失时指出变量名。
- 使用 `ProviderConfig` 完成活动条目的类型、必填字段、协议及 `thinking` 适用范围校验。
- 未激活条目的非 `name` 字段不进入 `ProviderConfig`，不访问其环境变量，也不产生相应校验错误。
- 不回写配置文件，不在异常文本中插入解析后的密钥。

#### `config/models.py`

定义 `ProviderProtocol`、`ProviderEntry`、`ProviderConfig` 和 `AppConfig`。只有活动条目会构造成 `ProviderConfig`；活动 OpenAI 配置的 `thinking: true` 在该阶段失败，未激活条目不触发协议级校验。

### Provider 层

#### `providers/factory.py`

根据 `ProviderConfig.protocol` 创建对应 Provider。工厂返回 `ChatProvider` 接口，不向调用方暴露具体类型。以后增加协议时，主要修改注册映射并增加适配器。

#### `providers/anthropic.py`

- 使用官方 `AsyncAnthropic` 客户端和 Messages API。
- 将统一 `ChatMessage` 转换为 Anthropic 消息。
- `thinking: true` 时启用 Claude adaptive extended thinking；首期使用内部固定输出上限 `max_tokens=16000`，不增加第七个 YAML 字段。
- `thinking: false` 时显式发送 `{"type": "disabled"}`，不依赖服务端的默认 Thinking 模式；若模型不支持关闭则按 API 拒绝路径显示明确错误。
- 映射思考增量为 `THINKING_DELTA`，文本增量为 `TEXT_DELTA`，正常结束产生 `COMPLETED`。
- 仅在配置启用 Thinking 时向上转发 `thinking_delta`；关闭时丢弃兼容服务意外返回的 Thinking 增量，确保 TUI 不显示 Thinking 区域。
- Thinking 不写入通用历史；后续轮次仅携带上一轮最终回答文本。
- 将认证、限流、网络、服务端拒绝、模型不支持 Thinking 和流中断映射为 `ProviderError`。

#### `providers/openai.py`

- 使用官方 `AsyncOpenAI` 客户端和 Chat Completions API。
- 通过 `base_url`、`api_key`、`model` 和完整消息历史发起 `stream=True` 请求。
- 将文本增量映射为 `TEXT_DELTA`，正常结束产生 `COMPLETED`。
- 不产生 Thinking 事件；非文本或本阶段不支持的响应内容不伪装成普通文本。
- 将认证、限流、网络、服务端错误和流中断映射为与 Anthropic 相同结构的 `ProviderError`。

两个官方 SDK 在首期关闭 SDK 自动重试，避免一次用户提交在不可见的情况下重复等待；失败后由用户决定是否重新提交。

### 会话层

#### `session/chat.py`

- 拒绝空白输入，避免无意义 API 请求。
- 在每次调用前复制已提交历史，并追加当前用户消息作为请求上下文。
- 转发 Provider 的 Thinking、文本和完成事件，同时累计最终回答原文。
- 只有正常完成时提交这一轮历史。
- 对失败或取消执行回滚，不删除此前成功轮次。
- 不识别 Anthropic/OpenAI 专用类型。

### TUI 层

#### `ui/header.py`

宽终端时左侧显示固定蓝色 ASCII 猫图标，右侧显示 Provider、Protocol、Model 和 Thinking 状态，不显示配置文件路径：

```text
 /\_/\      Provider  local-claude
( o.o )     Protocol  anthropic
 > ^ <      Model     claude-...
  YCode     Thinking  on
```

窄终端时，信息区移动到图标下方，避免横向截断。首期颜色固定，不提供配置字段。

#### `ui/input_box.py`

- 使用 prompt_toolkit 的非全屏 `Application`、`Buffer` 和 `HSplit` 构建固定四行输入布局，不再把下横线伪装成 `bottom_toolbar`。
- 四行依次为：普通上横线、带输入指示符和占位符的单行 `BufferControl`、普通下横线、静态 `? for help` 提示。
- 上下横线使用相同字符、相同计算宽度和相同 `class:input-border` 样式；保留终端最后一列以避免自动换行。
- 蓝色输入指示符为 `❯`；终端不支持时回退为 `>`。
- 空输入时显示 `Send a message...` 占位提示。
- Application 在输入结束时清理动态四行布局，再由 TerminalUI 按现有规则打印用户正文，避免提示区进入对话滚动历史。
- `? for help` 使用独立的 `class:input-hint`，本期不绑定按键或命令处理。
- 用户提交后只显示消息正文，不显示 `You` 标签。

#### `ui/styles.py`

- 定义输入提示、占位符和提示区的固定默认样式。
- 提供 `InputBorderStyle` 或等价的轻量内部样式参数，默认颜色为低对比度灰色。
- InputBox 构造时接收该样式参数，并将同一个值应用到上下横线；提示区样式不从横线颜色继承。
- 该入口只用于代码注入和测试，不读取 YAML，也不形成用户主题配置系统。

#### `ui/timer.py`

实现 `ResponseTimer`。每轮非空输入提交后立即启动，按约 100 毫秒刷新一次显示值。即使尚未收到首个 SSE 增量，回答标题和计时也保持可见。完成、失败或取消时停止；下一轮提交调用 `start()` 后从零重新计时。

#### `ui/renderer.py`

- 使用 Rich Console 和 Live 管理当前回答区域。
- 本轮开始后立即显示蓝色 `● YCode` 标题和实时耗时。
- Claude Thinking 以 `◇ Thinking` 标题和纯文本增量显示。
- `TEXT_DELTA` 到达时，将内容作为纯文本按顺序追加到当前回答；流式阶段不解析 Markdown，并对 Rich markup 字符进行安全处理。
- `COMPLETED` 到达时，使用累计完整原文构造一次 Rich Markdown 渲染，替换 Live 区域中的纯文本回答，然后停止 Live 并保留总耗时。
- 失败时保留已经显示的部分纯文本，冻结耗时并显示整理后的错误；部分文本不进入会话历史。
- Markdown 最终渲染支持标题、粗体、斜体、行内代码、围栏代码块、列表、引用和链接；不额外实现语言级代码高亮或自定义主题系统。

#### `ui/terminal.py`

- 显示启动头部并循环读取输入。
- 协调 ChatSession 事件消费、LiveResponseRenderer 和计时刷新任务。
- 响应生成期间不同时激活新的输入提示，避免 `prompt_toolkit` 与 Rich 争用终端光标。
- 捕获 ProviderError 后恢复输入循环；捕获取消或退出后停止计时并释放界面资源。

### 错误层

#### `errors.py`

定义 `ConfigError`、`ProviderError` 和 `UIError`。用户可预期错误在边界层转为简洁提示；默认不打印 traceback、请求头、完整响应对象或密钥。

### 测试支持

#### `tests/support/fake_provider.py`

按预设顺序产生统一事件或错误，用于独立测试会话事务、TUI、计时和 Markdown 完成切换。

#### `tests/support/sse_server.py`

本机模拟 Anthropic Messages 和 OpenAI Chat Completions SSE：记录请求并可控制增量内容、跨增量 Markdown、延迟、状态码和中途断开。自动化测试只使用占位 Key。

## 完整数据流

### 启动流程

```text
用户执行 ycode [--config PATH]
    ↓
cli.py 解析参数
    ↓
discovery.py
    ├── 有 --config：只解析指定文件
    └── 无 --config：从 cwd 逐级向上查找 .ycode/config.yaml
    ↓
loader.py safe_load YAML
    ↓
第一阶段：校验 active、providers、name 和重名 → 找到活动原始条目
    ↓
第二阶段：只展开活动 api_key 环境变量 → ProviderConfig 完整校验
    ↓
AppConfig.active_provider
    ↓
ProviderFactory
    ├── anthropic → AsyncAnthropic → AnthropicProvider
    └── openai    → AsyncOpenAI    → OpenAIProvider
    ↓
ChatSession(provider)
    ↓
TerminalUI 显示头部和输入框
```

任何配置错误都在创建网络客户端和进入 TUI 前终止，并返回非零退出码。

### 单轮正常对话

```text
用户提交非空文本
    ↓
TerminalUI 隐藏输入提示，显示 ● YCode 和 0.0s
    ↓
ResponseTimer.start()，周期刷新任务启动
    ↓
ChatSession 复制已提交历史 + 当前用户消息
    ↓
ChatProvider.stream_chat(messages)
    ↓
官方异步 SDK 接收 SSE
    ↓
Provider 将供应商事件转换为统一 StreamEvent
    ├── THINKING_DELTA → 纯文本追加到 ◇ Thinking
    ├── TEXT_DELTA     → 原始文本追加到回答区和回答缓冲
    └── COMPLETED      → 通知本轮成功
    ↓
ChatSession 提交 user + 完整 assistant 原文到 history
    ↓
Renderer 用完整原文整体渲染 Markdown，替换流式纯文本
    ↓
ResponseTimer.stop()，显示冻结的总耗时
    ↓
重新显示输入框，等待下一轮；下一次提交重新从 0.0s 计时
```

### 失败与取消流程

```text
SDK/流异常 → ProviderError
    ↓
ChatSession 不提交当前轮次，保留旧历史
    ↓
Renderer 保留已显示的部分纯文本并显示安全错误
    ↓
计时冻结，用户返回输入状态
```

`Ctrl+C` 在输入阶段直接安全退出；在响应阶段先取消当前流和计时，再关闭 Provider 客户端并恢复终端。无论哪条退出路径，`app.py` 的 `finally` 都执行资源关闭。

## 文件组织

```text
YCode/
├── .venv/                         # 当前项目本地虚拟环境，不提交仓库
├── .ycode/
│   └── config.example.yaml
├── docs/
│   ├── Development-Workflow.md
│   ├── manual-api-test.md
│   └── features/interactive-chat/
│       ├── spec.md
│       ├── plan.md
│       ├── task.md
│       └── checklist.md
├── ycode/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── app.py
│   ├── errors.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── messages.py
│   │   ├── events.py
│   │   └── provider.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── discovery.py
│   │   └── loader.py
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── factory.py
│   │   ├── anthropic.py
│   │   └── openai.py
│   ├── session/
│   │   ├── __init__.py
│   │   └── chat.py
│   └── ui/
│       ├── __init__.py
│       ├── terminal.py
│       ├── header.py
│       ├── input_box.py
│       ├── renderer.py
│       ├── timer.py
│       └── styles.py
├── tests/
│   ├── unit/
│   │   ├── config/
│   │   ├── providers/
│   │   ├── session/
│   │   └── ui/
│   ├── integration/
│   │   ├── test_anthropic_stream.py
│   │   └── test_openai_stream.py
│   ├── e2e/
│   │   └── test_terminal_chat.py
│   └── support/
│       ├── fake_provider.py
│       └── sse_server.py
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── README.md
```

真实 `.ycode/config.yaml` 和 `.venv/` 加入 `.gitignore`；仓库只提供不含真实密钥的 `config.example.yaml`。`pyproject.toml` 是项目元数据、运行依赖、开发依赖和 CLI 入口的唯一定义来源；`requirements.txt` 只包含 `-e .[dev]`，作为异地拉取后的一键安装入口，避免维护两套依赖清单。开发、测试和运行命令均使用当前项目虚拟环境中的 Python 与依赖。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Python 版本 | Python 3.12+ | 作为项目最低版本写入 `pyproject.toml`；当前 `.venv` 的 Python 3.14.0 满足要求。 |
| Python 环境 | 项目根目录 `.venv` | 隔离 YCode 依赖；文档中的安装、测试和运行步骤均以激活该虚拟环境为前提。 |
| 包结构 | 项目根目录下的 `ycode/` 平铺包 + `pyproject.toml` | 当前只有一个应用包，省略 `src` 层级更直接；通过 editable install 和 pytest `--import-mode=importlib` 保持导入行为可控。 |
| 依赖安装入口 | `requirements.txt` 包含 `-e .[dev]` | 异地拉取后可用一条 pip 命令安装项目及开发依赖，实际依赖仍只在 `pyproject.toml` 维护。 |
| CLI 入口 | `ycode = ycode.cli:main`，同时支持 `python -m ycode` | 兼顾安装后的命令体验和开发期运行。 |
| 异步模型 | 单一 `asyncio` 事件循环 | 官方 SDK 与 `prompt_toolkit` 均支持异步，适合串接 SSE、计时和 UI 刷新。 |
| 输入组件 | `prompt_toolkit` | 提供 Windows 兼容的异步输入、占位提示和样式控制。 |
| 输入提示布局 | 非全屏 prompt_toolkit `Application` + 四行 `HSplit` | `PromptSession.bottom_toolbar` 固定为单行且带默认反色语义，无法同时表达普通下横线和其下方独立提示区；自定义布局能稳定控制层级和清理。 |
| 输出组件 | Rich Console、Live、Markdown | 支持稳定的动态区域、终端格式和完成后的整体 Markdown 渲染。 |
| 配置解析 | PyYAML `safe_load` + Pydantic v2 两阶段校验 | 第一阶段只建立名称索引，第二阶段只强校验活动配置；既允许保留未完成的备用配置，又保证运行时 Provider 始终获得完整类型。 |
| Anthropic 后端 | 官方 `anthropic` 异步 SDK | 直接支持 Messages 流和 extended thinking 事件，减少自写协议风险。 |
| OpenAI 后端 | 官方 `openai` 异步 SDK的 Chat Completions | 满足已确认的协议选择、SSE 和自定义 `base_url`。 |
| Provider 抽象 | 自定义轻量 `Protocol` + 统一事件 | 不引入 Agent 框架，同时保留以后新增后端的扩展点。 |
| 会话存储 | 进程内列表，成功轮次事务提交 | 满足多轮记忆和失败恢复，不提前引入持久化。 |
| Claude Thinking | `true` 映射为 adaptive，`false` 映射为 disabled | 配置保持六字段；显式控制协议行为，不依赖官方或兼容服务的默认值。关闭时同时过滤意外 Thinking 增量。 |
| 输出上限 | Provider 内部固定 `max_tokens=16000` | 首期不扩展配置字段，并给 Thinking 与最终回答留出空间。 |
| 响应计时 | `time.perf_counter()`，约 100 ms UI 刷新 | 单调时钟不受系统时间调整影响，首增量前即可显示。 |
| Markdown 时机 | 流中纯文本，完成后一次整体渲染 | 保持真实增量体验，避免未闭合 Markdown 导致画面跳动或解析错误。 |
| 重试策略 | 首期关闭 SDK 自动重试 | 请求失败及时回到输入状态，由用户决定是否重试，计时含义清晰。 |
| 自动化 API 测试 | 官方 SDK连接本机模拟 SSE 服务 | 覆盖实际协议适配链路，不需要真实 Key 或外网。 |
| 真实 API 测试 | 用户按文档手动执行 | 避免自动化环境保存或消耗真实凭据。 |
| 首期平台 | Windows 10/11、PowerShell、Windows Terminal | 与 Spec 范围一致，不为未确认平台增加兼容分支。 |

## Spec 覆盖检查

| Spec | 设计归属 |
|---|---|
| F1 启动与配置加载 | `cli.py`、`config/discovery.py`、`config/loader.py`、`app.py` |
| F2 多供应商配置 | `config/models.py`、`config/loader.py` |
| F3 配置校验 | `config/discovery.py`、`config/loader.py`、`ConfigError` |
| F4 供应商路由 | `providers/factory.py`、两个 Provider 适配器 |
| F5 交互式对话 | `ui/terminal.py`、`ui/input_box.py`、`ChatSession` |
| F6 流式回复 | 官方异步 SDK、统一事件、`ui/renderer.py` |
| F7 当前会话多轮记忆 | `session/chat.py` 的进程内历史和事务提交 |
| F8 Claude extended thinking | 配置校验、`providers/anthropic.py`、Thinking 纯文本区域 |
| F9 统一 Provider 行为 | `core/provider.py`、`core/events.py`、ProviderFactory |
| F10 错误恢复 | `ProviderError`、会话回滚、TUI 输入循环 |
| F11 安全退出 | `cli.py`、`app.py`、`TerminalUI`、Provider `close()` |
| F12 响应耗时 | `ui/timer.py`、`ui/renderer.py`、失败和完成流程 |
| F13 Markdown 回答渲染 | `ui/renderer.py` 的流中纯文本和完成后整体 Rich Markdown |
| F14 输入提示区 | `ui/input_box.py` 的四行 Application 布局、`ui/styles.py` 的横线颜色入口 |
| F15 用户消息展示 | `ui/user_message.py` 的无边框背景板与指示符渲染 |
| AC22 未激活配置延迟校验 | `ProviderEntry`、`config/loader.py` 两阶段校验、配置单元测试与 CLI 启动测试 |
| AC23 输入提示布局 | 输入组件单元测试、样式注入测试、Windows ConPTY 输入等待场景 |
| AC24 用户消息背景板 | 用户启动后的实际终端视觉验收 |

F1–F14 及 AC22–AC23 均有明确模块归属；模块依赖无循环；具体 Provider 类型不会进入会话层或 TUI 主流程。
