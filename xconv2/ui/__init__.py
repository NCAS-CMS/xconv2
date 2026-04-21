"""UI support modules for the xconv2 main window."""

from .dialogs import (
    InputDialogCustom,
    OpenGlobDialog,
    OpenURIDialog,
    RemoteConfigurationDialog,
)
# RemoteFileNavigatorDialog is intentionally NOT imported here; it drags in
# p5rem/paramiko at import time which adds ~400 ms to GUI startup.  Import it
# lazily wherever it is needed.

__all__ = [
    "InputDialogCustom",
    "OpenGlobDialog",
    "OpenURIDialog",
    "RemoteConfigurationDialog",
    "RemoteFileNavigatorDialog",
]
