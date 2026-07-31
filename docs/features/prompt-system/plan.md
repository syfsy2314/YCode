# YCode 提示词系统 Plan

## 架构概览

提示词系统位于 Agent 与 Provider 之间：

```text
ChatSession
  └─ 提供真实历史和当前模式
       ↓
AgentLoop
  └─ 调用 Prompt System 组装本轮请求
       ├─ 稳定内置指令
       ├─ 环境快照
       ├─ 完整或精简模式提醒
       ├─ 会话级动态补充
       └─ 当前工具定义
       ↓
AnthropicProvider
  └─ 转换协议、设置缓存、解析流和使用量
```

`ChatSession` 继续只管理模式和真实对话历史。`AgentLoop` 在每个用户任务开始时构造一次
动态上下文，同一任务内的工具轮次复用该上下文。Provider 只负责供应商协议，不参与
提示词内容和生命周期决策。

## 核心结构

### PromptSection

表示一个内置提示词章节：

- `id`：稳定且唯一的章节标识。
- `priority`：章节排序优先级。
- `content`：非空 Markdown 正文。

章节按 `(priority, id)` 排序，重复 ID、空正文或无效优先级在启动时明确报错。

### PromptBundle

保存排序后的 `PromptSection` 集合，并提供：

- 稳定的章节顺序。
- 供模型请求使用的 System Prompt 内容块。
- 供测试和诊断使用的章节元数据。

相同版本和相同内置章节必须产生字节级一致的内容。

### SystemSupplement

表示动态系统补充：

- `kind`：环境、模式、工具状态、外部记忆或提醒。
- `content`：带固定边界标签的非空文本。
- `scope`：当前请求或当前会话。

请求级补充不进入真实历史；会话级补充由提示词运行时上下文保存，并在后续请求中继续
发送。普通 `ChatMessage` 不增加 system 角色。

### PromptRuntimeContext

保存当前会话的提示词运行状态：

- 已生效的会话级补充。
- 上一次发送完整模式指令时的模式。

首次用户任务或模式发生变化时生成完整模式指令，其他用户任务生成精简提醒。不维护
用户回合计数；Agent 内部工具轮次不更新该状态。

### AgentModelRequest

Provider 接收的供应商无关请求：

- `system_prompt`：稳定的 System Prompt 内容块。
- `supplements`：本轮需要发送的系统补充。
- `messages`：真实历史和当前回合消息。
- `tools`：当前允许的工具定义。

### TokenUsage

统一表示：

- 输入 token。
- 输出 token。
- 缓存创建 token。
- 缓存读取 token。

缺失字段按零处理。多次 Agent 请求的使用量汇总到当前回合结果，默认 UI 不展示。

## 模块设计

### 内置提示词

内置正文使用包内 Markdown 资源，分别保存身份、行为、工具策略、通用代码规范、安全
边界和输出风格。Python 代码维护 ID、优先级和资源映射，不支持用户模板、变量表达式或
热更新。

### 环境采集

每个用户任务采集一次工作区、操作系统、Shell、本地时间和时区。Git 摘要使用只读、
短超时的 Git 命令获取，包含分支及 staged、modified、untracked 数量。Git 不可用、
当前目录不是仓库或命令失败时省略 Git 字段，不阻止模型请求。

### 动态提醒

环境和模式提醒属于请求级补充。工具状态和外部记忆可以由调用方声明为请求级或会话级。
动态补充在模型请求中使用系统级语义，不作为用户消息展示。

### Anthropic 协议

Anthropic Provider 执行以下协议工作：

1. 转换稳定 System Prompt、系统补充、真实消息和工具定义。
2. 在最后一个稳定 System Prompt 内容块设置显式的 5 分钟缓存断点；该断点同时覆盖
   位于其前面的工具定义。
3. 解析响应中的文本、Thinking、工具调用、停止原因和使用量。
4. 原生动态 system message 被服务明确拒绝且响应流尚未建立时，将动态补充并入当轮
   顶层 system 后重试一次，并在当前 Provider 生命周期内复用该能力结果。

Provider 不拼装提示词章节、不采集环境、不选择模式提醒。兼容处理使用现有类中的私有
转换函数，不新增额外框架。

## 请求流程

1. 应用启动时加载并校验内置章节，生成稳定 `PromptBundle`。
2. `ChatSession` 把真实历史、用户消息和当前模式交给 `AgentLoop`。
3. `AgentLoop` 获取当前允许的工具，采集环境，并从 `PromptRuntimeContext` 获取模式与
   会话级补充。
4. `AgentLoop` 生成 `AgentModelRequest` 并调用 Provider。
5. 工具调用产生 Assistant 工具块和用户工具结果后，Agent 只扩展真实工作消息；稳定
   提示词和本轮补充保持不变。
6. 最终响应正常完成后，现有事务规则提交真实回合消息；请求级补充不提交。
7. 每次 Provider 请求的使用量汇总到回合结果，供测试和诊断读取。

## 文件组织

```text
ycode/
├── prompt/
│   ├── __init__.py
│   ├── models.py
│   ├── builder.py
│   ├── runtime.py
│   ├── environment.py
│   └── resources/
│       ├── identity.md
│       ├── behavior.md
│       ├── tool-use.md
│       ├── coding.md
│       ├── safety.md
│       └── output.md
├── core/
│   ├── provider.py
│   └── events.py
├── agent/
│   ├── contracts.py
│   └── loop.py
└── providers/
    └── anthropic.py
```

其他变化：

- `ycode/app.py` 装配提示词组件。
- 现有 `ycode/agent/prompt.py` 由新提示词包替代。
- 相关工具描述补充关键工具规则。
- 更新 Agent、Session、Provider、配置装配和集成测试。
- 确保 Markdown 资源在安装后的包中可读取。

## 技术决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 提示词正文 | 包内 Markdown 资源 | 便于独立维护和评审，不引入模板系统 |
| 模块顺序 | 优先级后按 ID | 保证确定性并允许插入章节 |
| 动态消息 | 独立 SystemSupplement | 不污染普通用户消息和真实历史 |
| 模式提醒 | 首次或模式变化时完整，其余精简 | 不需要用户回合计数 |
| 缓存 | 最后一个稳定 system 块的显式 5 分钟断点 | 简单且可直接验证 |
| Git 信息 | 短超时 Git 命令 | 不增加依赖，失败可安全忽略 |
| 模型兼容 | 明确拒绝后单次降级 | 不硬编码模型名单，不静默伪造用户消息 |
| OpenAI | 保持现有纯聊天路径 | 遵守当前 Provider 开发范围 |

## Spec 覆盖

- F1、F2：内置章节、`PromptBundle` 和结构化请求。
- F3、F5、F8：`SystemSupplement` 与 `PromptRuntimeContext`。
- F4：环境采集模块。
- F6：稳定工具策略和工具自身描述。
- F7：Anthropic Provider 的原生系统消息与单次降级。
- N1–N5：供应商无关契约、失败隔离、现有行为回归和分层测试。
