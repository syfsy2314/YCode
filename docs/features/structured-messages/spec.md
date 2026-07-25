# YCode 结构化消息基础层 Spec

> 状态：已批准

## 背景

YCode 当前面向纯对话，将供应商 SSE 转换为 `TEXT_DELTA`、`THINKING_DELTA` 和 `COMPLETED`，再由 ChatSession 把最终文本拼接为字符串。该设计可以支持流式文本，但会丢失内容块索引、停止原因、Thinking 签名、工具调用 ID、工具名称和分段 JSON 参数，无法作为 Tool Use 的可靠基础。

本阶段只重构消息模型、流事件、消息组装、会话历史和 Provider 协议适配。现有终端纯对话行为保持不变，不实现工具注册、执行、权限确认或 Agent 循环。

## 目标

- 使用供应商无关的结构化内容块表示用户和模型消息。
- 在保留实时流式展示的同时，可靠组装完整的结构化 Assistant 消息。
- 支持乱序交错的多个内容块和并行工具调用增量。
- 保留 Anthropic Thinking 签名及工具调用往返所需信息。
- 统一 Anthropic 与 OpenAI 的文本、Thinking、工具调用和停止原因。
- ChatSession 只提交完整成功的结构化轮次，失败时继续保持事务回滚。
- 为下一阶段 Tool Registry、Tool Executor 和 Agent Loop 提供稳定接口。
- 统一把官方大模型 SDK 实例命名为 `client`，与 Provider 适配器概念区分。

## 功能需求

- **F1：结构化内容块**  
  核心层至少定义以下不可变内容块：
  - `TextBlock`：普通文本。
  - `ThinkingBlock`：Thinking 文本及可选签名。
  - `RedactedThinkingBlock`：供应商返回的不可读 Thinking 数据。
  - `ToolCallBlock`：工具调用 ID、工具名称和解析完成的参数对象。
  - `ToolResultBlock`：对应的工具调用 ID、结果内容和错误标记。

- **F2：结构化消息**  
  `ChatMessage` 不再以单个字符串作为唯一内容，而是保存有序内容块。用户普通输入转换为一个 `TextBlock`；Assistant 消息可以同时包含 Thinking、文本和多个 ToolCall。提供清晰的文本读取辅助方法，避免 TUI 为普通文本场景遍历供应商结构。内容块序列是消息的唯一真实数据源，不额外保存 `content`、`tool_uses`、`tool_results`、`thinking_blocks` 等可能彼此失去同步的平行状态；这些分类信息只能通过只读便利接口从有序内容块派生。

- **F3：统一块级流事件**  
  Provider 向上返回可区分的消息开始、内容块开始、文本增量、Thinking 增量、Thinking 签名增量、工具参数 JSON 增量、内容块结束和消息完成事件。所有块级事件携带稳定的块索引；消息完成事件携带统一停止原因。

- **F4：停止原因**  
  核心层统一表示正常结束、工具调用、达到输出上限、停止序列、内容过滤和未知原因。Provider 保留无法映射的供应商原始原因供诊断，但不得把完整原始响应暴露给 TUI。

- **F5：MessageAssembler**  
  新增供应商无关的单轮消息组装器。它按块索引维护构建状态，把流式增量组装为有序内容块，并仅在所有已开始块结束且消息完成后产生不可变 Assistant 消息。

- **F6：工具 JSON 拼接**  
  工具参数增量按块索引原样拼接；只在对应工具块结束时解析一次 JSON。结果必须是 JSON 对象。增量到达前没有工具块、块重复开始、块类型不匹配、JSON 无效或消息结束时仍有未关闭块，均作为结构化流错误处理。

- **F7：并行内容块**  
  Assembler 可以同时维护多个索引，不假设文本、Thinking 或工具调用严格串行。最终消息按内容块索引排序，而不是按完成时间排序。

- **F8：Anthropic 事件映射**  
  AnthropicProvider 映射 Messages API 的 `content_block_start`、各类 `content_block_delta`、`content_block_stop`、`message_delta` 和 `message_stop`。`tool_use`、`input_json_delta`、Thinking signature 和 redacted Thinking 不得被忽略或转换成普通文本。

- **F9：OpenAI 事件映射**  
  OpenAIProvider 映射 Chat Completions 的文本 delta、`tool_calls[index]`、工具 ID、函数名、arguments 分片和 finish reason。Provider 为没有显式内容块生命周期的 OpenAI 流合成统一的块开始与结束事件。

- **F10：结构化请求转换**  
  两个 Provider 在发起请求前把统一内容块转换为各自协议格式。普通纯文本历史继续产生与当前版本等价的请求；工具调用和工具结果具备协议转换能力，但本阶段应用不会主动生成或执行工具结果。

- **F11：结构化会话事务**  
  ChatSession 为当前用户输入创建结构化用户消息，在转发流事件给 TUI 的同时交给 MessageAssembler。只有完整消息成功组装后才提交用户消息和 Assistant 消息；Provider 错误、组装错误、取消或缺少完成事件时不提交残缺轮次。

- **F12：Thinking 与工具上下文保留**  
  完成的 Thinking、签名、ToolCall 和 ToolResult 内容块保留在当前进程的结构化历史中，以便同一 Provider 在后续请求或未来工具循环中按协议要求原样转换。TUI 不重新展示历史 Thinking；所有结构化历史仍不写入磁盘。

- **F13：现有流式 UI 兼容**  
  TUI 仍能在首个增量到达时立即显示 Thinking 或文本，并在正常完成后对累计最终文本执行 Markdown 渲染。新增的结构化事件不得让 TUI 依赖 Anthropic/OpenAI SDK 类型。当前未配置 tools 时，用户看到的纯对话行为不变。

- **F14：客户端命名**  
  AnthropicProvider 和 OpenAIProvider 中，官方 SDK 实例统一命名为 `client`；`provider` 只表示协议适配器。构造器继续允许注入测试 client，关闭逻辑通过 `client.close()` 执行。

## 非功能需求

- **N1：不可变完成消息**  
  完成后的 ChatMessage 和 ContentBlock 不允许原地修改；流式可变状态只存在于 MessageAssembler 内。

- **N2：协议隔离**  
  ChatSession、MessageAssembler 和 TUI 不导入官方 SDK 类型，也不判断供应商 SSE 事件名称。

- **N3：增量完整性**  
  文本、Thinking、签名和 JSON 分片必须按接收顺序无损拼接，不得按句子缓冲或提前解析不完整 JSON。

- **N4：错误安全**  
  结构化流错误不得包含 API Key、认证头、完整响应对象或未经整理的供应商异常。

- **N5：兼容现有体验**  
  纯文本多轮上下文、Thinking 展示、计时、最终 Markdown、失败回滚和退出行为不得回归。

- **N6：可测试性**  
  ContentBlock、事件映射、Assembler 状态机、Provider 请求转换和 ChatSession 事务必须可以使用 FakeProvider 与本机 SSE 服务测试，不依赖真实 Key。

- **N7：扩展边界**  
  下一阶段增加工具执行时，主要新增 Agent Loop、Tool Registry 和 Tool Executor，不应再次重写 ContentBlock、MessageAssembler 或 Provider 的工具事件解析。

## 与现有交互式对话 Spec 的关系

- 保持现有 UI、配置、SSE、计时、Markdown 和会话不落盘要求。
- 将内部 `ChatMessage.content: str` 升级为结构化内容块。
- 替代“Thinking 不进入通用历史、后续轮次只携带最终回答文本”的旧内部策略：Thinking 不作为可见聊天文本，但完整块及签名会保留在进程内结构化历史中，由 Provider 按协议要求转换。
- 不改变用户可见的 Thinking 区域，也不把 Thinking 写入磁盘。

## 不做的事

- 注册、发现、执行或取消任何工具。
- 向模型请求中传入 tools 定义。
- Tool Use 权限确认、危险操作拦截或沙箱。
- Agent Loop、自动继续请求或最大工具轮次控制。
- MCP、Skill、插件、子代理或多代理。
- 文件读取、编辑、Shell 执行或其他本地能力。
- 新的 TUI 工具调用卡片；本阶段只建立底层事件和消息结构。
- 会话持久化、数据库或结构化历史落盘。
- 在运行中切换 Provider 或跨 Provider 转换 Thinking 私有数据。

## 验收标准

- **AC1（F1、F2）**  
  用户纯文本、Assistant 文本、Thinking、redacted Thinking、工具调用和工具结果都能表示为不可变的统一内容块；ChatMessage 保持内容块顺序并能提取普通文本。

- **AC2（F3、F4）**  
  统一流事件可以完整表达块索引、块生命周期、各类增量和停止原因，不需要可选字段的无效组合，也不包含 SDK 类型。

- **AC3（F5、F7）**  
  将多个索引的文本、Thinking 和工具增量交错输入 Assembler 后，最终消息按索引排序，所有分片按各自接收顺序拼接。

- **AC4（F6）**  
  工具 arguments 被拆成多个 JSON 分片时，Assembler 在工具块结束后得到完整参数对象；无效 JSON、非对象 JSON、未知索引、重复开始、重复结束和未关闭块均产生安全结构化流错误。

- **AC5（F8）**  
  本机 Anthropic SSE 同时包含 Thinking、signature、文本和两个 Tool Use 块时，Provider 产生完整统一事件，Assembler 保留调用 ID、名称、参数、块顺序和停止原因。

- **AC6（F9）**  
  本机 OpenAI SSE 将两个并行 `tool_calls` 的 ID、名称和 arguments 交错分片时，Provider 合成正确的块生命周期，Assembler 生成两个独立 ToolCallBlock。

- **AC7（F10）**  
  普通结构化文本历史转换出的 Anthropic/OpenAI 请求与当前请求语义一致；包含 ToolCall/ToolResult 的测试历史分别转换为两种协议要求的角色和内容结构。

- **AC8（F11）**  
  ChatSession 一边向 TUI 转发文本和 Thinking 增量，一边组装完整 Assistant 消息；只在成功完成时提交结构化用户与 Assistant 消息，所有失败和取消场景均回滚。

- **AC9（F12）**  
  Anthropic Thinking 文本、signature、redacted Thinking 和 ToolCall 保存在进程内历史并可无损转换回后续请求；TUI 捕获输出和临时目录中没有历史 Thinking 重放或持久化文件。

- **AC10（F13）**  
  现有纯文本双轮对话、Thinking、计时、Markdown、错误恢复和 Windows ConPTY 场景保持通过，用户可见输出没有因结构化消息而改变。

- **AC11（F14）**  
  两个 Provider 的 SDK 依赖统一通过 `client` 属性调用；代码中不再使用 `_client` 表示大模型 SDK 实例，ProviderFactory、ChatSession 和 TUI 仍使用 `provider` 命名。

- **AC12（范围）**  
  应用没有发送 tools 定义、执行工具、自动继续模型请求或增加 Agent 框架；模拟 Tool Use 只用于验证解析、组装和协议转换。

- **AC13（N4、N6）**  
  单元、Provider 和本机 SSE 集成测试使用占位 Key 完成，所有错误和捕获输出中均不存在 Key、认证头或完整响应对象。
