"""
Shared pytest fixtures.
"""

import sys
import pytest
from unittest.mock import MagicMock


class _FakeSessionState:
    """Mimics st.session_state: supports both attribute and dict-style access."""
    def __contains__(self, item):
        return item in self.__dict__

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)


@pytest.fixture(autouse=False)
def mock_streamlit(monkeypatch):
    """
    Replace the entire streamlit module with a MagicMock so app.py can be
    imported without a running Streamlit server.
    - MagicMock auto-creates any attribute/method access.
    - session_state supports both attribute and dict-style access.
    - tabs/columns return proper-length lists so tuple-unpacking works.
    - slider/number_input return ints so int() coercions don't fail.
    """
    st_mock = MagicMock()
    st_mock.session_state = _FakeSessionState()
    st_mock.slider.return_value = 10
    st_mock.number_input.return_value = 0
    st_mock.tabs.side_effect = lambda items: [MagicMock() for _ in items]
    st_mock.columns.side_effect = lambda n, **kw: [
        MagicMock() for _ in (range(n) if isinstance(n, int) else n)
    ]

    monkeypatch.setitem(sys.modules, "streamlit", st_mock)
    monkeypatch.setitem(sys.modules, "streamlit.components", MagicMock())
    monkeypatch.setitem(sys.modules, "streamlit.components.v1", MagicMock())

    return st_mock
