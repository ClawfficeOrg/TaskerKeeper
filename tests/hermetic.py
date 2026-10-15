"""Keep the developer's shell out of the test run. Imported first by every test module.

With `TIDETABLE_SERVER` exported, the CLI routes a temp todo path to the real hub
as a slug, and `tkrun kill` resolves its repo to the cwd: a test once wrote a live
KILL sentinel into the checkout's own `.overnight/`. A test that needs one of
these variables sets it itself, under `mock.patch.dict`.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from typing import MutableMapping

# The project's own env namespaces, current and pre-rename: TIDETABLE_/TASKERKEEPER_
# (jsonio.env_get), TKRUN_/OVERNIGHT_ (tkrun._env_alt), TK_ (serve's DB and token).
PREFIXES = ("TIDETABLE_", "TASKERKEEPER_", "TKRUN_", "OVERNIGHT_", "TK_")


def scrub(environ: MutableMapping[str, str]) -> list[str]:
    """Drop every project variable from `environ`; returns the names removed."""
    gone = [k for k in environ if k.upper().startswith(PREFIXES)]
    for k in gone:
        del environ[k]
    return gone


scrub(os.environ)
# A test that forgets TIDETABLE_CONFIG_HOME must still never read ~/.config.
CONFIG_HOME = tempfile.mkdtemp(prefix="tidetable-test-config-")
os.environ["TIDETABLE_CONFIG_HOME"] = CONFIG_HOME
atexit.register(shutil.rmtree, CONFIG_HOME, True)
