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
import subprocess
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


def _is_public_relative(name: str) -> bool:
    """Check a repository-relative path against the public release allowlist."""
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or _is_excluded(relative):
        return False
    normalized = PurePosixPath(name).as_posix()
    return normalized in PUBLIC_ROOT_FILES or any(
        normalized.startswith(f"{directory}/") for directory in PUBLIC_DIRS
    )


def _git_changed_paths(root: Path, base_tag: str, diff_filter: str) -> set[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                f"--diff-filter={diff_filter}",
                f"{base_tag}..HEAD",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as err:
        raise BuildError(f"Cannot calculate changes from release tag {base_tag}: {err}") from err
    return {name for name in result.stdout.split("\0") if name}


def _archive_matches_delta(
    root: Path,
    archive_path: Path,
    changed_files: list[str],
    deleted_files: list[str],
) -> bool:
    expected_names = set(changed_files)
    if deleted_files:
        expected_names.add("DELETED_FILES.txt")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != expected_names:
                return False
            if archive.testzip() is not None:
                return False
            for relative in changed_files:
                if archive.read(relative) != (root / relative).read_bytes():
                    return False
            if deleted_files:
                expected_deleted = ("\n".join(deleted_files) + "\n").encode("utf-8")
                if archive.read("DELETED_FILES.txt") != expected_deleted:
                    return False
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return True


def _write_delta_archive(
    root: Path,
    current_version: str,
    changed_files: list[str],
    deleted_files: list[str],
    previous_archives: list[str],
    *,
    reuse_matching_archive: bool = False,
    replace_mismatched_archive: bool = False,
) -> tuple[Path, list[str], list[str]]:
    if not changed_files and not deleted_files:
        raise BuildError("No changed files to archive")

    archive_name = f"domres_{current_version}_changed_files.zip"
    archive_path = root / "release" / archive_name
    write_archive = True
    if archive_path.exists():
        matches_delta = reuse_matching_archive and _archive_matches_delta(
            root,
            archive_path,
            changed_files,
            deleted_files,
        )
        if matches_delta:
            write_archive = False
        elif not replace_mismatched_archive:
            raise BuildError(f"Archive already exists and will not be overwritten: {archive_path}")
    if write_archive:
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

    archives = [*previous_archives, archive_name]
    new_state = {
        "schema": 1,
        "last_version": current_version,
        "files": file_hashes(root),
        "semantic_files": file_hashes(root, semantic=True),
        "archives": archives,
        "built_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    _save_state(root, new_state)
    return archive_path, changed_files, deleted_files


def _git_tag_exists(root: Path, tag: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", tag],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as err:
        raise BuildError(f"Cannot inspect repository release tags: {err}") from err
    return tag in result.stdout.splitlines()


def build_delta_from_git(root: Path, base_tag: str) -> tuple[Path, list[str], list[str]]:
    """Build a true delta from the latest published tag when build state is absent."""
    root = root.resolve()
    tag_match = re.fullmatch(r"v0\.1\.(\d+)", base_tag)
    if not tag_match:
        raise BuildError(f"Base release tag must match v0.1.N; got {base_tag!r}")
    base_version_number = int(tag_match.group(1))
    if _load_state(root) is not None:
        raise BuildError("Git-tag delta mode is only for checkouts without a build state")

    current_version = str(_read_manifest(root).get("version", ""))
    current_version_number = _version_number(current_version)
    if base_version_number >= current_version_number:
        raise BuildError(f"Base tag {base_tag} must be older than version {current_version}")
    if _git_tag_exists(root, f"v{current_version}"):
        raise BuildError(f"Version {current_version} is already tagged; refusing to rebuild its release archive")
    current_files = {
        path.relative_to(root).as_posix()
        for path in public_files(root)
    }
    changed_paths = _git_changed_paths(root, base_tag, "ACMRT")
    deleted_paths = _git_changed_paths(root, base_tag, "D")
    changed_files = sorted(
        name for name in changed_paths if name in current_files and _is_public_relative(name)
    )
    deleted_files = sorted(
        name
        for name in deleted_paths
        if name not in current_files and _is_public_relative(name)
    )
    if not changed_files and not deleted_files:
        raise BuildError(f"No public source changes since {base_tag}")

    release_dir = root / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    return _write_delta_archive(
        root,
        current_version,
        changed_files,
        deleted_files,
        previous_archives=[],
        reuse_matching_archive=True,
        replace_mismatched_archive=True,
    )


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
    parser.add_argument(
        "--from-git-tag",
        metavar="TAG",
        help="build a release delta from a previous v0.1.N tag when build state is absent",
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
            if args.from_git_tag:
                path, changed, deleted = build_delta_from_git(ROOT, args.from_git_tag)
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
