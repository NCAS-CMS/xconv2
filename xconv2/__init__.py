"""xconv2 package."""

from __future__ import annotations

from importlib.metadata import version, PackageNotFoundError


try:
    __version__ = version("xconv2")
except PackageNotFoundError as exc:
    msg = (
        "xconv2 package not found, please run `pip install -e .` before "
        "importing the package."
    )
    raise PackageNotFoundError(
        msg,
    ) from exc


