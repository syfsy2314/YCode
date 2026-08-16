"""上下文预检、摘要事务和会话级熔断。"""

import asyncio

from ycode.context.artifacts import ContextArtifactStore, ToolResultExternalizer
from ycode.context.models import (
    ContextCommit,
    ContextCompactionCandidate,
    ContextCompactionReport,
    ContextFailureReport,
    ContextPolicy,
    ConversationMemory,
    PreparedContextRequest,
    RestoreContextResult,
    SummarySource,
)
from ycode.context.summary import ConversationCompactor
from ycode.context.tokens import TokenEstimator
from ycode.core.messages import ChatMessage
from ycode.core.provider import AgentModelRequest
from ycode.tools.contracts import ToolExecutionRecord

MEMORY_TEMPLATE = "<conversation_memory>\n{summary}\n</conversation_memory>"
BOUNDARY_REMINDER = """<reminder>
摘要不是代码事实。需要精确的文件、接口、错误或工具结果细节时，必须重新读取工作区
文件或摘要中引用的存盘结果；禁止依据摘要臆造不存在的代码。
</reminder>"""


class ContextLimitError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        failure_report: ContextFailureReport | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.failure_report = failure_report


class ContextCompactionError(Exception):
    def __init__(self, report: ContextFailureReport) -> None:
        super().__init__(report.message)
        self.report = report


class ContextCompactionNotNeeded(Exception):
    pass


def _request_with(
    request: AgentModelRequest,
    messages: tuple[ChatMessage, ...],
    memory: ConversationMemory | None,
    continuation_messages: tuple[ChatMessage, ...] | None = None,
) -> AgentModelRequest:
    supplements = list(request.supplements)
    if memory is not None:
        supplements.insert(0, MEMORY_TEMPLATE.format(summary=memory.summary))
        supplements.append(BOUNDARY_REMINDER)
    return AgentModelRequest(
        messages=messages,
        system_prompt=request.system_prompt,
        supplements=tuple(supplements),
        continuation_messages=(
            request.continuation_messages
            if continuation_messages is None
            else continuation_messages
        ),
        tools=request.tools,
        max_output_tokens=request.max_output_tokens,
        thinking_enabled=request.thinking_enabled,
    )


def _preserved_request_with(
    request: AgentModelRequest,
    messages: tuple[ChatMessage, ...],
    continuation_messages: tuple[ChatMessage, ...],
    memory: ConversationMemory | None,
) -> AgentModelRequest:
    supplements = list(request.supplements)
    if memory is not None:
        supplements.extend((MEMORY_TEMPLATE.format(summary=memory.summary), BOUNDARY_REMINDER))
    return AgentModelRequest(
        messages=messages,
        system_prompt=request.system_prompt,
        supplements=tuple(supplements),
        continuation_messages=continuation_messages,
        tools=request.tools,
        max_output_tokens=request.max_output_tokens,
        thinking_enabled=request.thinking_enabled,
    )


class ContextManager:
    """保存单个会话的上下文运行状态。"""

    def __init__(
        self,
        policy: ContextPolicy,
        store: ContextArtifactStore,
        compactor: ConversationCompactor,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.externalizer = ToolResultExternalizer(store)
        self.compactor = compactor
        self.estimator = estimator or TokenEstimator()
        self.memory: ConversationMemory | None = None
        self.failure_count = 0
        self.auto_compaction_fused = False

    def begin_turn(
        self,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
    ) -> "ContextTransaction":
        return ContextTransaction(self, history, user_message, self.memory)

    async def compact_committed_history(
        self,
        history: tuple[ChatMessage, ...],
    ) -> ContextCompactionReport:
        candidate = await self.prepare_manual_compaction(history)
        self.activate_compaction(candidate)
        return candidate.report

    async def prepare_manual_compaction(
        self,
        history: tuple[ChatMessage, ...],
    ) -> ContextCompactionCandidate:
        if not history and self.memory is None:
            raise ContextCompactionNotNeeded
        before = self._estimate_memory_history(history, self.memory)
        try:
            result = await self.compactor.compact(SummarySource(self.memory, history))
            after = self._estimate_memory_history((), result.summary)
            if after > self.policy.auto_compact_threshold:
                raise ValueError("摘要后上下文仍超过自动压缩阈值")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ContextCompactionError(self._record_failure(error, False)) from error
        return ContextCompactionCandidate(
            result.retained_messages,
            result.summary,
            ContextCompactionReport(before, after, manual=True),
        )

    def activate_compaction(self, candidate: ContextCompactionCandidate) -> None:
        self.memory = candidate.memory
        self._reset_failures()

    async def prepare_restore(
        self,
        history: tuple[ChatMessage, ...],
        memory: ConversationMemory | None,
    ) -> RestoreContextResult:
        """预检恢复上下文，整个过程不修改当前会话状态。"""

        before = self._estimate_memory_history(history, memory)
        if before <= self.policy.auto_compact_threshold:
            return RestoreContextResult(history, memory)
        try:
            result = await self.compactor.compact(SummarySource(memory, history))
            after = self._estimate_memory_history(result.retained_messages, result.summary)
            if after > self.policy.auto_compact_threshold:
                raise ValueError("摘要后上下文仍超过自动压缩阈值")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            report = ContextFailureReport(
                str(getattr(error, "code", "summary_failed")),
                "恢复会话时上下文摘要失败。",
                self.failure_count,
                self.auto_compaction_fused,
                False,
            )
            raise ContextCompactionError(report) from error
        return RestoreContextResult(
            result.retained_messages,
            result.summary,
            ContextCompactionReport(before, after),
        )

    def activate_restore(self, result: RestoreContextResult) -> None:
        self.memory = result.memory
        self.reset_runtime_state()

    def reset_runtime_state(self) -> None:
        self._reset_failures()

    def commit(self, context_commit: ContextCommit) -> None:
        self.memory = context_commit.memory

    def observe_main_usage(self, local_tokens: int, actual_input_tokens: int) -> None:
        if actual_input_tokens > 0:
            self.estimator.observe(local_tokens, actual_input_tokens)

    async def close(self) -> None:
        self.store.close()

    def _estimate_memory_history(
        self,
        history: tuple[ChatMessage, ...],
        memory: ConversationMemory | None,
    ) -> int:
        messages = history or (ChatMessage.user_text(memory.summary if memory else "无"),)
        request = AgentModelRequest(
            messages=messages,
            supplements=(MEMORY_TEMPLATE.format(summary=memory.summary),) if memory else (),
        )
        return self.estimator.estimate(request).total_tokens

    def _record_failure(self, error: Exception, request_continues: bool) -> ContextFailureReport:
        self.failure_count += 1
        self.auto_compaction_fused = self.failure_count >= self.policy.failure_fuse_count
        code = getattr(error, "code", "summary_failed")
        return ContextFailureReport(
            str(code),
            "上下文摘要失败。",
            self.failure_count,
            self.auto_compaction_fused,
            request_continues,
        )

    def _reset_failures(self) -> None:
        self.failure_count = 0
        self.auto_compaction_fused = False


class ContextTransaction:
    """一个 Agent 回合内可回滚的上下文视图。"""

    def __init__(
        self,
        manager: ContextManager,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
        memory: ConversationMemory | None,
    ) -> None:
        self.manager = manager
        self.history = tuple(history)
        self.latest_user_message = user_message
        self.memory = memory
        self._compacted = False

    def build_result_message(self, records: list[ToolExecutionRecord]) -> ChatMessage:
        return self.manager.externalizer.build_result_message(records)

    async def prepare_request(
        self,
        request: AgentModelRequest,
        *,
        preserve_messages: bool = False,
        allow_preserved_compaction: bool = False,
    ) -> PreparedContextRequest:
        if preserve_messages:
            return await self._prepare_preserved_request(
                request,
                allow_compaction=allow_preserved_compaction,
            )
        messages = self.manager.externalizer.normalize_messages(request.messages)
        continuation_messages = self.manager.externalizer.normalize_messages(
            request.continuation_messages
        )
        original_request = _request_with(
            request,
            messages,
            self.memory,
            continuation_messages,
        )
        original_estimate = self.manager.estimator.estimate(original_request)
        if original_estimate.total_tokens <= self.manager.policy.auto_compact_threshold:
            return PreparedContextRequest(original_request, messages, original_estimate)

        if self.manager.auto_compaction_fused:
            if original_estimate.total_tokens <= self.manager.policy.continue_request_limit:
                return PreparedContextRequest(original_request, messages, original_estimate)
            raise ContextLimitError(
                "context_limit",
                "上下文超过可继续发送上限，请执行 /compact。",
            )

        minimal_request = _request_with(
            request,
            (self.latest_user_message,),
            None,
            continuation_messages,
        )
        if (
            not self.memory
            and messages == (self.latest_user_message,)
            or self.manager.estimator.estimate(minimal_request).total_tokens
            > self.manager.policy.auto_compact_threshold
        ):
            raise ContextLimitError(
                "context_uncompressible",
                "固定上下文或最新用户消息超过可压缩预算。",
            )

        try:
            result = await self.manager.compactor.compact(
                SummarySource(self.memory, messages, self.latest_user_message)
            )
            compacted_request = _request_with(
                request,
                result.retained_messages,
                result.summary,
                continuation_messages,
            )
            compacted_estimate = self.manager.estimator.estimate(compacted_request)
            if compacted_estimate.total_tokens > self.manager.policy.auto_compact_threshold:
                raise ValueError("摘要后上下文仍超过自动压缩阈值")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            continues = original_estimate.total_tokens <= self.manager.policy.continue_request_limit
            report = self.manager._record_failure(error, continues)
            if not continues:
                raise ContextLimitError(
                    "context_limit",
                    "摘要失败且上下文超过可继续发送上限，请执行 /compact。",
                    report,
                ) from error
            return PreparedContextRequest(
                original_request,
                messages,
                original_estimate,
                failure_report=report,
            )

        self.memory = result.summary
        self._compacted = True
        self.manager._reset_failures()
        report = ContextCompactionReport(
            original_estimate.total_tokens,
            compacted_estimate.total_tokens,
        )
        return PreparedContextRequest(
            compacted_request,
            result.retained_messages,
            compacted_estimate,
            compaction_report=report,
        )

    async def _prepare_preserved_request(
        self,
        request: AgentModelRequest,
        *,
        allow_compaction: bool,
    ) -> PreparedContextRequest:
        messages = self.manager.externalizer.normalize_messages(request.messages)
        continuation = self.manager.externalizer.normalize_messages(request.continuation_messages)
        original_request = _preserved_request_with(
            request,
            messages,
            continuation,
            self.memory,
        )
        original_estimate = self.manager.estimator.estimate(original_request)
        if original_estimate.total_tokens <= self.manager.policy.auto_compact_threshold:
            return PreparedContextRequest(original_request, messages, original_estimate)
        if not allow_compaction:
            raise ContextLimitError(
                "context_uncompressible",
                "Fork 首次请求超过上下文预算，不能改写继承前缀。",
            )
        if self.manager.auto_compaction_fused:
            if original_estimate.total_tokens <= self.manager.policy.continue_request_limit:
                return PreparedContextRequest(original_request, messages, original_estimate)
            raise ContextLimitError("context_limit", "Fork continuation 超过可继续发送上限。")

        minimal_request = _preserved_request_with(
            request,
            messages,
            (self.latest_user_message,),
            None,
        )
        if (
            not continuation
            or self.manager.estimator.estimate(minimal_request).total_tokens
            > self.manager.policy.auto_compact_threshold
        ):
            raise ContextLimitError(
                "context_uncompressible",
                "Fork 固定前缀或任务消息超过可压缩预算。",
            )
        try:
            result = await self.manager.compactor.compact(
                SummarySource(self.memory, continuation, self.latest_user_message)
            )
            compacted_request = _preserved_request_with(
                request,
                messages,
                result.retained_messages,
                result.summary,
            )
            compacted_estimate = self.manager.estimator.estimate(compacted_request)
            if compacted_estimate.total_tokens > self.manager.policy.auto_compact_threshold:
                raise ValueError("Fork continuation 摘要后仍超过自动压缩阈值")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            continues = original_estimate.total_tokens <= self.manager.policy.continue_request_limit
            report = self.manager._record_failure(error, continues)
            if not continues:
                raise ContextLimitError(
                    "context_limit",
                    "Fork continuation 摘要失败且超过可继续发送上限。",
                    report,
                ) from error
            return PreparedContextRequest(
                original_request,
                messages,
                original_estimate,
                failure_report=report,
            )

        self.memory = result.summary
        self._compacted = True
        self.manager._reset_failures()
        report = ContextCompactionReport(
            original_estimate.total_tokens,
            compacted_estimate.total_tokens,
        )
        return PreparedContextRequest(
            compacted_request,
            messages,
            compacted_estimate,
            compaction_report=report,
        )

    def create_commit(self, history: tuple[ChatMessage, ...]) -> ContextCommit:
        return ContextCommit(history, self.memory, self._compacted)
