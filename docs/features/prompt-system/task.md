# YCode 提示词系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `ycode/prompt/__init__.py` | 提示词系统公共导出 |
| 新建 | `ycode/prompt/models.py` | 章节、提示词包和动态补充模型 |
| 新建 | `ycode/prompt/builder.py` | 内置资源加载、校验、排序和拼装 |
| 新建 | `ycode/prompt/runtime.py` | 模式提醒和会话级补充状态 |
| 新建 | `ycode/prompt/environment.py` | 环境与 Git 摘要采集 |
| 新建 | `ycode/prompt/resources/*.md` | 六个内置提示词章节 |
| 修改 | `pyproject.toml` | 将 Markdown 资源包含在安装包中 |
| 修改 | `ycode/core/provider.py` | `AgentModelRequest` 与 Provider 契约 |
| 修改 | `ycode/core/events.py` | `TokenUsage` 和流结束用量 |
| 修改 | `ycode/core/__init__.py` | 新核心契约导出 |
| 修改 | `ycode/agent/contracts.py` | 回合结果用量 |
| 修改 | `ycode/agent/loop.py` | 动态上下文和结构化请求接入 |
| 修改 | `ycode/agent/__init__.py` | 移除旧 Builder 导出并更新公共类型 |
| 删除 | `ycode/agent/prompt.py` | 由新提示词包替代 |
| 修改 | `ycode/providers/anthropic.py` | 系统消息、缓存、降级和 usage |
| 修改 | `ycode/app.py` | 装配提示词组件 |
| 修改 | `ycode/tools/builtin/*.py` | 强化相关工具描述 |
| 修改 | `tests/support/fake_provider.py` | 记录结构化 Agent 请求 |
| 新建 | `tests/unit/prompt/*.py` | 提示词、运行状态和环境测试 |
| 修改 | `tests/unit/agent/*.py` | Agent 请求与回合统计测试 |
| 修改 | `tests/unit/providers/test_anthropic.py` | Anthropic 协议和缓存测试 |
| 修改 | `tests/unit/test_app.py` | 应用装配测试 |
| 修改 | `tests/integration/test_anthropic_stream.py` | 流与使用量集成测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 真实终端回归 |
| 修改 | `docs/manual-api-test.md` | 真实缓存验证步骤 |

## T1：提示词领域模型

**依赖：** 无  
**文件：** `ycode/prompt/models.py`、`ycode/prompt/__init__.py`

1. 定义 `PromptSection`、`PromptBundle`、`SystemSupplement` 及补充类型和生命周期枚举。
2. 使用不可变结构并校验稳定 ID、优先级、非空正文和补充内容。
3. 提供稳定章节视图和 System Prompt 内容块，不暴露可变内部集合。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/prompt/test_models.py -q
```

## T2：内置章节与稳定拼装

**依赖：** T1  
**文件：** `ycode/prompt/builder.py`、`ycode/prompt/resources/*.md`、`pyproject.toml`、
`tests/unit/prompt/test_builder.py`

1. 添加身份、行为、工具策略、代码规范、安全边界和输出风格 Markdown 章节。
2. 建立内置 ID、优先级和资源映射，使用包资源 API 加载正文。
3. 按 `(priority, id)` 排序，拒绝重复 ID、空资源和缺失资源。
4. 确保重复构建产生字节级一致内容，并将 Markdown 文件纳入安装包。
5. 删除旧 `SystemPromptBuilder` 对应测试，由新 Builder 测试替代。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/prompt/test_builder.py -q
```

## T3：环境快照

**依赖：** T1  
**文件：** `ycode/prompt/environment.py`、`tests/unit/prompt/test_environment.py`

1. 采集工作区、操作系统、Shell、本地时间和时区。
2. 使用只读、短超时 Git 命令采集分支与 staged、modified、untracked 数量。
3. 使用固定标签渲染紧凑环境补充，不包含环境变量、diff 或完整文件列表。
4. Git 缺失、非仓库、超时和异常时省略 Git 字段并返回其余环境信息。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/prompt/test_environment.py -q
```

## T4：运行时补充与模式提醒

**依赖：** T1  
**文件：** `ycode/prompt/runtime.py`、`tests/unit/prompt/test_runtime.py`

1. 保存会话级工具状态和外部记忆补充，并允许添加请求级补充。
2. 首次用户任务或模式变化时生成完整模式指令，其他任务生成精简提醒。
3. 同一用户任务内重复读取上下文时返回相同补充，不增加计数或重复持久化。
4. 验证请求级内容不会进入会话级存储。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/prompt/test_runtime.py -q
```

## T5：结构化请求与使用量契约

**依赖：** T1  
**文件：** `ycode/core/provider.py`、`ycode/core/events.py`、`ycode/core/__init__.py`、
`ycode/agent/contracts.py`、`tests/support/fake_provider.py`、相关核心和 Agent 契约测试

1. 定义供应商无关的 `AgentModelRequest`，分离 System Prompt、补充、消息和工具。
2. 定义可相加的 `TokenUsage`，缺失用量按零处理。
3. 让流结束事件和 Agent 回合结果携带用量，并支持多次模型请求汇总。
4. 更新 FakeProvider 记录完整结构化请求；保持普通 `ChatProvider` 接口不变。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/core tests/unit/agent/test_contracts.py -q
```

## T6：Anthropic 协议、缓存与降级

**依赖：** T2、T5  
**文件：** `ycode/providers/anthropic.py`、`tests/unit/providers/test_anthropic.py`、
`tests/integration/test_anthropic_stream.py`

1. 将稳定章节转换为 Anthropic 顶层 system 内容块，在最后一块设置 5 分钟缓存断点。
2. 将动态补充转换为对话中的原生 system message，保持真实用户消息身份不变。
3. 解析响应流中的输入、输出、缓存创建和缓存读取量。
4. 只在服务明确拒绝 system message 且流尚未建立时，将补充合并到顶层 system 并重试
   一次；后续请求复用能力结果。
5. 保留 Thinking、工具调用、停止原因、错误映射和不带新字段的旧请求行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/providers/test_anthropic.py tests/integration/test_anthropic_stream.py -q
```

## T7：Agent 与应用接入

**依赖：** T2、T3、T4、T5、T6  
**文件：** `ycode/agent/loop.py`、`ycode/agent/contracts.py`、`ycode/agent/__init__.py`、
`ycode/app.py`、`ycode/agent/prompt.py`、`ycode/tools/builtin/*.py`、
`tests/unit/agent/*.py`、`tests/unit/test_app.py`

1. 在应用启动时构建稳定 `PromptBundle`，为 Anthropic Agent 装配环境采集器和运行时
   上下文。
2. Agent 每个用户任务采集一次环境并生成一次模式提醒，工具轮次复用同一上下文。
3. 每轮 Provider 调用使用 `AgentModelRequest`，并把各轮 `TokenUsage` 汇总到回合结果。
4. 保持 plan-only 工具过滤和执行边界；更新工具描述以表达专用工具优先和修改前读取。
5. 删除旧 `agent/prompt.py`，更新导出和测试；OpenAI 继续使用原纯聊天路径。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_app.py tests/unit/tools -q
```

## T8：回归、安装资源和真实验证

**依赖：** T1–T7  
**文件：** `docs/manual-api-test.md`、相关集成与端到端测试

1. 验证源码运行和安装后的包都能读取全部 Markdown 资源。
2. 扩展 Anthropic 本机模拟服务场景，检查稳定前缀、动态 system message 和 usage。
3. 运行现有 OpenAI 集成回归，确认请求结构未增加 Agent system 或缓存字段。
4. 运行真实终端端到端测试，覆盖普通工具任务、plan-only、模式切换和取消。
5. 在手工文档中增加真实 Anthropic 连续请求步骤，观察首次缓存创建和后续缓存读取。
6. 用少量固定任务记录工具选择、修改前读取、模式遵守和输出风格。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

随后按 `docs/manual-api-test.md` 执行真实 Anthropic 缓存验证。

## 执行顺序

```text
T1 → T2 ───────────────┐
 ├→ T3 ────────────────┤
 ├→ T4 ────────────────┼→ T7 → T8
 └→ T5 → T6 ───────────┘
```
