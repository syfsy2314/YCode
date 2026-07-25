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

启动时，YCode 只完整校验 `active` 指向的配置，也只解析该配置中的 `${ENV_VAR}`。未激活配置只要求提供唯一的 `name`，其他字段可以暂时不完整；切换 `active` 并重新启动后，新活动配置才会接受完整校验。未激活配置不会创建 SDK 客户端。

YCode 默认从当前工作目录开始逐级向上寻找最近的 `.ycode/config.yaml`。也可以显式指定：

```powershell
ycode --config D:\path\to\config.yaml
```

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
