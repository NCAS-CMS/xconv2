"""Application entrypoint and compatibility exports for cf-view windows.

`CFVCore` and `CFVMain` now live in dedicated modules:
- `core_window.py`: presentation/UI responsibilities
- `main_window.py`: worker request/response responsibilities
"""

import logging
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .core_window import CFVCore
from .main_window import CFVMain
from . import __version__
from .logging_utils import configure_logging

logger = logging.getLogger(__name__)

__all__ = ["CFVCore", "CFVMain", "main"]


def _launch_paths_from_argv(argv: Sequence[str]) -> list[str]:
    """Return normalized positional file paths from CLI arguments."""
    if not argv:
        return []
    return [str(Path(arg).expanduser()) for arg in argv]


def _open_paths_from_cli(window: CFVMain, file_paths: Sequence[str]) -> None:
    """Open startup files passed on the command line.

    One file opens in single-file mode and is added to recents.
    Multiple files switch to multi-file mode and open all selected files.
    """
    normalized = [str(Path(path).expanduser()) for path in file_paths if str(path).strip()]
    if not normalized:
        return

    if len(normalized) == 1:
        file_path = normalized[0]
        window._set_window_title_for_file(file_path)
        window._record_recent_file(file_path)
        window.on_file_selected(file_path)
        return

    window._set_file_open_mode("multi")
    for file_path in normalized:
        window._record_recent_file(file_path)
    window.setWindowTitle(f"{window.base_window_title}: {len(normalized)} files")
    window.on_files_selected(normalized)



def main() -> None:
    launch_paths = _launch_paths_from_argv(sys.argv[1:])

    log_file = configure_logging()
    logger.info("Launching cf-view GUI")
    logger.info("Log file: %s", log_file)
    logger.info("PLOT_DIAG gui_runtime version=%s module_dir=%s", __version__, Path(__file__).resolve().parent)

    app = QApplication.instance() or QApplication(sys.argv)

    window = CFVMain()
    if not window.app_icon.isNull():
        app.setWindowIcon(window.app_icon)
    window.show()
    if launch_paths:
        # Defer file opening so startup UI/worker plumbing is initialized first.
        QTimer.singleShot(0, lambda: _open_paths_from_cli(window, launch_paths))

    # On macOS the PyInstaller bootloader may start the process with an
    # .accessory activation policy (no Dock icon, opens behind other windows).
    # Fix both issues using the ObjC runtime via ctypes — no PyObjC dependency.
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util
            _lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

            _get_class = _lib.objc_getClass
            _get_class.restype = ctypes.c_void_p
            _get_class.argtypes = [ctypes.c_char_p]

            _sel = _lib.sel_registerName
            _sel.restype = ctypes.c_void_p
            _sel.argtypes = [ctypes.c_char_p]

            _msg_addr = ctypes.cast(_lib.objc_msgSend, ctypes.c_void_p).value

            # sharedApplication — (id, SEL) -> id
            _shared_app_fn = ctypes.CFUNCTYPE(
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
            )(_msg_addr)

            # setActivationPolicy: — (id, SEL, NSInteger) -> BOOL
            # NSApplicationActivationPolicyRegular = 0
            _set_policy_fn = ctypes.CFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
            )(_msg_addr)

            # activateIgnoringOtherApps: — (id, SEL, BOOL) -> void
            _activate_fn = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool
            )(_msg_addr)

            _ns_app = _shared_app_fn(
                _get_class(b"NSApplication"), _sel(b"sharedApplication")
            )
            # Ensure we're a regular foreground app (shows in Dock + App Switcher).
            _set_policy_fn(_ns_app, _sel(b"setActivationPolicy:"), 0)
            # Defer activation until after the event loop has rendered the window.
            QTimer.singleShot(
                0,
                lambda: _activate_fn(_ns_app, _sel(b"activateIgnoringOtherApps:"), True),
            )
        except Exception:
            pass  # Non-fatal: window simply may not come to front

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
