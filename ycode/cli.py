"""YCode 命令行入口。"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from ycode.app import run_app
from ycode.errors import ConfigError, UIError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ycode", description="YCode 终端 AI 助手")
    parser.add_argument("--config", help="显式指定 YAML 配置文件")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run_app(args.config))
    except (ConfigError, UIError) as error:
        print(f"YCode: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0
