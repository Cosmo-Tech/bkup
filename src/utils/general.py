import os
import sys
from datetime import datetime

def now() -> str:
    """Return current timestamp formatted as YYYY-MM-DD HH:MM:SS."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_function() -> str:
    """Get the current function name to ease debug logs"""
    return f"func={sys._getframe(1).f_code.co_name}"