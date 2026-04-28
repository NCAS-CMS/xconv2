"""Application entrypoint and compatibility exports for cf-view windows.

`CFVCore` and `CFVMain` now live in dedicated modules:
- `core_window.py`: presentation/UI responsibilities
- `main_window.py`: worker request/response responsibilities
"""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .core_window import CFVCore
from .main_window import CFVMain
from . import __version__
from .logging_utils import configure_logging

logger = logging.getLogger(__name__)

__all__ = ["CFVCore", "CFVMain", "main"]



def main() -> None:
    log_file = configure_logging()
    logger.info("Launching cf-view GUI")
    logger.info("Log file: %s", log_file)
    logger.info("PLOT_DIAG gui_runtime version=%s module_dir=%s", __version__, Path(__file__).resolve().parent)

    app = QApplication.instance() or QApplication(sys.argv)

    window = CFVMain()
    if not window.app_icon.isNull():
        app.setWindowIcon(window.app_icon)
    window.show()

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
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0,
                lambda: _activate_fn(_ns_app, _sel(b"activateIgnoringOtherApps:"), True),
            )
        except Exception:
            pass  # Non-fatal: window simply may not come to front

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
