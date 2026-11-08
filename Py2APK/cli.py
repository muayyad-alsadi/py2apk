"""Command-line interface for Py2APK."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from Py2APK.cli_build import build_project
from Py2APK.cli_compile import compile_project
from Py2APK.cli_fetch_jars import fetch_jars
from Py2APK.cli_fetch_platform import main as fetch_platform_main
from Py2APK.cli_setup import download_jars
from Py2APK.cli_init import init_project


def _setup(args: argparse.Namespace) -> int:
    destination_arg = cast(str | None, args.destination)
    destination = None if destination_arg is None else Path(destination_arg)
    return download_jars(destination)


def _init(args: argparse.Namespace) -> int:
    icon = None if args.icon is None else str(args.icon)
    return init_project(
        template_name=str(args.template),
        icon=icon,
        package=str(args.package),
        name=str(args.name),
        destination=Path(str(args.directory)),
    )


def _fetch_jars(args: argparse.Namespace) -> int:
    repositories = list(args.repositories or [])
    dependency_file = None if args.dependencies is None else Path(args.dependencies)
    return fetch_jars(Path(args.directory).expanduser(), dependency_file, repositories)


def _fetch_platform(args: argparse.Namespace) -> int:
    return fetch_platform_main(Path(args.directory).expanduser())


def _compile(_args: argparse.Namespace) -> int:
    return compile_project()


def _build(args: argparse.Namespace) -> int:
    return build_project(Path(args.directory).expanduser())


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py2apk",
        description="Build Android APKs from Python source trees.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    setup_parser = subparsers.add_parser(
        "setup",
        help="set up the required toolchain JAR files",
    )
    _ = setup_parser.add_argument(
        "-d",
        "--dst",
        dest="destination",
        help="destination jars directory (default: detected package assets)",
    )
    setup_parser.set_defaults(handler=_setup)

    init_parser = subparsers.add_parser(
        "init",
        help="initialize a project from a template",
    )
    init_parser.add_argument(
        "--template",
        default="blank",
        help="project template (default: blank)",
    )
    init_parser.add_argument("--icon", help="path to the app icon")
    init_parser.add_argument(
        "--pkg",
        dest="package",
        required=True,
        help="Android package name",
    )
    init_parser.add_argument("--name", required=True, help="display name of the app")
    init_parser.add_argument("directory", help="directory to create")
    init_parser.set_defaults(handler=_init)

    compile_parser = subparsers.add_parser(
        "compile",
        help="find Python sources that need Jasmin output",
    )
    compile_parser.set_defaults(handler=_compile)

    build_parser = subparsers.add_parser(
        "build",
        help="build a project into an APK",
    )
    build_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="project directory (default: current directory)",
    )
    build_parser.set_defaults(handler=_build)

    fetch_jars_parser = subparsers.add_parser(
        "fetch_jars",
        help="fetch dependency jars and the Android platform jar",
    )
    fetch_jars_parser.add_argument(
        "-r",
        dest="dependencies",
        help="dependency file (default: deps.txt)",
    )
    fetch_jars_parser.add_argument(
        "-R",
        dest="repositories",
        action="append",
        help="extra Maven repository URL, tried before Central (repeatable)",
    )
    fetch_jars_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="project directory (default: current directory)",
    )
    fetch_jars_parser.set_defaults(handler=_fetch_jars)

    fetch_platform_parser = subparsers.add_parser(
        "fetch_platform",
        help="fetch the Android platform stub jar for targetSdkVersion",
    )
    fetch_platform_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="project directory (default: current directory)",
    )
    fetch_platform_parser.set_defaults(handler=_fetch_platform)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
