import sys
import pickle
import base64
import traceback
import logging
import warnings
import inspect
import re
import textwrap
from io import BytesIO
from pathlib import Path

import matplotlib
import numpy as np

# Worker renders to bytes/files only, so force a headless backend and
# avoid spawning a separate matplotlib GUI app/window (e.g. extra dock icon).
matplotlib.use("Agg", force=True)

import cf
import cfplot as cfp
from matplotlib import pyplot as plt

from . import xconv_cf_interface

# cf-plot may still call show(); in Agg mode this is non-interactive and noisy.
plt.show = lambda *args, **kwargs: None  # type: ignore[assignment]
warnings.filterwarnings(
    "ignore",
    message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
    category=UserWarning,
)


logger = logging.getLogger(__name__)
SAVE_TASK_HEADER = "#SAVE_TASK_CODE_PATH_B64:"
EMIT_IMAGE_HEADER = "#EMIT_IMAGE:"
INTERFACE_EXPORTS = tuple(getattr(xconv_cf_interface, "__all__", ()))
OMIT4SAVE_TOKEN = "#omit4save"

# This dictionary persists data (like 'f') between GUI commands
worker_globals = {
    'cf': cf,
    'cfp': cfp,
    'plt': plt,
    'np': np,
}

# Expose helper functions/constants from the interface module to generated code.
worker_globals.update(
    {
        name: getattr(xconv_cf_interface, name)
        for name in INTERFACE_EXPORTS
    }
)

def send_to_gui(prefix, data=None):
    """Helper to format messages for the GUI pipe."""
    if data is not None:
        payload = base64.b64encode(pickle.dumps(data)).decode()
        print(f"{prefix}:{payload}", flush=True)
        logger.debug("Sent message to GUI with payload prefix=%s size=%d", prefix, len(payload))
    else:
        print(prefix, flush=True)
        logger.debug("Sent message to GUI: %s", prefix)


def _extract_task_headers(code: str) -> tuple[str | None, bool, str]:
    """Extract optional task headers and return save path, emit flag, and code."""
    save_path: str | None = None
    emit_image = True
    payload = code

    while payload.startswith("#"):
        first_newline = payload.find("\n")
        if first_newline < 0:
            break

        header = payload[:first_newline].strip()
        payload = payload[first_newline + 1 :]

        if header.startswith(SAVE_TASK_HEADER):
            encoded = header[len(SAVE_TASK_HEADER) :]
            try:
                save_path = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
            except Exception:
                logger.exception("Invalid save-code header in worker task")
                save_path = None
        elif header.startswith(EMIT_IMAGE_HEADER):
            emit_image = header[len(EMIT_IMAGE_HEADER) :] != "0"
        else:
            # Unknown preamble line; stop parsing and preserve remaining payload.
            payload = header + "\n" + payload
            break

    return save_path, emit_image, payload


def _build_saved_plot_script(exec_code: str) -> str:
    """Build a reproducible script with worker state preamble plus plot code."""
    lines: list[str] = [
        "from __future__ import annotations",
        "import cf",
        "import cfplot as cfp",
        "from matplotlib import pyplot as plt",
        "",
        "# Inlined helpers from xconv2.xconv_cf_interface for standalone execution.",
        "",
    ]

    needed_helpers = [
        name for name in INTERFACE_EXPORTS if re.search(rf"\b{re.escape(name)}\b", exec_code)
    ]

    for name in needed_helpers:
        obj = getattr(xconv_cf_interface, name, None)
        if obj is None or not callable(obj):
            continue
        try:
            lines.append(textwrap.dedent(inspect.getsource(obj)).rstrip())
            lines.append("")
        except (OSError, TypeError):
            logger.exception("Unable to inline helper source for %s", name)
            lines.append(f"# NOTE: helper source unavailable for {name}")
            lines.append("")

    source_path = worker_globals.get("_cfview_file_path")
    if isinstance(source_path, str) and source_path:
        lines.append(f"f = cf.read({source_path!r})")
    else:
        lines.append("# NOTE: source file path unavailable in worker state")

    field_index = worker_globals.get("_cfview_field_index")
    if isinstance(field_index, int):
        lines.append(f"fld = f[{field_index}]")
    else:
        lines.append("# NOTE: field index unavailable; select a field before saving code")

    # Drop GUI-only task lines from the saved standalone script.
    save_exec_code = "\n".join(
        line for line in exec_code.splitlines() if OMIT4SAVE_TOKEN not in line
    ).rstrip()
    lines.append("")
    lines.append(save_exec_code)
    lines.append("")
    lines.append("plt.show(block=True)")
    lines.append("")
    return "\n".join(lines)


def _emit_latest_plot_image() -> None:
    """Send the latest matplotlib figure to GUI as PNG bytes, if available."""
    fig_numbers = plt.get_fignums()
    if not fig_numbers:
        return

    fig = plt.figure(fig_numbers[-1])
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120)
    buffer.seek(0)
    send_to_gui("IMG_READY", buffer.getvalue())
    buffer.close()
    plt.close("all")

def main():
    """Entry point for the cf-worker command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("Worker starting")

    # Expose helper in the exec namespace so GUI-issued tasks can emit messages.
    worker_globals['send_to_gui'] = send_to_gui
    send_to_gui("STATUS:Worker Initialized (Pure-Python/pyfive)")
    
    current_block = []
    
    while True:
        line = sys.stdin.readline()
        if not line:
            logger.info("Worker stdin closed; shutting down")
            break
            
        if line.strip() == "#END_TASK":
            code = "".join(current_block)
            save_path, emit_image, exec_code = _extract_task_headers(code)
            logger.info("Executing task block (%d lines, %d chars)", len(current_block), len(exec_code))

            if save_path:
                try:
                    destination = Path(save_path).expanduser()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    script_text = _build_saved_plot_script(exec_code)
                    destination.write_text(script_text, encoding="utf-8")
                    send_to_gui(f"STATUS:Saved plot code: {destination}")
                    logger.info("Saved plot code to %s", destination)
                except OSError:
                    logger.exception("Failed to save plot code to %s", save_path)
                    send_to_gui(f"STATUS:Error - failed to save plot code: {save_path}")

            try:
                # Execute the code block in our persistent global namespace
                exec(exec_code, worker_globals)
                if emit_image:
                    _emit_latest_plot_image()
                send_to_gui("STATUS:Task Complete")
                logger.info("Task complete")
            except Exception:
                # Send the full error back to the GUI for debugging
                err = traceback.format_exc()
                send_to_gui(f"STATUS:Error - {err.splitlines()[-1]}")
                print(err, file=sys.stderr) 
                logger.exception("Task failed")
            
            current_block = [] # Reset for the next GUI command
        else:
            current_block.append(line)

if __name__ == "__main__":
    main()