"""Command-line interface for TigerMemory protocol validation."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TigerMemory protocol schema tools")
    parser.add_argument("--version", action="store_true", help="print package version")
    args = parser.parse_args(argv)
    if args.version:
        from tigermemory_protocols import __version__

        print(__version__)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
