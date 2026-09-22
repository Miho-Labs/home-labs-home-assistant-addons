"""Shared fixtures.

The integration's HA-free modules (api.py, uploader.py, const.py) are loaded
under a stand-in package, so these tests run without Home Assistant: its
real `__init__.py` imports homeassistant and is not executed here.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent
INTEGRATION = ADDON / "custom_components" / "home_labs_backup"

_spec = importlib.machinery.ModuleSpec("hlb_integration", None, is_package=True)
_pkg = importlib.util.module_from_spec(_spec)
_pkg.__path__ = [str(INTEGRATION)]
sys.modules.setdefault("hlb_integration", _pkg)
