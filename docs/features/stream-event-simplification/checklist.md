# YCode 流事件简化 Checklist

> 状态：已完成

## 使用说明

- Checklist 获得批准后才开始修改实现代码。
- 只有实际执行验证且观察到通过结果后，才能勾选对应项目。
- 任一必选项未通过时，不将本功能报告为完成。
- 全程使用占位 Key 与本地 SSE 服务，不连接真实模型 API。
- 不修改用户的 `.ycode/config.yaml`，不创建 Git commit。

## 七事件公共契约（AC1、AC5）

- [x] 公共 `StreamEvent` 只包含 `TextDelta`、`ThinkingDelta`、`ThinkingComplete`、`ToolCallStart`、`ToolCallDelta`、`ToolCallComplete` 和 `StreamEnd`。
- [x] 七种事件均为冻结且带 slots 的 dataclass，完成事件携带的 ContentBlock 也不可原地修改。
- [x] 六种内容事件都携带非负 index，空文本或空参数 delta 不会进入公共流。
- [x] `StreamEventKind`、公共 `kind` 属性和旧生命周期事件已完全删除，且没有兼容别名。
- [x] `StreamEnd` 只携带 `stop_reason` 与安全的 `provider_reason`。
- [x] 每次成功响应只有一个 `StreamEnd`，其中不包含消息、内容块、累计文本、signature、redacted data 或工具参数。

## ResponseAssembler 与有序消息（AC2、AC7）

- [x] `ResponseAssembler` 取代 `MessageAssembler`，模块路径仍为 `ycode.session.assembler`。
- [x] Text delta 可按 index 隐式建立文本状态，并按同一索引的接收顺序无损拼接。
- [x] 多个文本索引可以交错接收；TUI 保持到达顺序，最终 TextBlock 按 index 排序。
- [x] ThinkingComplete 的块类型和文本与同索引 ThinkingDelta 一致。
- [x] ToolCallStart 必须先于同索引 ToolCallDelta/ToolCallComplete，ID 与名称非空。
- [x] ToolCall 参数只在完整分片到齐后解析，最终值必须是 JSON object；空参数按 `{}` 处理。
- [x] ToolCallComplete 的 ID、名称和参数与此前同索引事件一致。
- [x] RedactedThinkingBlock 可以没有可见 delta，直接通过 ThinkingComplete 完成。
- [x] StreamEnd 到达时，响应非空且不存在未完成 Thinking/ToolCall 状态。
- [x] 重复开始、重复完成、类型冲突、缺少开始/完成/StreamEnd、重复 StreamEnd 和结束后事件均被拒绝。
- [x] `finish()` 只在合法 StreamEnd 后成功一次，并生成按 index 排序的不可变 Assistant ChatMessage。
- [x] 组装错误不包含工具参数原文、Thinking signature、redacted data、密钥或完整响应。

## Anthropic 归一化（AC3、AC4、AC6）

- [x] Anthropic message/content block 生命周期只存在于 AnthropicProvider 的私有解析状态。
- [x] text 和 thinking 非空增量到达后立即发出 TextDelta/ThinkingDelta，不等待块或消息结束。
- [x] Thinking signature 只在 Provider 内累积，不形成公共 delta 事件。
- [x] thinking block 结束时产生唯一 ThinkingComplete，并携带完整文本与 signature。
- [x] redacted thinking 结束时产生携带完整 RedactedThinkingBlock 的 ThinkingComplete。
- [x] tool_use start 产生包含完整 ID/name 的 ToolCallStart，不暴露供应商 ID/name 分片。
- [x] input JSON 分片按到达顺序产生 ToolCallDelta，块结束时产生已解析 ToolCallComplete。
- [x] 无效 JSON、非 object JSON、空工具 ID/name 和非法生命周期产生安全流错误。
- [x] `thinking: false` 继续显式发送 `{"type": "disabled"}`，并忽略异常返回的完整 thinking 块。
- [x] 收到 message_stop 且 SDK 迭代器自然结束后，才发出位于公共流末尾的 StreamEnd。
- [x] ChatSession、ResponseAssembler、TUI、核心事件和消息模块不导入 SDK，也不判断 Anthropic 原始事件名称。

## OpenAI 最小兼容（AC9）

- [x] OpenAI 文本仍使用 index 0 实时产生 TextDelta。
- [x] 并行工具调用继续使用 `tool_index + 1`，不同工具分片不会互相混合。
- [x] Provider 内部缓存分片 ID/name，并在完成后产生完整 Start、Delta、Complete 序列。
- [x] OpenAI 请求消息转换、Chat Completions 端点、停止原因、单 choice 和错误映射行为保持不变。
- [x] 没有新增 OpenAI 配置字段、端点、Thinking、工具能力或新的产品测试场景。

## ChatSession 与 TUI（AC7、AC8）

- [x] ChatSession 在事件转发给调用方之前先交给 ResponseAssembler 校验。
- [x] 只有 Provider 自然结束且 `finish()` 成功后，才一次性提交本轮 user + assistant 历史。
- [x] Provider 错误、组装错误、缺少结束、提前停止、取消和结束后额外事件不会提交残缺历史。
- [x] 多轮请求继续携带已提交的有序结构化历史。
- [x] TUI 只展示 TextDelta 与 ThinkingDelta，并由 StreamEnd 触发 renderer.complete()。
- [x] ThinkingComplete 与三个工具事件当前不展示、不执行，也不重复显示内容。
- [x] 首个增量仍立即可见，流式阶段仍为纯文本，结束后整体渲染 Markdown。
- [x] 每轮响应计时重新开始并在结束时显示总耗时。
- [x] 输入提示区、用户消息背景板、错误恢复和退出行为没有变化。

## 范围与安全（AC10、AC11）

- [x] ToolResult 仍只作为结构化用户消息内容块，不是响应流事件。
- [x] 没有新增 Tool Registry、Tool Executor、AgentLoop、权限确认或工具执行事件。
- [x] 应用没有发送新的 tools 定义、执行工具或自动继续模型请求。
- [x] 没有修改 ChatMessage/ContentBlock、YAML 配置、ProviderFactory、配置发现或 CLI 参数。
- [x] 所有自动化与 E2E 测试使用占位 Key 和本地服务，不连接真实 API。
- [x] 错误文本和终端捕获中不存在 API Key、认证头、完整响应、signature、redacted data 或无效工具参数原文。

## 局部自动化验证

- [x] `.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/session/test_assembler.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py tests/unit/ui/test_terminal.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/integration/test_openai_stream.py -q`
- [x] `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q`

## 完整质量门禁（AC12）

- [x] `.venv\Scripts\python.exe -m ruff format --check .`
- [x] `.venv\Scripts\python.exe -m ruff check .`
- [x] `.venv\Scripts\python.exe -m pytest -q`
- [x] `.venv\Scripts\python.exe -m compileall -q ycode tests`
- [x] `.venv\Scripts\python.exe -m ycode --help`
- [x] `.venv\Scripts\ycode.exe --help`

## 真实交互与最终检查

- [x] 使用本机 Anthropic SSE 服务和 Windows ConPTY 启动 YCode，完成至少两轮纯文本对话。
- [x] 在真实终端观察首增量实时出现、每轮计时重置、最终 Markdown 和正常退出。
- [x] 在真实终端回归 Thinking enabled、Thinking disabled 和流错误后恢复。
- [x] 在真实终端确认输入提示区与用户消息视觉布局没有回归。
- [x] 搜索确认旧事件、`StreamEventKind`、旧 `kind` 访问和 `MessageAssembler` 均无残留。
- [x] 搜索确认没有新增 tools 请求、工具执行、AgentLoop 或新的 OpenAI 能力。
- [x] 当前 `.git` 目录为空，无法执行 Git diff；已按本轮工具记录和批准的文件清单复核修改范围。
- [x] 最终报告列出所有执行命令、实际结果、测试数量和任何未通过项目。

## 验收结果

- 完整 pytest：137 passed。
- Windows ConPTY：10 passed。
- Ruff format、Ruff check、compileall 和两个 CLI help 入口全部通过。
- 旧契约、越界工具能力和上层 SDK/原始事件耦合搜索均无匹配。
- 未连接真实 API，未发现未通过项目。
