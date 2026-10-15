"""Compatibility shim: the unattended runner was named `overnight` before 0.11.

`tidetable tkrun ...` is the real command and `tidetable/tkrun.py` is the real
module. This shim keeps `from tidetable import overnight`, `import
tidetable.overnight` and `tidetable.overnight.<name>` working: it makes
`tidetable.overnight` the *same module object* as `tidetable.tkrun`, so
patching one patches both. New code imports `tkrun`.
"""

import sys as _sys

from tidetable import tkrun as _tkrun

_sys.modules[__name__] = _tkrun
