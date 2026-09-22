"""Tests inside a real Home Assistant (pytest-homeassistant-custom-component).

Kept apart from ../tests, which run without Home Assistant: the HA plugin
takes over the event loop and fixtures for the whole session. CI runs this
directory in its own job.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `custom_components` must be importable for HA's loader.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
