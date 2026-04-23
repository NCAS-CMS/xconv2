"""PyInstaller entrypoint for the xconv2 worker executable."""

import ctypes.util
import os
import sys

# PyInstaller static-analysis anchor imports.
#
# These packages are imported dynamically down the cf/cfdm remote-read path,
# which means PyInstaller can miss them in some build configurations. Keep
# this block so they are always discoverable at build time without affecting
# runtime behavior.
if False:  # pragma: no cover
    import pyfive  # noqa: F401
    import cbor2  # noqa: F401
    import p5rem  # noqa: F401
    import paramiko  # noqa: F401


def _configure_matplotlib_cache() -> None:
    """Use a persistent matplotlib config dir so the font cache survives restarts.

    PyInstaller's bootloader sets MPLCONFIGDIR to a per-run temp dir before
    Python starts, causing matplotlib to rebuild its font cache on every launch.
    Unconditionally override it with a stable, platform-appropriate location.
    """
    import platform
    if platform.system() == "Darwin":
        config_dir = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", "xconv2", "matplotlib"
        )
    else:
        # Linux/other: respect XDG_DATA_HOME or fall back to ~/.local/share
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        config_dir = os.path.join(xdg, "xconv2", "matplotlib")
    os.makedirs(config_dir, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = config_dir


def _configure_udunits_runtime() -> None:
    """Point cfunits at bundled UDUNITS assets in frozen builds."""
    if not getattr(sys, "frozen", False):
        return

    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    xml_path = os.path.join(base, "udunits", "udunits2.xml")
    if os.path.exists(xml_path):
        os.environ.setdefault("UDUNITS2_XML_PATH", xml_path)

    # cfunits uses ctypes.util.find_library('udunits2'); return bundled dylib.
    for name in ("libudunits2.dylib", "libudunits2.0.dylib"):
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            original = ctypes.util.find_library

            def _find_library(libname: str, _orig=original, _candidate=candidate):
                if libname == "udunits2":
                    return _candidate
                return _orig(libname)

            ctypes.util.find_library = _find_library
            break


_configure_matplotlib_cache()
_configure_udunits_runtime()

from xconv2.worker import main


if __name__ == "__main__":
    main()
