import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from winpty import PtyProcess

from tests.support.sse_server import SSETestServer, StreamResponse, sse_event


def chunk(content: str | None, finish_reason: str | None = None) -> str:
    return sse_event(
        None,
        {
            "id": "chatcmpl-e2e",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {} if content is None else {"content": content},
                    "finish_reason": finish_reason,
                }
            ],
        },
    )


def response(*parts: str, delay: float = 0.05) -> StreamResponse:
    events = [chunk(part) for part in parts]
    events.extend([chunk(None, "stop"), sse_event(None, "[DONE]")])
    return StreamResponse(events=events, delay=delay)


def anthropic_message_start() -> str:
    return sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_e2e_agent",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-test",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        },
    )


def anthropic_tool_response(
    calls: list[tuple[str, str, dict[str, object]]],
) -> StreamResponse:
    events = [anthropic_message_start()]
    for index, (call_id, name, arguments) in enumerate(calls):
        events.extend(
            [
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": call_id,
                            "name": name,
                            "input": {},
                        },
                    },
                ),
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(arguments),
                        },
                    },
                ),
                sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": index},
                ),
            ]
        )
    events.extend(
        [
            sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 6},
                },
            ),
            sse_event("message_stop", {"type": "message_stop"}),
        ]
    )
    return StreamResponse(events=events, delay=0.01)


def anthropic_text_response(text: str) -> StreamResponse:
    return StreamResponse(
        events=[
            anthropic_message_start(),
            sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
            ),
            sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
            ),
            sse_event("message_stop", {"type": "message_stop"}),
        ],
        delay=0.01,
    )


def context_summary_text() -> str:
    headings = (
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
    body = "\n".join(f"## {heading}\n无" for heading in headings)
    return f"<analysis_draft>草稿</analysis_draft><summary>{body}</summary>"


class PtyReader:
    def __init__(self, process: PtyProcess) -> None:
        self.process = process
        self.output = ""
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while self.process.isalive():
            try:
                value = self.process.read(4096)
            except (EOFError, OSError):
                break
            with self._lock:
                self.output += value

    def snapshot(self) -> str:
        with self._lock:
            return self.output

    def wait_for(self, expected: str, timeout: float = 10.0, *, count: int = 1) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.snapshot()
            if value.count(expected) >= count:
                return value
            time.sleep(0.05)
        raise AssertionError(f"等待终端文本超时：{expected!r}\n当前输出：\n{self.snapshot()}")


def write_config(path: Path, server: SSETestServer) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "active: local\nproviders:\n  - name: local\n    protocol: openai\n"
        f"    model: gpt-test\n    base_url: {server.base_url}\n"
        "    api_key: e2e-placeholder\n    thinking: false\n"
        "  - name: unfinished\n"
        "    protocol: future\n"
        "    api_key: ${YCODE_E2E_UNUSED_KEY}\n",
        encoding="utf-8",
    )


def write_anthropic_config(path: Path, server: SSETestServer, *, thinking: bool = True) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "active: local\nproviders:\n  - name: local\n    protocol: anthropic\n"
        f"    model: claude-test\n    base_url: {server.origin}\n"
        f"    api_key: e2e-placeholder\n    thinking: {str(thinking).lower()}\n",
        encoding="utf-8",
    )


def spawn_ycode(
    cwd: Path,
    *,
    dimensions: tuple[int, int] = (30, 100),
    arguments: tuple[str, ...] = (),
) -> tuple[PtyProcess, PtyReader]:
    python = Path.cwd() / ".venv" / "Scripts" / "python.exe"
    environment = os.environ.copy()
    environment.pop("YCODE_E2E_UNUSED_KEY", None)
    environment["PROMPT_TOOLKIT_NO_CPR"] = "1"
    process = PtyProcess.spawn(
        [str(python), "-m", "ycode", *arguments],
        cwd=str(cwd),
        env=environment,
        dimensions=dimensions,
    )
    return process, PtyReader(process)


def stop_process(process: PtyProcess) -> None:
    if process.isalive():
        process.terminate(force=True)


def wait_for_exit(process: PtyProcess, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while process.isalive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process.isalive() is False
    process.wait()
    assert process.exitstatus == 0


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_input_hint_and_question_mark_message(
    tmp_path: Path, sse_server: SSETestServer
) -> None:
    sse_server.enqueue(response("question mark received", delay=0.02))
    project = tmp_path / "input-hint"
    write_config(project / ".ycode" / "config.yaml", sse_server)
    process, reader = spawn_ycode(project, dimensions=(30, 60))
    try:
        reader.wait_for("Send a message...", timeout=15)
        output = reader.wait_for("? for help", timeout=15)
        assert output.count("─") >= 118
        assert output.index("Send a message...") < output.index("? for help")
        assert "\x1b[7m" not in output

        process.write("?\r")
        reader.wait_for("question mark received", timeout=15)
        reader.wait_for("? for help", timeout=15, count=2)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert sse_server.requests[0].json["messages"] == [{"role": "user", "content": "?"}]


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_two_turn_stream_and_exit(
    tmp_path: Path, sse_server: SSETestServer
) -> None:
    sse_server.enqueue(response("# First\n\n", "**bo", "ld**", delay=0.15))
    sse_server.enqueue(response("Second answer", delay=0.03))
    project = tmp_path / "project"
    config_path = project / ".ycode" / "config.yaml"
    write_config(config_path, sse_server)
    child = project / "nested" / "child"
    child.mkdir(parents=True)

    process, reader = spawn_ycode(child)
    try:
        output = reader.wait_for("Send a message...", timeout=15)
        assert "/\\_/\\" in output
        assert "Provider" in output and "Protocol" in output and "Model" in output
        assert "Config" not in output

        process.write("first question\r")
        reader.wait_for("**bold**", timeout=15)
        assert re.search(r"\d+\.[1-9]s", reader.snapshot())

        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("second question\r")
        reader.wait_for("Second answer", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/exit\r")

        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 2
    assert sse_server.requests[0].json["model"] == "gpt-test"
    second_messages = sse_server.requests[1].json["messages"]
    assert [message["content"] for message in second_messages] == [
        "first question",
        "# First\n\n**bold**",
        "second question",
    ]
    assert not any(path.name.endswith("history.json") for path in project.rglob("*"))

    sse_server.enqueue(response("fresh process", delay=0.02))
    restarted, restarted_reader = spawn_ycode(child)
    try:
        restarted_reader.wait_for("Send a message...", timeout=15)
        restarted.write("fresh question\r")
        restarted_reader.wait_for("fresh process", timeout=15)
        restarted_reader.wait_for("Send a message...", timeout=15, count=2)
        restarted.write("/exit\r")
        wait_for_exit(restarted)
    finally:
        stop_process(restarted)
    assert sse_server.requests[2].json["messages"] == [
        {"role": "user", "content": "fresh question"}
    ]


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_anthropic_thinking(tmp_path: Path, sse_server: SSETestServer) -> None:
    events = [
        sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_e2e",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-test",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            },
        ),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "分析过程"},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "**Answer**"},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events, delay=0.03))
    project = tmp_path / "claude"
    write_anthropic_config(project / ".ycode" / "config.yaml", sse_server)
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("think about this\r")
        reader.wait_for("◇ Thinking", timeout=15)
        reader.wait_for("分析过程", timeout=15)
        reader.wait_for("**Answer**", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert sse_server.requests[0].json["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_agent_executes_six_tools(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "agent-tools"
    project.mkdir()
    (project / "sample.txt").write_text("needle\n", encoding="utf-8")
    sse_server.enqueue(
        anthropic_tool_response(
            [
                ("read-1", "read_file", {"path": "sample.txt"}),
                ("glob-1", "glob", {"pattern": "*.txt"}),
                ("grep-1", "grep", {"pattern": "needle"}),
                (
                    "write-1",
                    "write_file",
                    {"path": "new.txt", "content": "before\n"},
                ),
                (
                    "edit-1",
                    "edit_file",
                    {"path": "new.txt", "old_text": "before", "new_text": "after"},
                ),
                ("command-1", "run_command", {"command": "Get-Content new.txt"}),
            ]
        )
    )
    sse_server.enqueue(anthropic_text_response("all tools completed"))
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    process, reader = spawn_ycode(project)
    try:
        output = reader.wait_for("mode: agen", timeout=15)
        assert "Send a message..." in output
        process.write("use every tool\r")
        for approval_tool in ("write_file", "edit_file", "run_command"):
            reader.wait_for(f"工具审批：{approval_tool}", timeout=20)
            process.write("2")
        for tool_name in (
            "read_file",
            "glob",
            "grep",
            "write_file",
            "edit_file",
            "run_command",
        ):
            reader.wait_for(tool_name, timeout=20)
        reader.wait_for("all tools completed", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        wait_for_exit(process)
        output = reader.snapshot()
        write_started = output.index("◇ write_file")
        assert output.index("✓ read_file") < write_started
        assert output.index("✓ glob") < write_started
        assert output.index("✓ grep") < write_started
        assert "Traceback" not in output
    finally:
        stop_process(process)

    assert (project / "new.txt").read_text(encoding="utf-8") == "after\n"
    assert len(sse_server.requests) == 3
    tool_results = [
        block
        for message in sse_server.requests[1].json["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert [result["tool_use_id"] for result in tool_results] == [
        "read-1",
        "glob-1",
        "grep-1",
        "write-1",
        "edit-1",
        "command-1",
    ]


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_dangerous_command_is_denied_without_approval(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "dangerous-command"
    project.mkdir()
    sse_server.enqueue(
        anthropic_tool_response(
            [
                (
                    "danger-1",
                    "run_command",
                    {"command": "git reset --hard HEAD~1"},
                )
            ]
        )
    )
    sse_server.enqueue(anthropic_text_response("dangerous command refused"))
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("/permission strict\r")
        reader.wait_for("permission: strict", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        assert sse_server.requests == []
        process.write("/permission allow\r")
        reader.wait_for("permission: allow", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        assert sse_server.requests == []
        process.write("run a dangerous command\r")
        output = reader.wait_for("禁止高破坏性 Git 操作", timeout=20)
        reader.wait_for("dangerous command refused", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=4)
        assert "工具审批：run_command" not in output
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 3
    result = next(
        block
        for message in sse_server.requests[1].json["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result"
    )
    assert result["is_error"] is True
    assert "permission_denied" in result["content"]


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_session_grant_reuse_change_and_clear(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "session-grant"
    project.mkdir()
    for call_id, command, final_text in (
        ("run-1", "Write-Output one", "first command done"),
        ("run-2", "Write-Output one", "second command done"),
        ("run-3", "Write-Output two", "changed command refused"),
    ):
        sse_server.enqueue(
            anthropic_tool_response([(call_id, "run_command", {"command": command})])
        )
        sse_server.enqueue(anthropic_text_response(final_text))
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)

        process.write("run first\r")
        reader.wait_for("工具审批：run_command", timeout=20, count=1)
        process.write("3")
        reader.wait_for("first command done", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=2)

        process.write("run same\r")
        reader.wait_for("second command done", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=3)
        assert reader.snapshot().count("工具审批：run_command") == 1

        process.write("run changed\r")
        reader.wait_for("工具审批：run_command", timeout=20, count=2)
        process.write("1")
        reader.wait_for("changed command refused", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=4)

        request_count = len(sse_server.requests)
        process.write("/permission clear\r")
        reader.wait_for("permission grants cleared:", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=5)
        assert len(sse_server.requests) == request_count

        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 7


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_ctrl_c_during_approval_cancels_entire_batch(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "approval-cancel"
    project.mkdir()
    sse_server.enqueue(
        anthropic_tool_response(
            [
                ("run-1", "run_command", {"command": "Write-Output safe"}),
                (
                    "write-1",
                    "write_file",
                    {"path": "must-not-exist.txt", "content": "unsafe"},
                ),
            ]
        )
    )
    sse_server.enqueue(anthropic_text_response("recovered after approval cancel"))
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("request two tools\r")
        reader.wait_for("工具审批：run_command", timeout=20)
        process.write("\x03")
        reader.wait_for("已取消", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        assert not (project / "must-not-exist.txt").exists()
        assert "工具审批：write_file" not in reader.snapshot()

        process.write("recover\r")
        reader.wait_for("recovered after approval cancel", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 3


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_plan_mode_and_tool_filter(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "plan-mode"
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    sse_server.enqueue(
        anthropic_tool_response(
            [
                (
                    "blocked-write",
                    "write_file",
                    {"path": "forbidden.txt", "content": "must not exist"},
                )
            ]
        )
    )
    sse_server.enqueue(anthropic_text_response("implementation plan"))
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("/plan\r")
        reader.wait_for("mode: plan-only", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("make a plan\r")
        reader.wait_for("当前模式不允许执行该工具", timeout=15)
        reader.wait_for("implementation plan", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/agent\r")
        reader.wait_for("mode: agent", timeout=15)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 3
    request = sse_server.requests[0].json
    assert [tool["name"] for tool in request["tools"]] == [
        "read_file",
        "glob",
        "grep",
        "load_skill",
    ]
    assert any(
        message["role"] == "system"
        and "<task_mode>" in message["content"]
        and "Current task mode: plan-only" in message["content"]
        for message in request["messages"]
    )
    error_results = [
        block
        for message in sse_server.requests[1].json["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert error_results[-1]["is_error"] is True
    assert not (project / "forbidden.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_agent_stops_at_ten_tool_rounds(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "agent-limit"
    project.mkdir()
    (project / "sample.txt").write_text("value\n", encoding="utf-8")
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    for index in range(10):
        sse_server.enqueue(
            anthropic_tool_response([(f"read-{index}", "read_file", {"path": "sample.txt"})])
        )
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("keep reading\r")
        reader.wait_for("Agent 已达到最大轮数 10", timeout=30)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 10


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_ctrl_c_cancels_active_command_and_recovers(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "agent-cancel"
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    sse_server.enqueue(
        anthropic_tool_response(
            [
                (
                    "command-1",
                    "run_command",
                    {"command": "Start-Sleep -Seconds 30"},
                )
            ]
        )
    )
    sse_server.enqueue(anthropic_text_response("recovered after cancel"))
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("run a long command\r")
        reader.wait_for("工具审批：run_command", timeout=15)
        process.write("2")
        reader.wait_for("◇ run_command", timeout=15)
        process.sendintr()
        reader.wait_for("已取消", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("retry\r")
        reader.wait_for("recovered after cancel", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 3
    retry_messages = sse_server.requests[1].json["messages"]
    assert [
        message
        for message in retry_messages
        if message["role"] == "user" and isinstance(message["content"], str)
    ] == [{"role": "user", "content": "retry"}]
    assert any(
        message["role"] == "system" and "<environment_context>" in message["content"]
        for message in retry_messages
    )


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_thinking_disabled_filters_server_delta(
    tmp_path: Path, sse_server: SSETestServer
) -> None:
    events = [
        sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_disabled",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-test",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            },
        ),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "thinking_delta",
                    "thinking": "THINKING-MUST-NOT-APPEAR",
                },
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "visible answer"},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events, delay=0.03))
    project = tmp_path / "thinking-disabled"
    write_anthropic_config(project / ".ycode" / "config.yaml", sse_server, thinking=False)
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("answer without thinking\r")
        reader.wait_for("visible answer", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        wait_for_exit(process)
        output = reader.snapshot()
        assert "THINKING-MUST-NOT-APPEAR" not in output
        assert "Traceback" not in output
    finally:
        stop_process(process)

    assert sse_server.requests[0].json["thinking"] == {"type": "disabled"}


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_recovers_after_interrupted_stream(
    tmp_path: Path, sse_server: SSETestServer
) -> None:
    sse_server.enqueue(StreamResponse(events=[chunk("partial")]))
    sse_server.enqueue(response("recovered", delay=0.02))
    project = tmp_path / "recovery"
    write_config(project / ".ycode" / "config.yaml", sse_server)
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("failed turn\r")
        reader.wait_for("partial", timeout=15)
        reader.wait_for("OpenAI 响应流意外结束", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("retry turn\r")
        reader.wait_for("recovered", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/quit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert len(sse_server.requests) == 2
    assert sse_server.requests[1].json["messages"] == [{"role": "user", "content": "retry turn"}]
    assert "e2e-placeholder" not in reader.snapshot()


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_mcp_deferred_flow(tmp_path: Path, sse_server: SSETestServer) -> None:
    project = tmp_path / "mcp-flow"
    project.mkdir()
    state_file = project / "mcp-state.jsonl"
    python = Path.cwd() / ".venv" / "Scripts" / "python.exe"
    mcp_server = Path.cwd() / "tests" / "support" / "mcp_stdio_server.py"
    config_path = project / ".ycode" / "config.yaml"
    write_anthropic_config(config_path, sse_server, thinking=False)
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "mcp_servers:\n"
        "  - name: fixture\n"
        "    transport: stdio\n"
        f"    command: '{python}'\n"
        "    args:\n"
        f"      - '{mcp_server}'\n"
        "    env:\n"
        "      YCODE_MCP_STATE_FILE: ${MCP_STATE_FILE}\n"
        "      YCODE_MCP_TEST_SECRET: ${MCP_TEST_SECRET}\n",
        encoding="utf-8",
    )
    (project / ".env").write_text(
        f"MCP_STATE_FILE={state_file}\nMCP_TEST_SECRET=terminal-secret\n",
        encoding="utf-8",
    )
    sse_server.enqueue(
        anthropic_tool_response([("search", "tool_search", {"tool_names": ["mcp_fixture_echo"]})])
    )
    sse_server.enqueue(
        anthropic_tool_response([("remote", "mcp_fixture_echo", {"value": "terminal-mcp"})])
    )
    sse_server.enqueue(anthropic_text_response("MCP flow completed"))
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("后台连接 1", timeout=25)
        reader.wait_for("Send a message...", timeout=20)
        deadline = time.monotonic() + 15
        while not state_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert state_file.exists()
        time.sleep(0.5)
        process.write("/mcp\r")
        reader.wait_for("MCP Servers", timeout=15)
        reader.wait_for("fixture", timeout=15)
        reader.wait_for("ready", timeout=15)
        process.write("use MCP echo\r")
        reader.wait_for("tool_search", timeout=20)
        reader.wait_for("工具审批：mcp_fixture_echo", timeout=20)
        process.write("2")
        reader.wait_for("MCP flow completed", timeout=20)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/exit\r")
        wait_for_exit(process, timeout=15)
        output = reader.snapshot()
        assert "terminal-secret" not in output
        assert "Traceback" not in output
    finally:
        stop_process(process)

    state = [json.loads(line) for line in state_file.read_text(encoding="utf-8").splitlines()]
    assert any(item.get("event") == "call" and item.get("tool") == "echo" for item in state)
    assert state[-1]["event"] == "stopped"
    assert [tool["name"] for tool in sse_server.requests[0].json["tools"]][-1] == ("tool_search")
    assert "mcp_fixture_echo" not in {tool["name"] for tool in sse_server.requests[0].json["tools"]}
    assert "mcp_fixture_echo" in {tool["name"] for tool in sse_server.requests[1].json["tools"]}


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_mcp_background_startup(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "mcp-background"
    project.mkdir()
    state_file = project / "mcp-state.jsonl"
    python = Path.cwd() / ".venv" / "Scripts" / "python.exe"
    mcp_server = Path.cwd() / "tests" / "support" / "mcp_stdio_server.py"
    delayed_server = (
        "import time,runpy;time.sleep(5);"
        f"runpy.run_path(r'{mcp_server.as_posix()}',run_name='__main__')"
    )
    config_path = project / ".ycode" / "config.yaml"
    write_anthropic_config(config_path, sse_server, thinking=False)
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "mcp_servers:\n"
        "  - name: slow_fixture\n"
        "    transport: stdio\n"
        f"    command: '{python}'\n"
        "    args:\n"
        "      - '-c'\n"
        f'      - "{delayed_server}"\n'
        "    startup_timeout_seconds: 10\n"
        "    env:\n"
        "      YCODE_MCP_STATE_FILE: ${MCP_STATE_FILE}\n",
        encoding="utf-8",
    )
    (project / ".env").write_text(f"MCP_STATE_FILE={state_file}\n", encoding="utf-8")

    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("后台连接 1", timeout=20)
        reader.wait_for("Send a message...", timeout=20)
        process.write("/mcp\r")
        reader.wait_for("starting", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)

        deadline = time.monotonic() + 15
        while not state_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert state_file.exists()
        time.sleep(0.5)
        assert "ready" not in reader.snapshot()

        process.write("/mcp\r")
        reader.wait_for("ready", timeout=15)
        reader.wait_for("slow_fixture", timeout=15)
        reader.wait_for("8", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("/exit\r")
        wait_for_exit(process, timeout=15)
        output = reader.snapshot()
        assert "Traceback" not in output
    finally:
        stop_process(process)

    assert sse_server.requests == []
    state = [json.loads(line) for line in state_file.read_text(encoding="utf-8").splitlines()]
    assert state[0]["event"] == "started"
    assert state[-1]["event"] == "stopped"


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_narrow_header_stacks_details(
    tmp_path: Path, sse_server: SSETestServer
) -> None:
    project = tmp_path / "narrow"
    write_config(project / ".ycode" / "config.yaml", sse_server)
    process, reader = spawn_ycode(project, dimensions=(30, 35))
    try:
        reader.wait_for("Send a message...", timeout=15)
        output = reader.wait_for("? for help", timeout=15)
        assert output.index("Provider") > output.index("> ^ <")
        assert "Protocol" in output and "Model" in output and "Thinking" in output
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_context_compact_and_continue(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "context-flow"
    write_anthropic_config(project / ".ycode" / "config.yaml", sse_server, thinking=False)
    sse_server.enqueue(anthropic_text_response("first answer"))
    sse_server.enqueue(anthropic_text_response(context_summary_text()))
    sse_server.enqueue(anthropic_text_response("after compact"))
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("first question\r")
        reader.wait_for("first answer", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/compact\r")
        reader.wait_for("上下文已压缩", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=3)
        process.write("continue now\r")
        reader.wait_for("after compact", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=4)
        process.write("/exit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    summary_request = sse_server.requests[1].json
    assert summary_request["max_tokens"] == 20_000
    assert summary_request["thinking"] == {"type": "disabled"}
    assert "tools" not in summary_request
    assert "<conversation_memory>" in json.dumps(sse_server.requests[2].json, ensure_ascii=False)
    context_root = project / ".ycode" / "context"
    assert context_root.is_dir()
    assert list(context_root.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_builtin_command_framework(
    tmp_path: Path,
    sse_server: SSETestServer,
) -> None:
    project = tmp_path / "builtin-commands"
    write_anthropic_config(project / ".ycode" / "config.yaml", sse_server, thinking=False)
    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("/help for commands", timeout=15)

        process.write("/he\t\r")
        reader.wait_for("可用命令", timeout=15)
        reader.wait_for("/help for commands", timeout=15, count=2)

        process.write("/p\t")
        output = reader.wait_for("/permission", timeout=15)
        assert "/plan" in output
        process.write("\x15")
        process.write("/unknown\r")
        reader.wait_for("未知命令", timeout=15)
        reader.wait_for("/help for commands", timeout=15, count=3)

        process.write("/plan\r")
        reader.wait_for("mode: plan-only", timeout=15)
        process.write("/permission strict\r")
        reader.wait_for("permission: strict", timeout=15)
        process.write("/mcp\r")
        reader.wait_for("当前没有 MCP 状态信息", timeout=15)
        process.write("/quit\r")
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    assert sse_server.requests == []


@pytest.mark.parametrize(
    ("config_content", "expected_message"),
    [
        (None, "找不到 .ycode/config.yaml"),
        ("providers: [", "YAML 无法解析"),
        (
            """\
active: missing-env
providers:
  - name: missing-env
    protocol: openai
    model: test-model
    base_url: http://127.0.0.1:1/v1
    api_key: ${YCODE_E2E_MISSING_KEY}
""",
            "引用的环境变量不存在：YCODE_E2E_MISSING_KEY",
        ),
        (
            """\
active: unfinished
providers:
  - name: valid-but-inactive
    protocol: openai
    model: test-model
    base_url: http://127.0.0.1:1/v1
    api_key: placeholder
  - name: unfinished
    protocol: future
    api_key: placeholder
""",
            "providers.1.protocol",
        ),
    ],
)
def test_startup_config_failures_are_safe(
    tmp_path: Path,
    config_content: str | None,
    expected_message: str,
) -> None:
    if config_content is not None:
        config_dir = tmp_path / ".ycode"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(config_content, encoding="utf-8")

    env = os.environ.copy()
    env.pop("YCODE_E2E_MISSING_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "ycode"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert expected_message in output
    assert "Traceback" not in output


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows ConPTY")
def test_windows_terminal_memory_system(tmp_path: Path, sse_server: SSETestServer) -> None:
    project = tmp_path / "memory-system"
    write_anthropic_config(
        project / ".ycode" / "config.yaml",
        sse_server,
        thinking=False,
    )
    (project / "YCODE.md").write_text("Remember stable preferences.", encoding="utf-8")
    memory_root = project / ".ycode" / "memory"
    memory_root.mkdir()
    (memory_root / "MEMORY.md").write_text(
        "- [技术栈](project-stack.md) — 项目语言\n",
        encoding="utf-8",
    )
    (memory_root / "project-stack.md").write_text(
        "---\nname: 技术栈\ndescription: 项目语言\ntype: project_knowledge\n---\nPython\n",
        encoding="utf-8",
    )
    no_change = '{"operations":[]}'
    update = json.dumps(
        {
            "operations": [
                {
                    "action": "create",
                    "path": "user-prefers-any.md",
                    "entry": {
                        "path": "user-prefers-any.md",
                        "name": "偏好 any",
                        "description": "用户要求使用 any",
                        "type": "user_preference",
                        "body": "使用 any 替代 interface{}。",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    sse_server.enqueue(
        anthropic_tool_response([("read-memory", "read_file", {"path": "README.md"})])
    )
    sse_server.enqueue(anthropic_text_response("session A first turn"))
    sse_server.enqueue(anthropic_text_response(no_change))
    sse_server.enqueue(anthropic_text_response("session A continued"))
    sse_server.enqueue(anthropic_text_response(no_change))
    sse_server.enqueue(anthropic_text_response("session B first turn"))
    sse_server.enqueue(anthropic_text_response("session A resumed again"))
    sse_server.enqueue(anthropic_text_response(update))

    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("read the project and remember my preference\r")
        reader.wait_for("read_file", timeout=15)
        reader.wait_for("session A first turn", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        reader.wait_for("无需更新项目记忆", timeout=15)
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    session_files = list((project / ".ycode" / "sessions").glob("*.jsonl"))
    assert len(session_files) == 1
    session_a = session_files[0]
    session_a_id = session_a.stem
    old_lines = []
    for line in session_a.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record["timestamp"] = "2020-01-01T00:00:00Z"
        old_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    session_a.write_text("\n".join(old_lines) + "\n", encoding="utf-8")

    process, reader = spawn_ycode(project, arguments=("--continue",))
    try:
        reader.wait_for("session restored:", timeout=15)
        process.write("continue session A\r")
        reader.wait_for("session A continued", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write("/exit\r")
        reader.wait_for("无需更新项目记忆", timeout=15)
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    process, reader = spawn_ycode(project)
    try:
        reader.wait_for("Send a message...", timeout=15)
        process.write("create session B\r")
        reader.wait_for("session B first turn", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=2)
        process.write(f"/resume {session_a_id}\r")
        reader.wait_for("session restored:", timeout=15)
        process.write("back in session A\r")
        reader.wait_for("session A resumed again", timeout=15)
        reader.wait_for("Send a message...", timeout=15, count=4)
        process.write("/exit\r")
        reader.wait_for("项目记忆已更新", timeout=15)
        wait_for_exit(process)
        assert "Traceback" not in reader.snapshot()
    finally:
        stop_process(process)

    session_files = list((project / ".ycode" / "sessions").glob("*.jsonl"))
    assert len(session_files) == 2
    assert session_a.read_text(encoding="utf-8").count('"type":"turn_commit"') == 3
    assert (memory_root / "user-prefers-any.md").is_file()
    main_requests = [request.json for request in sse_server.requests if request.json.get("tools")]
    assert all("Remember stable preferences." in json.dumps(item) for item in main_requests)
    assert all("project-stack.md" in json.dumps(item) for item in main_requests)
    assert "long time gap" in json.dumps(sse_server.requests[3].json)
    exit_transcript = json.loads(sse_server.requests[-1].json["messages"][0]["content"])
    assert {item["session_id"] for item in exit_transcript["new_conversations"]} == {
        session_a_id,
        next(path.stem for path in session_files if path.stem != session_a_id),
    }
