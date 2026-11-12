"""The bounded YAML loaders behind every config parse.

Kept out of ``deepctl_core.config`` so that importing it doesn't import
yaml: ``config._loaders()`` imports this module the first time a file is
parsed. The loaders are used only through ``config._yaml_from_text`` and
``config.load_config_text``, which add the byte cap and the size and depth
walk.
"""

from __future__ import annotations

from typing import Any

import yaml

from .config import (
    _FLOAT_PATTERN,
    _FLOAT_TAG,
    _INT_PATTERN,
    _INT_TAG,
    _MAX_CONFIG_DEPTH,
    _MAX_CONFIG_NODES,
    _MAX_NUMBER_CHARS,
    _MERGE_TAG,
    ConfigTooLarge,
)


class _ConfigLoader(yaml.SafeLoader):
    """SafeLoader for config files, bounded while it loads.

    - Merge keys (``<<``): every merged entry in the document counts against
      one budget. Each merged mapping is flattened first (its own merges
      resolved) and charged at that full size before PyYAML copies it, so
      neither one mapping merging many aliases, nor many mappings each
      merging one, nor merges nested inside merges can grow past the node
      limit.
    - Nesting and size: the composer refuses a node more than 32
      collections deep, flow or block, before the scanner reads any deeper,
      and stops after 10,000 nodes.
    - Numbers: no sexagesimal forms, and at most 64 characters.
    """

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._merged = 0
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        self._nodes += 1
        if self._nodes > _MAX_CONFIG_NODES + 1:  # the root, plus the limit
            raise ConfigTooLarge("config too large")
        self._depth += 1
        try:
            # 32 nested collections, plus the scalar inside the last one.
            if self._depth > _MAX_CONFIG_DEPTH + 1:
                raise ConfigTooLarge("config nested too deeply")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        for key_node, value_node in node.value:
            if key_node.tag != _MERGE_TAG:
                continue
            if isinstance(value_node, yaml.SequenceNode):
                merged = value_node.value
            else:
                merged = [value_node]
            for sub in merged:
                if isinstance(sub, yaml.MappingNode):
                    # Charge what will be copied: its own merges first.
                    self.flatten_mapping(sub)
                    self._merged += len(sub.value)
                    if self._merged > _MAX_CONFIG_NODES:
                        raise ConfigTooLarge("config too large")
        super().flatten_mapping(node)  # nested merges come back through here

    def construct_yaml_int(self, node: Any) -> Any:
        if len(self.construct_scalar(node)) > _MAX_NUMBER_CHARS:
            raise ConfigTooLarge("config number too long")
        return super().construct_yaml_int(node)

    def construct_yaml_float(self, node: Any) -> Any:
        if len(self.construct_scalar(node)) > _MAX_NUMBER_CHARS:
            raise ConfigTooLarge("config number too long")
        return super().construct_yaml_float(node)


# A copied resolver table for this loader only; yaml.SafeLoader is untouched.
_ConfigLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp) for tag, regexp in resolvers if tag not in (_INT_TAG, _FLOAT_TAG)
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ConfigLoader.add_implicit_resolver(_INT_TAG, _INT_PATTERN, list("-+0123456789"))
_ConfigLoader.add_implicit_resolver(_FLOAT_TAG, _FLOAT_PATTERN, list("-+0123456789."))
_ConfigLoader.add_constructor(_INT_TAG, _ConfigLoader.construct_yaml_int)
_ConfigLoader.add_constructor(_FLOAT_TAG, _ConfigLoader.construct_yaml_float)


class _KeyWatchingLoader(_ConfigLoader):
    """_ConfigLoader that notes a watched key repeated in one mapping."""

    def __init__(self, stream: Any, watch: frozenset[str]) -> None:
        super().__init__(stream)
        self._watch = watch
        self.repeated_key = False

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = key_node.value
                if key in self._watch and key in seen:
                    self.repeated_key = True
                seen.add(key)
        return super().construct_mapping(node, deep=deep)
