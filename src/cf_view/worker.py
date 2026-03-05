import sys
import pickle
import base64
import traceback
import logging
import cf
import cfplot as cfp
from matplotlib import pyplot as plt


logger = logging.getLogger(__name__)

# This dictionary persists data (like 'f') between GUI commands
worker_globals = {
    'cf': cf,
    'cfp': cfp,
    'plt': plt
}

def send_to_gui(prefix, data=None):
    """Helper to format messages for the GUI pipe."""
    if data is not None:
        payload = base64.b64encode(pickle.dumps(data)).decode()
        print(f"{prefix}:{payload}", flush=True)
        logger.debug("Sent message to GUI with payload prefix=%s size=%d", prefix, len(payload))
    else:
        print(prefix, flush=True)
        logger.debug("Sent message to GUI: %s", prefix)

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
            logger.info("Executing task block (%d lines, %d chars)", len(current_block), len(code))
            try:
                # Execute the code block in our persistent global namespace
                exec(code, worker_globals)
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