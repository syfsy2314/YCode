"""结构化会话摘要的输入和结果校验。"""

import json
import re
from dataclasses import dataclass
from importlib.resources import files

from ycode.context.models import ConversationMemory, SummaryResult, SummarySource
from ycode.core.events import (
    StopReason,
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from ycode.core.messages import ChatMessage, TextBlock, ToolCallBlock, ToolResultBlock, thaw_json
from ycode.core.provider import AgentChatProvider, AgentModelRequest

SUMMARY_HEADINGS = (
    "主要请求",
    "关键概念",
    "文件代码",
    "错误修复",
    "解决过程",
    "用户原话",
    "待办",
    "当前工作",
    "下一步",
)
_RESPONSE_PATTERN = re.compile(
    r"\A\s*<analysis_draft>(.*?)</analysis_draft>\s*<summary>(.*?)</summary>\s*\Z",
    re.DOTALL,
)
_HEADING_PATTERN = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_ORIGINAL_PATTERN = re.compile(r'^- 原文 \[(U\d{4})\]: ("(?:[^"\\]|\\.)*")$')
_OVERVIEW_PATTERN = re.compile(r"^- 概述 \[(U\d{4})\]: (.+)$")


class SummaryValidationError(Exception):
    """摘要响应不满足固定结构。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SummaryTranscript:
    text: str
    user_messages: dict[str, str]


def load_summary_prompt() -> str:
    return files("ycode.context.resources").joinpath("summary.md").read_text(encoding="utf-8")


def build_transcript(source: SummarySource) -> SummaryTranscript:
    lines = ["<summary_source>"]
    if source.previous_memory is not None:
        lines.extend(
            (
                "<previous_memory>",
                source.previous_memory.summary,
                "</previous_memory>",
            )
        )
    lines.append("<transcript>")
    user_messages: dict[str, str] = {}
    user_index = 0
    assistant_index = 0
    tool_index = 0
    tool_names: dict[str, str] = {}

    for message in source.messages:
        text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
        if message.role == "user" and text:
            user_index += 1
            message_id = f"U{user_index:04d}"
            user_messages[message_id] = text
            lines.append(
                json.dumps(
                    {"id": message_id, "role": "user", "text": text},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif message.role == "assistant" and text:
            assistant_index += 1
            lines.append(
                json.dumps(
                    {"id": f"A{assistant_index:04d}", "role": "assistant", "text": text},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )

        for block in message.blocks(ToolCallBlock):
            tool_index += 1
            tool_names[block.id] = block.name
            lines.append(
                json.dumps(
                    {
                        "id": f"T{tool_index:04d}",
                        "role": "tool_call",
                        "tool_call_id": block.id,
                        "tool_name": block.name,
                        "arguments": thaw_json(block.arguments),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        for block in message.blocks(ToolResultBlock):
            tool_index += 1
            lines.append(
                json.dumps(
                    {
                        "id": f"T{tool_index:04d}",
                        "role": "tool_result",
                        "tool_call_id": block.tool_call_id,
                        "tool_name": tool_names.get(block.tool_call_id, "unknown_tool"),
                        "is_error": block.is_error,
                        "content": block.content,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )

    lines.extend(("</transcript>", "</summary_source>"))
    return SummaryTranscript("\n".join(lines), user_messages)


def parse_summary_response(
    response: str,
    user_messages: dict[str, str],
) -> ConversationMemory:
    match = _RESPONSE_PATTERN.fullmatch(response)
    if match is None or not match.group(1).strip():
        raise SummaryValidationError("summary_format", "摘要缺少有效的草稿或正式边界。")
    summary = match.group(2).strip()
    headings = tuple(_HEADING_PATTERN.findall(summary))
    if headings != SUMMARY_HEADINGS:
        raise SummaryValidationError("summary_structure", "摘要九个标题缺失或顺序错误。")

    sections = _sections(summary)
    if any(not value.strip() for value in sections.values()):
        raise SummaryValidationError("summary_structure", "摘要存在空部分，必须写“无”。")
    _validate_user_quotes(sections["用户原话"], user_messages)
    return ConversationMemory(summary)


def _sections(summary: str) -> dict[str, str]:
    matches = tuple(_HEADING_PATTERN.finditer(summary))
    return {
        match.group(1): summary[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else None
        ].strip()
        for index, match in enumerate(matches)
    }


def _validate_user_quotes(section: str, user_messages: dict[str, str]) -> None:
    if section == "无":
        return
    for line in section.splitlines():
        if not line.startswith("- "):
            continue
        original = _ORIGINAL_PATTERN.fullmatch(line)
        if original is not None:
            message_id, encoded = original.groups()
            try:
                value = json.loads(encoded)
            except json.JSONDecodeError as error:
                raise SummaryValidationError("summary_quote", "摘要用户原话无法解析。") from error
            if user_messages.get(message_id) != value:
                raise SummaryValidationError("summary_quote", "摘要用户原话与来源不一致。")
            continue
        overview = _OVERVIEW_PATTERN.fullmatch(line)
        if overview is not None and overview.group(1) in user_messages:
            continue
        raise SummaryValidationError("summary_quote", "摘要用户原话格式或来源无效。")


class ConversationCompactor:
    """通过隔离的 Anthropic 请求生成一份结构化记忆。"""

    def __init__(self, provider: AgentChatProvider) -> None:
        self._provider = provider

    async def compact(self, source: SummarySource) -> SummaryResult:
        transcript = build_transcript(source)
        request = AgentModelRequest(
            messages=(ChatMessage.user_text(transcript.text),),
            system_prompt=(load_summary_prompt(),),
            tools=(),
            max_output_tokens=20_000,
            thinking_enabled=False,
        )
        text: list[str] = []
        ended = False
        async for event in self._provider.stream_agent(request):
            if ended:
                raise SummaryValidationError("summary_stream", "摘要完成后出现额外事件。")
            if isinstance(event, TextDelta):
                text.append(event.text)
            elif isinstance(event, StreamEnd):
                if event.stop_reason is not StopReason.END_TURN:
                    raise SummaryValidationError("summary_stop", "摘要没有正常结束。")
                ended = True
            elif isinstance(
                event,
                ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete,
            ):
                raise SummaryValidationError("summary_content", "摘要响应包含不允许的内容。")
        if not ended:
            raise SummaryValidationError("summary_stream", "摘要响应缺少完成事件。")
        memory = parse_summary_response("".join(text), transcript.user_messages)
        retained = (source.latest_user_message,) if source.latest_user_message is not None else ()
        return SummaryResult(memory, retained)
