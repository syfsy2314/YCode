# YCode 结构化消息基础层 Checklist

> 状态：已完成

## 使用说明

- Checklist 获得批准后才开始修改实现代码。
- 实施过程中仅在实际验证通过后勾选对应项目。
- 任一必选项未通过时，不将本功能报告为完成。
- 本阶段不创建 Git commit。

## 范围检查

- [x] 只修改消息、事件、Assembler、ChatSession、Provider 和必要的 TUI 事件边界。
- [x] 不向真实请求添加 `tools` 定义。
- [x] 不实现 Tool Registry、Tool Executor、Agent Loop、权限确认或本地工具能力。
- [x] 不引入会话落盘、数据库或跨 Provider 的私有 Thinking 转换。
- [x] 当前配置格式、Provider 选择方式和启动入口保持不变。

## 结构化消息模型

- [x] `TextBlock`、`ThinkingBlock`、`RedactedThinkingBlock`、`ToolCallBlock` 和 `ToolResultBlock` 均已实现。
- [x] 完成后的 `ChatMessage` 和全部 `ContentBlock` 不可原地修改。
- [x] `ChatMessage.content` 使用有序 tuple，并且是消息内容的唯一真实数据源。
- [x] 没有额外保存可能与 `content` 失去同步的 `tool_uses`、`tool_results` 或 `thinking_blocks` 字段。
- [x] `message.text` 和按类型读取内容块的接口均从有序 `content` 只读派生。
- [x] user/assistant 角色与允许的内容块组合得到校验。
- [x] ToolCall ID、名称和参数对象得到保留。
- [x] ToolResult 通过 `tool_call_id` 与 ToolCall 关联，并保留错误标记。
- [x] ToolCall 参数的递归 JSON 数据在核心消息中不可变，交给 SDK 前可恢复为普通 dict/list。

## Typed StreamEvent

- [x] 消息开始、块开始、块增量、块结束和消息完成使用明确的事件类型。
- [x] 文本、Thinking、Thinking signature、工具 ID、工具名称和工具 JSON 参数分别使用正确的增量事件。
- [x] 所有块级事件携带稳定且非负的 block index。
- [x] `MessageCompleted` 携带统一 `StopReason` 和安全的供应商原始原因。
- [x] ChatSession、MessageAssembler 和 TUI 不导入或判断官方 SDK 事件类型。

## MessageAssembler

- [x] Assembler 能按 block index 同时维护多个交错内容块。
- [x] 最终内容块按 index 排序，而不是按完成时间排序。
- [x] Text、Thinking、signature 和工具 JSON 分片均按各自接收顺序无损拼接。
- [x] 工具参数只在对应块结束时解析一次，并且最终值必须是 JSON object。
- [x] 空工具参数按 `{}` 处理。
- [x] 未知索引、负索引、重复开始、重复结束、类型不匹配和完成后额外事件均被拒绝。
- [x] 无效 JSON、非 object JSON、空工具 ID 和空工具名称均被拒绝。
- [x] 消息完成时仍存在未关闭内容块会失败。
- [x] 缺少消息完成事件时 `finish()` 会失败。
- [x] 组装错误不包含 API Key、认证头、完整原始响应或未整理的工具参数原文。

## Anthropic Provider

- [x] SDK 实例统一使用 `self.client`，并继续支持测试 client 注入与关闭。
- [x] `content_block_start`、`content_block_delta`、`content_block_stop`、`message_delta` 和 `message_stop` 均正确映射。
- [x] Text、Thinking、signature、redacted Thinking、Tool Use 和 `input_json_delta` 均不会被静默丢弃。
- [x] Anthropic stop reason 正确映射到统一 `StopReason`。
- [x] 有序 ChatMessage 可转换为符合 Anthropic Messages API 的内容块。
- [x] Assistant ToolCall 与后续 user ToolResult 保持正确消息顺序和调用 ID。
- [x] Thinking disabled/adaptive 的现有请求行为不回归。
- [x] 本机 SSE 测试覆盖 Thinking、signature、文本、两个 Tool Use 和交错 JSON 分片。

## OpenAI Provider

- [x] SDK 实例统一使用 `self.client`，并继续支持测试 client 注入与关闭。
- [x] 文本 delta 和 `tool_calls[index]` 的 ID、名称、arguments 分片均正确映射。
- [x] 文本和多个并行 ToolCall 使用互不冲突的统一 block index。
- [x] 多个并行 ToolCall 的交错分片分别组装到正确调用。
- [x] finish reason 正确映射到统一 `StopReason`。
- [x] 多个有效 choice 被明确拒绝，不会合并成一条消息。
- [x] 结构化 ToolCall/ToolResult 历史正确转换为 assistant tool_calls 和后续 `role="tool"` 消息。
- [x] 不兼容的 Thinking 历史产生明确转换错误，不会静默删除。
- [x] OpenAIProvider 仍使用已批准的 Chat Completions 协议，没有擅自迁移到 Responses API。

## ChatSession 与事务

- [x] 普通用户输入转换为包含单个 TextBlock 的结构化用户消息。
- [x] 每次模型请求创建独立 MessageAssembler。
- [x] 流事件在交给 TUI 的同时被 Assembler 消费。
- [x] Provider 流完全正常结束且 Assembler 成功完成后，用户与 Assistant 消息才一次性提交。
- [x] Provider 错误、组装错误、取消、缺少完成事件和完成后非法事件均不会提交残缺历史。
- [x] 多轮对话历史保留有序内容块、Thinking signature、ToolCall 和 ToolResult。
- [x] 结构化历史只保存在当前进程内。

## TUI 与现有行为

- [x] 首个 Thinking 或文本增量到达时立即开始可见输出。
- [x] 流式阶段继续追加普通文本，完成后使用完整文本进行 Markdown 渲染。
- [x] Thinking、计时、错误恢复和退出行为保持正常。
- [x] block 生命周期、signature 和工具 JSON 事件不会直接产生多余终端输出。
- [x] 四行输入提示区、横线、蓝色指示符和预留帮助提示保持不变。
- [x] 用户消息指示符和背景板保持不变。
- [x] 未配置 tools 时，用户可见的纯聊天流程没有变化。

## 自动化验证

- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/session/test_assembler.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py tests/unit/ui/test_renderer.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/integration/test_openai_stream.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/test_cli.py -q`
- [x] `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q`
- [x] `.\.venv\Scripts\python.exe -m ruff format --check .`
- [x] `.\.venv\Scripts\python.exe -m ruff check .`
- [x] `.\.venv\Scripts\python.exe -m pytest -q`
- [x] `.\.venv\Scripts\python.exe -m compileall -q ycode tests`
- [x] `.\.venv\Scripts\python.exe -m ycode --help`
- [x] `.\.venv\Scripts\ycode.exe --help`

## 真实交互与最终检查

- [x] 使用本机 SSE 服务和真实 PTY/ConPTY 启动 YCode，完成 OpenAI 双轮纯文本对话。
- [x] 使用本机 SSE 服务和真实 PTY/ConPTY 完成 Anthropic Thinking 对话。
- [x] 在真实终端中确认流式输出、总耗时和最终 Markdown 渲染。
- [x] 在真实终端中确认输入提示区和用户消息视觉行为没有回归。
- [x] 搜索确认旧 `StreamEvent(kind, text)`、字符串 `ChatMessage.content` 和 Provider `_client` 已从受影响实现中移除。
- [x] 搜索确认没有新增 Tool Executor、Agent Loop、本地执行入口或真实请求的 `tools` 字段。
- [x] 测试和终端输出中不存在 API Key、认证头或完整原始响应泄漏。
- [x] 最终报告列出所有执行命令、实际结果和任何未通过项目。
