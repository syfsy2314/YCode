# YCode 交互式对话 Tasks

> 状态：已批准

> T1–T31 为首期已完成任务及其历史基线。本次变更从 T32 开始，不重复执行已经完成的实现任务，但会在 T35 运行完整回归。

## 当前项目基线

- 项目根目录已存在 `.venv`，由标准库 `venv` 创建。
- 虚拟环境使用 Python 3.14.0，满足项目的 Python 3.12+ 要求。
- `include-system-site-packages = false`，环境与系统包隔离。
- 当前仅安装 `pip 25.2`，尚未安装 YCode 运行或开发依赖。
- 当前仓库尚无 `pyproject.toml`、`requirements.txt`、`ycode/`、`tests/` 或业务实现代码。

后续任务直接使用现有 `.venv`，不得删除、重建或替换它。项目元数据、依赖声明、依赖安装和源码骨架仍按下列任务完成。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `pyproject.toml` | Python 3.12+、运行依赖、开发依赖、CLI 入口及 pytest/Ruff 配置 |
| 新建 | `requirements.txt` | 一键安装入口，通过 `-e .[dev]` 引用项目及开发依赖 |
| 新建 | `.gitignore` | 忽略 `.venv/`、真实配置、缓存和构建产物 |
| 新建 | `.ycode/config.example.yaml` | 不含真实密钥的双 Provider 示例配置 |
| 新建 | `README.md` | 安装、配置、启动和退出说明 |
| 新建 | `docs/manual-api-test.md` | 用户填写真实 Key 后的 Anthropic/OpenAI 手动冒烟步骤 |
| 新建 | `ycode/__init__.py` | 包版本及公共包标识 |
| 新建 | `ycode/__main__.py` | `python -m ycode` 入口 |
| 新建 | `ycode/cli.py` | CLI 参数、异步入口和退出码 |
| 新建 | `ycode/app.py` | 配置、Provider、Session、TUI 的应用装配与资源释放 |
| 新建 | `ycode/errors.py` | `ConfigError`、`ProviderError`、`UIError` |
| 新建 | `ycode/core/__init__.py` | 核心包导出 |
| 新建 | `ycode/core/messages.py` | `ChatMessage` |
| 新建 | `ycode/core/events.py` | `StreamEventKind`、`StreamEvent` |
| 新建 | `ycode/core/provider.py` | `ChatProvider` Protocol |
| 新建 | `ycode/config/__init__.py` | 配置包导出 |
| 新建 | `ycode/config/models.py` | `ProviderProtocol`、`ProviderConfig`、`AppConfig` |
| 新建 | `ycode/config/discovery.py` | 显式配置路径和逐级向上搜索 |
| 新建 | `ycode/config/loader.py` | YAML、环境变量和模型校验 |
| 新建 | `ycode/providers/__init__.py` | Provider 包导出 |
| 新建 | `ycode/providers/factory.py` | 按协议创建 Provider |
| 新建 | `ycode/providers/anthropic.py` | Anthropic Messages/Thinking 流适配 |
| 新建 | `ycode/providers/openai.py` | OpenAI Chat Completions 流适配 |
| 新建 | `ycode/session/__init__.py` | 会话包导出 |
| 新建 | `ycode/session/chat.py` | 多轮历史、事件转发和事务提交 |
| 新建 | `ycode/ui/__init__.py` | UI 包导出 |
| 新建 | `ycode/ui/styles.py` | 固定颜色和终端样式 |
| 新建 | `ycode/ui/header.py` | ASCII 猫图标及供应商信息布局 |
| 新建 | `ycode/ui/input_box.py` | 横线输入框、蓝色指示符和占位提示 |
| 新建 | `ycode/ui/timer.py` | 单调响应计时器 |
| 新建 | `ycode/ui/renderer.py` | Thinking、流式纯文本、完成后 Markdown 和错误渲染 |
| 新建 | `ycode/ui/terminal.py` | 对话输入循环、事件消费、退出和恢复 |
| 新建 | `tests/conftest.py` | 测试通用夹具 |
| 新建 | `tests/__init__.py` | 测试包标识，保证支持模块可稳定导入 |
| 新建 | `tests/unit/core/test_contracts.py` | 核心消息、事件和 Provider 接口测试 |
| 新建 | `tests/unit/config/test_models.py` | 配置模型与跨字段校验测试 |
| 新建 | `tests/unit/config/test_discovery.py` | 配置发现测试 |
| 新建 | `tests/unit/config/test_loader.py` | YAML、环境变量和敏感信息测试 |
| 新建 | `tests/unit/providers/test_anthropic.py` | Anthropic 请求、事件和错误映射测试 |
| 新建 | `tests/unit/providers/test_openai.py` | OpenAI 请求、事件和错误映射测试 |
| 新建 | `tests/unit/providers/test_factory.py` | Provider 工厂路由测试 |
| 新建 | `tests/unit/session/test_chat.py` | 多轮历史、成功提交和失败回滚测试 |
| 新建 | `tests/unit/ui/test_timer.py` | 响应计时测试 |
| 新建 | `tests/unit/ui/test_header.py` | 宽窄终端头部测试 |
| 新建 | `tests/unit/ui/test_input_box.py` | 输入提示、占位和字符回退测试 |
| 新建 | `tests/unit/ui/test_renderer.py` | 流式纯文本、Thinking、Markdown 切换和错误测试 |
| 新建 | `tests/unit/ui/test_terminal.py` | 对话循环、恢复和退出测试 |
| 新建 | `tests/unit/test_app.py` | 应用装配和资源关闭测试 |
| 新建 | `tests/unit/test_cli.py` | 参数、退出码和无堆栈错误测试 |
| 新建 | `tests/support/__init__.py` | 测试支持包标识 |
| 新建 | `tests/support/fake_provider.py` | 可编排统一事件和异常的虚拟 Provider |
| 新建 | `tests/support/sse_server.py` | 本机 Anthropic/OpenAI SSE 模拟服务 |
| 新建 | `tests/integration/test_anthropic_stream.py` | 官方 Anthropic SDK 到统一事件的本机集成测试 |
| 新建 | `tests/integration/test_openai_stream.py` | 官方 OpenAI SDK 到统一事件的本机集成测试 |
| 新建 | `tests/e2e/test_terminal_chat.py` | Windows ConPTY 中的完整双轮对话测试 |

所有缺失的包级 `__init__.py` 与对应模块在所属任务中一并创建，不承载额外业务逻辑。

## 实现任务

### T1：补齐项目元数据并接入现有虚拟环境

**文件：** `pyproject.toml`、`requirements.txt`、`.gitignore`  
**依赖：** 无

**步骤：**

1. 保留并使用现有 `.venv`，验证其解释器版本不低于 Python 3.12，不创建新的虚拟环境。
2. 设置构建后端、根目录 `ycode/` 包发现和 `requires-python = ">=3.12"`。
3. 添加运行依赖：Anthropic SDK、OpenAI SDK、Pydantic v2、PyYAML、prompt_toolkit、Rich。
4. 添加开发依赖：pytest、pytest-asyncio、Ruff，以及 Windows 端到端测试所需的 pywinpty。
5. 注册 `ycode = "ycode.cli:main"`；配置 pytest 的 asyncio 模式和 `--import-mode=importlib`，配置 Ruff 的 Python 3.12 目标。
6. 创建只包含 `-e .[dev]` 的 `requirements.txt`，使 `pyproject.toml` 保持唯一依赖来源。
7. 通过 `requirements.txt` 将项目及开发依赖安装到现有 `.venv`，不写入系统 Python。
8. 忽略 `.venv/`、`.ycode/config.yaml`、Python 缓存、pytest/Ruff 缓存和构建产物，但不忽略示例配置。

**验证：** 先运行 `.venv\Scripts\python.exe -m pip install -r requirements.txt`；再运行 `.venv\Scripts\python.exe -c "import sys, tomllib, pathlib, anthropic, openai, pydantic, yaml, prompt_toolkit, rich, pytest; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text('utf-8')); assert sys.version_info >= (3, 12); assert d['project']['requires-python'] == '>=3.12'; assert pathlib.Path('requirements.txt').read_text('utf-8').strip() == '-e .[dev]'"`，两条命令均成功退出。

### T2：建立包骨架和核心数据契约

**文件：** `ycode/__init__.py`、`ycode/core/__init__.py`、`ycode/core/messages.py`、`ycode/core/events.py`、`ycode/core/provider.py`、`tests/unit/core/test_contracts.py`  
**依赖：** T1

**步骤：**

1. 定义不可变 `ChatMessage`，角色只允许 `user` 和 `assistant`。
2. 定义 `StreamEventKind` 及不可变 `StreamEvent`。
3. 定义带 `stream_chat()` 和 `close()` 的运行时可检查 `ChatProvider` Protocol。
4. 用最小测试 Provider 验证接口兼容性、事件类型和值语义。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q` 全部通过。

### T3：实现统一错误类型

**文件：** `ycode/errors.py`、`tests/unit/core/test_contracts.py`  
**依赖：** T2

**步骤：**

1. 定义 `ConfigError`、`ProviderError` 和 `UIError`。
2. 为 `ProviderError` 实现 `code`、`user_message`、`retryable`，确保 `str(error)` 只返回安全提示。
3. 增加错误字符串不泄漏异常链内容的测试。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/core/test_contracts.py -q` 全部通过。

### T4：实现配置模型与跨字段校验

**文件：** `ycode/config/__init__.py`、`ycode/config/models.py`、`tests/unit/config/test_models.py`  
**依赖：** T1、T3

**步骤：**

1. 定义 `ProviderProtocol`、`ProviderConfig` 和 `AppConfig`。
2. 校验非空字符串、合法 URL、唯一 `name` 和存在的 `active`。
3. 拒绝 OpenAI 配置的 `thinking: true`，Anthropic 缺省为 `false`。
4. 使用 `SecretStr` 保存已解析密钥，测试对象表示不会暴露密钥。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/config/test_models.py -q` 全部通过。

### T5：实现配置文件发现

**文件：** `ycode/config/discovery.py`、`tests/unit/config/test_discovery.py`  
**依赖：** T3

**步骤：**

1. 实现显式路径存在性检查和规范化。
2. 实现从指定工作目录逐级向上查找最近的 `.ycode/config.yaml`。
3. 覆盖最近配置优先、到达盘符根目录、显式路径绕过搜索和找不到配置的错误信息。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/config/test_discovery.py -q` 全部通过。

### T6：实现 YAML 与环境变量加载

**文件：** `ycode/config/loader.py`、`tests/unit/config/test_loader.py`  
**依赖：** T4、T5

**步骤：**

1. 使用 `yaml.safe_load` 读取 UTF-8 配置并拒绝非映射顶层。
2. 识别完整 `${ENV_VAR}` 密钥引用并只在内存中展开。
3. 把 YAML 解析错误、环境变量缺失和 Pydantic 校验错误整理为带字段定位的 `ConfigError`。
4. 测试明文 Key、环境变量 Key、缺失变量、无效 YAML、活动项缺失及真实 Key 不出现在错误中。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/config -q` 全部通过。

### T7：实现可编排 FakeProvider

**文件：** `tests/__init__.py`、`tests/support/__init__.py`、`tests/support/fake_provider.py`  
**依赖：** T2、T3

**步骤：**

1. 支持按轮次预设统一事件序列、延迟和 `ProviderError`。
2. 记录每次收到的 `ChatMessage` 快照。
3. 记录 `close()` 是否被调用，供应用资源测试使用。

**验证：** `.venv\Scripts\python.exe -c "from tests.support.fake_provider import FakeProvider; assert FakeProvider"` 成功退出。

### T8：实现 ChatSession 成功提交

**文件：** `ycode/session/__init__.py`、`ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T2、T7

**步骤：**

1. 实现空白输入拒绝、请求上下文复制和 Provider 调用。
2. 转发 Thinking/Text 增量并累计最终回答原文。
3. 仅在收到 `COMPLETED` 后提交用户消息和完整回答。
4. 验证第二轮 Provider 请求包含第一轮完整历史。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q -k "success or multi_turn"` 全部通过。

### T9：实现 ChatSession 失败回滚和关闭

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T8

**步骤：**

1. Provider 抛错、缺少完成事件或任务取消时不提交本轮临时内容。
2. 保留此前已成功轮次并允许下一次调用继续进行。
3. 实现幂等 `close()` 并转交 Provider 关闭。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q` 全部通过。

### T10：实现 Anthropic 消息转换和普通文本流

**文件：** `ycode/providers/__init__.py`、`ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`  
**依赖：** T2、T3、T4

**步骤：**

1. 用注入的 `AsyncAnthropic` 客户端构造适配器，设置 `base_url`、密钥并关闭自动重试。
2. 把通用历史转换成 Messages API 的角色与内容结构。
3. 在 `thinking: false` 时发起流请求并把文本增量、结束映射为统一事件。
4. 验证模型、`max_tokens=16000`、消息顺序和客户端关闭。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q -k "text or request or close"` 全部通过。

### T11：实现 Anthropic extended thinking 和错误映射

**文件：** `ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`  
**依赖：** T10

**步骤：**

1. 将 `thinking: true` 映射为 adaptive extended thinking 请求。
2. 把 thinking delta 和 text delta 分别映射为统一事件，忽略不属于首期范围的事件。
3. 将认证、限流、连接、超时、服务端拒绝、Thinking 不支持及流中断转换为安全 `ProviderError`。
4. 验证 Thinking 不混入最终文本，异常文本不包含 Key。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q` 全部通过。

### T12：实现 OpenAI 消息转换和文本流

**文件：** `ycode/providers/openai.py`、`tests/unit/providers/test_openai.py`  
**依赖：** T2、T3、T4

**步骤：**

1. 用注入的 `AsyncOpenAI` 客户端构造适配器，设置 `base_url`、密钥并关闭自动重试。
2. 把通用历史转换为 Chat Completions messages。
3. 发起 `stream=True` 请求，将文本 delta 与结束转换为统一事件。
4. 验证模型、消息顺序、非文本内容处理和客户端关闭。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q -k "text or request or close"` 全部通过。

### T13：实现 OpenAI 错误映射

**文件：** `ycode/providers/openai.py`、`tests/unit/providers/test_openai.py`  
**依赖：** T12

**步骤：**

1. 将认证、限流、连接、超时、服务端错误和流中断映射成与 Anthropic 一致的错误类别。
2. 确保 OpenAI Provider 永不产生 `THINKING_DELTA`。
3. 验证错误字符串不包含 Key、请求头或完整响应体。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_openai.py -q` 全部通过。

### T14：实现 ProviderFactory

**文件：** `ycode/providers/factory.py`、`tests/unit/providers/test_factory.py`  
**依赖：** T11、T13

**步骤：**

1. 建立协议到 Provider 构造器的显式映射。
2. 返回 `ChatProvider`，不把具体类型要求传给上层。
3. 验证 Anthropic/OpenAI 路由、活动配置字段传递和未知协议防御。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/providers/test_factory.py -q` 全部通过。

### T15：实现本机 SSE 模拟服务

**文件：** `tests/support/sse_server.py`、`tests/conftest.py`  
**依赖：** T1

**步骤：**

1. 使用标准库本机 HTTP 服务实现可启动、可停止的后台测试服务器。
2. 支持 Anthropic `/v1/messages` 与 OpenAI `/v1/chat/completions` 的协议兼容 SSE 序列。
3. 记录请求路径、请求头和 JSON；支持逐块延迟、状态码、Thinking、跨增量 Markdown 和中途断开。
4. 提供 pytest 夹具，确保测试结束后释放端口和线程。

**验证：** `.venv\Scripts\python.exe -c "from tests.support.sse_server import SSETestServer; s=SSETestServer(); s.start(); assert s.base_url.startswith('http://'); s.stop()"` 成功退出且进程不残留后台线程。

### T16：验证 Anthropic 官方 SDK 集成链路

**文件：** `tests/integration/test_anthropic_stream.py`、`tests/support/sse_server.py`  
**依赖：** T11、T15

**步骤：**

1. 连接本机模拟 Messages API，验证认证头、模型、消息和自定义 `base_url`。
2. 验证延迟文本增量在完成前产出。
3. 验证 adaptive Thinking 请求及 Thinking/Text 事件顺序。
4. 验证服务端错误和流中断被转换为安全 `ProviderError`。

**验证：** `.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q` 全部通过。

### T17：验证 OpenAI 官方 SDK 集成链路

**文件：** `tests/integration/test_openai_stream.py`、`tests/support/sse_server.py`  
**依赖：** T13、T15

**步骤：**

1. 连接本机模拟 Chat Completions API，验证认证头、模型、消息和自定义 `base_url`。
2. 验证多个延迟文本增量在完成前依次产出。
3. 验证服务端错误和流中断被转换为安全 `ProviderError`。

**验证：** `.venv\Scripts\python.exe -m pytest tests/integration/test_openai_stream.py -q` 全部通过。

### T18：实现响应计时器

**文件：** `ycode/ui/__init__.py`、`ycode/ui/timer.py`、`tests/unit/ui/test_timer.py`  
**依赖：** T1

**步骤：**

1. 使用可注入单调时钟实现 `start()`、`elapsed` 和 `stop()`。
2. 确保重复开始会清除上一轮冻结值，停止后读数保持不变。
3. 覆盖首增量前运行、完成冻结和下一轮归零测试。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_timer.py -q` 全部通过。

### T19：实现固定样式和响应式头部

**文件：** `ycode/ui/styles.py`、`ycode/ui/header.py`、`tests/unit/ui/test_header.py`  
**依赖：** T4

**步骤：**

1. 定义猫图标和输入指示符使用的固定蓝色样式。
2. 实现宽终端左右布局与窄终端上下布局。
3. 只显示 Provider、Protocol、Model、Thinking，不显示 Config 路径。
4. 对不同终端宽度渲染到测试 Console，并断言信息完整且布局切换。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_header.py -q` 全部通过。

### T20：实现异步输入框

**文件：** `ycode/ui/input_box.py`、`tests/unit/ui/test_input_box.py`  
**依赖：** T19

**步骤：**

1. 用 `PromptSession.prompt_async()` 实现上下横线、蓝色 `❯` 和 `Send a message...` 占位提示。
2. 支持不兼容终端回退到 `>`。
3. 让空白提交返回空结果供 TerminalUI 忽略，不触发 Provider。
4. 使用 prompt_toolkit 的测试输入输出验证提示和提交行为。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py -q` 全部通过。

### T21：实现流式纯文本与 Thinking 渲染

**文件：** `ycode/ui/renderer.py`、`tests/unit/ui/test_renderer.py`  
**依赖：** T18、T19

**步骤：**

1. 创建可向内存 Console 渲染的 `LiveResponseRenderer`。
2. `start()` 后立即显示 `● YCode` 和运行中的耗时，不等待事件。
3. Thinking 增量以 `◇ Thinking` 纯文本追加，文本增量以未经 Markdown 解析的纯文本追加。
4. 对 Rich markup 特殊字符按普通文本处理，保证接收顺序和内容不变。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_renderer.py -q -k "start or delta or thinking"` 全部通过。

### T22：实现完成后整体 Markdown 和计时冻结

**文件：** `ycode/ui/renderer.py`、`tests/unit/ui/test_renderer.py`  
**依赖：** T21

**步骤：**

1. 在 `complete()` 中停止计时任务并取得总耗时。
2. 使用完整累计回答创建 Rich Markdown，一次性替换流式纯文本区域。
3. 覆盖标题、粗体、斜体、行内代码、围栏代码块、列表、引用和链接。
4. 将 Markdown 标记拆分为多个 delta，验证完成前是原文、完成后是格式化结果且内容不丢失。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_renderer.py -q -k "complete or markdown or timer"` 全部通过。

### T23：实现渲染失败与取消状态

**文件：** `ycode/ui/renderer.py`、`tests/unit/ui/test_renderer.py`  
**依赖：** T22

**步骤：**

1. 失败时冻结计时、保留已显示的部分纯文本并显示安全错误。
2. 取消时停止后台刷新任务并正确关闭 Live 区域。
3. 确保异常或取消后再次 `start()` 不残留上一轮计时和缓冲。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_renderer.py -q` 全部通过。

### T24：实现 TerminalUI 正常对话循环

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T9、T20、T23

**步骤：**

1. 依次显示头部、输入框、用户消息正文和响应区域。
2. 忽略空白输入；非空输入调用 `ChatSession.stream_reply()` 并分派统一事件。
3. 响应期间暂停新输入提示，完成后恢复输入。
4. 使用 FakeProvider 验证两轮输入、统一事件和历史传递。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py -q -k "conversation or empty or multi_turn"` 全部通过。

### T25：实现 TerminalUI 错误恢复和退出

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T24

**步骤：**

1. 捕获 `ProviderError`，结束当前渲染并返回输入循环。
2. 支持 `/exit`、`/quit` 和输入阶段 `Ctrl+C` 安全退出。
3. 响应阶段取消时停止流、计时与 Live 区域，不打印 traceback。
4. 验证失败后可以继续发送下一轮消息。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py -q` 全部通过。

### T26：实现应用装配和资源生命周期

**文件：** `ycode/app.py`、`tests/unit/test_app.py`  
**依赖：** T6、T14、T25

**步骤：**

1. 根据显式路径或当前工作目录加载配置。
2. 创建活动 Provider、ChatSession 和 TerminalUI，并运行输入循环。
3. 使用 `try/finally` 保证正常、配置后异常及用户取消路径都关闭 Session/Provider。
4. 通过依赖注入测试装配顺序和关闭行为。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/test_app.py -q` 全部通过。

### T27：实现 CLI 与模块入口

**文件：** `ycode/cli.py`、`ycode/__main__.py`、`tests/unit/test_cli.py`  
**依赖：** T26

**步骤：**

1. 解析可选 `--config PATH` 并调用 `asyncio.run()`。
2. 配置错误显示简洁原因并返回非零退出码；正常退出返回零。
3. 处理 `KeyboardInterrupt`，不输出异常堆栈。
4. 验证 `ycode` 与 `python -m ycode` 共用同一主入口。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py -q` 全部通过。

### T28：提供示例配置和使用文档

**文件：** `.ycode/config.example.yaml`、`README.md`、`docs/manual-api-test.md`  
**依赖：** T6、T27

**步骤：**

1. 示例 YAML 同时包含 Anthropic 和 OpenAI 配置，使用 `${ANTHROPIC_API_KEY}`、`${OPENAI_API_KEY}`，不含真实密钥。
2. README 说明 `.venv` 激活、安装、复制配置、逐级发现、`--config`、启动和退出。
3. 手动测试文档分别列出两种真实 API 的 Key 设置、`active` 切换、预期流式/Thinking/Markdown/计时行为和清理步骤。
4. 搜索仓库文档，确认没有形似真实密钥的示例。

**验证：** `.venv\Scripts\python.exe -c "from pathlib import Path; import yaml; d=yaml.safe_load(Path('.ycode/config.example.yaml').read_text('utf-8')); assert {p['protocol'] for p in d['providers']} == {'anthropic','openai'}"` 成功退出。

### T29：实现 Windows ConPTY 端到端对话测试

**文件：** `tests/e2e/test_terminal_chat.py`、`tests/support/sse_server.py`  
**依赖：** T15、T17、T27、T28

**步骤：**

1. 在临时项目层级写入占位 Key 配置，并从子目录启动 YCode，验证向上发现配置。
2. 用 pywinpty/Windows ConPTY 启动 `.venv` Python 的 `-m ycode`，等待猫图标、右侧信息和 `Send a message...`。
3. 输入两轮消息，模拟服务延迟发送跨增量 Markdown，验证首增量前计时可见、增量原文、最终 Markdown 和第二轮历史请求。
4. 输入 `/exit`，验证进程零退出、无 traceback、无会话历史文件且模拟服务释放。

**验证：** `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q` 在 Windows ConPTY 中全部通过。

### T30：补齐端到端错误与 Thinking 场景

**文件：** `tests/e2e/test_terminal_chat.py`、`tests/support/sse_server.py`  
**依赖：** T29

**步骤：**

1. 增加 Anthropic Thinking 流场景，验证 Thinking 纯文本区和最终回答区分离。
2. 增加首轮流中断、下一轮成功场景，验证界面恢复、旧历史不损坏且计时重新开始。
3. 在捕获的全部终端输出中检查占位 Key 不出现。

**验证：** `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q` 全部通过。

### T31：执行完整静态检查和测试回归

**文件：** 全部 Python、配置和文档文件  
**依赖：** T1–T30

**步骤：**

1. 运行 Ruff 格式检查和 lint，修复全部问题。
2. 运行完整 pytest，确保单元、集成和 Windows ConPTY 端到端测试全部通过。
3. 编译全部源码，检查包导入和 CLI 帮助。
4. 搜索真实配置、API Key、traceback 和会话落盘等禁止项。

**验证：** 依次运行以下命令并全部成功：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
```

### T32：建立活动配置与备用条目的两阶段模型

**文件：** `ycode/config/models.py`、`ycode/config/__init__.py`、`tests/unit/config/test_models.py`  
**依赖：** T31、已批准的活动 Provider 校验 Plan

**步骤：**

1. 增加只强制校验非空 `name`、允许保留其余原始字段的 `ProviderEntry`。
2. 保留 `ProviderConfig` 作为活动配置的完整强类型模型，不放宽 ProviderFactory 的输入契约。
3. 调整 `AppConfig`，使其保存名称级校验后的条目和单独物化的 `active_provider`。
4. 继续检查顶层 `active`、配置名称缺失、名称重复和活动名称不存在。
5. 增加模型测试，证明未激活条目不会被构造成 `ProviderConfig`，活动配置仍执行协议和 `thinking` 组合校验。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/config/test_models.py -q` 全部通过。

### T33：实现只解析和校验活动 Provider

**文件：** `ycode/config/loader.py`、`tests/unit/config/test_loader.py`  
**依赖：** T32

**步骤：**

1. YAML 解析后先执行顶层、名称和活动项查找，再复制活动条目的原始映射。
2. 仅对活动条目的 `api_key` 展开 `${ENV_VAR}`，随后构造完整 `ProviderConfig`。
3. 未激活条目缺少必填字段、协议无效、引用不存在的环境变量或包含不适用的 `thinking` 时均不阻止加载。
4. 将 `active` 切换到同一个未完成条目后，验证对应字段错误或环境变量错误才会出现。
5. 保持错误定位和密钥脱敏行为，不修改或回写 YAML 文件。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/config -q` 全部通过。

### T34：验证应用边界并同步配置文档

**文件：** `tests/unit/test_app.py`、`tests/unit/test_cli.py`、`tests/e2e/test_terminal_chat.py`、`README.md`、`docs/manual-api-test.md`  
**依赖：** T33

**步骤：**

1. 验证应用装配和 ProviderFactory 只收到完整的活动 `ProviderConfig`，未激活条目不创建 SDK 客户端。
2. 增加启动级场景：配置包含一个有效活动项和一个不完整备用项时正常进入 TUI，模拟服务只收到活动项请求。
3. 增加切换场景：把 `active` 改为不完整备用项后，启动以非零状态退出、无 traceback、无 API 请求。
4. 更新 README 和手动测试文档，说明只有活动配置会完整校验和解析环境变量。

**验证：** 依次运行以下命令并全部通过：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/test_cli.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q
```

### T35：执行增量变更完整回归

**文件：** 全部受影响的 Python、测试、配置和文档文件  
**依赖：** T32–T34

**步骤：**

1. 运行 Ruff 格式检查和 lint。
2. 运行完整 pytest，确保原有 Provider、Session、TUI 和 Windows ConPTY 行为没有回归。
3. 编译源码和测试并复核两个 CLI 入口。
4. 搜索真实 API Key，并确认未增加未批准的热更新或多 Provider 预创建逻辑。

**验证：** 依次运行以下命令并全部成功：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

### T36：修复输入期间不可见的下边界

**文件：** `ycode/ui/input_box.py`、`ycode/ui/styles.py`、`tests/unit/ui/test_input_box.py`、`tests/e2e/test_terminal_chat.py`  
**依赖：** T35、已批准的输入框修复 Plan

**步骤：**

1. 保留输入前写入滚动区的上横线。
2. 使用 `prompt_async(bottom_toolbar=...)` 在输入激活期间持续显示等宽下横线。
3. 输入提交、取消或异常结束后，把下横线写入滚动区，保证终端历史布局完整。
4. 单元测试断言 `bottom_toolbar` 包含完整下边界；Windows ConPTY 在 `Send a message...` 仍可见时同时观察上下两条横线。

**验证：** 依次运行以下命令并全部通过：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k "input"
```

### T37：显式关闭 Anthropic 协议 Thinking

**文件：** `ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`、`tests/integration/test_anthropic_stream.py`、`tests/e2e/test_terminal_chat.py`  
**依赖：** T35、已批准的 Thinking 修复 Plan

**步骤：**

1. `thinking: true` 继续发送 adaptive summarized 配置。
2. `thinking: false` 显式发送 `{"type": "disabled"}`，不再依赖 API 服务的默认模式。
3. 关闭 Thinking 时忽略服务端意外返回的 `thinking_delta`，但继续接收文本和完成事件。
4. 单元测试分别断言启用和关闭请求体；集成测试记录本机 SSE 请求并验证 disabled。
5. Windows ConPTY 使用 `thinking: false` 启动，模拟服务仍发送带唯一标记的 Thinking 增量，断言该标记不出现在终端，最终文本正常显示。

**验证：** 依次运行以下命令并全部通过：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py -q
.venv\Scripts\python.exe -m pytest tests/integration/test_anthropic_stream.py -q
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k "thinking_disabled"
```

### T38：执行缺陷修复完整回归

**文件：** 全部受影响的 Python、测试和验收文档  
**依赖：** T36–T37

**步骤：**

1. 运行 Ruff 格式检查、lint 和完整 pytest。
2. 编译全部源码与测试，复核两个 CLI 入口。
3. 更新 Checklist 的修复项和最终测试数量。
4. 不调用真实 API，不修改用户的 `.ycode/config.yaml`。

**验证：** 依次运行以下命令并全部成功：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

### T39：实现四行输入提示区与横线颜色入口

**文件：** `ycode/ui/input_box.py`、`ycode/ui/styles.py`、`tests/unit/ui/test_input_box.py`  
**依赖：** T38、已批准的输入提示区 Plan

**步骤：**

1. 用非全屏 prompt_toolkit `Application`、单行 `Buffer` 和 `HSplit` 替换 `PromptSession.bottom_toolbar`。
2. 按固定顺序创建上横线、输入行、下横线和 `? for help` 四个区域。
3. 保留蓝色 `❯`、ASCII `>` 回退、`Send a message...` 占位符、Enter 提交、Ctrl+C 和 EOF 行为。
4. 输入结束时清理动态布局，避免横线和提示区残留到回答区域。
5. 在 `styles.py` 定义轻量横线样式参数，默认低对比度灰色；上下横线使用同一参数，提示文字使用独立样式。
6. 单元测试覆盖布局顺序、普通横线、无反色样式、宽度预留、颜色注入、占位符、字符回退、提交和异常清理。

**验证：** `.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py -q` 全部通过。

### T40：验证 Windows 输入提示布局与普通问号消息

**文件：** `tests/e2e/test_terminal_chat.py`  
**依赖：** T39

**步骤：**

1. 在 Windows ConPTY 中停留于输入状态，捕获上横线、输入行、下横线和 `? for help`。
2. 验证四个区域顺序正确，上下横线等宽，没有左右竖边、反色工具栏或背景色块。
3. 输入 `?` 并提交，验证模拟服务收到内容为 `?` 的普通用户消息，而不是触发本地帮助或 Skill。
4. 等待流式回答和下一次输入提示，验证动态布局可重复创建且无 traceback。
5. 使用窄终端复核边界不折行、提示文字仍完整显示。

**验证：** `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k "input_hint"` 全部通过。

### T41：执行输入提示区完整回归

**文件：** 全部受影响的 Python、测试和验收文档  
**依赖：** T39–T40

**步骤：**

1. 运行 Ruff 格式检查、lint 和完整 pytest。
2. 编译全部源码与测试，复核两个 CLI 入口。
3. 更新 Checklist 的 AC23、样式入口和最终测试数量。
4. 不实现帮助命令、Skill、YAML 颜色配置或主题系统。

**验证：** 依次运行以下命令并全部成功：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m ycode --help
.venv\Scripts\ycode.exe --help
```

### T42：突出显示已提交的用户消息

**文件：** `ycode/ui/user_message.py`、`ycode/ui/terminal.py`、`README.md`  
**依赖：** T39

**步骤：**

1. 新增无边框用户消息背景板，宽度与输入区一致且最大 100 列。
2. 首行显示蓝色 `❯`，不支持 Unicode 时回退为 `>`。
3. 多行正文在同一背景板内与正文列对齐，不显示 `You`。
4. TerminalUI 使用该组件替换原有纯文本用户消息输出。
5. 按用户要求不新增测试代码、不运行自动化验证，由用户启动 YCode 验收视觉效果。

**验证：** 用户在真实终端启动 YCode，分别提交单行和多行消息后人工确认。

## 执行顺序

```text
T1
├── T2 → T3 ─┬→ T4 → T5 → T6 ────────────────┐
│            └→ T7 → T8 → T9 ────────────────┤
│                                             ├→ T26 → T27 → T28
├── T2/T4 → T10 → T11 ─┬→ T14 ───────────────┤                  │
│                       └→ T15 → T16           │                  ├→ T29 → T30
├── T2/T4 → T12 → T13 ─┬→ T14                 │                  │
│                       └→ T15 → T17 ──────────┘                  │
└── T18 → T19 → T20 → T21 → T22 → T23 → T24 → T25 ─────────────┘

T1–T30 → T31

T31 → T32 → T33 → T34 → T35

T35 ─┬→ T36 ─┐
     └→ T37 ─┴→ T38

T38 → T39 → T40 → T41

T39 → T42
```

可并行的分支仅表示依赖允许；实际实现仍按每个任务的验证结果推进。任何任务验证失败时，先在该任务范围内修复并重新运行，再开始依赖它的后续任务。

## Plan 覆盖检查

| Plan 组件 | 对应任务 |
|---|---|
| 现有虚拟环境接入与项目元数据 | T1 |
| 核心消息、事件、Provider 接口 | T2–T3 |
| 配置模型、发现与加载 | T4–T6 |
| Provider 与 ChatSession 解耦 | T7–T9、T14 |
| Anthropic 普通流与 Thinking | T10–T11、T16 |
| OpenAI Chat Completions 流 | T12–T13、T17 |
| 本机模拟 SSE | T15–T17 |
| 响应计时 | T18、T21–T23、T29 |
| 头部和输入框 | T19–T20 |
| 流式纯文本与完成后 Markdown | T21–T23、T29 |
| TUI 循环、恢复和安全退出 | T24–T25、T29–T30 |
| 应用装配与 CLI | T26–T27 |
| 示例配置与真实 API 手册 | T28 |
| Windows 真实终端端到端测试 | T29–T30 |
| 全量质量门禁 | T31 |
| 活动配置与备用条目两阶段模型 | T32 |
| 只解析和校验活动 Provider | T33 |
| 应用边界、启动场景与文档同步 | T34 |
| 增量变更完整回归 | T35 |
| 输入期间下边界 | T36 |
| Thinking 显式关闭与输出过滤 | T37 |
| 缺陷修复完整回归 | T38 |
| 四行输入提示区与颜色入口 | T39 |
| Windows 输入布局与普通问号消息 | T40 |
| 输入提示区完整回归 | T41 |
| 用户消息背景板 | T42 |

Plan 中的每个组件均至少有一个实现任务和对应验证；任务依赖无循环，未包含 Spec 范围外的 tool use、文件操作 Agent、持久化或多代理功能。
