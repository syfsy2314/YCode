"""子 Agent 独立循环的跑到底执行。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING, Protocol

from ycode.agent import (
    AgentLoop,
    AgentMode,
    AgentRequestSnapshot,
    AgentTermination,
)
from ycode.core.messages import ChatMessage
from ycode.core.provider import AgentChatProvider, AgentModelRequest
from ycode.security.models import PermissionMode
from ycode.subagents.models import (
    SubagentCreationMode,
    SubagentError,
    SubagentInvocation,
    SubagentRoleSnapshot,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)
from ycode.subagents.policy import SubagentToolPolicy, stricter_permission_mode
from ycode.subagents.providers import SubagentProviderPool
from ycode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from ycode.worktrees.runtime import SubagentWorkspaceFactory


@dataclass(frozen=True, slots=True)
class SubagentRuntimeRequest:
    task_id: str
    provider: AgentChatProvider
    role: SubagentRoleSnapshot | None
    run_mode: SubagentRunMode
    permission_mode: PermissionMode
    mode: AgentMode
    policy: SubagentToolPolicy
    max_rounds: int
    hook_scope_id: str
    preserve_seed_prefix: bool


class SubagentLoopFactory(Protocol):
    def __call__(self, request: SubagentRuntimeRequest) -> AgentLoop: ...


class SubagentRunner:
    def __init__(
        self,
        provider_pool: SubagentProviderPool,
        registry: ToolRegistry,
        loop_factory: SubagentLoopFactory,
        async_allowed_tools: frozenset[str],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        workspace_factory: SubagentWorkspaceFactory | None = None,
    ) -> None:
        self._provider_pool = provider_pool
        self._registry = registry
        self._loop_factory = loop_factory
        self._async_allowed_tools = async_allowed_tools
        self._clock = clock
        self._workspace_factory = workspace_factory

    async def run(
        self,
        task_id: str,
        invocation: SubagentInvocation,
        parent: AgentRequestSnapshot,
    ) -> SubagentTaskView:
        started_at = self._clock()
        loop: AgentLoop | None = None
        turn = None
        try:
            role = invocation.role
            model_name = role.config.model if role is not None else None
            provider = self._provider_pool.get(model_name)
            permission_mode = (
                parent.permission_mode
                if role is None
                else stricter_permission_mode(parent.permission_mode, role.config.permission)
            )
            plan_only = parent.mode is AgentMode.PLAN_ONLY
            base_tools = (
                parent.effective_tool_names
                if invocation.creation_mode is SubagentCreationMode.FORK
                else frozenset(tool.definition.name for tool in self._registry)
            )
            policy = SubagentToolPolicy(
                self._registry,
                base_tools,
                role=role,
                run_mode=invocation.run_mode,
                async_allowed_tools=self._async_allowed_tools,
                plan_only=plan_only,
            )
            runtime = SubagentRuntimeRequest(
                task_id,
                provider,
                role,
                invocation.run_mode,
                permission_mode,
                AgentMode.PLAN_ONLY if plan_only else AgentMode.AGENT,
                policy,
                role.config.max_rounds if role is not None else 10,
                f"subagent:{task_id}",
                invocation.creation_mode is SubagentCreationMode.FORK,
            )
            if invocation.worktree_lease is not None:
                if self._workspace_factory is None:
                    raise RuntimeError("隔离子 Agent 工作区工厂未装配。")
                loop = self._workspace_factory.create(runtime, invocation.worktree_lease).loop
            else:
                loop = self._loop_factory(runtime)
            if invocation.creation_mode is SubagentCreationMode.FORK:
                seed = _fork_request(parent.request, invocation.task)
                turn = loop.start_seeded_turn(seed, runtime.mode)
            else:
                task = _workspace_task(invocation) if invocation.worktree_lease else invocation.task
                turn = loop.start_turn((), ChatMessage.user_text(task), runtime.mode)
            async for _ in turn:
                pass
            result = turn.result
            if result is None:
                return self._failed(
                    task_id,
                    invocation,
                    started_at,
                    "missing_result",
                    "子 Agent 结束时缺少结果。",
                )
            text = _last_assistant_text(result.messages)
            if result.termination is AgentTermination.COMPLETED:
                if not text:
                    return self._failed(
                        task_id,
                        invocation,
                        started_at,
                        "empty_result",
                        "子 Agent 没有返回文本结果。",
                        usage=result.usage,
                    )
                return self._view(
                    task_id,
                    invocation,
                    SubagentStatus.COMPLETED,
                    started_at,
                    result=text,
                    usage=result.usage,
                )
            if result.termination is AgentTermination.LIMIT_REACHED:
                return self._view(
                    task_id,
                    invocation,
                    SubagentStatus.LIMIT_REACHED,
                    started_at,
                    result=text or None,
                    usage=result.usage,
                    error=SubagentError(
                        "limit_reached",
                        result.error_message or "子 Agent 达到最大轮次。",
                    ),
                )
            if result.termination is AgentTermination.CANCELLED:
                return self._view(
                    task_id,
                    invocation,
                    SubagentStatus.CANCELLED,
                    started_at,
                    result=text or None,
                    usage=result.usage,
                    error=SubagentError("cancelled", result.error_message or "子 Agent 已取消。"),
                )
            return self._failed(
                task_id,
                invocation,
                started_at,
                result.error_code or "agent_failed",
                result.error_message or "子 Agent 执行失败。",
                result=text or None,
                usage=result.usage,
            )
        except asyncio.CancelledError:
            if turn is not None:
                turn.cancel()
            return self._view(
                task_id,
                invocation,
                SubagentStatus.CANCELLED,
                started_at,
                error=SubagentError("cancelled", "子 Agent 已取消。"),
            )
        except Exception as error:
            code = str(getattr(error, "code", "subagent_failed"))
            return self._failed(task_id, invocation, started_at, code, str(error))
        finally:
            if loop is not None:
                await loop.close()

    def _failed(
        self,
        task_id: str,
        invocation: SubagentInvocation,
        started_at: datetime,
        code: str,
        message: str,
        *,
        result: str | None = None,
        usage=None,
    ) -> SubagentTaskView:
        from ycode.core.events import TokenUsage

        return self._view(
            task_id,
            invocation,
            SubagentStatus.FAILED,
            started_at,
            result=result,
            usage=usage or TokenUsage(),
            error=SubagentError(code, message),
        )

    def _view(
        self,
        task_id: str,
        invocation: SubagentInvocation,
        status: SubagentStatus,
        started_at: datetime,
        *,
        result: str | None = None,
        usage=None,
        error: SubagentError | None = None,
    ) -> SubagentTaskView:
        from ycode.core.events import TokenUsage

        return SubagentTaskView(
            task_id,
            status,
            invocation.creation_mode,
            invocation.run_mode,
            invocation.role.config.name if invocation.role is not None else None,
            invocation.task,
            result,
            usage or TokenUsage(),
            started_at,
            self._clock(),
            error,
        )


def _fork_request(parent: AgentModelRequest, task: str) -> AgentModelRequest:
    instructions = (
        files("ycode.subagents.resources").joinpath("fork.md").read_text(encoding="utf-8").strip()
    )
    task_message = ChatMessage.user_text(f"{instructions}\n\n<task>\n{task}\n</task>")
    return AgentModelRequest(
        messages=parent.messages,
        system_prompt=parent.system_prompt,
        supplements=parent.supplements,
        continuation_messages=(*parent.continuation_messages, task_message),
        tools=parent.tools,
        max_output_tokens=parent.max_output_tokens,
        thinking_enabled=parent.thinking_enabled,
    )


def _last_assistant_text(messages: tuple[ChatMessage, ...]) -> str:
    return next(
        (
            message.text.strip()
            for message in reversed(messages)
            if message.role == "assistant" and message.text.strip()
        ),
        "",
    )


def _workspace_task(invocation: SubagentInvocation) -> str:
    assert invocation.worktree_lease is not None
    parent = invocation.parent_workspace or "<unknown>"
    child = str(invocation.worktree_lease.path)
    return (
        "<trusted-workspace-mapping>\n"
        f"父项目路径：{parent}\n"
        f"本任务 Worktree 路径：{child}\n"
        "任务正文中的父项目绝对路径应翻译到本 Worktree 的相对对应位置。"
        "所有工具调用必须以本 Worktree 为工作区，不要访问父项目目录。\n"
        "</trusted-workspace-mapping>\n\n"
        f"{invocation.task}"
    )
