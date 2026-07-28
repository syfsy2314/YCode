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
    cwd: Path, *, dimensions: tuple[int, int] = (30, 100)
) -> tuple[PtyProcess, PtyReader]:
    python = Path.cwd() / ".venv" / "Scripts" / "python.exe"
    environment = os.environ.copy()
    environment.pop("YCODE_E2E_UNUSED_KEY", None)
    environment["PROMPT_TOOLKIT_NO_CPR"] = "1"
    process = PtyProcess.spawn(
        [str(python), "-m", "ycode"],
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
    assert len(sse_server.requests) == 2
    tool_results = sse_server.requests[1].json["messages"][-1]["content"]
    assert [result["tool_use_id"] for result in tool_results] == [
        "read-1",
        "glob-1",
        "grep-1",
        "write-1",
        "edit-1",
        "command-1",
    ]


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

    assert len(sse_server.requests) == 2
    request = sse_server.requests[0].json
    assert [tool["name"] for tool in request["tools"]] == [
        "read_file",
        "glob",
        "grep",
    ]
    assert "Plan-only mode" in request["system"]
    assert sse_server.requests[1].json["messages"][-1]["content"][0]["is_error"] is True
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
        reader.wait_for("run_command", timeout=15)
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

    assert len(sse_server.requests) == 2
    assert sse_server.requests[1].json["messages"] == [{"role": "user", "content": "retry"}]


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
