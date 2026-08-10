import io
import json
import zipfile
from pathlib import Path

import pytest

from ycode.skills import SkillLoader, SkillValidationEnvironment
from ycode.skills.installer import SkillInstaller, SkillInstallError


def zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


def json_response(value, status=200) -> "Response":
    return Response(json.dumps(value).encode(), status=status)


def skill_bytes(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: {name} skill\n---\nDo it.\n".encode()


class Response:
    def __init__(self, content=b"", status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    async def aiter_bytes(self):
        yield self.content

    async def aclose(self):
        self.closed = True


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    async def get(self, url, *, follow_redirects=False):
        self.urls.append(url)
        return self.responses.pop(0)


def installer(tmp_path: Path, client: Client, refresh=None) -> SkillInstaller:
    return SkillInstaller(
        tmp_path,
        client,
        SkillLoader(),
        SkillValidationEnvironment(frozenset({"read_file"}), frozenset(), frozenset()),
        refresh,
    )


@pytest.mark.asyncio
async def test_downloads_redirect_and_atomically_installs_single_skill(tmp_path: Path) -> None:
    archive = zip_bytes(
        {"review/SKILL.md": b"---\nname: review\ndescription: Review\n---\nDo it.\n"}
    )
    redirect = Response(status=302, headers={"location": "https://cdn.example/review.zip"})
    content = Response(archive)
    refreshed = []

    async def refresh():
        refreshed.append(True)

    result = await installer(tmp_path, Client(redirect, content), refresh).install(
        "https://example.com/review.zip"
    )

    assert result.snapshot is not None
    assert result.snapshot.name == "review"
    assert (tmp_path / ".ycode" / "skills" / "review" / "SKILL.md").is_file()
    assert redirect.closed and content.closed
    assert refreshed == [True]
    assert not list((tmp_path / ".ycode" / "skills").glob(".install-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.zip",
        "https://user:secret@example.com/a.zip",
        "https://127.0.0.1/a.zip",
    ],
)
async def test_rejects_non_public_https_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(SkillInstallError):
        await installer(tmp_path, Client()).install(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        {"../escape/SKILL.md": b"x"},
        {"one/SKILL.md": b"x", "two/SKILL.md": b"x"},
        {"SKILL.md": b"x"},
    ],
)
async def test_rejects_unsafe_or_multiple_top_level_paths(
    tmp_path: Path, files: dict[str, bytes]
) -> None:
    with pytest.raises(SkillInstallError):
        await installer(tmp_path, Client(Response(zip_bytes(files)))).install(
            "https://example.com/a.zip"
        )
    assert not any((tmp_path / ".ycode" / "skills").glob(".install-*"))


@pytest.mark.asyncio
async def test_rejects_existing_skill_without_overwrite(tmp_path: Path) -> None:
    existing = tmp_path / ".ycode" / "skills" / "review"
    existing.mkdir(parents=True)
    original = b"original"
    (existing / "SKILL.md").write_bytes(original)
    archive = zip_bytes({"review/SKILL.md": b"---\nname: review\ndescription: Review\n---\nnew\n"})

    with pytest.raises(SkillInstallError, match="已存在"):
        await installer(tmp_path, Client(Response(archive))).install(
            "https://example.com/review.zip"
        )
    assert (existing / "SKILL.md").read_bytes() == original


@pytest.mark.asyncio
async def test_valid_skill_with_missing_dependency_is_installed_unavailable(
    tmp_path: Path,
) -> None:
    archive = zip_bytes(
        {
            "review/SKILL.md": (
                b"---\nname: review\ndescription: Review\nallowed-tools: MissingTool\n---\nDo it.\n"
            )
        }
    )

    result = await installer(tmp_path, Client(Response(archive))).install(
        "https://example.com/review.zip"
    )

    assert result.snapshot is None
    assert (tmp_path / ".ycode" / "skills" / "review" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_installs_raw_skill_md_without_guessing_adjacent_resources(tmp_path: Path) -> None:
    result = await installer(tmp_path, Client(Response(skill_bytes("raw-review")))).install(
        "https://raw.example/skills/raw-review/SKILL.md"
    )

    root = tmp_path / ".ycode" / "skills" / "raw-review"
    assert result.snapshot is not None
    assert [path.name for path in root.iterdir()] == ["SKILL.md"]


@pytest.mark.asyncio
async def test_installs_github_tree_directory_with_nested_resources(tmp_path: Path) -> None:
    listing = [
        {
            "type": "file",
            "name": "SKILL.md",
            "path": "skills/review/SKILL.md",
            "download_url": "https://raw.githubusercontent.com/acme/repo/main/skills/review/SKILL.md",
        },
        {"type": "dir", "name": "scripts", "path": "skills/review/scripts"},
    ]
    scripts = [
        {
            "type": "file",
            "name": "check.py",
            "path": "skills/review/scripts/check.py",
            "download_url": "https://raw.githubusercontent.com/acme/repo/main/skills/review/scripts/check.py",
        }
    ]
    client = Client(
        Response(status=404),
        json_response(listing),
        Response(skill_bytes("review")),
        json_response(scripts),
        Response(b"print('ok')\n"),
    )

    result = await installer(tmp_path, client).install(
        "https://github.com/acme/repo/tree/main/skills/review"
    )

    root = tmp_path / ".ycode" / "skills" / "review"
    assert result.snapshot is not None
    assert (root / "scripts" / "check.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert "ref=main%2Fskills" in client.urls[0]
    assert "ref=main" in client.urls[1]


@pytest.mark.asyncio
async def test_installs_skills_sh_page_by_unique_github_skill_directory(
    tmp_path: Path,
) -> None:
    tree = {
        "tree": [
            {"type": "blob", "path": "skills/frontend-design/SKILL.md"},
            {"type": "blob", "path": "skills/other/SKILL.md"},
        ]
    }
    listing = [
        {
            "type": "file",
            "name": "SKILL.md",
            "path": "skills/frontend-design/SKILL.md",
            "download_url": "https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md",
        },
        {
            "type": "file",
            "name": "guide.md",
            "path": "skills/frontend-design/guide.md",
            "download_url": "https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/guide.md",
        },
    ]
    client = Client(
        json_response(tree),
        json_response(listing),
        Response(skill_bytes("frontend-design")),
        Response(b"guide"),
    )

    result = await installer(tmp_path, client).install(
        "https://www.skills.sh/anthropics/skills/frontend-design"
    )

    root = tmp_path / ".ycode" / "skills" / "frontend-design"
    assert result.snapshot is not None
    assert (root / "guide.md").read_bytes() == b"guide"
    assert client.urls[0].startswith("https://api.github.com/repos/anthropics/skills/")


@pytest.mark.asyncio
async def test_rejects_ambiguous_skills_sh_and_github_links(tmp_path: Path) -> None:
    ambiguous = {
        "tree": [
            {"type": "blob", "path": "one/review/SKILL.md"},
            {"type": "blob", "path": "two/review/SKILL.md"},
        ]
    }
    with pytest.raises(SkillInstallError, match="唯一"):
        await installer(tmp_path, Client(json_response(ambiguous))).install(
            "https://skills.sh/acme/repo/review"
        )

    listing = [{"type": "symlink", "name": "linked", "path": "skills/review/linked"}]
    with pytest.raises(SkillInstallError, match="symlink"):
        await installer(
            tmp_path,
            Client(Response(status=404), json_response(listing)),
        ).install("https://github.com/acme/repo/tree/main/skills/review")
