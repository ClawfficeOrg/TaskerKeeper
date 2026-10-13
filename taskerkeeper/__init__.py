"""Compatibility shim: TideTable was named TaskerKeeper before 0.9.0.

`import taskerkeeper` and `python -m taskerkeeper` keep working; every
submodule (`taskerkeeper.cli`, `taskerkeeper.jsonio`, ...) is the same module
object as its `tidetable` counterpart, so patching one patches both. New code
should import `tidetable`.
"""

import importlib
import pkgutil
import sys

import tidetable
from tidetable import __version__  # noqa: F401

for _info in pkgutil.iter_modules(tidetable.__path__):
    if _info.name.startswith("_"):
        continue
    _mod = importlib.import_module(f"tidetable.{_info.name}")
    sys.modules[f"{__name__}.{_info.name}"] = _mod
    globals()[_info.name] = _mod
del _info, _mod
