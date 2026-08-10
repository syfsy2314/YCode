import io
import zipfile
from pathlib import Path

import httpx
import pytest

from ycode.skills import SkillLoader, SkillValidationEnvironment
from ycode.skills.installer import SkillInstaller


def _skill(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: {name} skill\n---\nDo it.\n".encode()


def _zip_skill(name: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(f"{name}/SKILL.md", _skill(name))
    return output.getvalue()


@pytest.mark.asyncio
async def test_four_https_source_types_install_without_external_network(
    tmp_path: Path,
) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://downloads.example/direct.zip":
            return httpx.Response(200, content=_zip_skill("direct"))
        if url == "https://raw.example/raw-one/SKILL.md":
            return httpx.Response(200, content=_skill("raw-one"))
        if "/git/trees/HEAD?recursive=1" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "type": "blob",
                            "path": "skills/frontend-design/SKILL.md",
                        }
                    ]
                },
            )
        if url.endswith("/contents/skills/frontend-design"):
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "file",
                        "name": "SKILL.md",
                        "path": "skills/frontend-design/SKILL.md",
                        "download_url": "https://raw.example/frontend-design/SKILL.md",
                    },
                    {
                        "type": "file",
                        "name": "guide.md",
                        "path": "skills/frontend-design/guide.md",
                        "download_url": "https://raw.example/frontend-design/guide.md",
                    },
                ],
            )
        if url == "https://raw.example/frontend-design/SKILL.md":
            return httpx.Response(200, content=_skill("frontend-design"))
        if url == "https://raw.example/frontend-design/guide.md":
            return httpx.Response(200, content=b"guide")
        if "contents/review" in url and "ref=main%2Fskills" in url:
            return httpx.Response(404)
        if "contents/skills/review" in url and "ref=main" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "file",
                        "name": "SKILL.md",
                        "path": "skills/review/SKILL.md",
                        "download_url": "https://raw.example/review/SKILL.md",
                    }
                ],
            )
        if url == "https://raw.example/review/SKILL.md":
            return httpx.Response(200, content=_skill("review"))
        return httpx.Response(404)

    refreshes = []

    async def refresh() -> None:
        refreshes.append(True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        installer = SkillInstaller(
            tmp_path,
            client,
            SkillLoader(),
            SkillValidationEnvironment(frozenset(), frozenset(), frozenset()),
            refresh,
        )
        await installer.install("https://downloads.example/direct.zip")
        await installer.install("https://raw.example/raw-one/SKILL.md")
        await installer.install("https://skills.sh/anthropics/skills/frontend-design")
        await installer.install("https://github.com/acme/repo/tree/main/skills/review")

    skills_root = tmp_path / ".ycode" / "skills"
    assert {path.name for path in skills_root.iterdir()} == {
        "direct",
        "raw-one",
        "frontend-design",
        "review",
    }
    assert (skills_root / "frontend-design" / "guide.md").read_bytes() == b"guide"
    assert [path.name for path in (skills_root / "raw-one").iterdir()] == ["SKILL.md"]
    assert refreshes == [True, True, True, True]
