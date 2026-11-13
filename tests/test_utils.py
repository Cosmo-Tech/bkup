import re
from src.lib import utils


def test_now_format():
    result = utils.now()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)


def test_current_function():
    def sample_function():
        return utils.current_function()

    assert sample_function() == "func=sample_function"
