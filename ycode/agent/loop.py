"""最小 ReAct Agent Loop。"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Protocol

from ycode.agent.contracts import (
    AgentMode,
    AgentTermination,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
    TurnMessage,
)
from ycode.agent.events import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentLimitReachedEvent,
    AgentTextDelta,
    AgentThinkingDelta,
    ContextCompactedEvent,
    ContextCompactionFailedEvent,
    FinalResponseEvent,
    ToolApprovalRequested,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ycode.context import ContextLimitError, ContextManager, ContextStorageError
from ycode.core.events import StopReason, TextDelta, ThinkingDelta, TokenUsage
from ycode.core.messages import (
    ChatMessage,
    ToolCallBlock,
    ToolResultBlock,
    thaw_json,
)
from ycode.core.provider import AgentChatProvider, AgentModelRequest
from ycode.errors import MessageAssemblyError, ProviderError
from ycode.prompt import EnvironmentCollector, PromptBundle, PromptRuntimeContext
from ycode.prompt.models import SupplementKind, SystemSupplement
from ycode.security import (
    ApprovalChoice,
    PermissionAction,
    PermissionEngine,
    PermissionSession,
)
from ycode.session.assembler import ResponseAssembler
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolExecutionRecord,
    ToolExecutionResult,
)
from ycode.tools.exposure import ToolExposureSession
from ycode.tools.registry import ToolRegistry
from ycode.tools.scheduler import (
    ScheduledToolCancelled,
    ScheduledToolCompleted,
    ScheduledToolStarted,
    ToolScheduler,
)


class AgentLoop:
    supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})

    def __init__(
        self,
        provider: AgentChatProvider,
        registry: ToolRegistry,
        scheduler: ToolScheduler,
        prompt_bundle: PromptBundle,
        prompt_runtime: PromptRuntimeContext,
        environment: EnvironmentCollector,
        context: ToolContext,
        *,
        permission_engine: PermissionEngine | None = None,
        permission_session: PermissionSession | None = None,
        plan_only_mcp_tools: frozenset[str] = frozenset(),
        resource_manager: "_AsyncCloseable | None" = None,
        context_manager: ContextManager | None = None,
        max_rounds: int = 10,
    ) -> None:
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
            raise ValueError("Agent 最大轮数必须是正整数")
        self._provider = provider
        self._registry = registry
        self._scheduler = scheduler
        self._prompt_bundle = prompt_bundle
        self._prompt_runtime = prompt_runtime
        self._environment = environment
        self._context = context
        if (permission_engine is None) != (permission_session is None):
            raise ValueError("权限引擎和权限会话必须同时提供")
        self._permission_engine = permission_engine
        self._permission_session = permission_session
        self._plan_only_mcp_tools = plan_only_mcp_tools
        self._resource_manager = resource_manager
        self._context_manager = context_manager
        self._close_task: asyncio.Task[None] | None = None
        self._max_rounds = max_rounds
        self._queued_request_supplements: list[SystemSupplement] = []

    def queue_request_supplement(self, supplement: SystemSupplement) -> None:
        """排队一个只用于下一次普通请求的系统补充。"""

        self._queued_request_supplements.append(supplement)

    def clear_queued_request_supplements(self, kind: SupplementKind) -> None:
        self._queued_request_supplements[:] = [
            item for item in self._queued_request_supplements if item.kind is not kind
        ]

    def start_turn(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AgentTurn:
        history_snapshot = tuple(history)
        if user_message.role != "user":
            raise ValueError("Agent 回合必须使用用户消息")
        if mode not in self.supported_modes:
            raise ValueError("AgentLoop 不支持当前模式")
        queued = tuple(self._queued_request_supplements)
        self._queued_request_supplements.clear()
        return AgentTurnStream(
            lambda turn: self._run(turn, history_snapshot, user_message, mode, queued)
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await self._close_task

    async def _close(self) -> None:
        try:
            if self._resource_manager is not None:
                await self._resource_manager.close()
        finally:
            await self._provider.close()

    async def _run(
        self,
        turn: AgentTurnStream,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
        mode: AgentMode,
        queued_supplements: tuple[SystemSupplement, ...],
    ) -> AsyncIterator[AgentEvent]:
        working_messages = [*history, user_message]
        turn_messages = [TurnMessage(user_message, datetime.now(UTC))]
        context_transaction = (
            self._context_manager.begin_turn(history, user_message)
            if self._context_manager is not None
            else None
        )
        allowed_access = (
            frozenset({ToolAccess.READ})
            if mode is AgentMode.PLAN_ONLY
            else frozenset({ToolAccess.READ, ToolAccess.WRITE, ToolAccess.UNKNOWN})
        )
        execution_access = (
            allowed_access | frozenset({ToolAccess.UNKNOWN})
            if mode is AgentMode.PLAN_ONLY and self._plan_only_mcp_tools
            else allowed_access
        )
        deferred_names = frozenset(
            tool.definition.name
            for tool in self._registry
            if tool.definition.defer_loading
            and (mode is AgentMode.AGENT or tool.definition.name in self._plan_only_mcp_tools)
        )
        exposure = ToolExposureSession(deferred_names)
        context = ToolContext(self._context.workspace, exposure)
        usage = TokenUsage()

        try:
            environment = await turn.run_child(self._environment.collect())
            request_supplements = [*queued_supplements, environment.to_supplement()]
            if deferred_names:
                request_supplements.append(
                    SystemSupplement(
                        SupplementKind.TOOL_CATALOG,
                        "Available deferred MCP tools (use tool_search before calling): "
                        + ", ".join(sorted(deferred_names)),
                    )
                )
            if self._permission_session is not None:
                request_supplements.append(
                    SystemSupplement(
                        SupplementKind.TOOL_STATE,
                        f"Current permission mode: {self._permission_session.mode.value}.",
                    )
                )
            prompt_context = self._prompt_runtime.begin_turn(
                mode.value,
                tuple(request_supplements),
            )
            system_prompt = self._prompt_bundle.content_blocks
            supplements = tuple(
                supplement.tagged_content for supplement in prompt_context.supplements
            )
            for round_number in range(1, self._max_rounds + 1):
                definitions = self._registry.definitions(allowed_access, exposure.exposed_names)
                if mode is AgentMode.PLAN_ONLY and not deferred_names:
                    definitions = tuple(
                        definition for definition in definitions if definition.name != "tool_search"
                    )
                advertised_names = frozenset(definition.name for definition in definitions)
                model_request = AgentModelRequest(
                    messages=tuple(working_messages),
                    system_prompt=system_prompt,
                    supplements=supplements,
                    tools=definitions,
                )
                prepared = None
                if context_transaction is not None:
                    try:
                        prepared = await turn.run_child(
                            context_transaction.prepare_request(model_request)
                        )
                    except ContextLimitError as error:
                        if error.failure_report is not None:
                            yield ContextCompactionFailedEvent(error.failure_report)
                        yield self._finish_error(
                            turn,
                            turn_messages,
                            error.code,
                            str(error),
                            usage,
                        )
                        return
                    except ContextStorageError:
                        yield self._finish_error(
                            turn,
                            turn_messages,
                            "context_storage_error",
                            "无法安全保存超限工具结果。",
                            usage,
                        )
                        return
                    working_messages = list(prepared.messages)
                    model_request = prepared.request
                    if prepared.compaction_report is not None:
                        yield ContextCompactedEvent(prepared.compaction_report)
                    if prepared.failure_report is not None:
                        yield ContextCompactionFailedEvent(prepared.failure_report)

                assembler = ResponseAssembler()
                stream = self._provider.stream_agent(model_request)
                while True:
                    try:
                        event = await turn.run_child(anext(stream))
                    except StopAsyncIteration:
                        break
                    assembler.consume(event)
                    if isinstance(event, ThinkingDelta):
                        yield AgentThinkingDelta(
                            round_number,
                            event.index,
                            event.text,
                        )
                    elif isinstance(event, TextDelta):
                        yield AgentTextDelta(
                            round_number,
                            event.index,
                            event.text,
                        )

                assistant_message = assembler.finish()
                if self._context_manager is not None and prepared is not None:
                    self._context_manager.observe_main_usage(
                        prepared.estimate.local_tokens,
                        assembler.usage.total_input_tokens,
                    )
                usage += assembler.usage
                stop_reason = assembler.stop_reason
                tool_calls = assistant_message.blocks(ToolCallBlock)
                working_messages.append(assistant_message)
                turn_messages.append(TurnMessage(assistant_message, datetime.now(UTC)))

                if stop_reason is StopReason.END_TURN and not tool_calls:
                    turn.complete(
                        AgentTurnResult(
                            termination=AgentTermination.COMPLETED,
                            turn_messages=tuple(turn_messages),
                            final_message=assistant_message,
                            usage=usage,
                            context_commit=(
                                context_transaction.create_commit(tuple(working_messages))
                                if context_transaction is not None
                                else None
                            ),
                        )
                    )
                    yield FinalResponseEvent(assistant_message)
                    return

                if stop_reason is not StopReason.TOOL_USE:
                    code = (
                        "inconsistent_response"
                        if stop_reason is StopReason.END_TURN and tool_calls
                        else "unexpected_stop_reason"
                    )
                    message = (
                        "模型结束原因与工具调用内容不一致。"
                        if code == "inconsistent_response"
                        else f"模型以异常原因停止：{stop_reason or StopReason.UNKNOWN}。"
                    )
                    yield self._finish_error(turn, turn_messages, code, message, usage)
                    return

                if not tool_calls:
                    yield self._finish_error(
                        turn,
                        turn_messages,
                        "missing_tool_calls",
                        "模型声明使用工具，但响应中没有工具调用。",
                        usage,
                    )
                    return

                denied_results = {
                    position: _tool_not_discovered_result()
                    for position, call in enumerate(tool_calls)
                    if (
                        (tool := self._registry.get(call.name)) is not None
                        and tool.definition.defer_loading
                        and call.name not in advertised_names
                    )
                }
                if self._permission_engine is not None and self._permission_session is not None:
                    for position, call in enumerate(tool_calls):
                        if position in denied_results:
                            continue
                        decision = await turn.run_child(
                            self._permission_engine.evaluate(
                                call,
                                self._permission_session,
                                allowed_access=allowed_access,
                                plan_only=mode is AgentMode.PLAN_ONLY,
                            )
                        )
                        if decision.action is PermissionAction.ALLOW:
                            continue
                        if decision.action is PermissionAction.ASK:
                            turn.begin_approval()
                            yield ToolApprovalRequested(
                                round_number,
                                position,
                                decision,
                            )
                            choice = await turn.run_child(turn.consume_approval())
                            if choice is ApprovalChoice.ALLOW_SESSION and decision.allow_session:
                                self._permission_session.grant(decision.subject.session_key)
                                continue
                            if choice is ApprovalChoice.ALLOW_ONCE:
                                continue
                        denied_results[position] = _permission_denied_result(decision)

                records: list[ToolExecutionRecord] = []
                scheduled = self._scheduler.stream(
                    tool_calls,
                    context,
                    execution_access,
                    denied_results,
                )
                while True:
                    try:
                        scheduled_event = await turn.run_child(anext(scheduled))
                    except StopAsyncIteration:
                        break
                    if isinstance(scheduled_event, ScheduledToolStarted):
                        yield ToolExecutionStarted(
                            round_number,
                            scheduled_event.position,
                            scheduled_event.call,
                        )
                    elif isinstance(scheduled_event, ScheduledToolCompleted):
                        records.append(scheduled_event.record)
                        yield ToolExecutionCompleted(round_number, scheduled_event.record)
                    elif isinstance(scheduled_event, ScheduledToolCancelled):
                        yield ToolExecutionCancelled(
                            round_number,
                            scheduled_event.position,
                            scheduled_event.call,
                        )

                if len(records) != len(tool_calls):
                    yield self._finish_error(
                        turn,
                        turn_messages,
                        "incomplete_tool_batch",
                        "工具批次没有产生完整结果。",
                        usage,
                    )
                    return

                records.sort(key=lambda record: record.position)
                result_message = (
                    context_transaction.build_result_message(records)
                    if context_transaction is not None
                    else ChatMessage(
                        role="user",
                        content=tuple(
                            ToolResultBlock(
                                record.call.id,
                                _result_content(record),
                                record.result.is_error,
                            )
                            for record in records
                        ),
                    )
                )
                working_messages.append(result_message)
                turn_messages.append(TurnMessage(result_message, datetime.now(UTC)))

                if round_number == self._max_rounds:
                    message = f"Agent 已达到最大轮数 {self._max_rounds}。"
                    turn.complete(
                        AgentTurnResult(
                            termination=AgentTermination.LIMIT_REACHED,
                            turn_messages=tuple(turn_messages),
                            error_message=message,
                            usage=usage,
                        )
                    )
                    yield AgentLimitReachedEvent(self._max_rounds, message)
                    return
        except asyncio.CancelledError:
            if not turn.cancellation_requested:
                raise
            message = "当前 Agent 回合已取消。"
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.CANCELLED,
                    turn_messages=tuple(turn_messages),
                    error_message=message,
                    usage=usage,
                )
            )
            yield AgentCancelledEvent(message)
        except ProviderError as error:
            yield self._finish_error(
                turn,
                turn_messages,
                error.code,
                error.user_message,
                usage,
            )
        except MessageAssemblyError:
            yield self._finish_error(
                turn,
                turn_messages,
                "invalid_response",
                "模型响应结构无效，请重试。",
                usage,
            )
        except ContextStorageError:
            yield self._finish_error(
                turn,
                turn_messages,
                "context_storage_error",
                "无法安全保存超限工具结果。",
                usage,
            )
        except Exception:
            yield self._finish_error(
                turn,
                turn_messages,
                "agent_internal_error",
                "Agent 运行时发生内部错误。",
                usage,
            )
        finally:
            exposure.clear()

    @staticmethod
    def _finish_error(
        turn: AgentTurnStream,
        messages: list[TurnMessage],
        code: str,
        message: str,
        usage: TokenUsage,
    ) -> AgentErrorEvent:
        turn.complete(
            AgentTurnResult(
                termination=AgentTermination.ERROR,
                turn_messages=tuple(messages),
                error_code=code,
                error_message=message,
                usage=usage,
            )
        )
        return AgentErrorEvent(code, message)


def _result_content(record: ToolExecutionRecord) -> str:
    return json.dumps(
        {
            "content": record.result.content,
            "metadata": thaw_json(record.result.metadata),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _permission_denied_result(decision: object) -> ToolExecutionResult:
    from ycode.security import PermissionDecision

    if not isinstance(decision, PermissionDecision):
        raise TypeError("权限拒绝结果必须来自 PermissionDecision")
    return ToolExecutionResult(
        content=decision.message,
        is_error=True,
        metadata={
            "code": "permission_denied",
            "reason_code": decision.reason_code,
            "rule_id": decision.rule_id,
        },
    )


def _tool_not_discovered_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        content="MCP 工具尚未通过 tool_search 发现。",
        is_error=True,
        metadata={"code": "tool_not_discovered"},
    )


class _AsyncCloseable(Protocol):
    async def close(self) -> None: ...
