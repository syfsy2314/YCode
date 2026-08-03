import hashlib
import json
from pathlib import Path

import pytest

from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextPolicy,
    ContextStorageError,
    ToolResultExternalizer,
)
from ycode.core import ChatMessage, ToolResultBlock


def make_store(tmp_path: Path, **kwargs: object) -> ContextArtifactStore:
    redactor = SecretRedactor()
    redactor.add("top-secret")
    return ContextArtifactStore(
        tmp_path,
        redactor,
        ContextPolicy(),
        session_id=str(kwargs.pop("session_id", "current")),
        **kwargs,  # type: ignore[arg-type]
    )


def test_store_chunks_redacts_and_reconstructs_content(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    original = "开头 top-secret " + ("内容" * 30_000) + " 结尾"

    artifact = store.store("read_file", "call-1", original)
    manifest_path = tmp_path / artifact.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunks = [(manifest_path.parent / chunk["path"]).read_bytes() for chunk in manifest["chunks"]]
    reconstructed = b"".join(chunks)

    assert all(len(chunk) <= 50 * 1024 for chunk in chunks)
    assert len(manifest_path.read_bytes()) <= 50 * 1024
    assert reconstructed.decode("utf-8") == original.replace("top-secret", "[REDACTED]")
    assert hashlib.sha256(reconstructed).hexdigest() == artifact.sha256
    assert all(
        "top-secret" not in path.read_text(encoding="utf-8")
        for path in manifest_path.parent.rglob("*")
        if path.is_file()
    )
    assert artifact.manifest_path.startswith(".ycode/context/current/")


def test_store_reuses_same_artifact(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    first = store.store("read_file", "call-1", "same content")
    second = store.store("read_file", "call-1", "same content")

    assert first == second
    results = store.session_dir / "tool-results"
    assert len([path for path in results.iterdir() if not path.name.startswith(".tmp")]) == 1


def test_cleanup_only_removes_old_inactive_session(tmp_path: Path) -> None:
    root = tmp_path / ".ycode" / "context"
    root.mkdir(parents=True)
    sessions = {
        "old-dead": {"session_id": "old-dead", "process_id": 1, "created_at": 0},
        "old-active": {"session_id": "old-active", "process_id": 2, "created_at": 0},
        "recent-dead": {"session_id": "recent-dead", "process_id": 3, "created_at": 99_000},
    }
    for name, manifest in sessions.items():
        directory = root / name
        directory.mkdir()
        (directory / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    invalid = root / "invalid"
    invalid.mkdir()
    (invalid / "session.json").write_text("not-json", encoding="utf-8")

    store = make_store(
        tmp_path,
        clock=lambda: 100_000,
        process_alive=lambda process_id: process_id == 2,
    )

    assert not (root / "old-dead").exists()
    assert (root / "old-active").exists()
    assert (root / "recent-dead").exists()
    assert invalid.exists()
    store.close()
    assert not (root / "current").exists()
    assert (root / "old-active").exists()


def test_externalizer_applies_single_result_boundary_and_preview(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    externalizer = ToolResultExternalizer(store)
    exact = ChatMessage("user", (ToolResultBlock("call-1", "a" * (50 * 1024)),))
    oversized_content = "HEAD" + ("中" * 20_000) + "TAIL"
    oversized = ChatMessage("user", (ToolResultBlock("call-2", oversized_content),))

    exact_result = externalizer.normalize_messages((exact,))[0].blocks(ToolResultBlock)[0]
    externalized = externalizer.normalize_messages((oversized,))[0].blocks(ToolResultBlock)[0]
    reference = json.loads(externalized.content)

    assert exact_result.content == exact.content[0].content  # type: ignore[union-attr]
    assert reference["externalized"] is True
    assert reference["tool_call_id"] == "call-2"
    assert len(reference["preview"].encode("utf-8")) <= 4 * 1024
    assert reference["preview"].startswith("HEAD")
    assert reference["preview"].endswith("TAIL")


def test_externalizer_applies_aggregate_limit_largest_first(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    externalizer = ToolResultExternalizer(store)
    message = ChatMessage(
        "user",
        tuple(
            ToolResultBlock(f"call-{index}", chr(96 + index) * (45 * 1024)) for index in range(1, 6)
        ),
    )

    result = externalizer.normalize_messages((message,))[0]
    references = [
        json.loads(block.content)
        for block in result.blocks(ToolResultBlock)
        if block.content.startswith('{"externalized":true')
    ]

    assert len(references) == 1
    assert references[0]["tool_call_id"] == "call-1"


def test_externalizer_redacts_inline_result_and_is_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    externalizer = ToolResultExternalizer(store)
    small = ChatMessage("user", (ToolResultBlock("call-1", "value=top-secret"),))
    large = ChatMessage("user", (ToolResultBlock("call-2", "top-secret" + "x" * 60_000),))

    small_result = externalizer.normalize_messages((small,))[0].blocks(ToolResultBlock)[0]
    once = externalizer.normalize_messages((large,))
    twice = externalizer.normalize_messages(once)

    assert small_result.content == "value=[REDACTED]"
    assert once == twice
    assert len(list((store.session_dir / "tool-results").glob("*/manifest.json"))) == 1


def test_externalizer_propagates_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    externalizer = ToolResultExternalizer(store)

    def fail(*args: object, **kwargs: object) -> object:
        raise ContextStorageError("无法写入")

    monkeypatch.setattr(store, "store", fail)
    message = ChatMessage("user", (ToolResultBlock("call-1", "x" * 60_000),))

    with pytest.raises(ContextStorageError, match="无法写入"):
        externalizer.normalize_messages((message,))
