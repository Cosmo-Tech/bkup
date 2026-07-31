import os
from datetime import datetime


def get_installation_name():
    """Find the installation name of bkup. This allow to use the Helm release name as well as name defined from manual manifests."""
    try:
        return os.environ['INSTALLATION_NAME']
    except Exception as e:
        print(f"error: cannot get the bkup installation name: {e}")


def now() -> str:
    """Return current timestamp formatted as YYYY-MM-DD HH:MM:SS."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def datetime_difference(start_time: datetime, end_time: datetime) -> str:
    """
    Get difference between 2 datetime objects:
    - 'Xd HH:MM:SS' if >= 1 day
    - 'HH:MM:SS' if < 1 day
    """
    time_format = "%Y-%m-%d %H:%M:%S"

    # Auto-convert strings to datetime objects if needed
    if isinstance(start_time, str):
        start_time = datetime.strptime(start_time, time_format)

    if isinstance(end_time, str):
        end_time = datetime.strptime(end_time, time_format)

    delta = abs(end_time - start_time)

    days = delta.days
    total_seconds = delta.seconds

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    if days > 0:
        return f"{days}d {time_str}"

    return time_str

