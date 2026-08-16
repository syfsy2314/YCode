"""最小 ReAct Agent Loop。"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from ycode.agent.contracts import (
    AgentLoopOptions,
    AgentMode,
    AgentRequestSnapshot,
    AgentTermination,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
    ToolPolicyDecision,
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
    HookNoticeEvent,
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
from ycode.hooks import HookContextFactory, HookEventName, HookRuntime
from ycode.prompt import EnvironmentCollector, PromptBundle, PromptRuntimeContext
from ycode.prompt.models import SupplementKind, SystemSupplement
from ycode.security import (
    ApprovalChoice,
    PermissionAction,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionSession,
)
from ycode.session.assembler import ResponseAssembler
from ycode.skills.runtime import SkillRuntime
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
        skill_runtime: SkillRuntime | None = None,
        hook_runtime: HookRuntime | None = None,
        hook_context: HookContextFactory | None = None,
        max_rounds: int = 10,
        options: AgentLoopOptions | None = None,
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
        self._skill_runtime = skill_runtime
        if (hook_runtime is None) != (hook_context is None):
            raise ValueError("Hook 运行时和上下文工厂必须同时提供")
        self._hook_runtime = hook_runtime
        self._hook_context = hook_context
        self._close_task: asyncio.Task[None] | None = None
        self._max_rounds = max_rounds
        self._options = options or AgentLoopOptions()
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
        turn_id = uuid4().hex
        if self._options.tool_scope is not None:
            self._options.tool_scope.turn_id = turn_id
            self._options.tool_scope.current_snapshot = None
        return AgentTurnStream(
            lambda turn: self._run(
                turn,
                history_snapshot,
                user_message,
                mode,
                queued,
                None,
                None,
            ),
            turn_id=turn_id,
        )

    def start_turn_with_skill_scope(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
        skill_scope,
    ) -> AgentTurn:
        queued = tuple(self._queued_request_supplements)
        self._queued_request_supplements.clear()
        turn_id = uuid4().hex
        if self._options.tool_scope is not None:
            self._options.tool_scope.turn_id = turn_id
            self._options.tool_scope.current_snapshot = None
        return AgentTurnStream(
            lambda turn: self._run(
                turn,
                tuple(history),
                user_message,
                mode,
                queued,
                skill_scope,
                None,
            ),
            turn_id=turn_id,
        )

    def start_seeded_turn(
        self,
        request: AgentModelRequest,
        mode: AgentMode,
    ) -> AgentTurn:
        if mode not in self.supported_modes:
            raise ValueError("AgentLoop 不支持当前模式")
        if not request.continuation_messages:
            raise ValueError("Seeded turn 必须包含 continuation 任务消息")
        user_message = request.continuation_messages[-1]
        if user_message.role != "user":
            raise ValueError("Seeded turn 最后一条 continuation 必须是用户消息")
        turn_id = uuid4().hex
        if self._options.tool_scope is not None:
            self._options.tool_scope.turn_id = turn_id
            self._options.tool_scope.current_snapshot = None
        return AgentTurnStream(
            lambda turn: self._run(turn, (), user_message, mode, (), None, request),
            turn_id=turn_id,
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
            if self._options.owns_provider:
                await self._provider.close()

    async def _run(
        self,
        turn: AgentTurnStream,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
        mode: AgentMode,
        queued_supplements: tuple[SystemSupplement, ...],
        provided_skill_scope=None,
        seed_request: AgentModelRequest | None = None,
    ) -> AsyncIterator[AgentEvent]:
        turn_id = turn.turn_id
        seed_messages = list(seed_request.messages) if seed_request is not None else []
        working_messages = (
            list(seed_request.continuation_messages)
            if seed_request is not None
            else [*history, user_message]
        )
        turn_messages = [TurnMessage(user_message, datetime.now(UTC))]
        context_transaction = (
            self._context_manager.begin_turn(
                tuple(working_messages[:-1]) if seed_request is not None else history,
                user_message,
            )
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
        skill_scope = provided_skill_scope
        if skill_scope is None and self._skill_runtime is not None:
            skill_scope = self._skill_runtime.begin_task(mode)
        context = ToolContext(
            self._context.workspace,
            exposure,
            skill_scope,
            self._options.tool_scope,
        )
        usage = TokenUsage()
        completed = False
        runtime_notifications: list[SystemSupplement] = []

        async def dispatch_hook(event):
            assert self._hook_runtime is not None
            return await self._hook_runtime.dispatch(
                event,
                scope_id=self._options.hook_scope_id,
            )

        async def finish_error(code: str, message: str) -> tuple[AgentEvent, ...]:
            events: list[AgentEvent] = []
            if self._hook_runtime is not None and self._hook_context is not None:
                error_result = await dispatch_hook(
                    self._hook_context.simple(
                        HookEventName.AGENT_ERROR,
                        turn={"id": turn_id},
                        error={"code": code, "message": message},
                    )
                )
                events.extend(HookNoticeEvent(item) for item in error_result.notices)
                end_result = await dispatch_hook(
                    self._hook_context.simple(
                        HookEventName.TURN_END,
                        turn={"id": turn_id, "status": "error"},
                        error={"code": code, "message": message},
                    )
                )
                events.extend(HookNoticeEvent(item) for item in end_result.notices)
            events.append(self._finish_error(turn, turn_messages, code, message, usage))
            return tuple(events)

        try:
            if self._hook_runtime is not None and self._hook_context is not None:
                hook_result = await turn.run_child(
                    dispatch_hook(
                        self._hook_context.simple(
                            HookEventName.TURN_START,
                            turn={"id": turn_id},
                            message={"role": user_message.role, "content": user_message.text},
                        )
                    )
                )
                for notice in hook_result.notices:
                    yield HookNoticeEvent(notice)
            fixed_supplement_contents: tuple[str, ...]
            if seed_request is None:
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
                system_prompt = self._prompt_bundle.content_blocks
                prompt_context = self._prompt_runtime.begin_turn(
                    mode.value,
                    tuple(request_supplements),
                )
                request_kinds = {item.kind for item in request_supplements}
                fixed_supplement_contents = tuple(
                    item.tagged_content
                    for item in prompt_context.supplements
                    if item.kind in request_kinds or item.kind is SupplementKind.MODE
                )
            else:
                system_prompt = seed_request.system_prompt
                fixed_supplement_contents = seed_request.supplements
            for round_number in range(1, self._max_rounds + 1):
                if self._skill_runtime is not None and skill_scope is not None:
                    self._skill_runtime.refresh_task_prompt(skill_scope)
                if self._options.notification_source is not None:
                    runtime_notifications.extend(self._options.notification_source.take_pending())
                session_supplements = (
                    tuple(
                        supplement.tagged_content
                        for supplement in self._prompt_runtime.session_supplements
                    )
                    if seed_request is None
                    else ()
                )
                supplements = (
                    *session_supplements,
                    *fixed_supplement_contents,
                    *(item.tagged_content for item in runtime_notifications),
                )
                if seed_request is None:
                    definitions = self._registry.definitions(allowed_access, exposure.exposed_names)
                    if mode is AgentMode.PLAN_ONLY and not deferred_names:
                        definitions = tuple(
                            definition
                            for definition in definitions
                            if definition.name != "tool_search"
                        )
                    if self._skill_runtime is not None and skill_scope is not None:
                        visible = self._skill_runtime.visible_tools(
                            skill_scope,
                            frozenset(definition.name for definition in definitions),
                        )
                        definitions = tuple(
                            definition for definition in definitions if definition.name in visible
                        )
                else:
                    definitions = seed_request.tools
                advertised_names = frozenset(definition.name for definition in definitions)
                model_request = AgentModelRequest(
                    messages=(
                        tuple(seed_messages)
                        if seed_request is not None
                        else tuple(working_messages)
                    ),
                    system_prompt=system_prompt,
                    supplements=supplements,
                    continuation_messages=(
                        tuple(working_messages) if seed_request is not None else ()
                    ),
                    tools=definitions,
                    max_output_tokens=(
                        seed_request.max_output_tokens if seed_request is not None else None
                    ),
                    thinking_enabled=(
                        seed_request.thinking_enabled if seed_request is not None else None
                    ),
                )
                prepared = None
                if context_transaction is not None:
                    try:
                        prepared = await turn.run_child(
                            context_transaction.prepare_request(
                                model_request,
                                preserve_messages=(
                                    seed_request is not None and self._options.preserve_seed_prefix
                                ),
                                allow_preserved_compaction=round_number > 1,
                            )
                        )
                    except ContextLimitError as error:
                        if error.failure_report is not None:
                            yield ContextCompactionFailedEvent(error.failure_report)
                        for terminal_event in await finish_error(error.code, str(error)):
                            yield terminal_event
                        return
                    except ContextStorageError:
                        for terminal_event in await finish_error(
                            "context_storage_error", "无法安全保存超限工具结果。"
                        ):
                            yield terminal_event
                        return
                    if seed_request is None:
                        working_messages = list(prepared.messages)
                    else:
                        seed_messages = list(prepared.request.messages)
                        working_messages = list(prepared.request.continuation_messages)
                    model_request = prepared.request
                    if prepared.compaction_report is not None:
                        if self._hook_runtime is not None and self._hook_context is not None:
                            hook_result = await turn.run_child(
                                dispatch_hook(
                                    self._hook_context.compacted(
                                        turn_id, prepared.compaction_report
                                    )
                                )
                            )
                            for notice in hook_result.notices:
                                yield HookNoticeEvent(notice)
                        yield ContextCompactedEvent(prepared.compaction_report)
                    if prepared.failure_report is not None:
                        yield ContextCompactionFailedEvent(prepared.failure_report)

                if self._hook_runtime is not None and self._hook_context is not None:
                    hook_result = await turn.run_child(
                        dispatch_hook(
                            self._hook_context.message(
                                HookEventName.MESSAGE_BEFORE_SEND,
                                turn_id,
                                (
                                    model_request.continuation_messages[-1]
                                    if model_request.continuation_messages
                                    else model_request.messages[-1]
                                ),
                            )
                        )
                    )
                    for notice in hook_result.notices:
                        yield HookNoticeEvent(notice)
                    reminders = self._hook_runtime.take_reminders(self._options.hook_scope_id)
                    if reminders:
                        model_request = replace(
                            model_request,
                            supplements=(
                                *model_request.supplements,
                                *(item.tagged_content for item in reminders),
                            ),
                        )

                if self._options.tool_scope is not None:
                    permission_mode = (
                        self._permission_session.mode
                        if self._permission_session is not None
                        else PermissionMode.DEFAULT
                    )
                    self._options.tool_scope.current_snapshot = AgentRequestSnapshot(
                        turn_id,
                        model_request,
                        mode,
                        permission_mode,
                        frozenset(definition.name for definition in model_request.tools),
                    )

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
                if self._hook_runtime is not None and self._hook_context is not None:
                    hook_result = await turn.run_child(
                        dispatch_hook(
                            self._hook_context.message(
                                HookEventName.MESSAGE_AFTER_RECEIVE,
                                turn_id,
                                assistant_message,
                            )
                        )
                    )
                    for notice in hook_result.notices:
                        yield HookNoticeEvent(notice)
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
                    if self._hook_runtime is not None and self._hook_context is not None:
                        hook_result = await turn.run_child(
                            dispatch_hook(
                                self._hook_context.simple(
                                    HookEventName.TURN_END,
                                    turn={"id": turn_id, "status": "completed"},
                                )
                            )
                        )
                        for notice in hook_result.notices:
                            yield HookNoticeEvent(notice)
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
                            active_skill_names=(
                                self._skill_runtime.candidate_active_names(skill_scope)
                                if self._skill_runtime is not None and skill_scope is not None
                                else ()
                            ),
                            skill_scope=skill_scope,
                        )
                    )
                    completed = True
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
                    for terminal_event in await finish_error(code, message):
                        yield terminal_event
                    return

                if not tool_calls:
                    for terminal_event in await finish_error(
                        "missing_tool_calls", "模型声明使用工具，但响应中没有工具调用。"
                    ):
                        yield terminal_event
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
                if self._options.tool_policy is not None:
                    for position, call in enumerate(tool_calls):
                        if position in denied_results:
                            continue
                        policy_decision = self._options.tool_policy.evaluate(call)
                        if not policy_decision.allowed:
                            denied_results[position] = _tool_policy_denied_result(policy_decision)
                if self._permission_engine is not None and self._permission_session is not None:
                    for position, call in enumerate(tool_calls):
                        if position in denied_results:
                            continue
                        preparation = await turn.run_child(
                            self._permission_engine.prepare(
                                call,
                                allowed_access=allowed_access,
                                plan_only=mode is AgentMode.PLAN_ONLY,
                            )
                        )
                        decision = preparation.denial
                        if decision is None:
                            hook_permission = None
                            if self._hook_runtime is not None and self._hook_context is not None:
                                hook_result = await turn.run_child(
                                    dispatch_hook(
                                        self._hook_context.tool_before(
                                            turn_id,
                                            call,
                                            preparation.subject.normalized_arguments,
                                        )
                                    )
                                )
                                for notice in hook_result.notices:
                                    yield HookNoticeEvent(notice)
                                hook_permission = hook_result.permission
                            if hook_permission is None:
                                decision = self._permission_engine.evaluate_policy(
                                    preparation,
                                    self._permission_session,
                                    skill_scope=skill_scope,
                                )
                            else:
                                action = PermissionAction(hook_permission.value)
                                decision = PermissionDecision(
                                    action,
                                    preparation.subject,
                                    "hook_rule",
                                    hook_result.reason or "Hook 已决定此工具调用。",
                                    allow_session=False,
                                )
                        assert decision is not None
                        if decision.action is PermissionAction.ALLOW:
                            continue
                        if decision.action is PermissionAction.ASK:
                            if self._options.non_interactive_approvals:
                                denied_results[position] = _permission_denied_result(decision)
                                continue
                            turn.begin_approval()
                            yield ToolApprovalRequested(
                                round_number,
                                position,
                                decision,
                            )
                            choice = await turn.run_child(turn.consume_approval())
                            if (
                                choice is not ApprovalChoice.DENY
                                and decision.reason_code == "skill_activation_approval"
                                and self._skill_runtime is not None
                                and skill_scope is not None
                            ):
                                name = str(decision.subject.normalized_arguments["name"])
                                snapshot = self._skill_runtime.load_current(name)
                                self._skill_runtime.approve_activation(skill_scope, snapshot)
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
                        if (
                            scheduled_event.record.position not in denied_results
                            and self._hook_runtime is not None
                            and self._hook_context is not None
                        ):
                            hook_result = await turn.run_child(
                                dispatch_hook(
                                    self._hook_context.tool_after(turn_id, scheduled_event.record)
                                )
                            )
                            for notice in hook_result.notices:
                                yield HookNoticeEvent(notice)
                        yield ToolExecutionCompleted(round_number, scheduled_event.record)
                    elif isinstance(scheduled_event, ScheduledToolCancelled):
                        yield ToolExecutionCancelled(
                            round_number,
                            scheduled_event.position,
                            scheduled_event.call,
                        )

                if len(records) != len(tool_calls):
                    for terminal_event in await finish_error(
                        "incomplete_tool_batch", "工具批次没有产生完整结果。"
                    ):
                        yield terminal_event
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
                    if self._hook_runtime is not None and self._hook_context is not None:
                        hook_result = await turn.run_child(
                            dispatch_hook(
                                self._hook_context.simple(
                                    HookEventName.TURN_END,
                                    turn={"id": turn_id, "status": "limit_reached"},
                                    error={"message": message},
                                )
                            )
                        )
                        for notice in hook_result.notices:
                            yield HookNoticeEvent(notice)
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
            if self._hook_runtime is not None and self._hook_context is not None:
                hook_result = await dispatch_hook(
                    self._hook_context.simple(
                        HookEventName.TURN_END,
                        turn={"id": turn_id, "status": "cancelled"},
                        error={"message": message},
                    )
                )
                for notice in hook_result.notices:
                    yield HookNoticeEvent(notice)
            yield AgentCancelledEvent(message)
        except ProviderError as error:
            for terminal_event in await finish_error(error.code, error.user_message):
                yield terminal_event
        except MessageAssemblyError:
            for terminal_event in await finish_error(
                "invalid_response", "模型响应结构无效，请重试。"
            ):
                yield terminal_event
        except ContextStorageError:
            for terminal_event in await finish_error(
                "context_storage_error", "无法安全保存超限工具结果。"
            ):
                yield terminal_event
        except Exception:
            for terminal_event in await finish_error(
                "agent_internal_error", "Agent 运行时发生内部错误。"
            ):
                yield terminal_event
        finally:
            exposure.clear()
            if self._options.clear_hook_scope_on_finish and self._hook_runtime is not None:
                self._hook_runtime.clear_scope(self._options.hook_scope_id)
            if not completed and self._skill_runtime is not None and skill_scope is not None:
                self._skill_runtime.discard_task(skill_scope)

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


def _tool_policy_denied_result(decision: ToolPolicyDecision) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=decision.message,
        is_error=True,
        metadata={"code": decision.code},
    )


class _AsyncCloseable(Protocol):
    async def close(self) -> None: ...
