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

logger = logging.getLogger(__name__)

__all__ = ["CFVCore", "CFVMain", "main"]



def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Launching cf-view GUI")
    logger.info("PLOT_DIAG gui_runtime version=%s module_dir=%s", __version__, Path(__file__).resolve().parent)

    app = QApplication.instance() or QApplication(sys.argv)

    window = CFVMain()
    if not window.app_icon.isNull():
        app.setWindowIcon(window.app_icon)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
