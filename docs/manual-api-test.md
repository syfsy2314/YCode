# YCode 真实 API 手动测试

> 真实 API 测试由用户在本机执行。不要把 Key 写入仓库、测试输出或截图。

## 准备

1. 激活项目虚拟环境并安装依赖：

   ```powershell
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. 复制 `.ycode/config.example.yaml` 为 `.ycode/config.yaml`。
3. 将示例中的 `model` 替换为账号实际可用的模型 ID。

YCode 只完整校验并解析 `active` 指向的配置。测试某一家 API 时，只需要设置该活动配置引用的 Key；未激活配置的 Key 环境变量可以不设置。切换 `active` 后需要重新启动，此时新活动配置才会执行必填字段、协议、Thinking 和环境变量校验。

## Anthropic Claude

1. 在当前 PowerShell 会话设置 Key：

   ```powershell
   $env:ANTHROPIC_API_KEY = "your-real-key"
   ```

2. 将配置的 `active` 设置为 `claude-local`，确认 `base_url` 为 `https://api.anthropic.com`。
3. 保持 `thinking: true`，运行 `ycode`。
4. 发送一条需要简短分析的问题。
5. 观察：首个文本前计时可见；Thinking 在独立区域流式显示；最终回答流中是纯文本，结束后渲染 Markdown；总耗时冻结。
6. 再发送一条引用上一轮内容的问题，确认模型能理解上下文。
7. 输入 `/exit`，确认无异常堆栈。

如所选模型不支持 adaptive extended thinking，YCode 应明确报错。可将 `thinking` 改为 `false` 后重启，验证普通文本流。

### Anthropic 缓存验证（开发验收）

保持 Anthropic 为活动配置并已设置 `ANTHROPIC_API_KEY`，在项目根目录运行：

```powershell
@'
import asyncio
import uuid
from pathlib import Path

from ycode.config.discovery import discover_config
from ycode.config.loader import load_config
from ycode.core import AgentModelRequest, ChatMessage, StreamEnd
from ycode.providers.anthropic import AnthropicProvider
from ycode.prompt import build_builtin_prompt
from ycode.tools import create_builtin_registry
from ycode.tools.command import PowerShellCommandRunner
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(discover_config()).active_provider
    provider = AnthropicProvider(config)
    registry = create_builtin_registry(
        WorkspacePathResolver(workspace),
        TextFileService(),
        PowerShellCommandRunner(),
    )
    marker = f"Cache verification: {uuid.uuid4()}"
    stable = (*build_builtin_prompt().content_blocks, marker)
    try:
        for index in range(2):
            request = AgentModelRequest(
                messages=(ChatMessage.user_text("Reply with OK only."),),
                system_prompt=stable,
                supplements=(
                    f"<environment_context>cache-test-run={index}</environment_context>",
                ),
                tools=registry.definitions(),
            )
            async for event in provider.stream_agent(request):
                if isinstance(event, StreamEnd):
                    usage = event.usage
                    print(
                        f"run={index + 1} "
                        f"create={usage.cache_creation_input_tokens} "
                        f"read={usage.cache_read_input_tokens}"
                    )
    finally:
        await provider.close()


asyncio.run(main())
'@ | .venv\Scripts\python.exe -
```

预期第一次请求的 `create` 大于零，紧接着的第二次请求 `read` 大于零。两次请求使用相同
的工具和稳定 System Prompt，但动态环境补充不同。若两项始终为零，先确认所选模型支持
Prompt Caching，且稳定前缀达到该模型的最小可缓存长度。

## OpenAI

1. 在当前 PowerShell 会话设置 Key：

   ```powershell
   $env:OPENAI_API_KEY = "your-real-key"
   ```

2. 将配置的 `active` 设置为 `openai-local`，确认 `base_url` 为 `https://api.openai.com/v1` 且 `thinking: false`。
3. 运行 `ycode`，发送一条要求包含标题、粗体、列表和代码块的问题。
4. 观察：首个文本前计时可见；内容流式出现；完成后整体渲染 Markdown；不显示 Thinking 区域。
5. 继续发送第二轮问题，确认上下文有效。
6. 输入 `/quit`，确认正常退出。

## 清理

```powershell
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
```

真实 `.ycode/config.yaml` 已被 `.gitignore` 忽略，但仍应在提交前运行 `git status --short` 确认它未被跟踪。
