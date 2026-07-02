"""Trivial smoke test: the package imports and exposes a version."""

import tradehelm


def test_package_imports_and_has_version():
    assert isinstance(tradehelm.__version__, str)
    assert tradehelm.__version__
