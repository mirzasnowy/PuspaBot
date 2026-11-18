"""Third-party plugin catalog (G27, hermes-agent ``plugin-catalog``).

A catalog entry is one YAML manifest per third-party plugin.
This module validates manifests and loads a catalog directory.
It is deliberately separate from the G3 skill marketplace: the
G3 manifest (``manifest.json`` + ``SKILL.md``) describes a skill
module; this manifest (``plugin.yaml``) describes a code plugin
(tool/hook/CLI via the ``ctx`` API). The seam between the two
is the loader's ``load_plugin`` function, which the G3
installer calls after installing a skill that ships plugin
code.

Admission rules (mirroring hermes-agent's catalog README):

- required fields: ``id``, ``name``, ``version``,
  ``description``, ``author``, ``license``, ``repository``,
  ``entry_point``, ``kind``, ``api_version``;
- ``id`` is lowercase slug, unique in the catalog;
- ``version`` is semver ``MAJOR.MINOR.PATCH``;
- ``license`` is one of the allowed open-source licenses;
- ``repository`` is an https URL on a known forge;
- ``kind`` is ``general``, ``memory_provider``, or
  ``context_engine``;
- ``api_version`` must match the current published API version;
- ``dependencies`` (optional) is a list of pip requirement
  strings; ``price_credits`` (optional, G3-owned pricing) is a
  non-negative integer when present.

Repository liveness (``check_repository_alive``) does a plain
HTTP HEAD/GET with a short timeout and never raises: it returns
False on any failure. The catalog CI test that needs the network
is skipped without one, with the reason recorded.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import yaml

from .errors import PluginStructureError

__all__ = [
    "ALLOWED_KINDS",
    "ALLOWED_LICENSES",
    "CATALOG_API_VERSION",
    "check_repository_alive",
    "load_catalog",
    "load_manifest",
    "validate_manifest",
]

#: Current published plugin API version (must match plugin_api.json).
CATALOG_API_VERSION = 1

#: Allowed plugin kinds (matches the single-select kinds + general).
ALLOWED_KINDS = ("general", "memory_provider", "context_engine")

#: Licenses a catalog entry may carry (permissive, commercial-use).
ALLOWED_LICENSES = (
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
)

#: Required manifest fields.
REQUIRED_FIELDS = (
    "id",
    "name",
    "version",
    "description",
    "author",
    "license",
    "repository",
    "entry_point",
    "kind",
    "api_version",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_REPO_RE = re.compile(r"^https://(github\.com|gitlab\.com)/[^/\s]+/[^/\s]+/?$")
_ENTRY_POINT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")
_REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?\s*([<>=!~].*)?$")


def validate_manifest(manifest) -> list[str]:
    """Validate one catalog manifest dict; return problem strings."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a YAML mapping"]
    for field_name in REQUIRED_FIELDS:
        if field_name not in manifest or manifest[field_name] in (None, ""):
            problems.append(f"missing required field: {field_name}")
    if problems:
        return problems
    plugin_id = manifest["id"]
    if not isinstance(plugin_id, str) or not _ID_RE.match(plugin_id):
        problems.append(
            f"id {plugin_id!r} must be a lowercase slug "
            "(letters/digits/'-'/'_', 3-64 chars, starting alphanumerically)"
        )
    for field_name in ("name", "description", "author"):
        if not isinstance(manifest[field_name], str) or not manifest[
            field_name
        ].strip():
            problems.append(f"{field_name} must be a non-empty string")
    version = manifest["version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        problems.append(
            f"version {version!r} must be semver MAJOR.MINOR.PATCH"
        )
    if manifest["license"] not in ALLOWED_LICENSES:
        problems.append(
            f"license {manifest['license']!r} is not allowed "
            f"(allowed: {', '.join(ALLOWED_LICENSES)})"
        )
    repository = manifest["repository"]
    if not isinstance(repository, str) or not _REPO_RE.match(repository):
        problems.append(
            f"repository {repository!r} must be an https URL on "
            "github.com or gitlab.com (owner/repo)"
        )
    entry_point = manifest["entry_point"]
    if not isinstance(entry_point, str) or not _ENTRY_POINT_RE.match(
        entry_point
    ):
        problems.append(
            f"entry_point {entry_point!r} must be a dotted module path"
        )
    elif entry_point.startswith("puspa."):
        problems.append(
            "entry_point must be a third-party module, not a puspa.* module"
        )
    if manifest["kind"] not in ALLOWED_KINDS:
        problems.append(
            f"kind {manifest['kind']!r} must be one of "
            f"{', '.join(ALLOWED_KINDS)}"
        )
    if manifest["api_version"] != CATALOG_API_VERSION:
        problems.append(
            f"api_version must be {CATALOG_API_VERSION}, "
            f"got {manifest['api_version']!r}"
        )
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and _REQUIREMENT_RE.match(item.strip())
        for item in dependencies
    ):
        problems.append(
            "dependencies must be a list of pip requirement strings"
        )
    price = manifest.get("price_credits")
    if price is not None and (
        not isinstance(price, int) or isinstance(price, bool) or price < 0
    ):
        problems.append("price_credits must be a non-negative integer")
    return problems


def load_manifest(path) -> dict:
    """Load and validate one manifest YAML file.

    Raises ``PluginStructureError`` with the file name and all
    problems when the file is not valid YAML or fails admission.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginStructureError(f"cannot read manifest {path}: {exc}") from exc
    try:
        manifest = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PluginStructureError(
            f"manifest {path} is not valid YAML: {exc}"
        ) from exc
    problems = validate_manifest(manifest)
    if problems:
        raise PluginStructureError(
            f"manifest {path} failed admission: " + "; ".join(problems)
        )
    return manifest


def load_catalog(directory) -> dict:
    """Load every ``*.yaml``/``*.yml`` manifest in a directory.

    Returns ``{plugin_id: manifest}``. Raises
    ``PluginStructureError`` on the first invalid manifest and on
    a duplicate id (the later file never silently replaces the
    earlier one).
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise PluginStructureError(f"catalog directory {directory} not found")
    catalog: dict[str, dict] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        manifest = load_manifest(path)
        plugin_id = manifest["id"]
        if plugin_id in catalog:
            raise PluginStructureError(
                f"duplicate plugin id {plugin_id!r} in catalog: "
                f"{path} duplicates an earlier manifest"
            )
        catalog[plugin_id] = manifest
    return catalog


def check_repository_alive(repository: str, *, timeout_s=5) -> bool:
    """Check a catalog repository URL responds (HEAD, then GET).

    Returns False on any failure (no network, DNS, HTTP error,
    redirect loop). Never raises and never follows credentials:
    only a plain anonymous request is made.
    """
    request = urllib.request.Request(repository, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return 200 <= response.status < 400
    except Exception:  # noqa: BLE001 - liveness is best-effort by design
        pass
    request = urllib.request.Request(repository, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return 200 <= response.status < 400
    except Exception:  # noqa: BLE001 - liveness is best-effort by design
        return False
