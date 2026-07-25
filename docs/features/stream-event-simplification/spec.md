# YCode 流事件简化 Spec

> 状态：已批准

## 背景

上一阶段已经把模型响应升级为有序结构化内容块，并通过块开始、字段增量、块结束和消息完成等强类型事件完成流式组装。该方案能够无损保存文本、Thinking、Thinking signature、redacted Thinking 和 ToolCall，但公共 `StreamEvent` 同时暴露了过多接近供应商 SSE 的生命周期细节。

当前每种事件既由独立类型表达，又重复携带事件种类枚举。ToolCall 的 ID、名称和参数也被拆成多个公共增量事件，导致 Provider 的原始分片方式向 ChatSession、MessageAssembler、TUI 和测试扩散。后续新增内容类型时，需要同时修改枚举、事件类型、组装状态机和多个消费者。

本功能将公共流事件收缩到 YCode 上层真正关心的语义：文本增量、Thinking 增量、Thinking 完成、工具调用开始、工具参数增量、工具调用完成和响应流结束。Anthropic 的 message/block 生命周期、signature 分片和 JSON 解析细节由 Anthropic 适配边界吸收，不再原样暴露给上层。

本 Spec 取代 `structured-messages` 已批准文档中关于公共块开始、字段级增量和块结束事件的设计，但不改变其有序 ContentBlock、不可变 ChatMessage、停止原因、事务式历史和协议隔离目标。

现有 OpenAIProvider 已连接到 Factory、配置与测试。根据用户本次明确授权，只允许为新核心事件契约进行保持现有行为所需的最小兼容修改；不增加任何 OpenAI 配置、能力、协议特性或新验收场景。

## 目标

- 删除重复表达事件类型的公共事件枚举。
- 将公共模型响应流缩减为七种稳定的语义事件。
- 保留文本和 Thinking 的实时增量显示。
- 保留工具调用开始、工具参数增量和工具调用完成事件，为后续工具 UI 与 Agent Loop 提供清晰边界。
- 在 Thinking 或 ToolCall 完成事件中提供已经验证的不可变完整内容块。
- 让响应流结束事件只负责停止原因和供应商诊断原因，不重复携带完整消息。
- 由 ChatSession 按内容块索引构造最终有序 Assistant 消息，并继续保持成功后一次性提交。
- 把 Anthropic 原始 SSE 生命周期、signature 分片和工具 JSON 拼接限制在 Provider/内部解析边界。
- 保持当前纯聊天 TUI、Thinking、计时、Markdown、多轮历史和错误恢复行为不变。
- 继续保留供应商无关的 Provider 接口，为未来独立 OpenAI 适配阶段预留扩展点。

## 功能需求

- **F1：七种公共流事件**  
  公共模型响应流只表达文本增量、Thinking 增量、Thinking 完成、工具调用开始、工具参数增量、工具调用完成和响应流结束七种语义。每种语义使用独立的不可变事件类型，不再额外维护事件种类枚举。

- **F2：稳定内容块索引**  
  除响应流结束外，所有内容相关事件都携带非负内容块索引。相同索引的增量和完成事件必须属于同一个内容块；最终 Assistant 消息按索引排序，不能按事件完成时间排序。

- **F3：文本增量**  
  文本增量携带索引和本次新增文本，既供 TUI 立即显示，也由会话层按索引累积。文本不设置单独的完成事件；收到合法的响应流结束后，会话层把每个文本索引累积的内容转换为完整文本块。

- **F4：Thinking 增量与完成**  
  Thinking 增量只携带索引和可见 Thinking 文本，供 TUI 实时显示。Thinking 完成事件携带同一索引对应的完整不可变 Thinking 块，包括最终文本和 signature；redacted Thinking 没有可见增量，直接通过 Thinking 完成事件携带完整 redacted 块。

- **F5：工具调用流**  
  工具调用开始事件携带索引、完整调用 ID 和完整工具名称。工具调用增量只携带该索引的参数 JSON 字符串分片。工具调用完成事件携带经过完整 JSON 解析与校验的不可变 ToolCall 块。供应商对调用 ID 或名称的原始分片不得直接成为公共事件。

- **F6：轻量响应流结束**  
  响应流结束事件只携带统一停止原因和可选的安全供应商原始原因，不携带 ChatMessage、内容块列表或累计文本。每次成功响应必须且只能产生一次响应流结束事件。

- **F7：Anthropic 协议收敛**  
  AnthropicProvider 负责吸收 Messages API 的 message start/stop、content block start/stop、Thinking signature 分片、redacted Thinking 和工具参数 JSON 分片。它只向上发出七种公共语义事件，不暴露 Anthropic SDK 类型或原始事件名称。

- **F8：结构化消息组装与事务提交**  
  ChatSession 在单次响应期间按索引维护文本缓冲和已完成的 Thinking/ToolCall 块。只有 Provider 流自然结束、收到唯一合法的响应流结束事件、且所有块状态完整时，才构造有序 Assistant ChatMessage，并一次性提交本轮用户与 Assistant 消息。任何 Provider 错误、结构错误、取消、缺少结束事件或结束后额外事件都回滚本轮。

- **F9：工具事件可消费性**  
  TUI 当前可以忽略工具事件，但工具开始、参数增量和工具完成事件必须能够被未来工具 UI 或 Agent Loop 独立消费，不要求它们读取供应商 SDK 对象或重新解析完整供应商响应。

- **F10：现有交互兼容**  
  TUI 继续立即展示文本和 Thinking 增量，在流结束后渲染完整 Markdown 并显示本轮总耗时。当前未执行工具时，用户可见的输入区、用户消息、双轮历史、错误恢复和退出行为保持不变。

- **F11：OpenAI 最小兼容例外**  
  现有 OpenAIProvider 只进行适配新七事件契约所必需的机械迁移，保持当前文本、并行工具调用、错误映射和请求转换行为。不得新增 OpenAI 配置字段、API 端点、Thinking 支持、工具能力或新的 OpenAI 产品行为。

- **F12：ToolResult 边界不变**  
  ToolResult 不是模型响应流事件，继续作为后续用户消息中的结构化内容块存在。本阶段不增加工具执行事件，也不执行任何工具。

## 非功能需求

- **N1：不可变公共契约**  
  七种公共事件及其携带的完成内容块均不可原地修改。流式可变缓冲只允许存在于 Provider 内部解析状态和单轮会话组装状态中。

- **N2：协议隔离**  
  ChatSession、TUI 和未来 Agent Loop 不导入 Anthropic/OpenAI SDK 类型，不判断供应商事件名称，也不读取供应商原始响应对象。

- **N3：单一事件判别方式**  
  消费者只通过事件的具体类型进行匹配，不再同时维护类类型与事件种类枚举两套判别机制。

- **N4：增量无损**  
  Text、Thinking 和工具参数分片必须按同一索引的接收顺序无损拼接。不得按句子缓冲、重新排序或在参数完整前提前解析 JSON。

- **N5：完成块一致性**  
  ThinkingComplete 和 ToolCallComplete 携带的最终块必须与此前同索引增量一致。索引类型冲突、重复开始、重复完成、完成前缺少开始或结束后新增事件均作为结构化流错误处理。

- **N6：错误安全**  
  结构化流错误和 ProviderError 不得包含 API Key、认证头、完整原始响应、Thinking signature、redacted Thinking 数据或未经整理的工具参数原文。

- **N7：实时性**  
  文本和 Thinking 增量到达后应立即向上转发，不得等待内容块完成或整个响应结束。内部组装不得改变当前流式首字延迟特征。

- **N8：兼容现有体验**  
  纯文本多轮对话、Thinking 显示、响应计时、完成后 Markdown、失败回滚、输入提示区和 Windows ConPTY 行为不得回归。

- **N9：可测试性**  
  七事件契约、Anthropic 映射、内部块完成、ChatSession 事务和 TUI 消费必须能使用 FakeProvider 与本机 SSE 服务验证，不依赖真实 API Key。

- **N10：扩展边界**  
  未来 Provider 可以通过同一七事件契约接入；公共核心不得保留仅为某家供应商原始分片格式服务的字段或事件。

## 不做的事

- 不注册、发现、执行或取消任何工具。
- 不实现 Tool Registry、Tool Executor、Agent Loop、权限确认或工具轮次上限。
- 不增加 ToolResult、工具执行开始、工具执行完成或工具执行失败等运行事件。
- 不新增工具调用卡片、工具参数展示或其他 TUI 视觉元素；当前 TUI 可以忽略全部工具事件。
- 不修改 ChatMessage 与 ContentBlock 的既有结构，也不引入 ConversationTurn。
- 不增加会话持久化、历史文件、数据库或跨进程恢复。
- 不新增或扩展 OpenAI 配置字段、模型参数、API 端点、Thinking、工具能力或测试场景。
- 不迁移 OpenAI Chat Completions 到 Responses API。
- 不删除现有 OpenAIProvider；只允许保持新核心事件契约兼容所需的最小修改。
- 不改变 YAML 配置格式、活动 Provider 选择、配置发现规则或 CLI 参数。
- 不改变现有输入提示区、用户消息背景板、响应计时和 Markdown 渲染设计。
- 不把 Anthropic SDK 事件对象、完整原始响应或私有 Thinking 数据暴露给公共事件消费者。

## 验收标准

- **AC1（F1、N1、N3）**  
  核心公共响应流只有 TextDelta、ThinkingDelta、ThinkingComplete、ToolCallStart、ToolCallDelta、ToolCallComplete 和 StreamEnd 七种不可变事件；代码中不存在公共 StreamEventKind，也不存在旧的消息开始、文本/Thinking 块开始、signature 增量、工具 ID/名称增量或通用块结束事件。

- **AC2（F2、F3、N4）**  
  本机 Anthropic SSE 把两个不同索引的文本增量交错发送时，TUI 按到达顺序实时收到增量，ChatSession 在 StreamEnd 后按索引生成两个有序 TextBlock，且每个块内部文本无损拼接。

- **AC3（F4、N4、N5）**  
  Anthropic Thinking 文本和 signature 被拆成多个分片时，公共流实时产生 ThinkingDelta，并在块结束时产生一个携带完整 ThinkingBlock 的 ThinkingComplete；redacted Thinking 直接产生携带完整 RedactedThinkingBlock 的 ThinkingComplete。

- **AC4（F5、F9、N4、N5）**  
  Anthropic Tool Use 的参数 JSON 被拆成多个分片时，公共流依次产生包含完整 ID/name 的 ToolCallStart、对应的 ToolCallDelta 和携带已解析 ToolCallBlock 的 ToolCallComplete。无效 JSON、非 object JSON、空 ID 或空名称产生安全结构化流错误。

- **AC5（F6、N6）**  
  每个成功响应只产生一个 StreamEnd，其中只有统一停止原因和安全供应商原因；事件不携带 ChatMessage、内容块集合、累计文本、signature、redacted 数据或工具参数原文。

- **AC6（F7、N2、N10）**  
  ChatSession、TUI、核心事件与消息模块中没有 Anthropic/OpenAI SDK 导入或原始 SSE 事件名称判断；Anthropic 原始生命周期只在 AnthropicProvider 及其内部解析组件中出现。

- **AC7（F8、N5）**  
  ChatSession 在 Provider 流自然结束后按索引构造完整 Assistant ChatMessage。重复开始、重复完成、索引类型冲突、完成前缺少开始、缺少 StreamEnd、重复 StreamEnd、StreamEnd 后额外事件、Provider 异常和取消均不提交本轮残缺历史。

- **AC8（F10、N7、N8）**  
  Windows ConPTY 中的 Anthropic 双轮纯文本、Thinking、错误恢复和退出场景保持通过；首个增量立即可见、每轮计时重新开始、结束后 Markdown 正常渲染，输入提示区和用户消息视觉行为不变。

- **AC9（F11）**  
  现有 OpenAI 文本与并行工具调用测试在只修改事件映射和消费契约后继续通过；请求体、配置字段、API 端点、错误映射和已支持行为没有新增或变化。

- **AC10（F12、范围）**  
  应用没有发送新的 tools 定义、执行工具、自动继续模型请求或增加工具执行事件；ToolResult 仍只作为结构化用户消息内容块存在。

- **AC11（N6、N9）**  
  单元、Provider 和本机 SSE 测试全部使用占位 Key；错误文本和终端捕获中不存在 Key、认证头、完整响应、Thinking signature、redacted Thinking 数据或无效工具参数原文。

- **AC12（完整回归）**  
  Ruff 格式检查、静态检查、完整 pytest、compileall、两个 CLI help 入口和真实 Windows 端到端测试全部通过。
