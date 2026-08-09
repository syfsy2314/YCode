# YCode

YCode 是一个使用 Python 3.12+ 开发的 Windows 终端 AI 助手。当前版本提供 Anthropic Claude 与 OpenAI Chat Completions 的流式多轮对话。

## 项目概览

YCode 的产品形态类似 Claude Code：用户在终端中与模型对话，模型可以读取和
编辑项目文件、搜索代码、执行 PowerShell 命令，也可以通过 MCP 扩展外部工具。
当前 Anthropic 路径提供完整 Agent 循环、工具、权限、上下文管理、会话恢复和项目记忆；
OpenAI 路径仍保持纯流式对话。

核心调用链：

```text
CLI
 → App 组件装配
 → TerminalUI
 → ChatSession
 → AgentLoop
 → Provider / ToolScheduler / ToolExecutor
 → SessionManager / ContextManager / MemoryStore
```

主要模块：

| 目录 | 职责 |
|---|---|
| `ycode/agent/` | Agent 多轮循环、回合结果与统一事件 |
| `ycode/session/` | 对话事务、JSONL 会话存档、恢复与修复 |
| `ycode/context/` | Token 估算、上下文压缩、摘要和检查点 |
| `ycode/prompt/` | 内置提示词、运行时补充、环境与项目上下文 |
| `ycode/tools/` | 内建工具、参数校验、暴露策略、调度与执行 |
| `ycode/security/` | 权限模式、工具决策与 PowerShell 安全检查 |
| `ycode/mcp/` | MCP Server 连接、工具发现、名称映射和调用 |
| `ycode/memory/` | 项目记忆索引、主题文件校验与退出整理 |
| `ycode/providers/` | Anthropic 和 OpenAI 协议适配 |
| `ycode/ui/` | 终端输入、流式渲染、审批和命令交互 |
| `ycode/commands/` | 斜杠命令定义、解析、注册和调度 |
| `ycode/config/` | 项目根发现、YAML/环境变量加载与配置校验 |

关键设计边界：

- JSONL 是会话的事实来源；正常回合先落盘，再提交内存历史并通知 UI。
- `SessionManager` 独占管理 `.ycode/sessions/`；`ChatSession` 负责跨组件的会话事务编排。
- 项目指令、项目记忆和会话压缩摘要是语义独立的上下文来源。
- `.ycode/memory/MEMORY.md` 只向模型提供索引，主题正文由模型按需读取。
- 工具调度允许读操作并发，写操作作为屏障串行；权限引擎和执行器会分别复核访问边界。
- 功能开发遵循 Spec 流程，现有功能文档位于 `docs/features/`。

建议阅读顺序：

1. 本 README：了解项目定位、安装、配置和主要能力。
2. [`docs/Development-Workflow.md`](docs/Development-Workflow.md)：了解 Spec 驱动开发流程。
3. [`docs/features/`](docs/features/)：查看各功能的需求、设计、任务和验收记录。
4. [`docs/notes/ycode-learning-notes.md`](docs/notes/ycode-learning-notes.md)：深入理解完整调用链和典型故障。

## 安装

需要 Python 3.12 或更高版本。在 PowerShell 中进入项目根目录，然后执行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\Activate.ps1
ycode --help
```

当前 `requirements.txt` 的内容是 `-e .[dev]`，会根据 `pyproject.toml` 以可编辑模式安装
YCode 本体、`ycode` 命令入口、运行依赖和开发依赖。已有 `.venv` 时无需重新创建，更新
代码后通常也无需重新安装；仅当 `pyproject.toml` 或依赖发生变化时，再执行安装命令。

如果只使用 YCode、不需要测试和代码检查工具，可以改用：

```powershell
.venv\Scripts\python.exe -m pip install -e .
```

如果 PowerShell 不允许执行激活脚本，或不想激活虚拟环境，可以直接运行：

```powershell
.venv\Scripts\ycode.exe --help
.venv\Scripts\ycode.exe
```

如果激活后仍提示找不到 `ycode`，请确认命令实际安装在当前虚拟环境：

```powershell
.venv\Scripts\python.exe -m pip show ycode
Get-ChildItem .venv\Scripts\ycode.exe
```

## 配置

复制示例配置：

```powershell
Copy-Item .ycode\config.example.yaml .ycode\config.yaml
```

编辑 `.ycode/config.yaml`，至少替换活动配置的 `model`。推荐通过环境变量提供 Key：

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
$env:OPENAI_API_KEY = "your-key"
```

配置顶层使用 `active` 选择当前 Provider。Anthropic 的官方 `base_url` 不包含 `/v1`；OpenAI 的官方 `base_url` 包含 `/v1`。

顶层 `context_window_tokens` 配置 Anthropic 模型的上下文窗口，默认 `200000`。默认会在
完整请求估算超过 167000 Token 时自动压缩，并为摘要输出和估算误差预留空间。该配置
目前不改变 OpenAI 路径。

启动时，YCode 只完整校验 `active` 指向的配置，也只解析该配置中的 `${ENV_VAR}`。未激活配置只要求提供唯一的 `name`，其他字段可以暂时不完整；切换 `active` 并重新启动后，新活动配置才会接受完整校验。未激活配置不会创建 SDK 客户端。

YCode 默认从当前工作目录开始逐级向上寻找最近的 `.ycode/config.yaml`。也可以显式指定：

```powershell
ycode --config D:\path\to\config.yaml
```

### MCP 工具

Anthropic 配置可在顶层使用 `mcp_servers` 接入本地 `stdio` 或远程 `streamable_http` MCP Server。项目根是标准 `.ycode/config.yaml` 的上级目录；显式使用其他 YAML 时则是该文件所在目录。项目根目录的 `.env` 会自动读取，系统环境变量优先于 `.env`；不要提交真实 `.env`。`${VARIABLE}` 可用于 stdio 的 `env` 值和 HTTP `headers` 值，变量只传给对应 Server。每个 Server 可设置 `enabled`、`startup_timeout_seconds`（默认 10 秒）和 `tool_timeout_seconds`（默认 60 秒）。

发现的工具以 `mcp_<server>_<tool>` 名称注册，并默认延迟加载：模型先通过 `tool_search` 搜索需要的名称，下一轮才会收到完整 Schema。输入 `/mcp` 可查看本地连接状态；工具目录只在启动时发现，修改 Server 后需重启。

plan-only 默认不显示 MCP 工具。可在 `.ycode/security.yaml` 配置 `plan_only.allow_mcp_tools` 精确白名单；即使在白名单内，每一次 MCP 调用仍需要人工确认，不能授予会话永久权限。首版不支持 OAuth、Resources、Prompts，也不向 OpenAI 路径接入 MCP。

### 上下文管理

Anthropic 会话会在请求前控制工具结果大小：单个结果超过 50 KiB，或同一工具结果消息
合计超过 200 KiB 时，脱敏后的完整内容会临时保存到
`.ycode/context/<session-id>/tool-results/`，对话只保留预览、哈希和 manifest 路径。
模型需要精确细节时可以使用现有文件读取工具重新读取。该目录已被 Git 忽略，并在正常
关闭会话时删除。

完整请求逼近窗口上限时，YCode 会调用当前 Anthropic 模型生成结构化摘要，同时原样
保留活动回合的最新用户消息。输入 `/compact` 可以在未达到自动阈值时手动压缩全部已
提交历史；命令本身不进入对话历史。连续三次摘要失败后自动摘要会熔断，仍可使用
`/compact` 重试并在成功后恢复。

### 项目指令

Anthropic 启动时会读取项目根目录的可选 `YCODE.md`，并把内容作为独立系统上下文注入
每次请求。文件可以用独占行 `@include relative/path.md` 在原位置引用其他 UTF-8 文本；
引用相对当前文件解析，最多允许第 0 至第 5 层，循环、绝对路径、缺失文件以及通过
`..` 或符号链接跳出项目目录都会阻止启动。启动后使用同一份快照，修改文件需重启生效。

### 会话恢复与项目记忆

Anthropic 的完整成功回合以 JSONL 追加保存在 `.ycode/sessions/`，每条结构化消息带 UTC
时间，并以 `turn_commit` 标记完整回合。默认启动新会话；`ycode --continue` 恢复最新
会话，空闲时可输入 `/resume <session-id>` 切换到指定会话。坏 JSON 行会被跳过，结构
不完整或工具调用结果不配对的尾部会修复到最后一个完整提交。当前 OpenAI 路径不支持
这些持久化和恢复入口。

项目记忆位于 `.ycode/memory/`。`MEMORY.md` 只保存
`- [名称](relative-file.md) — 简短说明` 形式的单层索引；主题文件使用 YAML frontmatter
声明 `name`、`description` 和四类之一的 `type`。普通请求只注入有效索引，模型需要正文
时通过 `read_file` 读取。正常使用 `/exit`、`/quit`、EOF 或空闲 `Ctrl+C` 退出时，如果
本次进程有新回合，YCode 最多等待 30 秒让隔离的无工具模型请求决定创建、更新或删除
记忆。进程崩溃、强制终止、断电以及系统崩溃不保证执行记忆整理；会话追加只刷新进程
缓冲，不承诺断电级持久性。

## 启动

```powershell
ycode
```

也可以使用：

```powershell
.venv\Scripts\python.exe -m ycode
```

输入消息后，回答以纯文本增量实时显示；本轮正常结束后再整体渲染 Markdown。Claude 启用 `thinking: true` 时，Thinking 在独立区域以纯文本流式显示。

等待输入时，`Send a message...` 位于两条普通横线之间。Anthropic 会话下方显示
`/help for commands`，输入命令名或别名前缀后按 Tab 可补全；多个匹配会显示候选列表。
补全只作用于命令词，不补全参数。OpenAI 会话保持原有 `? for help` 提示和输入行为。

Anthropic 当前提供以下内置命令：

- `/help [command]`：列出命令或查看某条命令的详细用法。
- `/exit`（别名 `/quit`）：正常退出。
- `/plan`、`/agent`：切换计划或执行模式。
- `/mcp`：显示 MCP 连接状态。
- `/compact`：手动压缩已提交的对话上下文。
- `/permission [strict|default|allow|clear]`：查看、切换权限模式或清除临时授权。
- `/resume <session-id>`：恢复指定会话。

所有命令名大小写不敏感；未知的 `/` 命令不会发送给模型，而会引导使用 `/help`。
当前不支持自定义命令或命令参数补全。

提交后，用户消息会以带蓝色 `❯` 的无边框背景板显示在终端滚动区，多行内容保持在同一背景板内。

使用 `/exit`、`/quit` 或 `Ctrl+C` 退出。Anthropic 对话按上述边界写入项目本地存档；
OpenAI 对话仍只存在于当前进程。

## 测试

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
```

自动化测试使用本机 SSE 模拟服务和占位 Key，不调用真实模型。真实 API 冒烟步骤见 [`docs/manual-api-test.md`](docs/manual-api-test.md)。
