import re
import os
from unittest.mock import patch
from src.lib import utils


def test_now_format():
    result = utils.now()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)


def test_current_function():
    def sample_function():
        return utils.current_function()

    assert sample_function() == "func=sample_function"

@patch("src.lib.utils.time.sleep", return_value=None)
def test_wait_for_pipe(mock_sleep, tmp_path):
    pipe_path = tmp_path / "pipe"
    os.mkfifo(pipe_path)

    # First call raises FileNotFoundError, second call finds the FIFO pipe
    real_stat = os.stat
    calls = {"count": 0}

    def side_effect_stat(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileNotFoundError()
        return real_stat(path)

    with patch("src.lib.utils.os.stat", side_effect=side_effect_stat):
        utils.wait_for_pipe(str(tmp_path))

    assert mock_sleep.call_count == 1
