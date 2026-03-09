"""xconv2 package."""

from __future__ import annotations

from pathlib import Path
import tomllib


def _project_version() -> str:
	"""Read package version from pyproject.toml with a safe fallback."""
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
	return "0.0.0"


__version__ = f"pre-alpha-{_project_version()}"
