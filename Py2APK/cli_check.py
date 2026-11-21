"""Typecheck a project with ty after refreshing imported stubs."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from Py2APK.cli_stubs import write_stubs

INSTALL_HINT = (
    "ty is not installed.\n"
    "python3 -m pip install ty\n"
    "https://docs.astral.sh/ty/installation/\n"
)


def check_project(project_root: Path) -> int:
    """Refresh stubs, then run ``ty check``. Return ty's status, or 1 if it is missing."""
    status = write_stubs(project_root)
    if status != 0:
        return status
    executable = shutil.which("ty")
    if executable is None:
        print(INSTALL_HINT, file=sys.stderr, end="")
        return 1
    completed = subprocess.run(
        [
            executable,
            "check",
            "-q",
            "--extra-search-path",
            "typings",
            "--project",
            str(project_root),
            "src",
        ],
        cwd=project_root,
        check=False,
    )
    return int(completed.returncode)
