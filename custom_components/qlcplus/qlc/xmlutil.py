"""Small helpers for namespace-agnostic XML parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator


class QLCParseError(ValueError):
    """A QLC+ file could not be parsed."""


def parse_xml(data: bytes | str, expected_root: str, source: str) -> ET.Element:
    """Parse ``data`` and strip namespaces. Raise QLCParseError on failure."""
    if isinstance(data, str):
        data = data.encode()
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise QLCParseError(f"{source}: invalid XML ({err})") from err
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    if root.tag != expected_root:
        raise QLCParseError(
            f"{source}: expected <{expected_root}> root element, found <{root.tag}>"
        )
    return root


def children(el: ET.Element, tag: str) -> Iterator[ET.Element]:
    return (c for c in el if c.tag == tag)


def text(el: ET.Element, tag: str, default: str = "") -> str:
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def to_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes")
