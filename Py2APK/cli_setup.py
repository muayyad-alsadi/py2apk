# pyright: reportMissingModuleSource=false

"""Set up the external JARs and the local signing key used by Py2APK."""

from __future__ import annotations

import os
import secrets
import shutil
import site
import subprocess
import sys
import sysconfig
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.cli_fetch_jars import _fetch
from Py2APK.cli_fetch_platform import cache_root
from Py2APK.downloader import DownloadError, download_files
from Py2APK.maven_resolve import repository_list


GOOGLE_MAVEN = "https://dl.google.com/dl/android/maven2"
KEY_ALIAS = "py2apk"
KEY_DNAME = "CN=Py2APK Release, OU=Mobile, O=Py2APK, L=Unspecified, ST=Unspecified, C=US"
KEY_VALIDITY_DAYS = "10000"

# Maven coordinates. Apktool 3.0.3 and uber-apk-signer 1.3.0 are not published
# to Maven Central, so those two stay as release URLs below.
MAVEN_JARS = (
    (
        "Jasmin",
        "3.0.3",
        None,
        "ca/mcgill/sable/jasmin/3.0.3/jasmin-3.0.3-jar-with-dependencies.jar",
    ),
    (
        "R8/D8",
        "9.4.24",
        GOOGLE_MAVEN,
        "com/android/tools/r8/9.4.24/r8-9.4.24.jar",
    ),
)
DIRECT_JARS = (
    (
        "apktool",
        "3.0.3",
        "https://github.com/iBotPeaches/Apktool/releases/download/"
        + "v3.0.3/apktool_3.0.3.jar",
        "apktool_3.0.3.jar",
    ),
    (
        "uber-apk-signer",
        "1.3.0",
        "https://github.com/patrickfav/uber-apk-signer/releases/download/"
        + "v1.3.0/uber-apk-signer-1.3.0.jar",
        "uber-apk-signer-1.3.0.jar",
    ),
)


def asset_directories() -> list[Path]:
    package_parent = Path(__file__).resolve().parent.parent
    candidates = [
        package_parent,
        package_parent / "share/py2apk",
        Path(sysconfig.get_path("data")) / "share/py2apk",
    ]
    if site.USER_BASE:
        candidates.append(Path(site.USER_BASE) / "share/py2apk")

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def default_jars_directory() -> Path:
    for assets in asset_directories():
        if (
            assets.is_dir()
            and (assets / "templates").is_dir()
            and os.access(assets, os.W_OK)
        ):
            return assets / "jars"
    raise RuntimeError(
        "could not find a writable Py2APK assets directory containing templates/"
    )


def keystore_directory() -> Path:
    """Return the per-user data directory that holds the signing key."""
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "share"
    return base / "py2apk"


def ensure_keystore(directory: Path | None = None) -> Path:
    """Create ``default.keystore`` once, with a release-style RSA certificate."""
    root = directory if directory is not None else keystore_directory()
    root.mkdir(parents=True, exist_ok=True)
    keystore = root / "default.keystore"
    password_file = root / "default.keystore.pass"
    if keystore.is_file() and password_file.is_file():
        return keystore
    if keystore.is_file():
        raise RuntimeError(f"keystore exists without a password file: {password_file}")
    keytool = shutil.which("keytool")
    if keytool is None:
        raise RuntimeError("keytool was not found; install a JDK")
    password = secrets.token_urlsafe(18)
    completed = subprocess.run(
        [
            keytool,
            "-genkeypair",
            "-noprompt",
            "-alias",
            KEY_ALIAS,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            KEY_VALIDITY_DAYS,
            "-storetype",
            "PKCS12",
            "-keystore",
            str(keystore),
            "-storepass",
            password,
            "-keypass",
            password,
            "-dname",
            KEY_DNAME,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        keystore.unlink(missing_ok=True)
        detail = completed.stderr.strip() or completed.stdout.strip() or "keytool failed"
        raise RuntimeError(detail)
    _ = password_file.write_text(password + "\n", encoding="utf-8")
    os.chmod(keystore, 0o600)
    os.chmod(password_file, 0o600)
    return keystore


def download_jars(destination: Path | None = None) -> int:
    try:
        jars_dir = (
            default_jars_directory()
            if destination is None
            else destination.expanduser().resolve()
        )
        jars_dir.mkdir(parents=True, exist_ok=True)
        if not jars_dir.is_dir() or not os.access(jars_dir, os.W_OK):
            print(f"error: destination is not a writable directory: {jars_dir}", file=sys.stderr)
            return 1
        store = cache_root()
        cached = _cache_toolchain(store)
        for path in cached:
            _symlink(jars_dir / path.name, path)
        keystore = ensure_keystore()
    except (OSError, RuntimeError, DownloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"installed {len(cached)} JARs in {jars_dir}")
    print(f"cache: {store}")
    print(f"keystore: {keystore}")
    print(f"alias: {KEY_ALIAS}")
    return 0


def _cache_toolchain(store: Path) -> list[Path]:
    cached: list[Path] = []
    steps = len(MAVEN_JARS) + len(DIRECT_JARS)
    progress = tqdm(total=steps, desc="Downloading JARs", unit="jar")
    try:
        for name, version, extra, relative in MAVEN_JARS:
            progress.set_description(f"Downloading {name} {version}")
            repos = repository_list([extra] if extra else None)
            payload = _fetch(repos, relative, store)
            destination = store / "maven" / relative
            if payload is None or not destination.is_file():
                raise RuntimeError(f"could not download {relative}")
            cached.append(destination)
            progress.update()
        for name, version, url, filename in DIRECT_JARS:
            progress.set_description(f"Downloading {name} {version}")
            destination = store / "toolchain" / filename
            missing = download_files([(url, destination)], on_complete=progress.update)
            if missing or not destination.is_file():
                raise RuntimeError(f"could not download {url}")
            cached.append(destination)
    finally:
        progress.close()
    return cached


def _symlink(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target.resolve())
