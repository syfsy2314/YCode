# YCode 学习笔记

> 本笔记以当前代码为准，主要沿 Anthropic 调用链理解项目。

## 命令启动流程

YCode 有两个入口：

~~~powershell
ycode
python -m ycode
~~~

两者最终都会调用 ycode.cli.main()。

### 1. ycode 命令

pyproject.toml 注册命令：

~~~toml
[project.scripts]
ycode = "ycode.cli:main"
~~~

安装项目后会生成 .venv\Scripts\ycode.exe：

~~~text
PowerShell 执行 ycode
    ↓
ycode.exe
    ↓
ycode.cli.main()
~~~

### 2. python -m ycode

Python 执行 ycode/__main__.py：

~~~python
from ycode.cli import main

raise SystemExit(main())
~~~

调用链：

~~~text
python -m ycode
    ↓
ycode/__main__.py
    ↓
ycode.cli.main()
~~~

### 3. cli.main()

~~~python
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        asyncio.run(run_app(args.config))
    except (ConfigError, UIError) as error:
        print(f"YCode: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0

    return 0
~~~

main() 负责：

- 解析 --config。
- 使用 asyncio.run() 启动异步应用。
- 处理启动错误和 Ctrl+C。
- 返回进程退出码。

### 4. run_app()

~~~python
async def run_app(config_path=None) -> None:
    path = discover_config(config_path)
    config = load_config(path)

    provider = create_provider(config.active_provider)
    session = ChatSession(provider)

    try:
        ui = TerminalUI(config.active_provider, session)
        await ui.run()
    finally:
        await session.close()
~~~

以下以 active Provider 为 Anthropic 为例：

~~~text
run_app()
    ├── discover_config()：查找 .ycode/config.yaml
    ├── load_config()：解析和校验配置
    ├── create_provider()：创建 AnthropicProvider
    ├── ChatSession(provider)
    ├── TerminalUI(config, session)
    └── await ui.run()
~~~

对象关系：

~~~text
TerminalUI
    └── ChatSession
            └── AnthropicProvider
                    └── AsyncAnthropic client
~~~

退出时，finally 保证依次关闭 ChatSession、Provider 和 SDK client。

### 面试表述

YCode 通过 pyproject.toml 注册 CLI 入口。cli.main() 负责参数解析和启动 asyncio 事件循环，run_app() 作为组合根完成配置加载、Provider 创建、ChatSession 注入和 TerminalUI 创建，并通过 finally 保证网络资源释放。

## 核心数据与组件关系

当前依赖方向：

~~~text
TerminalUI
    ↓
ChatSession
    ↓
ChatProvider
    ↓
AnthropicProvider
    ↓
Anthropic SDK
~~~

请求与响应分别使用不同的核心数据：

~~~text
请求：ChatMessage
响应：StreamEvent
完整响应组装：ResponseAssembler
~~~

### 1. ChatMessage 与 ContentBlock

ChatMessage 不再只保存一个字符串，而是保存有序内容块：

~~~python
@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: tuple[ContentBlock, ...]
~~~

主要内容块：

| 类型 | 含义 |
|---|---|
| TextBlock | 普通文本 |
| ThinkingBlock | Thinking 文本和 signature |
| RedactedThinkingBlock | 不可读的加密 Thinking |
| ToolCallBlock | 工具 ID、名称和完整参数 |
| ToolResultBlock | 工具执行结果 |

普通用户输入：

~~~python
user_message = ChatMessage.user_text(user_text)
~~~

得到：

~~~python
ChatMessage(
    role="user",
    content=(TextBlock(user_text),),
)
~~~

message.text 可以提取一条消息中所有 TextBlock 的文本。

### 2. 七种 StreamEvent

公共流不再使用 StreamEventKind。每种事件都是独立、不可变的数据类：

~~~text
TextDelta
ThinkingDelta
ThinkingComplete
ToolCallStart
ToolCallDelta
ToolCallComplete
StreamEnd
~~~

StreamEvent 是这七种类型的联合：

~~~python
type StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
~~~

消费者通过 isinstance() 判断事件类型：

~~~python
if isinstance(event, TextDelta):
    ...
~~~

而不是读取 event.kind。

### 3. AnthropicProvider

Provider 是协议适配器，负责两个方向：

~~~text
ChatMessage
    ↓
Anthropic Messages API 请求格式

Anthropic SDK Event
    ↓
七种公共 StreamEvent
~~~

Provider 内部处理 Anthropic 的 message/block 生命周期、Thinking signature 和工具参数 JSON，不把 SDK 类型暴露给上层。

Provider 只负责协议解析，不负责创建最终历史消息。

### 4. ResponseAssembler

ResponseAssembler 把一轮中的多个 StreamEvent 组装成完整 Assistant ChatMessage：

~~~text
多个 StreamEvent
    ↓ consume(event)
ResponseAssembler
    ↓ finish()
完整 ChatMessage
~~~

核心接口：

~~~python
class ResponseAssembler:
    def consume(self, event: StreamEvent) -> None:
        ...

    def finish(self) -> ChatMessage:
        ...
~~~

它按内容块 index 保存状态：

~~~text
index 0：Thinking
index 1：Text
index 2：ToolCall
~~~

例如：

~~~text
ThinkingDelta(0, "分析")
ThinkingComplete(0, ThinkingBlock(...))
TextDelta(1, "你")
TextDelta(1, "好")
StreamEnd(END_TURN)
~~~

finish() 最终生成：

~~~python
ChatMessage(
    role="assistant",
    content=(
        ThinkingBlock(...),
        TextBlock("你好"),
    ),
)
~~~

Assembler 还会校验：

- 是否收到 StreamEnd。
- 内容块索引和类型是否一致。
- Thinking 和 ToolCall 是否完整。
- 工具参数是否为有效 JSON object。
- 响应结束后是否还有额外事件。

### 5. ChatSession

ChatSession 管理多轮历史和单轮事务：

~~~python
user_message = ChatMessage.user_text(user_text)
request_messages = (*self._history, user_message)
assembler = ResponseAssembler()

async for event in self._provider.stream_chat(request_messages):
    assembler.consume(event)
    yield event

assistant_message = assembler.finish()
self._history.extend((user_message, assistant_message))
~~~

关键顺序：

~~~text
创建 user_message，但暂不保存
    ↓
Provider 产生事件
    ↓
Assembler 先校验和收集
    ↓
事件再 yield 给 UI
    ↓
Provider 自然结束
    ↓
assembler.finish()
    ↓
同时提交 user + assistant 历史
~~~

如果 Provider、Assembler 或调用方中途失败，本轮不会写入历史。

### 6. TerminalUI

TerminalUI 只消费当前需要展示的三种事件：

~~~python
if isinstance(event, ThinkingDelta):
    renderer.append_thinking(event.text)
elif isinstance(event, TextDelta):
    renderer.append_text(event.text)
elif isinstance(event, StreamEnd):
    await renderer.complete()
~~~

ThinkingComplete 和工具事件会进入 Assembler，但当前 TUI 不展示它们。

当前项目已经具备工具调用的结构化消息和事件基础，但尚未实现：

- 工具注册与执行。
- Tool Registry 和 Tool Executor。
- Agent Loop。
- 工具权限确认和工具 UI。

### 7. 职责边界

| 组件 | 职责 |
|---|---|
| AnthropicProvider | 协议转换和原始 SSE 解析 |
| ResponseAssembler | 事件校验和完整 Assistant 消息组装 |
| ChatSession | 多轮历史与事务提交 |
| TerminalUI | 输入和可见事件分发 |
| Renderer | 流式刷新、计时和 Markdown 渲染 |

可以记成：

~~~text
Provider 负责翻译
Assembler 负责拼装
ChatSession 负责协调和提交
TerminalUI 负责显示
~~~

### 面试表述

YCode 使用结构化 ChatMessage 和七种语义 StreamEvent 隔离供应商协议。AnthropicProvider 将 Messages API 的 SSE 转换为统一事件，ResponseAssembler 按内容块索引验证并组装完整 Assistant 消息，ChatSession 在流完整结束后事务式提交本轮历史，TerminalUI 只消费文本、Thinking 和结束事件。

## Anthropic SSE 流式链路

YCode 不直接解析 SSE 文本，而是由 Anthropic SDK 解析，再由 AnthropicProvider 转成公共语义事件。

~~~text
Anthropic API
    ↓ 原始 SSE
AsyncAnthropic client
    ↓ Anthropic SDK Event
AnthropicProvider
    ↓ StreamEvent
ResponseAssembler + ChatSession
    ↓ StreamEvent
TerminalUI
    ↓
Renderer
~~~

### 1. 建立响应流

~~~python
stream = await self.client.messages.create(
    model=self._config.model,
    max_tokens=16_000,
    messages=self._messages(messages),
    stream=True,
    thinking=thinking_config,
)
~~~

这里分为两个阶段：

~~~text
await create()
    得到一个异步 Stream 对象

async for event in stream
    逐个读取 SDK Event
~~~

await create() 只等待请求和响应流建立，不等待完整回答。

### 2. Anthropic 原始事件映射

Provider 在单次请求内维护私有内容块状态，并按 index 关联事件。

| Anthropic 原始事件 | Provider 行为 | 公共事件 |
|---|---|---|
| message_start | 标记消息开始 | 无 |
| text_delta | 读取文本 | TextDelta |
| thinking_delta | 累计并输出 Thinking | ThinkingDelta |
| signature_delta | 只在 Provider 内累计 | 无 |
| input_json_delta | 累计工具参数 | ToolCallDelta |
| thinking block stop | 构造完整 ThinkingBlock | ThinkingComplete |
| tool block start | 保存 ID 和名称 | ToolCallStart |
| tool block stop | 解析完整参数 | ToolCallComplete |
| message_delta | 保存停止原因 | 无 |
| message_stop | 标记供应商响应完成 | 无 |
| SDK 迭代器自然结束 | 验证响应完整 | StreamEnd |

Provider 对文本的处理示例：

~~~python
if delta_type == "text_delta":
    text = _string_field(delta, "text")
    if text:
        yield TextDelta(index, text)
~~~

Thinking 完成时会保留 signature：

~~~python
yield ThinkingComplete(
    index,
    ThinkingBlock(
        text,
        "".join(state.signature_parts),
    ),
)
~~~

工具参数会在内容块结束时解析为 ToolCallBlock。

### 3. 事件逐层传递

~~~python
# Provider：读取 SDK 事件并产生公共事件
async for sdk_event in stream:
    yield TextDelta(index, text)


# ChatSession：先交给 Assembler，再向 UI 转发
async for event in provider.stream_chat(messages):
    assembler.consume(event)
    yield event


# TerminalUI：最终消费可见事件
async for event in session.stream_reply(user_text):
    if isinstance(event, TextDelta):
        renderer.append_text(event.text)
~~~

一个文本增量的路径：

~~~text
Anthropic text_delta("你好")
    ↓
SDK Event
    ↓
Provider yield TextDelta(index, "你好")
    ↓
Assembler.consume(event)
    ↓
ChatSession yield event
    ↓
TerminalUI
    ↓
Renderer 显示“你好”
~~~

### 4. await、async for 与 yield

| 写法 | 作用 |
|---|---|
| await create() | 等待一个异步结果：Stream 对象 |
| async for | 逐个读取多个异步事件 |
| yield | 向上交付一个事件并暂停生成器 |

~~~text
await：向下等待
async for：逐个读取
yield：向上交付
~~~

### 5. 完成与错误

AnthropicProvider 只有在满足以下条件时才产生 StreamEnd：

- 收到 message_stop。
- 所有内容块都已关闭。
- SDK 迭代器自然结束。

ChatSession 收到事件后先调用 assembler.consume()。Provider 自然结束后再调用 assembler.finish()，成功后才提交历史。

任何协议错误、缺少结束事件、组装错误、取消或提前停止迭代都会回滚本轮消息。

### 面试表述

YCode 使用 Anthropic 异步 SDK 建立 SSE 响应流。AnthropicProvider 吸收供应商原始 message/block 生命周期，将文本、Thinking 和工具调用映射为七种公共语义事件。ChatSession 对每个事件先调用 ResponseAssembler 进行校验和组装，再实时转发给 TUI；只有 Provider 自然结束且 finish() 成功后，才一次性提交用户消息和完整 Assistant 消息。
