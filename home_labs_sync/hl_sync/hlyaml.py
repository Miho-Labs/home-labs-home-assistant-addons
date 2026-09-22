"""Tolerant YAML loading: every unknown tag becomes a ``Tagged`` marker instead of an error.

Home Assistant configuration files use custom tags (``!include``, ``!include_dir_named``,
``!secret``, ``!env_var`` ...). We only need to inspect the structure, never resolve them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(eq=True)
class Tagged:
    """A YAML node with a non-standard tag, kept verbatim (tag + raw value)."""

    tag: str
    value: Any

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tagged({self.tag} {self.value!r})"


class TolerantLoader(yaml.SafeLoader):
    """SafeLoader that maps every ``!tag`` to :class:`Tagged`."""


def _construct_tagged(loader: TolerantLoader, _suffix: str, node: yaml.Node) -> Tagged:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:  # pragma: no cover - PyYAML has no other node types
        value = None
    return Tagged(node.tag, value)


TolerantLoader.add_multi_constructor("!", _construct_tagged)


def load_tolerant(text: str) -> Any:
    """Parse YAML text; unknown tags become ``Tagged``. Raises ``yaml.YAMLError`` on bad syntax."""
    return yaml.load(text, Loader=TolerantLoader)


def contains_tagged(obj: Any) -> bool:
    """True when ``obj`` (nested dicts/lists) contains at least one ``Tagged`` marker."""
    if isinstance(obj, Tagged):
        return True
    if isinstance(obj, dict):
        return any(contains_tagged(k) or contains_tagged(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(contains_tagged(v) for v in obj)
    return False


def dump_plain(data: Any) -> str:
    """Dump plain (tag-free) data the way we write YAML everywhere in this add-on."""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
