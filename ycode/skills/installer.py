"""从公开 HTTPS 来源原子安装单个项目 Skill。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote, urlencode, urljoin, urlsplit

import yaml

from ycode.skills.loader import SkillLoader
from ycode.skills.models import SkillCatalogEntry, SkillValidationEnvironment

_MAX_BYTES = 30 * 1024 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class HttpResponse(Protocol):
    status_code: int
    headers: object

    def aiter_bytes(self): ...

    async def aclose(self) -> None: ...


class HttpClient(Protocol):
    async def get(self, url: str, *, follow_redirects: bool = False) -> HttpResponse: ...


class SkillInstallError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _DownloadBudget:
    used: int = 0

    def add(self, size: int) -> None:
        self.used += size
        if self.used > _MAX_BYTES:
            raise SkillInstallError("download_too_large", "Skill 来源内容累计超过 30 MB。")


class SkillInstaller:
    def __init__(
        self,
        project_root: Path,
        client: HttpClient,
        loader: SkillLoader,
        environment: SkillValidationEnvironment,
        refresh: Callable[[], Awaitable[None] | None] | None = None,
    ) -> None:
        self._project_root = project_root.resolve(strict=True)
        self._skills_root = self._project_root / ".ycode" / "skills"
        self._client = client
        self._loader = loader
        self._environment = environment
        self._refresh = refresh

    async def install(self, source_url: str) -> SkillCatalogEntry:
        self._skills_root.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=".install-", dir=self._skills_root))
        target: Path | None = None
        try:
            content_root = temp_root / "content"
            content_root.mkdir()
            top_name = await self._prepare_source(
                source_url,
                content_root,
                _DownloadBudget(),
            )
            source = content_root / top_name / "SKILL.md"
            declared_name = _declared_name(source)
            if declared_name != top_name:
                raise SkillInstallError(
                    "skill_name_mismatch",
                    "来源目录名与 Skill name 不一致。",
                )
            target = self._skills_root / declared_name
            if target.exists():
                raise SkillInstallError("skill_exists", f"Skill 已存在：{declared_name}")
            os.replace(source.parent, target)
            installed = self._loader.load(target / "SKILL.md", self._environment)
            try:
                if self._refresh is not None:
                    result = self._refresh()
                    if result is not None:
                        await result
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return installed
        except asyncio.CancelledError:
            if target is not None:
                shutil.rmtree(target, ignore_errors=True)
            raise
        except SkillInstallError:
            raise
        except (
            OSError,
            UnicodeError,
            zipfile.BadZipFile,
            yaml.YAMLError,
            json.JSONDecodeError,
        ) as error:
            raise SkillInstallError("skill_install_failed", "Skill 安装失败。") from error
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    async def _prepare_source(
        self,
        source_url: str,
        destination: Path,
        budget: _DownloadBudget,
    ) -> str:
        parsed = urlsplit(source_url)
        host = (parsed.hostname or "").casefold()
        parts = tuple(part for part in parsed.path.split("/") if part)
        if host in {"skills.sh", "www.skills.sh"}:
            if len(parts) != 3:
                raise SkillInstallError(
                    "skills_sh_url_invalid",
                    "skills.sh URL 必须指向单个 Skill 详情页。",
                )
            return await self._prepare_skills_sh(*parts, destination, budget)
        if host == "github.com" and len(parts) >= 6 and parts[2] == "tree":
            owner, repo = parts[:2]
            ref, path, listing = await self._resolve_github_tree(
                owner,
                repo,
                parts[3:],
                budget,
            )
            top_name = PurePosixPath(path).name
            await self._download_github_listing(
                owner,
                repo,
                path,
                ref,
                listing,
                destination / top_name,
                budget,
            )
            return top_name
        if parts and parts[-1].casefold() == "skill.md":
            data = await self._fetch_bytes(source_url, budget)
            name = _declared_name_bytes(data)
            skill_root = destination / name
            skill_root.mkdir()
            (skill_root / "SKILL.md").write_bytes(data)
            return name
        archive = destination.parent / "skill.zip"
        archive.write_bytes(await self._fetch_bytes(source_url, budget))
        return self._extract(archive, destination)

    async def _prepare_skills_sh(
        self,
        owner: str,
        repo: str,
        slug: str,
        destination: Path,
        budget: _DownloadBudget,
    ) -> str:
        tree_url = (
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/git/trees/HEAD?recursive=1"
        )
        tree = await self._fetch_json(tree_url, budget)
        items = tree.get("tree") if isinstance(tree, dict) else None
        if not isinstance(items, list):
            raise SkillInstallError("github_tree_invalid", "GitHub 仓库目录响应无效。")
        matches = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            raw_path = item.get("path")
            if not isinstance(raw_path, str):
                continue
            path = PurePosixPath(raw_path)
            if path.name.casefold() != "skill.md" or not path.parent.parts:
                continue
            if path.parent.name.casefold() == slug.casefold():
                matches.append(path.parent.as_posix())
        unique = tuple(sorted(set(matches)))
        if len(unique) != 1:
            raise SkillInstallError(
                "skills_sh_skill_ambiguous",
                "skills.sh 来源无法定位唯一 Skill 目录。",
            )
        path = unique[0]
        listing = await self._github_contents(owner, repo, path, None, budget)
        await self._download_github_listing(
            owner,
            repo,
            path,
            None,
            listing,
            destination / slug,
            budget,
        )
        return slug

    async def _resolve_github_tree(
        self,
        owner: str,
        repo: str,
        tail: tuple[str, ...],
        budget: _DownloadBudget,
    ) -> tuple[str, str, list[object]]:
        if len(tail) < 2:
            raise SkillInstallError("github_tree_url_invalid", "GitHub tree URL 缺少目录路径。")
        for index in range(len(tail) - 1, 0, -1):
            ref = "/".join(tail[:index])
            path = "/".join(tail[index:])
            listing = await self._github_contents(
                owner,
                repo,
                path,
                ref,
                budget,
                missing_ok=True,
            )
            if listing is not None:
                return ref, path, listing
        raise SkillInstallError(
            "github_tree_not_found",
            "GitHub tree URL 无法解析为公开 Skill 目录。",
        )

    async def _github_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None,
        budget: _DownloadBudget,
        *,
        missing_ok: bool = False,
    ) -> list[object] | None:
        url = (
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/"
            f"{quote(path, safe='/')}"
        )
        if ref is not None:
            url += "?" + urlencode({"ref": ref})
        status, data = await self._fetch_bytes_with_status(
            url,
            budget,
            allowed_statuses=frozenset({404}) if missing_ok else frozenset(),
        )
        if status == 404:
            return None
        value = json.loads(data)
        if not isinstance(value, list):
            raise SkillInstallError("github_directory_invalid", "GitHub URL 不是目录。")
        return value

    async def _download_github_listing(
        self,
        owner: str,
        repo: str,
        source_path: str,
        ref: str | None,
        listing: list[object],
        destination: Path,
        budget: _DownloadBudget,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        for raw_item in listing:
            if not isinstance(raw_item, dict):
                raise SkillInstallError("github_directory_invalid", "GitHub 目录条目无效。")
            item_type = raw_item.get("type")
            name = raw_item.get("name")
            item_path = raw_item.get("path")
            if not isinstance(name, str) or not isinstance(item_path, str):
                raise SkillInstallError("github_directory_invalid", "GitHub 目录条目无效。")
            relative = _safe_source_name(name)
            output = destination.joinpath(*relative.parts)
            if item_type == "dir":
                nested = await self._github_contents(owner, repo, item_path, ref, budget)
                assert nested is not None
                await self._download_github_listing(
                    owner,
                    repo,
                    item_path,
                    ref,
                    nested,
                    output,
                    budget,
                )
                continue
            if item_type != "file":
                raise SkillInstallError(
                    "github_link_unsupported",
                    "GitHub Skill 目录不允许 symlink 或 submodule。",
                )
            download_url = raw_item.get("download_url")
            if not isinstance(download_url, str) or not download_url:
                raise SkillInstallError(
                    "github_download_url_missing",
                    "GitHub 文件缺少公开下载地址。",
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(await self._fetch_bytes(download_url, budget))

    async def _fetch_json(self, url: str, budget: _DownloadBudget) -> object:
        return json.loads(await self._fetch_bytes(url, budget))

    async def _fetch_bytes(self, url: str, budget: _DownloadBudget) -> bytes:
        _, data = await self._fetch_bytes_with_status(url, budget)
        return data

    async def _fetch_bytes_with_status(
        self,
        url: str,
        budget: _DownloadBudget,
        *,
        allowed_statuses: frozenset[int] = frozenset(),
    ) -> tuple[int, bytes]:
        current = url
        for _ in range(6):
            _validate_public_https(current)
            response = await self._client.get(current, follow_redirects=False)
            try:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")  # type: ignore[union-attr]
                    if not isinstance(location, str) or not location:
                        raise SkillInstallError(
                            "redirect_invalid",
                            "下载重定向缺少目标地址。",
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code in allowed_statuses:
                    return response.status_code, b""
                if response.status_code < 200 or response.status_code >= 300:
                    raise SkillInstallError(
                        "download_http_error",
                        f"Skill 来源请求失败：HTTP {response.status_code}",
                    )
                chunks = []
                async for chunk in response.aiter_bytes():
                    budget.add(len(chunk))
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)
            finally:
                await response.aclose()
        raise SkillInstallError("redirect_limit", "Skill 来源重定向次数过多。")

    def _extract(self, archive: Path, destination: Path) -> str:
        total_declared = 0
        total_written = 0
        top_names: set[str] = set()
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            for info in infos:
                path = _safe_zip_path(info)
                top_names.add(path.parts[0])
                total_declared += info.file_size
                if total_declared > _MAX_BYTES:
                    raise SkillInstallError(
                        "archive_too_large",
                        "Skill 解压声明大小超过 30 MB。",
                    )
            if len(top_names) != 1:
                raise SkillInstallError(
                    "archive_top_level",
                    "ZIP 必须只包含一个顶层 Skill 目录。",
                )
            top_name = next(iter(top_names))
            if not any(
                PurePosixPath(info.filename.replace("\\", "/"))
                == PurePosixPath(top_name) / "SKILL.md"
                for info in infos
            ):
                raise SkillInstallError("skill_file_missing", "Skill 缺少 SKILL.md。")
            for info in infos:
                path = _safe_zip_path(info)
                output = destination.joinpath(*path.parts)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, output.open("wb") as target:
                    while chunk := source.read(64 * 1024):
                        total_written += len(chunk)
                        if total_written > _MAX_BYTES:
                            raise SkillInstallError(
                                "archive_too_large",
                                "Skill 实际解压大小超过 30 MB。",
                            )
                        target.write(chunk)
        return top_name


def _validate_public_https(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SkillInstallError("url_invalid", "只支持 HTTPS URL。")
    if parsed.username is not None or parsed.password is not None:
        raise SkillInstallError("url_credentials", "URL 不允许包含凭据。")
    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise SkillInstallError("url_not_public", "URL 必须指向公开地址。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise SkillInstallError("url_not_public", "URL 必须指向公开地址。")


def _safe_zip_path(info: zipfile.ZipInfo) -> PurePosixPath:
    normalized = info.filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise SkillInstallError("archive_path_invalid", "ZIP 包含不安全路径。")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or (info.external_attr & 0x0400):
        raise SkillInstallError("archive_link", "ZIP 不允许包含链接或重解析点。")
    return path


def _safe_source_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {"", ".", ".."}:
        raise SkillInstallError("source_path_invalid", "来源包含不安全路径。")
    return path


def _declared_name(source: Path) -> str:
    return _declared_name_bytes(source.read_bytes())


def _declared_name_bytes(content: bytes) -> str:
    text = content.decode("utf-8")
    if not text.startswith("---\n"):
        raise SkillInstallError("frontmatter_missing", "Skill 缺少 YAML frontmatter。")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SkillInstallError("frontmatter_invalid", "Skill frontmatter 未闭合。")
    data = yaml.safe_load(text[4:end])
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name) or "--" in name:
        raise SkillInstallError("skill_name_invalid", "Skill name 无效。")
    return name


__all__ = ["SkillInstallError", "SkillInstaller"]
