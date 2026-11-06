#!/usr/bin/env python3
"""Decide whether a validated main-branch commit should create a new release."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"0\.1\.(\d+)")
TAG_PATTERN = re.compile(r"v0\.1\.(\d+)")


def patch_number(version: str) -> int:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ValueError(f"DomRes version must match 0.1.N, got {version!r}")
    return int(match.group(1))


def should_release(version: str, tags: list[str]) -> tuple[bool, str]:
    """Return true only for a version newer than every existing DomRes release tag."""
    current_patch = patch_number(version)
    current_tag = f"v{version}"
    parsed_tags = [
        (tag, int(match.group(1)))
        for tag in tags
        if (match := TAG_PATTERN.fullmatch(tag))
    ]
    if any(tag == current_tag for tag, _ in parsed_tags):
        return False, f"Release {current_tag} already exists"
    if parsed_tags:
        latest_tag, latest_patch = max(parsed_tags, key=lambda item: item[1])
        if current_patch <= latest_patch:
            return False, f"{current_tag} is not newer than existing {latest_tag}"
    return True, f"{current_tag} is newer than existing DomRes release tags"


def main() -> int:
    try:
        manifest = json.loads(
            (ROOT / "custom_components/domres/manifest.json").read_text(encoding="utf-8")
        )
        version = str(manifest["version"])
        patch_number(version)
        result = subprocess.run(
            ["git", "tag", "--list", "v0.1.*"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        tags = result.stdout.splitlines()
        release, reason = should_release(version, tags)
    except (OSError, KeyError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as err:
        print(f"Release gate error: {err}", file=sys.stderr)
        return 1

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"should_release={'true' if release else 'false'}\n")
            output.write(f"version={version}\n")
            output.write(f"tag=v{version}\n")
    print(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
