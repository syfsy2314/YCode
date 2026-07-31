# YCode 提示词系统 Checklist

> 每项必须通过运行或观察验证。自动化测试不读取、修改用户的 `.ycode/config.yaml`，
> 不连接真实模型；只有明确标注的手工缓存测试使用真实 Anthropic API。

## 实现完整性

- [x] 六个内置章节可以从源码和安装包加载，按优先级与 ID 稳定排序；重复 ID、空正文
  和缺失资源产生明确错误。（验证：提示词 Builder 与安装资源测试通过）
- [x] 请求分别携带稳定 System Prompt、动态补充、真实消息和工具；请求级补充不进入
  历史，会话级补充能持续生效。（验证：FakeProvider、运行状态和会话测试通过）
- [x] 环境补充包含约定字段并安全处理 Git 失败；首次任务和模式变化后使用完整模式
  指令，其他任务精简，工具轮次不重复添加。（验证：环境、运行状态和 Agent 测试通过）
- [x] 全局工具策略与相关工具描述同时包含专用工具优先、修改前读取等关键规则。
  （验证：提示词资源与工具定义测试通过）

## Anthropic 与缓存

- [x] Anthropic 请求在最后一个稳定 System Prompt 块设置显式 5 分钟缓存断点，动态
  环境或模式变化不改变稳定前缀。（验证：Provider 请求捕获测试通过）
- [x] 动态补充使用原生 system message；明确不支持时只降级重试一次并复用结果，始终
  不以 user message 发送。（验证：Provider 单元与集成测试通过）
- [x] 输入、输出、缓存创建和缓存读取量被准确解析，并汇总到 Agent 回合结果。
  （验证：流式 usage 和多轮 Agent 测试通过）

## 回归与安全

- [x] 普通对话、Thinking、工具循环、历史事务、取消、轮数上限和 plan-only 写入拦截
  保持正确。（验证：Agent、Session、工具和 Anthropic 集成测试通过）
- [x] OpenAI 继续走纯聊天路径，请求中没有新增 Agent system、工具或缓存字段。
  （验证：OpenAI 单元、集成和端到端回归通过）
- [x] 环境、诊断和错误中没有 API Key、认证头、完整环境变量、Git diff 或机器标识。
  （验证：安全字段断言与输出检查通过）

## 质量检查

- [x] 格式检查通过。（验证：`.venv\Scripts\python.exe -m ruff format --check .`）
- [x] 静态检查通过。（验证：`.venv\Scripts\python.exe -m ruff check .`）
- [x] 编译检查通过。（验证：`.venv\Scripts\python.exe -m compileall -q ycode tests`）
- [x] 完整自动化测试通过。（验证：`.venv\Scripts\python.exe -m pytest -q`）

## 端到端与人工验证

- [x] 在真实 Windows 交互终端中完成一次读取、编辑和命令工具任务，动态补充不作为
  用户消息显示，最终回复正常。（验证：PTY/ConPTY 场景与终端输出）
- [ ] 在真实交互终端切换 plan-only 和 agent，观察模式提醒与写入拦截，并用少量固定
  任务观察工具选择、修改前读取和输出风格。（验证：模拟服务请求与人工行为记录）
- [ ] 使用真实 Anthropic API 连续发送稳定前缀相同、动态内容不同的请求，首次观察到
  缓存创建，后续观察到缓存读取。（验证：按 `docs/manual-api-test.md` 记录 usage）
