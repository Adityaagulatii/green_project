"""Collect the Hy test modules (test_*.hy) in this directory.

Importing hy installs its import hook, so pytest's own module import finds
the .hy files; the tests themselves are written in Hy. The Hypothesis
profiles match impl/python/tests: "default" is quick, "thorough" is for
seals (select with HYPOTHESIS_PROFILE).
"""

import os

import hy  # noqa: F401  (installs the .hy import hook)
import pytest
from hypothesis import HealthCheck, settings


def _keep_the_assert_rewriter_off_hy():
    # pytest rewrites the asserts of every file named on the command line,
    # and its rewriter parses source as Python. Hy compiles its own asserts,
    # so .hy files are left alone (otherwise `pytest path/test_x.hy` fails
    # with a Python SyntaxError).
    try:
        from _pytest.assertion.rewrite import AssertionRewritingHook
    except ImportError:  # pragma: no cover
        return
    original = AssertionRewritingHook._should_rewrite

    def should_rewrite(self, name, fn, state):
        return not str(fn).endswith(".hy") and original(self, name, fn, state)

    AssertionRewritingHook._should_rewrite = should_rewrite


_keep_the_assert_rewriter_off_hy()


def pytest_collect_file(file_path, parent):
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return pytest.Module.from_parent(parent, path=file_path)
    return None


_QUIET = [HealthCheck.too_slow, HealthCheck.data_too_large]
settings.register_profile("default", deadline=None, max_examples=60,
                          suppress_health_check=_QUIET)
settings.register_profile("thorough", deadline=None, max_examples=600,
                          suppress_health_check=_QUIET)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
