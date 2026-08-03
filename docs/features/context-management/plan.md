# YCode 上下文管理 Plan

> 状态：已批准

## 架构概览

首版上下文管理只接入 Anthropic Agent 路径。新增独立的 `ycode.context` 包，集中负责
工具结果外置、完整请求估算、结构化摘要、事务提交和会话级熔断；现有 Provider、
AgentLoop、ChatSession 和终端层只保留各自边界内的接入代码。

```text
app.py（仅 Anthropic）
    ├── AnthropicProvider
    ├── ContextManager（借用同一个 Provider）
    │   ├── ToolResultExternalizer
    │   ├── TokenEstimator
    │   ├── ConversationCompactor
    │   └── ContextArtifactStore
    ├── AgentLoop ── 每个主请求前执行 ContextTransaction 预检
    └── ChatSession ── 提交历史/记忆、处理 /compact、展示上下文事件
```

一次 Anthropic 主请求的处理顺序固定为：

```text
工具结果规范化与外置
    ↓
组装完整逻辑请求并估算 Token
    ↓
达到阈值时尝试全量摘要
    ↓
重新组装并校验预算
    ↓
发送 Anthropic 主请求
```

`ContextManager` 是会话级对象，保存当前唯一记忆、估算校准值、连续失败次数和自动
摘要熔断状态。`ContextTransaction` 是单个 Agent 回合的临时视图；自动摘要和工作历史
只写入该视图。Agent 回合正常完成后，`ChatSession` 在发送终态事件前原子应用完整替换
历史和新记忆；失败、取消或达到轮数上限时直接丢弃事务。

上下文包不依赖会话层或 UI 层。摘要响应由上下文包直接消费 Provider 通用流事件，避免
复用 `ResponseAssembler` 形成 `context → session → context` 的循环依赖。

## 核心数据结构与接口

### `ContextPolicy`

不可变的会话策略对象：

- `context_window_tokens`：来自顶层 YAML，默认 `200_000`，必须是大于 `33_000` 的
  严格整数。
- `summary_output_tokens = 20_000`：草稿和正式摘要的合计输出上限。
- `safety_margin_tokens = 13_000`：本地估算安全余量。
- `single_tool_result_bytes = 50 * 1024`。
- `message_tool_results_bytes = 200 * 1024`。
- `preview_bytes = 4 * 1024`。
- `failure_fuse_count = 3`。
- `stale_session_seconds = 24 * 60 * 60`。
- `auto_compact_threshold`：窗口减去摘要输出和安全余量，默认 `167_000`。
- `continue_request_limit`：窗口减去摘要输出，默认 `180_000`。

除 `context_window_tokens` 外，其余数值首版均为内部固定策略，不进入配置文件。

### Provider 请求覆盖

`AgentModelRequest` 增加两个可选字段：

- `max_output_tokens: int | None = None`
- `thinking_enabled: bool | None = None`

`None` 表示维持现有 Provider 配置。摘要请求使用 `20_000` 和 `False`，并传入空工具
列表；Anthropic Provider 将覆盖值映射到单次 Messages 请求。普通主请求不设置覆盖，
现有最大输出和 Thinking 行为保持不变。

### 存盘模型

#### `ArtifactChunk`

- `index: int`
- `path: str`：相对当前工作区的路径。
- `bytes: int`
- `sha256: str`

#### `ToolResultManifest`

- 格式版本、工具名、调用 ID、错误状态。
- 脱敏后完整序列化结果的总字节数和 SHA-256。
- 按顺序排列的 `ArtifactChunk`。
- 创建时间及所属会话 ID。

Manifest 和每个正文分片自身均不得超过 50 KiB。Manifest 不保存未脱敏正文。

#### `ToolResultArtifact`

- `manifest_path: str`
- `original_bytes: int`
- `sha256: str`
- `preview: str`

它负责生成稳定 JSON 引用，字段固定为：`externalized`、工具名、调用 ID、manifest
相对路径、原始字节数、SHA-256 和预览。最小引用只移除预览，不移除定位及完整性字段。

#### `ContextSessionManifest`

记录会话 ID、进程 ID、进程启动标识和创建时间，用于正常关闭及启动清理。目录名和
文件名全部由程序生成，不能使用未经校验的工具名或调用 ID 作为路径片段。

### 记忆与事务模型

#### `ConversationMemory`

- `summary: str`：只保存通过校验的 `<summary>` 正文，不保存分析草稿。

#### `ContextCommit`

- `history: tuple[ChatMessage, ...]`：本轮完成后应成为会话历史的完整替换值。
- `memory: ConversationMemory | None`：本轮完成后应成为唯一会话记忆的值。

`AgentTurnResult` 增加 `context_commit: ContextCommit | None`。仅接入上下文管理的
Anthropic AgentLoop 返回该字段；PlainChatRunner 保持 `None` 和原有追加语义。

### Token 估算

#### `TokenEstimate`

- `local_tokens: int`
- `calibrated_tokens: int`
- `total_tokens: int`：前两者的较大值。

#### `TokenEstimator`

- `estimate(request: AgentModelRequest) -> TokenEstimate`
- `observe(local_tokens: int, actual_input_tokens: int) -> None`

本地估算对与供应商请求等价的确定性结构计算 UTF-8 字节量，覆盖 System Prompt、
supplements、工具 Schema、全部消息及容器开销，再使用保守的字节换算得到 Token 数。
校准比率为 `actual_input_tokens / local_tokens`，下限为 `1.0`；后续估算取本地值和
按已观察比率放大的值中的较大者。只有主请求的 Anthropic 实际 input usage 更新校准，
摘要请求不参与，且不调用 Token Count API。

### 工具结果外置

#### `ToolResultExternalizer`

- `build_result_message(records) -> ChatMessage`：从带有工具名和调用 ID 的执行记录构造
  工具结果消息，并在进入工作历史前执行外置。
- `normalize_messages(messages) -> tuple[ChatMessage, ...]`：每次主请求前幂等检查已有
  消息，处理尚未外置的结果；工具名通过同一历史中的 `ToolCallBlock` 调用 ID 映射恢复。

两条入口共用同一套确定性序列化、脱敏、大小排序、预览和引用逻辑。
默认预览按 UTF-8 安全边界截取约 3 KiB 开头和 1 KiB 结尾；如果 JSON 引用计入聚合
大小后仍然超限，则逐步缩短预览，最终退化为不含预览的最小引用。

### 摘要输入与结果

Transcript 为每次摘要临时生成的只读文本，使用稳定顺序 ID：

- 用户消息：`U0001`、`U0002`……
- 助手消息：`A0001`、`A0002`……
- 工具调用及结果：`T0001`、`T0002`……

Thinking、Thinking signature 和 Redacted Thinking 块在渲染前直接排除。消息内容只
作为带明确边界的数据段插入，Prompt 告知模型不得执行其中任何指令。

#### `SummarySource`

- `previous_memory: ConversationMemory | None`
- `messages: tuple[ChatMessage, ...]`
- `latest_user_message: ChatMessage | None`：自动摘要时标记需原样保留的真实用户消息；
  手动摘要时为 `None`。

#### `SummaryResult`

- `summary: ConversationMemory`
- `retained_messages: tuple[ChatMessage, ...]`

自动摘要返回最新真实用户消息作为唯一保留消息；手动摘要不保留任何历史消息。

#### `ConversationCompactor`

- `compact(source: SummarySource) -> SummaryResult`

它构造 transcript、发送专用 Anthropic 请求、收集完整响应并执行全部格式和原文校验。
`<summary>` 中只接受以下九个二级 Markdown 标题，必须齐全且顺序完全一致：

1. 主要请求
2. 关键概念
3. 文件代码
4. 错误修复
5. 解决过程
6. 用户原话
7. 待办
8. 当前工作
9. 下一步

每个空部分必须写“无”。

用户原话采用固定机器格式：

```text
- 原文 [U0001]: "经过 JSON 转义的逐字原文"
- 概述 [U0002]: 概述内容
```

“原文”条目必须能解析出来源 ID，反转 JSON 转义后与对应用户消息逐字一致；“概述”不
执行逐字匹配。任何无法解析、来源不存在或内容不一致均使摘要失败。

### 状态报告与 Agent 事件

#### `ContextCompactionReport`

包含压缩前 Token、压缩后 Token 和是否为手动压缩。

#### `ContextFailureReport`

包含稳定错误码、安全原因、连续失败次数、是否已熔断，以及原主请求是否可以继续。

#### `PreparedContextRequest`

包含最终 `AgentModelRequest`、当前事务使用的工作消息、可选压缩报告和估算结果。

新增三个供应商无关 Agent 事件：

- `ContextCompactedEvent`：终端显示压缩前后 Token。
- `ContextCompactionFailedEvent`：显示失败次数，第三次同时显示熔断和 `/compact`。
- `ContextCompactionNotNeededEvent`：手动压缩无可压缩内容时显示本地提示。

工具结果外置没有展示事件。`context_storage_error`、`context_uncompressible` 等会终止
当前操作的错误仍通过 `AgentErrorEvent` 表达。

### `ContextManager` 与 `ContextTransaction`

`ContextManager` 对外提供：

- `begin_turn(history, user_message) -> ContextTransaction`
- `compact_committed_history(history) -> ContextCompactionReport`
- `commit(context_commit) -> None`
- `observe_main_usage(local_tokens, actual_input_tokens) -> None`
- `close() -> Awaitable[None]`

`ContextTransaction` 提供工具结果消息构造和每轮请求预检。它持有会话记忆、历史和失败
熔断状态的快照，但连续失败计数及 Token 校准更新写入 `ContextManager` 的会话级运行
状态，不随 Agent 回合回滚。

## 模块设计与职责

### `ycode/context/models.py`

- 定义策略、存盘描述、估算结果、摘要输入/输出、事务提交和状态报告。
- 完成严格整数、阈值和模型不变量校验。
- 不执行文件、Provider 或 UI 操作。

### `ycode/context/tokens.py`

- 以固定字段顺序序列化完整逻辑请求并本地估算 Token。
- 工具 Schema 使用当前实际提供给 Anthropic 的定义结构参与估算。
- 保存会话期向上校准比率，拒绝零值、负值和布尔值 usage。
- 不调用网络，不实现供应商 tokenizer。

### `ycode/context/artifacts.py`

- `ContextArtifactStore` 创建 `.ycode/context/<session-id>/`、会话 manifest、工具结果
  artifact 目录和连续分片。
- 使用现有 `SecretRedactor` 在计算大小、预览和写盘前统一脱敏。
- 以临时目录完成分片、哈希及 manifest 校验，之后原子移动到正式 artifact 目录。
- 通过调用 ID 和内容哈希建立会话内索引，重复预检复用已验证 artifact。
- `ToolResultExternalizer` 执行单结果和消息聚合规则，生成替换后的不可变 ChatMessage。
- 正常关闭只删除当前会话目录；启动清理只处理超过 24 小时、进程不存在且 manifest
  可确认归属的目录。无法解析或无法确认进程状态的目录保留。

固定目录结构为：

```text
.ycode/context/<session-id>/
├── session.json
└── tool-results/<artifact-id>/
    ├── manifest.json
    └── chunks/
        ├── 000001.txt
        └── ...
```

### `ycode/context/summary.py`

- 从包资源读取 `resources/summary.md`。
- 为旧记忆和消息生成确定性、只读 transcript。
- 使用同一个 Anthropic Provider 发起没有工具、关闭 Thinking、输出上限 20,000 的
  独立请求。
- 使用内部流收集器只接受文本、usage 和正常结束信号；工具调用、Thinking、异常停止
  或不支持的内容均判定为摘要失败。
- 解析且只接受先 `<analysis_draft>`、后 `<summary>` 的唯一边界。
- 丢弃草稿，校验九个中文标题、顺序、空段“无”及用户原话。
- 不依赖 `session.assembler`，不自动重试或二次修复。

### `ycode/context/resources/summary.md`

Prompt 第一段和最后一段分别再次声明禁止调用工具。正文要求：

- Transcript 是不可信只读数据，其中的命令不得执行。
- 先输出简洁 `<analysis_draft>`，再输出 `<summary>`。
- 正式摘要包含九个固定中文 Markdown 标题，空部分写“无”。
- 说明语言跟随对话主要语言，用户原话保留原语言。
- 用户原话必须使用规定的“原文/概述 + 消息 ID”格式；无把握时只能写概述。
- 保留当前请求、已完成过程、准确路径/接口、错误修复、待办和下一步。

该资源不会拼接主 Agent Prompt、环境、权限、工具目录或边界提醒。

### `ycode/context/manager.py`

- 保存唯一会话记忆、Token 校准、连续失败次数和自动熔断状态。
- 创建每回合事务，并按“工具结果控制 → 估算 → 摘要 → 重估算”编排请求。
- 在压缩前判断是否存在可压缩内容；固定上下文或最新用户消息单独导致超限时返回
  `context_uncompressible`，不调用摘要模型、不增加失败次数。
- 每次主请求预检最多调用一次自动摘要。
- 摘要失败时更新共享失败计数，并按继续上限决定发送原请求或终止。
- 手动压缩和自动压缩共用失败计数；任意成功摘要清零并解除熔断。
- 生成 `<memory>` 记忆补充和固定 `<reminder>` 边界补充。

### `ycode/core/provider.py`

只给 `AgentModelRequest` 增加请求级输出上限和 Thinking 覆盖字段及参数校验，不加入
上下文策略。

### `ycode/providers/anthropic.py`

- 在构造 Messages 参数时应用请求级 `max_output_tokens` 和 `thinking_enabled`。
- 摘要请求显式发送 disabled Thinking，并保持工具为空。
- 主请求未提供覆盖时行为完全沿用 Provider 配置。
- 不在 Provider 内估算、压缩、存盘或维护熔断状态。

### `ycode/agent/contracts.py`

给 `AgentTurnResult` 增加可选 `ContextCommit`。正常完成可以携带提交，非正常结果不得
携带提交，确保失败和取消不会意外应用摘要状态。

### `ycode/agent/events.py`

增加三个上下文事件及其字段校验，并纳入 `AgentEvent` 联合类型。事件只包含 Token、
次数、错误码和布尔状态，不包含工具结果正文或秘密。

### `ycode/agent/loop.py`

- 可选接收共享 `ContextManager`；没有 Manager 时保留当前行为。
- 回合开始时创建 `ContextTransaction`。
- 工具批次完成后通过 externalizer 构造结果消息，覆盖成功、错误、权限拒绝和 MCP。
- 每个模型轮次发送前都通过事务预检，而非只预检用户首轮。
- 记忆补充位于动态补充之前，边界提醒始终位于全部 supplements 最后。
- 收到每个 Anthropic 主响应的实际 input usage 后更新估算校准。
- 自动压缩产生报告时发出上下文事件；硬错误生成稳定 `AgentErrorEvent`。
- 只有 `COMPLETED` 结果携带完整 `ContextCommit`。

### `ycode/session/chat.py`

- 可选接收共享 `ContextManager`。
- Anthropic 正常回合使用 `ContextCommit` 完整替换历史并提交唯一记忆，再发送已暂存的
  `FinalResponseEvent`；其他终态不提交。
- 本地识别 `/compact`，命令不交给 AgentLoop，也不追加到历史。
- 手动压缩全部已提交历史，不保留最近用户消息；空历史返回
  `ContextCompactionNotNeededEvent`。
- 将当前可取消操作从单一 AgentTurn 扩展为 Agent 回合或手动压缩任务，使 Ctrl+C 能
  取消摘要；用户取消不计摘要失败。
- 关闭时先取消并等待活动操作，再关闭 runner，最后关闭 ContextManager 并删除本会话
  artifact 目录。

### `ycode/ui/terminal.py`

- 消费三种上下文事件并输出一行简洁状态。
- 自动工具结果外置保持静默。
- 摘要失败显示连续次数；第三次显示自动摘要已熔断及 `/compact` 提示。
- 不增加复杂进度 UI 或生产诊断面板。

### 配置与应用装配

- `ycode/config/models.py` 在 `AppConfig` 顶层增加 `context_window_tokens`，默认
  `200_000`，拒绝布尔、字符串、浮点及不大于 `33_000` 的值。
- 配置加载错误沿用当前字段定位和安全错误处理。
- `ycode/app.py` 只在活动协议为 Anthropic 时创建 `ContextPolicy` 和
  `ContextManager`，并将同一实例注入 AgentLoop 与 ChatSession。
- ContextManager 借用 Provider，不负责关闭；Provider 仍由 runner 生命周期管理。
- OpenAI 分支继续创建 PlainChatRunner，不创建或注入上下文管理对象。

### 包资源、忽略与文档

- `pyproject.toml` 增加 `ycode.context.resources = ["*.md"]` 包数据。
- `.gitignore` 增加 `.ycode/context/`。
- `.ycode/config.example.yaml` 和 README 说明 `context_window_tokens`、`/compact`、默认
  阈值及工具结果临时存盘行为。

## 模块交互与关键调用链

### 启动

```text
load_config
    ↓
创建 AnthropicProvider
    ↓
ContextPolicy(context_window_tokens)
    ↓
ContextManager.start
    ├── 保守清理失效超过 24 小时的会话目录
    └── 创建本会话 manifest
    ↓
同一 ContextManager 注入 AgentLoop 与 ChatSession
```

如果会话目录初始化失败，Anthropic 交互不进入不完整的上下文状态，应用按安全配置或
启动错误退出。OpenAI 启动链不经过以上步骤。

### 未达到阈值的正常请求

```text
ChatSession.start_turn(history, latest_user)
    ↓
AgentLoop 创建 ContextTransaction
    ↓
事务幂等规范化历史工具结果
    ↓
组装 system prompt + memory + 动态 supplements + reminder + tools + messages
    ↓
TokenEstimator 本地估算并应用历史校准
    ↓  未超过 auto_compact_threshold
AnthropicProvider.stream_agent
    ↓
记录实际 input usage
    ↓
Agent 正常完成，生成 ContextCommit
    ↓
ChatSession 原子替换 history 和 memory
    ↓
发送 FinalResponseEvent
```

没有记忆时不注入空 `<memory>`；没有发生过摘要时也不需要边界提醒。首次摘要成功后，
后续请求固定按下列 supplement 顺序组装：

```text
<memory>结构化摘要</memory>
环境 / 工具目录 / 权限 / 模式等现有动态补充
<reminder>摘要不是代码事实；精确细节必须重读，禁止臆造</reminder>
```

### 工具批次外置

```text
工具批次全部完成并按 position 排序
    ↓
按当前 content + metadata 格式确定性序列化
    ↓
SecretRedactor 脱敏
    ↓
先处理每个 > 50 KiB 的结果
    ↓
计算整条工具结果消息的最终序列化大小
    ↓  > 200 KiB
从仍保留完整正文的结果中按大小降序依次外置
    ↓
引用仍计入聚合大小；逐步缩短预览，必要时使用最小引用
    ↓
生成不可变 ToolResultBlock 消息并加入工作历史
```

单个 artifact 的原子写入顺序：

```text
创建程序生成的临时目录
    ↓
写入并校验所有 <= 50 KiB 分片
    ↓
写入并校验 manifest、总字节数和 SHA-256
    ↓
原子移动为正式 artifact 目录
    ↓
构造历史 JSON 引用
```

任何一步失败都不构造引用，当前回合以 `context_storage_error` 结束。已经完整提交的
artifact 可以保留至会话关闭；不完整临时目录由本次失败路径清理。

### 自动摘要

```text
完整请求估算 > auto_compact_threshold
    ↓
识别最新真实用户消息，检查是否存在可压缩内容
    ↓
旧 memory + 全部工作消息 → 只读 transcript
    ↓
独立 system prompt + transcript
tools=(), thinking_enabled=False, max_output_tokens=20_000
    ↓
收集 <analysis_draft> + <summary>
    ↓
校验正常结束、无工具、标签、九部分和用户原文
    ↓
丢弃草稿，生成唯一新 memory
    ↓
工作消息替换为原样最新用户消息
    ↓
重新组装完整主请求并估算
    ↓  <= auto_compact_threshold
报告压缩前后 Token，继续发送主请求
```

Transcript 可以包含最新用户消息供摘要理解任务，但该消息不从摘要恢复，而是直接使用
原始 `ChatMessage` 实例留在摘要之外。当前任务中已经完成的 Assistant、工具调用和工具
结果过程进入摘要。

### 自动摘要失败与熔断

实际发起摘要后发生 Provider 异常、异常停止、工具调用、格式错误、原文校验错误或摘要
后仍超阈值时：

1. 丢弃本次摘要候选，保留事务压缩前的 memory 和工作消息。
2. 连续失败数加一；第三次打开自动摘要熔断。
3. 发出不含敏感正文的 `ContextCompactionFailedEvent`。
4. 原请求估算不超过 `continue_request_limit` 时，发送未经摘要的主请求。
5. 超过继续上限时停止当前回合，并提示用户执行 `/compact`。

每个主请求预检至多发起一次摘要。熔断后的行为为：

- 不超过自动阈值：正常发送。
- 超过自动阈值但不超过继续上限：跳过自动摘要并发送。
- 超过继续上限：停止并提示 `/compact`。

如果固定上下文或最新用户消息本身导致没有可压缩空间，直接以
`context_uncompressible` 结束，不调用摘要、不增加失败次数。

### 事务状态

```text
AgentTermination.COMPLETED
    → ChatSession 应用 ContextCommit.history + ContextCommit.memory

ERROR / CANCELLED / LIMIT_REACHED / 流消费中断
    → 丢弃事务中的摘要和工作历史
    → 已提交 history 与 memory 保持原值
```

安全写出的 artifact 不跟随回合回滚，保留到会话关闭，以免删除仍可能被旧事务或诊断
过程引用的文件。摘要失败计数和主请求 usage 校准是会话运行状态，不随事务回滚；用户
主动取消摘要不计失败。

### 手动 `/compact`

```text
ChatSession 识别本地命令，不产生 UserMessageEvent 历史提交
    ↓
无 memory 且无已提交 history
    → ContextCompactionNotNeededEvent，不调用 LLM、不计失败

存在可压缩内容
    → previous memory + 全部已提交 history 生成摘要
    → 不设置 latest_user_message
    → 成功后 memory = 新摘要，history = 空
    → 显示压缩前后 Token
```

手动摘要同样只能调用一次，不自动修复。失败计入共享计数但手动命令始终可再次执行；
任意手动或自动摘要成功都将失败数清零并解除熔断。手动摘要任务受 ChatSession 当前活动
操作约束，Ctrl+C 取消后不提交状态且不计失败。

### 重复摘要与关闭

后续压缩始终以“旧唯一 memory + 新历史”为输入，成功后替换成一份新 memory，不把多
份 `<memory>` 叠加进请求。

关闭顺序为：取消并等待活动 Agent/手动压缩操作，关闭 runner 所持有的 MCP 和 Provider，
再由 ContextManager 删除当前会话目录。启动时的遗留清理只删除 manifest 有效、超过
24 小时且能确认原进程不存在的目录；无法确认的目录保持不动。

## 文件组织与改动范围

采用独立上下文包，现有文件只增加必要接入点：

```text
YCode/
├── ycode/
│   ├── context/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── tokens.py
│   │   ├── artifacts.py
│   │   ├── summary.py
│   │   ├── manager.py
│   │   └── resources/
│   │       ├── __init__.py
│   │       └── summary.md
│   ├── core/provider.py
│   ├── providers/anthropic.py
│   ├── agent/contracts.py
│   ├── agent/events.py
│   ├── agent/loop.py
│   ├── session/chat.py
│   ├── ui/terminal.py
│   ├── config/models.py
│   └── app.py
├── tests/
│   ├── unit/context/
│   │   ├── test_models.py
│   │   ├── test_tokens.py
│   │   ├── test_artifacts.py
│   │   ├── test_summary.py
│   │   └── test_manager.py
│   ├── unit/agent/
│   ├── unit/config/
│   ├── unit/providers/
│   ├── unit/session/
│   ├── unit/ui/
│   ├── integration/
│   └── e2e/test_terminal_chat.py
├── .ycode/config.example.yaml
├── .gitignore
├── pyproject.toml
└── README.md
```

现有测试文件按受影响接口补充用例；上下文核心使用 `tests/unit/context/`。功能测试使用
虚拟 Provider、临时工作区、虚拟进程状态和占位秘密。

明确不修改 OpenAI Provider、PlainChatRunner 的上下文行为或 OpenAI 专用测试，不引入
跨进程恢复、数据库、加密存储或新文件读取工具。

## 技术决策

| 决策点 | 选择 | 理由与未选方案 |
|---|---|---|
| 上下文模块边界 | 独立 `ycode.context` 包 | 避免将摘要事务和存盘策略分散进 AgentLoop/ChatSession；未选直接内嵌。 |
| 大小口径 | 最终脱敏序列化结果的 UTF-8 字节数，1 KiB=1024 字节 | 与实际发送及磁盘内容一致；未选字符数。 |
| 文件格式 | JSON manifest + 不超过 50 KiB 的连续正文分片 | 可用现有读取工具重组并校验；未选不可控的单一大文件。 |
| 写入安全 | 临时目录完整写入校验后原子移动 | 引用只指向完整 artifact；未选直接写正式目录。 |
| 历史变更 | `ContextTransaction` 暂存，成功后完整替换提交 | 保持现有整轮事务语义；未选先修改再补偿回滚。 |
| 摘要 Provider | 借用当前 Anthropic Provider并使用请求级覆盖 | 生命周期和认证只有一份；未选创建第二个 Provider。 |
| Token 计量 | 本地保守估算 + 主请求实际 usage 向上校准 | 无额外网络延迟；未选 Token Count API。 |
| 用户原话 | 稳定消息 ID + JSON 字符串逐字校验 | 能拒绝模型改写；未选仅依赖 Prompt 自律。 |
| 摘要记忆 | 旧记忆和新增历史滚动生成唯一新记忆 | 避免摘要堆叠、重复和漂移。 |
| 失败策略 | 三次连续实际失败熔断自动摘要，保留手动入口 | 防止请求前死循环；未选持续自动重试。 |
| 清理策略 | 正常退出删本会话；启动时保守清理失效目录 | 隔离并发会话；未选启动时清空根目录。 |
| 边界提醒 | 独立且位于最后的 system supplement | 不被摘要改写，并在模型处理历史前持续可见。 |
| Provider 范围 | 仅 Anthropic 接入 | 符合当前范围；OpenAI 等项目完成后统一适配。 |
| 验证强度 | 核心功能测试 + 仓库规定检查 + 简单终端流程 | 覆盖功能正确性，不扩展生产级验证。 |

## 验证策略

### 核心功能测试

- 50 KiB 等于边界时保留，超过边界时外置；所有工具来源及成功/错误状态规则一致。
- 工具结果合计超过 200 KiB 时，先处理单结果，再按剩余大小降序外置并缩短预览。
- 分片、manifest、相对路径、SHA-256、重组、幂等和已知秘密脱敏正确。
- 模拟写盘失败得到 `context_storage_error`，不调用主模型且不提交本轮状态。
- 默认窗口得到 167,000 自动阈值和 180,000 继续上限，非法配置被拒绝。
- 完整请求各组成部分影响估算，主请求实际 usage 只向上校准。
- 摘要请求无工具、关闭 Thinking、输出上限 20,000，且不包含主 Prompt 或动态补充。
- Transcript 排除全部 Thinking 数据，摘要草稿被丢弃，九段结构及用户原话校验正确。
- 自动摘要原样保留最新用户消息；手动 `/compact` 压缩全部已提交历史。
- 压缩后仍超阈值、格式错误、Provider 错误和工具调用均只失败一次并计数。
- 三次失败熔断自动摘要，手动成功或自动成功后重置。
- 正常完成提交上下文事务；Provider 失败、取消和轮数上限回滚。
- 正常退出、活动会话、未满 24 小时及超过 24 小时失效目录的基础清理行为正确。
- 未触发阈值时 Anthropic 现有工具、权限、MCP、Plan 模式和流事件行为不变。
- OpenAI 路径不创建 ContextManager，原有测试行为不变。

### 仓库基础检查

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
```

### 简单终端功能流程

使用本地可控 Provider/模拟服务执行一条交互流程：启动 Anthropic 会话，触发大工具结果
存盘，执行 `/compact`，检查压缩状态提示，并继续下一轮对话。测试不连接真实 Anthropic
服务。

不新增压力测试、性能基准、长时间运行、大规模并发、复杂故障注入、多平台矩阵或真实
付费 API 验证。

## Spec 覆盖检查

| Spec | 设计归属 |
|---|---|
| F1 请求前处理顺序 | `ContextTransaction` 每轮预检、AgentLoop 调用链 |
| F2 完整请求计量 | `TokenEstimator`、`AgentModelRequest` 完整逻辑结构、usage 校准 |
| F3–F6 计量、单结果、聚合与预览 | `ToolResultExternalizer` 的统一序列化和两级外置算法 |
| F7–F9 存盘、脱敏、失败和生命周期 | `ContextArtifactStore`、`SecretRedactor`、会话 manifest、关闭清理 |
| F10 配置与预算 | `ContextPolicy`、`AppConfig.context_window_tokens` |
| F11 自动摘要范围 | `ContextManager` 可压缩性判断、`SummarySource.latest_user_message` |
| F12 只读摘要输入 | `summary.py` 的稳定 transcript 和 Thinking 过滤 |
| F13–F16 专用请求、草稿、结构与原话 | `summary.md`、请求覆盖、内部收集器、解析及逐字校验 |
| F17–F18 唯一记忆、边界和成功条件 | `ConversationMemory`、最后 reminder、摘要后重估算 |
| F19 自动压缩事务 | `ContextTransaction`、`ContextCommit`、ChatSession 原子应用 |
| F20 手动压缩 | ChatSession `/compact`、`compact_committed_history`、可取消操作 |
| F21–F22 失败、继续上限与熔断 | `ContextManager` 共享失败状态和单次尝试控制 |
| F23 终端反馈 | 三种上下文 Agent 事件、TerminalUI 单行状态 |
| N1、N8 Provider 范围与兼容 | `app.py` 仅 Anthropic 装配，OpenAI/PlainChatRunner 不变 |
| N2 请求开销 | 阈值下仅本地外置与估算，不发额外请求 |
| N3–N7 完整性、安全、隔离、事务和诊断 | 原子 artifact、路径生成、脱敏、会话事务、稳定错误码 |
| N9–N10 验证范围与安全 | 虚拟 Provider、临时工作区、基础检查和简单终端流程 |

F1–F23、N1–N10 及 AC1–AC20 均有对应数据结构、模块调用链和验证入口。上下文包依赖
`core`、工具契约、安全脱敏和 Anthropic Provider 协议，不反向依赖 session 或 UI；
现有层级不会形成循环依赖。
