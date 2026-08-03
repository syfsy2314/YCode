# YCode

YCode 是一个使用 Python 3.12+ 开发的 Windows 终端 AI 助手。当前版本提供 Anthropic Claude 与 OpenAI Chat Completions 的流式多轮对话。

## 安装

在 PowerShell 中进入项目目录：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 会从 `pyproject.toml` 安装 YCode、运行依赖和开发依赖。已有 `.venv` 时无需重新创建。

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

## 启动

```powershell
ycode
```

也可以使用：

```powershell
.venv\Scripts\python.exe -m ycode
```

输入消息后，回答以纯文本增量实时显示；本轮正常结束后再整体渲染 Markdown。Claude 启用 `thinking: true` 时，Thinking 在独立区域以纯文本流式显示。

等待输入时，`Send a message...` 位于两条普通横线之间，下方显示静态提示 `? for help`。该提示目前只预留未来的帮助或 Skill 入口；输入 `?` 会作为普通消息发送。

提交后，用户消息会以带蓝色 `❯` 的无边框背景板显示在终端滚动区，多行内容保持在同一背景板内。

使用 `/exit`、`/quit` 或 `Ctrl+C` 退出。对话历史只存在于当前进程，不写入磁盘。

## 测试

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
```

自动化测试使用本机 SSE 模拟服务和占位 Key，不调用真实模型。真实 API 冒烟步骤见 [`docs/manual-api-test.md`](docs/manual-api-test.md)。
