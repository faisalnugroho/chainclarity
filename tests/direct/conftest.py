from pathlib import Path

import pytest

from gltest.direct.sdk_loader import setup_sdk_paths

_CONTRACT = Path(__file__).resolve().parents[2] / 'contracts/chainclarity.py'

setup_sdk_paths(_CONTRACT)


@pytest.fixture(autouse=True)
def _rearm_sdk_paths():
    setup_sdk_paths(_CONTRACT)
    yield
