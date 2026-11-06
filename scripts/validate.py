#!/usr/bin/env python3
"""Local static validator for the DomRes Home Assistant integration."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import struct
import sys
import zipfile

try:
    from .build_release import BuildError, _is_excluded, verify_current_state
except ImportError:
    from build_release import BuildError, _is_excluded, verify_current_state

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/domres"
LOGIN_HINT = "Введите в качестве логина номер телефона или email, который использовался при регистрации"
ALLOWED_HACS_KEYS = {
    "name",
    "hacs",
    "hide_default_branch",
    "homeassistant",
    "persistent_directory",
    "zip_release",
    "filename",
    "country",
    "content_in_root",
    "render_readme",
}
BRAND_SIZES = {
    "icon.png": (256, 256),
    "icon@2x.png": (512, 512),
    "dark_icon.png": (256, 256),
    "dark_icon@2x.png": (512, 512),
}
HA_PROVIDED_MODULES = {"aiohttp", "homeassistant", "voluptuous"}
BLOCKING_FILE_METHODS = {
    "exists",
    "is_file",
    "is_dir",
    "iterdir",
    "glob",
    "rglob",
    "read_bytes",
    "read_text",
    "write_bytes",
    "write_text",
    "stat",
    "open",
}


def _flatten_keys(value: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(value, dict):
        return {prefix}
    keys: set[tuple[str, ...]] = set()
    for key, child in value.items():
        keys |= _flatten_keys(child, prefix + (str(key),))
    return keys


def _load_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        errors.append(f"Invalid JSON {path.relative_to(ROOT)}: {err}")
        return None


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return int(width), int(height)


def _requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    return match.group(1).replace("-", "_").casefold() if match else ""


def _publication_metadata_errors(
    manifest: dict[str, object], version: str
) -> list[str]:
    missing: list[str] = []
    if not (ROOT / "LICENSE").is_file() and not (ROOT / "LICENSE.txt").is_file():
        missing.append("LICENSE")
    codeowners = manifest.get("codeowners")
    if not isinstance(codeowners, list) or not codeowners:
        missing.append("manifest codeowners")
    elif any(not isinstance(owner, str) or not owner.startswith("@") for owner in codeowners):
        missing.append("valid @handle in manifest codeowners")
    issue_tracker = manifest.get("issue_tracker")
    if not isinstance(issue_tracker, str) or not issue_tracker.startswith("https://"):
        missing.append("manifest issue_tracker URL")
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.is_file() or version not in changelog.read_text(encoding="utf-8"):
        missing.append(f"CHANGELOG entry for {version}")
    return missing


def validate(
    strict_release: bool = False,
    strict_publication: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    parsed_trees: dict[Path, ast.Module] = {}
    requirement_names: set[str] = set()

    required_files = (
        COMPONENT / "__init__.py",
        COMPONENT / "api.py",
        COMPONENT / "config_flow.py",
        COMPONENT / "const.py",
        COMPONENT / "coordinator.py",
        COMPONENT / "data.py",
        COMPONENT / "manifest.json",
        COMPONENT / "sensor.py",
        COMPONENT / "translations/strings.json",
        COMPONENT / "translations/ru.json",
        COMPONENT / "translations/en.json",
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "hacs.json",
        ROOT / "scripts/build_release.py",
        ROOT / "scripts/validate.py",
        ROOT / "scripts/release_gate.py",
        ROOT / "tests/test_api.py",
        ROOT / "tests/test_release_gate.py",
        ROOT / "tests/test_static_validation.py",
        ROOT / ".github/workflows/validate.yml",
        ROOT / ".github/workflows/release.yml",
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"Required file is missing: {path.relative_to(ROOT)}")

    manifest = _load_json(COMPONENT / "manifest.json", errors)
    if isinstance(manifest, dict):
        if manifest.get("domain") != "domres":
            errors.append("manifest.json domain must be 'domres'")
        if manifest.get("name") != "ДомРесурс":
            errors.append("manifest.json name must be 'ДомРесурс'")
        if manifest.get("config_flow") is not True:
            errors.append("manifest.json must enable config_flow")
        if manifest.get("integration_type") != "hub":
            errors.append("manifest.json integration_type must be 'hub'")
        if manifest.get("iot_class") != "cloud_polling":
            errors.append("manifest.json iot_class must be cloud_polling")
        version = str(manifest.get("version", ""))
        if not re.fullmatch(r"0\.1\.\d+", version):
            errors.append("manifest.json version must match 0.1.N")
        if not isinstance(manifest.get("codeowners"), list):
            errors.append("manifest.json codeowners must be a list")
        if not str(manifest.get("documentation", "")).startswith("https://"):
            errors.append("manifest.json must contain an HTTPS documentation URL")
        keys = list(manifest)
        expected_keys = ["domain", "name"] + sorted(
            key for key in keys if key not in {"domain", "name"}
        )
        if keys != expected_keys:
            errors.append("manifest.json keys must be ordered as domain, name, then alphabetically")

        requirements = manifest.get("requirements", [])
        if not isinstance(requirements, list) or any(not isinstance(req, str) for req in requirements):
            errors.append("manifest.json requirements must be a list of strings")
            requirements = []
        requirement_names = {_requirement_name(req) for req in requirements}
        for requirement in requirements:
            if not re.search(r"(?:>=|==|~=|<=|!=|>|<)\s*\d", requirement):
                errors.append(f"External dependency must have a version constraint: {requirement}")

        missing_publication_metadata = _publication_metadata_errors(manifest, version)
        if missing_publication_metadata:
            message = "Publication metadata is incomplete: " + ", ".join(missing_publication_metadata)
            if strict_publication:
                errors.append(message)
            else:
                warnings.append(message)

    hacs = _load_json(ROOT / "hacs.json", errors)
    if isinstance(hacs, dict):
        if not isinstance(hacs.get("name"), str) or not hacs.get("name"):
            errors.append("hacs.json must define a non-empty name")
        if hacs.get("country") != "RU":
            errors.append("hacs.json country must be RU")
        unknown_keys = set(hacs) - ALLOWED_HACS_KEYS
        if unknown_keys:
            errors.append(f"hacs.json contains unsupported keys: {sorted(unknown_keys)}")

    brand_dir = COMPONENT / "brand"
    if not brand_dir.is_dir():
        errors.append("custom_components/domres/brand is required")
    else:
        present = {path.name for path in brand_dir.iterdir() if path.is_file()}
        for name, dimensions in BRAND_SIZES.items():
            path = brand_dir / name
            if not path.is_file():
                errors.append(f"Missing brand asset: {path.relative_to(ROOT)}")
                continue
            if _png_dimensions(path) != dimensions:
                errors.append(f"Brand asset {name} must be a valid {dimensions[0]}x{dimensions[1]} PNG")
        unknown_brand_files = present - set(BRAND_SIZES)
        if unknown_brand_files:
            errors.append(f"Unsupported files in brand/: {sorted(unknown_brand_files)}")

    translations: dict[str, object] = {}
    for language in ("strings", "ru", "en"):
        path = COMPONENT / "translations" / f"{language}.json"
        value = _load_json(path, errors)
        if isinstance(value, dict):
            translations[language] = value
    if len(translations) == 3:
        base_keys = _flatten_keys(translations["strings"])
        for language in ("ru", "en"):
            if _flatten_keys(translations[language]) != base_keys:
                errors.append(f"Translation keys differ between strings.json and {language}.json")
    ru = translations.get("ru")
    if isinstance(ru, dict):
        config = ru.get("config", {})
        step = config.get("step", {}).get("user", {}) if isinstance(config, dict) else {}
        descriptions = step.get("data_description", {}) if isinstance(step, dict) else {}
        description = descriptions.get("username", "") if isinstance(descriptions, dict) else ""
        if LOGIN_HINT not in description:
            errors.append("Russian login-field hint is missing the required text")
        data_fields = step.get("data", {}) if isinstance(step, dict) else {}
        if not isinstance(data_fields, dict) or "username" not in data_fields or "password" not in data_fields:
            errors.append("Config flow must have one phone/email login field and a password field")

    python_files = sorted(COMPONENT.rglob("*.py"))
    if not python_files:
        errors.append("No Python files found in the integration")
    for path in python_files:
        try:
            parsed_trees[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as err:
            errors.append(f"Python syntax error in {path.relative_to(ROOT)}: {err}")

    # Prevent regressions to blocking event-loop I/O/imports like those flagged
    # in earlier HACS reviews. Keep file work in scripts or executor jobs.
    for path, tree in parsed_trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                caught = (
                    {child.id for child in ast.walk(node.type) if isinstance(child, ast.Name)}
                    if node.type is not None
                    else {"BaseException"}
                )
                if caught & {"Exception", "BaseException"}:
                    errors.append(f"Broad exception handler in integration: {path.relative_to(ROOT)}")
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                errors.append(f"Synchronous file I/O in integration: {path.relative_to(ROOT)}")
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in BLOCKING_FILE_METHODS:
                    errors.append(
                        f"Synchronous filesystem call {node.func.attr}() in integration: "
                        f"{path.relative_to(ROOT)}"
                    )
                if node.func.attr == "import_module":
                    errors.append(f"Dynamic import in integration runtime: {path.relative_to(ROOT)}")

        # Every non-standard-library runtime import must be provided by Home Assistant
        # or be declared as a version-constrained manifest requirement.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".", 1)[0]]
            else:
                continue
            for module_name in imported:
                normalized = module_name.replace("-", "_").casefold()
                if (
                    normalized not in sys.stdlib_module_names
                    and normalized not in HA_PROVIDED_MODULES
                    and normalized not in requirement_names
                ):
                    errors.append(
                        f"Undeclared runtime dependency '{module_name}' imported by "
                        f"{path.relative_to(ROOT)}; add a version-constrained manifest requirement"
                    )

    # Guard against config-flow/client method-name mismatches (a known earlier bug).
    api_tree = parsed_trees.get(COMPONENT / "api.py")
    flow_tree = parsed_trees.get(COMPONENT / "config_flow.py")
    api_methods: set[str] = set()
    if api_tree:
        for node in ast.walk(api_tree):
            if isinstance(node, ast.ClassDef) and node.name == "DomResApi":
                api_methods = {
                    item.name
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                break
    if flow_tree:
        for node in ast.walk(flow_tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_validate_credentials":
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id == "api"
                        and child.func.attr not in api_methods
                    ):
                        errors.append(f"Config flow calls missing DomResApi method {child.func.attr}()")

    api_path = COMPONENT / "api.py"
    if api_path.is_file():
        api_source = api_path.read_text(encoding="utf-8")
        if api_source.count("self._session.post(") != 1:
            errors.append("API client must have exactly one POST call, reserved for authentication")
        if "/v8/users/auth" not in api_source:
            errors.append("Authentication endpoint /v8/users/auth is missing")
        prohibited = (
            "submit_reading",
            "async_set_reading",
            "set_meter_reading",
            "/meters/{registration}/reading",
        )
        for marker in prohibited:
            if marker.casefold() in api_source.casefold():
                errors.append(f"Read-only check failed: prohibited write marker {marker!r}")

    readme = ROOT / "README.md"
    if readme.is_file() and "только на чтение" not in readme.read_text(encoding="utf-8").casefold():
        errors.append("README must clearly state the read-only scope")

    validation_workflow = ROOT / ".github/workflows/validate.yml"
    if validation_workflow.is_file():
        workflow_text = validation_workflow.read_text(encoding="utf-8")
        if "hacs/action@" not in workflow_text or "category: integration" not in workflow_text:
            errors.append("Validation workflow must include HACS integration validation")
        if "home-assistant/actions/hassfest@" not in workflow_text:
            errors.append("Validation workflow must include Hassfest")
        if "scripts/validate.py --strict-release" in workflow_text:
            errors.append("Source validation workflow must not require a pre-existing release archive")

    release_workflow = ROOT / ".github/workflows/release.yml"
    if release_workflow.is_file():
        release_text = release_workflow.read_text(encoding="utf-8")
        if "tags:" in release_text or "workflow_dispatch:" in release_text:
            errors.append("Release workflow must not publish from tags or manual dispatch")
        if "branches:" not in release_text or "needs:" not in release_text:
            errors.append("Release workflow must gate publication on validated main-branch jobs")
        if re.search(r"(?m)^\s*sleep\s+120\s*$", release_text):
            errors.append("Release workflow must not use a fixed two-minute delay")
        bootstrap_position = release_text.find("--bootstrap-current-version")
        git_delta_position = release_text.find("--from-git-tag")
        strict_position = release_text.find("--strict-release --strict-publication")
        if (
            bootstrap_position < 0
            or git_delta_position < 0
            or strict_position < bootstrap_position
            or strict_position < git_delta_position
        ):
            errors.append("Release workflow must build a baseline or tag-based delta before strict release validation")
        if "refusing to publish a full snapshot as a delta" in release_text:
            errors.append("Release workflow must allow a delta from the latest tag when build state is absent")

    release_dir = ROOT / "release"
    for archive_path in sorted(release_dir.glob("*.zip")) if release_dir.is_dir() else []:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                bad_names = [
                    name
                    for name in archive.namelist()
                    if _is_excluded(Path(name)) or name.casefold().endswith((".env", ".netrc"))
                ]
                if bad_names:
                    errors.append(
                        f"Archive {archive_path.name} contains excluded files: "
                        + ", ".join(bad_names)
                    )
        except (OSError, zipfile.BadZipFile) as err:
            errors.append(f"Invalid release archive {archive_path.name}: {err}")

    state_path = release_dir / ".build_state.json"
    if strict_release:
        try:
            verify_current_state(ROOT)
        except BuildError as err:
            errors.append(str(err))
    elif state_path.exists():
        try:
            verify_current_state(ROOT)
        except BuildError as err:
            warnings.append(f"Release state needs rebuilding: {err}")
    else:
        warnings.append("No release archive has been built yet")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-release",
        action="store_true",
        help="fail unless source files match build state and all archives are retained",
    )
    parser.add_argument(
        "--strict-publication",
        action="store_true",
        help="fail unless LICENSE, codeowners, issue tracker, and current changelog are set",
    )
    args = parser.parse_args(argv)
    errors, warnings = validate(
        strict_release=args.strict_release,
        strict_publication=args.strict_publication,
    )
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"DomRes validation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"DomRes validation passed ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
