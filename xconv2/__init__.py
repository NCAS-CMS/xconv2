"""xconv2 package."""

from __future__ import annotations

from pathlib import Path
import tomllib


def _project_version() -> str:
	"""Read package version from pyproject.toml with a safe fallback."""
	# In a frozen bundle, pyproject.toml is not present beside the package.
	# Try the source tree location first, then fall back to importlib.metadata
	# which reads from the installed dist-info (present in both dev and frozen).
	pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
	try:
		data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
		version = data.get("project", {}).get("version")
		if isinstance(version, str) and version.strip():
			return version.strip()
	except OSError:
		pass
	except tomllib.TOMLDecodeError:
		pass
	try:
		from importlib.metadata import version
		return version("xconv2")
	except Exception:
		pass
	return "0.0.0"


__version__ = f"beta-{_project_version()}"
