"""Scientific runtime reporting and recorded installation provenance."""

from __future__ import annotations

from importlib.metadata import Distribution, distributions, packages_distributions
import json
import logging
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def redact_home_directory(text: str) -> str:
    """Replace the current user's home prefix, respecting path boundaries."""
    home = str(Path.home())
    prefixes = {home, home.replace("\\", "/")}
    pattern = "(?:" + "|".join(re.escape(prefix) for prefix in sorted(prefixes)) + r")(?=$|[/\\\s])"
    return re.sub(pattern, lambda match: "~", text)


def _installation_source(distribution: Distribution) -> str:
    installer = (distribution.read_text("INSTALLER") or "").strip().lower()
    if installer == "conda":
        return "Conda"
    text = distribution.read_text("direct_url.json")
    if text is None:
        return "pip index" if installer == "pip" else "unknown source"
    origin = json.loads(text)
    if not isinstance(origin, dict):
        raise ValueError("direct_url.json must contain an object")
    for key in ("vcs_info", "dir_info", "archive_info"):
        if key in origin and not isinstance(origin[key], dict):
            raise ValueError(f"{key} must contain an object")
    vcs = origin.get("vcs_info")
    directory = origin.get("dir_info")
    if isinstance(vcs, dict):
        if not vcs.get("vcs") or not vcs.get("commit_id"):
            raise ValueError("VCS provenance requires vcs and commit_id")
        details = [str(vcs["vcs"])]
        if vcs.get("requested_revision"):
            details.append(f"ref: {vcs['requested_revision']}")
        details.append(f"commit: {vcs['commit_id']}")
        return "; ".join(details)
    if isinstance(directory, dict):
        return "local editable" if directory.get("editable") else "local source"
    if isinstance(origin.get("archive_info"), dict):
        return "direct archive/URL"
    raise ValueError("No installation origin type recorded")


def _claims_path(distribution: Distribution, path: Path) -> bool:
    if any(Path(distribution.locate_file(file)).resolve() == path for file in distribution.files or ()):
        return True
    text = distribution.read_text("direct_url.json")
    if text is not None:
        origin = json.loads(text)
        if not isinstance(origin, dict):
            raise ValueError("direct_url.json must contain an object")
        directory = origin.get("dir_info")
        url = urlparse(origin.get("url", ""))
        if isinstance(directory, dict) and directory.get("editable") and url.scheme == "file":
            return path.is_relative_to(Path(unquote(url.path)).resolve())
    return False


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def science_environment_report(environment: list[str] | str) -> str:
    """Annotate only cf.environment entries, preserving metadata conflicts."""
    lines = environment.splitlines() if isinstance(environment, str) else environment
    by_name: dict[str, list[Distribution]] = {}
    seen: set[tuple[str, str, str]] = set()
    for distribution in distributions():
        name = _normalized_name(distribution.metadata.get("Name", ""))
        identity = (name, distribution.version, str(distribution.locate_file("")))
        if identity not in seen:
            seen.add(identity)
            by_name.setdefault(name, []).append(distribution)
    module_names = packages_distributions()
    aliases = {"cf": ["cf-python"], "cfplot": ["cf-plot"], "esmpy/ESMF": ["esmpy"]}
    output: list[str] = []
    for line in lines:
        entity, separator, value = line.partition(":")
        if not separator or entity in {"Platform", "Python"} or entity.endswith(" library") or value.strip().startswith("not available"):
            output.append(line)
            continue
        root = entity.split(".", 1)[0]
        names = aliases.get(entity, module_names.get(root, [root]))
        candidates = [d for name in {_normalized_name(n) for n in names} for d in by_name.get(name, [])]
        version, _, location = value.strip().partition(" ")
        try:
            matching = [d for d in candidates if location and _claims_path(d, Path(location.strip()).resolve())]
            version_matches = [d for d in matching if d.version == version]
            selected = version_matches if version_matches else matching
            source = _installation_source(selected[0]) if len(selected) == 1 else "unknown source"
            if len(selected) == 1 and selected[0].version != version:
                source += f"; metadata version: {selected[0].version}"
            if len(candidates) > 1:
                records = ", ".join(sorted(f"{d.metadata['Name']} {d.version}" for d in candidates))
                source += f"; WARNING: multiple metadata records: {records}"
                logger.warning("Multiple installation metadata records for %s: %s", entity, records)
        except (OSError, UnicodeError, ValueError) as exc:
            logger.warning("Cannot read installation provenance for %s: %s", entity, exc)
            source = "unknown source; metadata error"
        output.append(f"{line} [{source}]")
    note = (
        "\npip index: PyPI or another index (not recorded). "
        "Unknown source: missing or ambiguous metadata.\n"
        "Versions are package-declared; editable sources may have changed since installation."
    )
    return redact_home_directory("Local science worker environment\n\n" + "\n".join(output) + "\n" + note)
