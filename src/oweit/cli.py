import argparse

from oweit import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oweit", description="Find promises you made; get reminded gently."
    )
    p.add_argument("--version", action="version", version=f"oweit {__version__}")
    p.add_subparsers(dest="cmd")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        build_parser().print_help()
    return 0
