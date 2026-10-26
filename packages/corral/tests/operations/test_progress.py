"""Tests for :mod:`corral.operations.progress` (task 3.3 / issue #71).

The progress helper wraps an iterable with a rich.progress bar, detects
notebook environments, auto-disables under pytest, and exposes a Spinner
context manager for indeterminate ops. Behavior-focused tests; we do
not assert on rich's rendering — only on what we yield and on the
notebook-detection heuristic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from corral.operations import Spinner, is_notebook, progress


def test_progress_yields_all_items() -> None:
    """progress() must yield every item from the wrapped iterable unchanged."""
    items = [1, 2, 3, 4, 5]
    result = list(progress(items, disable=True))
    assert result == items


def test_progress_yields_from_generator_with_total() -> None:
    """Generators have no len(); caller passes total= and we still yield all."""

    def gen() -> Any:
        yield from range(10)

    result = list(progress(gen(), total=10, description="gen", disable=True))
    assert result == list(range(10))


def test_progress_disable_flag_silences_output(capsys: pytest.CaptureFixture[str]) -> None:
    """disable=True must produce no stdout/stderr writes."""
    for _ in progress(range(5), disable=True):
        pass
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_is_notebook_false_in_pytest() -> None:
    """Plain pytest run is not a notebook."""
    assert is_notebook() is False


def test_is_notebook_true_when_kernel_trait_present() -> None:
    """Notebook detection via IPython get_ipython().has_trait('kernel')."""
    fake_ipython = MagicMock()
    fake_ipython.has_trait.return_value = True
    with patch("corral.operations.progress._get_ipython", return_value=fake_ipython):
        assert is_notebook() is True
    fake_ipython.has_trait.assert_called_with("kernel")


def test_is_notebook_false_when_no_ipython() -> None:
    """No IPython installed / not running under one → not a notebook."""
    with patch("corral.operations.progress._get_ipython", return_value=None):
        assert is_notebook() is False


def test_spinner_enter_exit_normal_returns_false() -> None:
    """Spinner.__exit__ returns False on clean exit (does not swallow)."""
    sp = Spinner("working...", disable=True)
    sp.__enter__()
    assert sp.__exit__(None, None, None) is False


def test_spinner_propagates_exception() -> None:
    """Spinner must re-raise exceptions raised inside its with-block."""
    with pytest.raises(ValueError, match="boom"), Spinner("validating...", disable=True):
        raise ValueError("boom")


def test_spinner_disable_silent(capsys: pytest.CaptureFixture[str]) -> None:
    """Disabled Spinner emits nothing."""
    with Spinner("noop", disable=True):
        pass
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
