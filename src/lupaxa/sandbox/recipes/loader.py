"""Load YAML configuration and merge it in precedence order."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from platformdirs import user_config_dir
from pydantic import ValidationError

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.recipes.models import Recipe

_SENSITIVE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|PASSWD)", re.I)


def user_config_path() -> Path:
    """Return the user configuration file, or ``LUPAXA_SANDBOX_CONFIG`` when set."""
    override = os.environ.get("LUPAXA_SANDBOX_CONFIG")
    if override:
        return Path(override)
    return Path(user_config_dir("lupaxa-sandbox", appauthor=False)) / "config.yml"


def load_mapping(path: Path) -> dict[str, object]:
    """Read a YAML mapping. The file must declare ``version: 1``."""
    if not path.is_file():
        raise UsageError(f"Configuration file not found: {path}")
    try:
        payload = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise UsageError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UsageError(f"{path} must contain a mapping")
    if "version" not in payload:
        raise UsageError(f"{path} is missing version. Expected version: 1")
    return {str(key): value for key, value in payload.items()}


def merge(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    """Deep-merge mappings. A recipe value replaces a user value for that key."""
    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge(current, value)
        else:
            result[key] = value
    return result


def parse_recipe(payload: dict[str, object]) -> Recipe:
    """Validate a merged mapping. Unknown keys are reported as errors."""
    try:
        return Recipe.model_validate(payload)
    except ValidationError as exc:
        raise UsageError(_format_errors(exc)) from exc


def resolve_recipe(
    *,
    user: dict[str, object] | None = None,
    recipe: dict[str, object] | None = None,
    cli: dict[str, object] | None = None,
) -> Recipe:
    """Apply defaults, then user config, then the recipe, then CLI overrides."""
    merged: dict[str, object] = {}
    merged = merge(merged, user or {})
    merged = merge(merged, recipe or {})
    merged = merge(merged, cli or {})
    merged.setdefault("version", 1)
    return parse_recipe(merged)


def resolved_document(recipe: Recipe) -> dict[str, object]:
    """JSON-ready configuration with sensitive environment values removed."""
    payload = recipe.model_dump(mode="json")
    environment = payload.get("execution", {})
    if isinstance(environment, dict):
        variables = environment.get("environment", {})
        if isinstance(variables, dict) and isinstance(variables.get("set"), dict):
            variables["set"] = {
                str(key): "***" if _SENSITIVE.search(str(key)) else value
                for key, value in variables["set"].items()
            }
    return payload


def default_recipe() -> Recipe:
    return parse_recipe({"version": 1})


def _format_errors(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"])
        lines.append(f"{location}: {message}" if location else message)
    return "\n".join(lines)
