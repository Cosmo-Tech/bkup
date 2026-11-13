import sys
import os
import stat
import time
from datetime import datetime


def now() -> str:
    """Return current timestamp formatted as YYYY-MM-DD HH:MM:SS."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_function() -> str:
    """Get the current function name to ease debug logs"""
    return f"func={sys._getframe(1).f_code.co_name}"


def wait_for_pipe(mount_path: str):
    """Wait until the sidecar UNIX pipe exists at <mount_path>/pipe."""
    pipe_path = os.path.join(mount_path, "pipe")
    timer = 1

    while True:
        try:
            if stat.S_ISFIFO(os.stat(pipe_path).st_mode):
                break
        except FileNotFoundError:
            pass

        time.sleep(timer)
        timer += 1
        print(f"waiting for sidecar UNIX pipe at {pipe_path}... {timer}")
