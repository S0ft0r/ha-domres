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


def validate(strict_release: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    required_files = (
        COMPONENT / "__init__.py",
        COMPONENT / "api.py",
        COMPONENT / "binary_sensor.py",
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
        ROOT / "tests/test_api.py",
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
        elif not manifest["codeowners"]:
            warnings.append("manifest.json codeowners is empty; add the chosen GitHub owner before publication")
        if not str(manifest.get("documentation", "")).startswith("https://"):
            errors.append("manifest.json must contain an HTTPS documentation URL")
        keys = list(manifest)
        expected_keys = ["domain", "name"] + sorted(key for key in keys if key not in {"domain", "name"})
        if keys != expected_keys:
            errors.append("manifest.json keys must be ordered as domain, name, then alphabetically")

    hacs = _load_json(ROOT / "hacs.json", errors)
    if isinstance(hacs, dict):
        if not isinstance(hacs.get("name"), str) or not hacs.get("name"):
            errors.append("hacs.json must define a non-empty name")
        if hacs.get("country") != "RU":
            errors.append("hacs.json country must be RU")
        unknown_keys = set(hacs) - ALLOWED_HACS_KEYS
        if unknown_keys:
            errors.append(f"hacs.json contains unsupported keys: {sorted(unknown_keys)}")

    if not (ROOT / "LICENSE").is_file() and not (ROOT / "LICENSE.txt").is_file():
        warnings.append("No LICENSE file: choose a license before publishing to HACS/GitHub")

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
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as err:
            errors.append(f"Python syntax error in {path.relative_to(ROOT)}: {err}")

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

    release_dir = ROOT / "release"
    for archive_path in sorted(release_dir.glob("*.zip")) if release_dir.is_dir() else []:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                bad_names = [
                    name
                    for name in archive.namelist()
                    if _is_excluded(Path(name))
                    or name.casefold().endswith((".env", ".netrc"))
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
    args = parser.parse_args(argv)
    errors, warnings = validate(strict_release=args.strict_release)
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
