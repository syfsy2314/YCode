"""从工作目录向上发现 YCode 配置。"""

from pathlib import Path

from ycode.errors import ConfigError

CONFIG_RELATIVE_PATH = Path(".ycode") / "config.yaml"


def resolve_project_root(config_path: str | Path) -> Path:
    """根据实际使用的配置文件路径确定项目根目录。"""

    path = Path(config_path).expanduser().resolve()
    if path.name == "config.yaml" and path.parent.name == ".ycode":
        return path.parent.parent
    return path.parent


def discover_config(
    explicit_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
) -> Path:
    """返回显式配置或从起始目录向上找到的最近配置。"""

    if explicit_path is not None:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"指定的配置文件不存在：{path}")
        return path

    start = Path.cwd() if start_dir is None else Path(start_dir)
    start = start.expanduser().resolve()
    if not start.is_dir():
        raise ConfigError(f"配置搜索起点不是目录：{start}")

    for directory in (start, *start.parents):
        candidate = directory / CONFIG_RELATIVE_PATH
        if candidate.is_file():
            return candidate

    raise ConfigError(f"从 {start} 逐级向上找不到 {CONFIG_RELATIVE_PATH.as_posix()}")
