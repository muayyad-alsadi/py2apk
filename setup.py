# pyright: reportMissingModuleSource=false

from collections.abc import Sequence
from pathlib import Path

from setuptools import setup


ROOT = Path(__file__).parent
TEMPLATES = ROOT / "templates"


def template_data_files() -> list[tuple[str, Sequence[str]]]:
    grouped: dict[str, list[str]] = {}
    for source in sorted(TEMPLATES.rglob("*")):
        if (
            not source.is_file()
            or source.name == ".gitignore"
            or "__pycache__" in source.parts
            or source.suffix in {".pyc", ".pyo"}
        ):
            continue

        relative_directory = source.parent.relative_to(TEMPLATES)
        destination = Path("share/py2apk/templates") / relative_directory
        relative_source = source.relative_to(ROOT).as_posix()
        grouped.setdefault(str(destination), []).append(relative_source)

    return [
        (destination, tuple(files))
        for destination, files in sorted(grouped.items())
    ]


_ = setup(data_files=template_data_files())
