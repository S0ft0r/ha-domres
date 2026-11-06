#!/usr/bin/env python3
"""Build retained delta archives and an installable integration ZIP."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INITIAL_VERSION = "0.1.0"
PUBLIC_ROOT_FILES = (
    ".gitignore",
    "README.md",
    "CHANGELOG.md",
    "hacs.json",
    "LICENSE",
    "LICENSE.txt",
)
PUBLIC_DIRS = (
    "custom_components/domres",
    "scripts",
    "tests",
    ".github/workflows",
)
STATE_PATH = Path("release/.build_state.json")
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "release",
    "internal",
    "internal_docs",
    "research",
    "private",
    "decision_log",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}
EXCLUDED_NAMES = {".env", ".netrc", ".ds_store", "thumbs.db"}


class BuildError(RuntimeError):
    """Raised when a release cannot be built or verified safely."""


def _is_excluded(relative: Path) -> bool:
    parts = [part.casefold() for part in relative.parts]
    if any(part in EXCLUDED_PARTS for part in parts):
        return True
    if any(
        any(token in part for token in ("internal", "research", "decision_log", "private_notes"))
        for part in parts
    ):
        return True
    name = relative.name.casefold()
    return name in EXCLUDED_NAMES or relative.suffix.casefold() in EXCLUDED_SUFFIXES


def public_files(root: Path = ROOT) -> list[Path]:
    """Return the explicit public-file allowlist, excluding private/build data."""
    selected: set[Path] = set()
    for name in PUBLIC_ROOT_FILES:
        path = root / name
        if path.is_file() and not _is_excluded(Path(name)):
            selected.add(path)
    for dirname in PUBLIC_DIRS:
        base = root / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if not _is_excluded(relative):
                selected.add(path)
    return sorted(selected, key=lambda item: item.relative_to(root).as_posix())


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / "custom_components/domres/manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise BuildError(f"Cannot read integration manifest: {err}") from err
    if not isinstance(value, dict):
        raise BuildError("Integration manifest must be a JSON object")
    return value


def _version_number(version: str) -> int:
    match = re.fullmatch(r"0\.1\.(\d+)", version)
    if not match:
        raise BuildError(f"Version must match 0.1.N; got {version!r}")
    return int(match.group(1))


def next_version(version: str) -> str:
    return f"0.1.{_version_number(version) + 1}"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _semantic_manifest_bytes(data: bytes) -> bytes:
    """Normalize only the patch number so a version bump is not a code change."""
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise BuildError(f"Invalid manifest JSON: {err}") from err
    if not isinstance(manifest, dict):
        raise BuildError("Integration manifest must be a JSON object")
    manifest["version"] = "0.1.<N>"
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def file_hashes(root: Path = ROOT, *, semantic: bool = False) -> dict[str, str]:
    """Hash all included files; optionally normalize the integration patch version."""
    result: dict[str, str] = {}
    for path in public_files(root):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        if semantic and relative == "custom_components/domres/manifest.json":
            content = _semantic_manifest_bytes(content)
        result[relative] = _hash_bytes(content)
    return result


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = root / "custom_components/domres/manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_state(root: Path) -> dict[str, Any] | None:
    path = root / STATE_PATH
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise BuildError(f"Cannot read build state: {err}") from err
    if not isinstance(state, dict):
        raise BuildError("Build state must be a JSON object")
    return state


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_delta(
    root: Path = ROOT,
    *,
    bootstrap_current_version: bool = False,
) -> tuple[Path, list[str], list[str]]:
    """Create the next retained delta archive and update build state.

    The first local build normally starts at 0.1.0. CI may bootstrap the first
    baseline at the manifest's current 0.1.N version when no release state exists.
    """
    root = root.resolve()
    manifest = _read_manifest(root)
    current_version = str(manifest.get("version", ""))
    _version_number(current_version)
    state = _load_state(root)
    release_dir = root / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    if state is None:
        if current_version != INITIAL_VERSION and not bootstrap_current_version:
            raise BuildError(
                f"The first build must start at {INITIAL_VERSION}; got {current_version}"
            )
        previous_version = None
        previous_hashes: dict[str, str] = {}
        previous_archives: list[str] = []
    else:
        previous_version = str(state.get("last_version", ""))
        _version_number(previous_version)
        previous_hashes = state.get("files", {})
        if not isinstance(previous_hashes, dict):
            raise BuildError("Build state has an invalid 'files' map")
        previous_archives = state.get("archives", [])
        if not isinstance(previous_archives, list):
            raise BuildError("Build state has an invalid 'archives' list")
        current_hashes_before_bump = file_hashes(root)
        if current_hashes_before_bump == previous_hashes:
            raise BuildError("No changed files since the previous build")
        expected_version = next_version(previous_version)
        if current_version == previous_version:
            manifest["version"] = expected_version
            _write_manifest(root, manifest)
            current_version = expected_version
        elif current_version != expected_version:
            raise BuildError(
                f"Version must be incremented exactly once: expected {expected_version}, "
                f"found {current_version}"
            )

    current_hashes = file_hashes(root)
    changed_files = sorted(
        name
        for name, digest in current_hashes.items()
        if previous_hashes.get(name) != digest
    )
    deleted_files = sorted(set(previous_hashes) - set(current_hashes))
    if not changed_files and not deleted_files:
        raise BuildError("No changed files to archive")

    archive_name = f"domres_{current_version}_changed_files.zip"
    archive_path = release_dir / archive_name
    if archive_path.exists():
        raise BuildError(f"Archive already exists and will not be overwritten: {archive_path}")
    temporary_archive = archive_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(
        temporary_archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in changed_files:
            archive.write(root / relative, arcname=relative)
        if deleted_files:
            archive.writestr("DELETED_FILES.txt", "\n".join(deleted_files) + "\n")
    os.replace(temporary_archive, archive_path)

    archives = [str(name) for name in previous_archives]
    archives.append(archive_name)
    new_state = {
        "schema": 1,
        "last_version": current_version,
        "files": current_hashes,
        "semantic_files": file_hashes(root, semantic=True),
        "archives": archives,
        "built_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    _save_state(root, new_state)
    return archive_path, changed_files, deleted_files


def verify_current_state(root: Path = ROOT) -> None:
    """Fail if source changes are unarchived or a retained archive is missing."""
    root = root.resolve()
    state = _load_state(root)
    if state is None:
        raise BuildError("No release build state found; build the first archive")
    manifest = _read_manifest(root)
    version = str(manifest.get("version", ""))
    _version_number(version)
    if state.get("last_version") != version:
        raise BuildError(
            f"Manifest version {version} does not match build state {state.get('last_version')}"
        )
    expected = state.get("files")
    if not isinstance(expected, dict):
        raise BuildError("Build state has an invalid 'files' map")
    actual = file_hashes(root)
    if actual != expected:
        changed = sorted(
            name for name in set(actual) | set(expected) if actual.get(name) != expected.get(name)
        )
        raise BuildError("Unarchived source changes: " + ", ".join(changed))
    archives = state.get("archives", [])
    if not isinstance(archives, list) or not archives:
        raise BuildError("Build state does not list retained archives")
    for archive_name in archives:
        archive_path = root / "release" / str(archive_name)
        if not archive_path.is_file():
            raise BuildError(f"Retained build archive is missing: {archive_path}")


def build_component_zip(root: Path = ROOT, output: Path | str = "domres.zip") -> Path:
    """Create a standalone install ZIP with a top-level domres/ directory."""
    root = root.resolve()
    component = root / "custom_components/domres"
    if not component.is_dir():
        raise BuildError(f"Integration directory not found: {component}")
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(component.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(component)
            if _is_excluded(relative):
                continue
            archive.write(path, arcname=(PurePosixPath("domres") / relative.as_posix()).as_posix())
    os.replace(temporary, output_path)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that all current public source files match the last build",
    )
    parser.add_argument(
        "--component-zip",
        metavar="PATH",
        help="create an installable ZIP containing only custom_components/domres",
    )
    parser.add_argument(
        "--bootstrap-current-version",
        action="store_true",
        help="allow the first archive to use the current 0.1.N version (CI bootstrap only)",
    )
    args = parser.parse_args(argv)
    try:
        if args.check:
            verify_current_state(ROOT)
            print("Release state is clean; retained archives are present.")
        elif args.component_zip:
            output = build_component_zip(ROOT, args.component_zip)
            print(f"Created integration ZIP: {output}")
        else:
            path, changed, deleted = build_delta(
                ROOT,
                bootstrap_current_version=args.bootstrap_current_version,
            )
            print(f"Created {path.relative_to(ROOT)}")
            print(f"Changed files ({len(changed)}):")
            for name in changed:
                print(f"  + {name}")
            for name in deleted:
                print(f"  - {name}")
    except BuildError as err:
        print(f"Release error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
