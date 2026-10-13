"""Fallback reader for interpreters older than 3.11, which have no tomllib.

Deliberately partial: tables, keys, strings, numbers, booleans and arrays, which
is the whole of what the configuration here uses. Anything else raises rather
than being guessed at. On 3.11 and later the real tomllib is used instead.
"""
from __future__ import annotations

import re
from typing import Any

_INT = re.compile(r"^[+-]?(0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|\d[\d_]*)$")
_FLOAT = re.compile(r"^[+-]?(\d[\d_]*\.\d[\d_]*([eE][+-]?\d+)?|\d[\d_]*[eE][+-]?\d+)$")
_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}


class TOMLDecodeError(ValueError):
    pass


def load(fp) -> dict:
    data = fp.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return loads(data)


def loads(text: str) -> dict:
    root: dict[str, Any] = {}
    table = root
    for raw, lineno in _statements(text):
        if raw.startswith("["):
            if not raw.endswith("]"):
                raise TOMLDecodeError(f"line {lineno}: unterminated table header")
            table = _descend(root, raw[1:-1].strip(), lineno)
            continue
        if "=" not in raw:
            raise TOMLDecodeError(f"line {lineno}: expected 'key = value'")
        key, value = raw.split("=", 1)
        key = key.strip().strip('"')
        if not _KEY.match(key):
            raise TOMLDecodeError(f"line {lineno}: unsupported key {key!r}")
        table[key] = _value(value.strip(), lineno)
    return root


def _statements(text: str):
    """Yield logical statements, joining the lines of a multi-line array."""
    buf, start = "", 0
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(line).strip()
        if not buf and not stripped:
            continue
        if not buf:
            start, buf = lineno, stripped
        else:
            buf += " " + stripped
        if _closed(buf):
            yield buf, start
            buf = ""
    if buf:
        yield buf, start


def _closed(text: str) -> bool:
    depth, in_str = 0, False
    for ch in text:
        if in_str:
            in_str = ch != '"'
        elif ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth <= 0


def _strip_comment(line: str) -> str:
    out, in_str, escaped = [], False, False
    for ch in line:
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _descend(root: dict, path: str, lineno: int) -> dict:
    node = root
    for part in path.split("."):
        part = part.strip().strip('"')
        if not _KEY.match(part):
            raise TOMLDecodeError(f"line {lineno}: unsupported table name {part!r}")
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise TOMLDecodeError(f"line {lineno}: {path} is not a table")
    return node


def _value(text: str, lineno: int) -> Any:
    if text.startswith("["):
        if not text.endswith("]"):
            raise TOMLDecodeError(f"line {lineno}: unterminated array")
        return [_value(i, lineno) for i in _split_items(text[1:-1])]
    if text.startswith('"'):
        return _string(text, lineno)
    if text.startswith("'"):
        if not text.endswith("'") or len(text) < 2:
            raise TOMLDecodeError(f"line {lineno}: unterminated string")
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if _INT.match(text):
        return int(text.replace("_", ""), 0)
    if _FLOAT.match(text):
        return float(text.replace("_", ""))
    raise TOMLDecodeError(f"line {lineno}: cannot read value {text!r}")


def _string(text: str, lineno: int) -> str:
    if len(text) < 2 or not text.endswith('"'):
        raise TOMLDecodeError(f"line {lineno}: unterminated string")
    out, i, body = [], 0, text[1:-1]
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt not in _ESCAPES:
                raise TOMLDecodeError(f"line {lineno}: unsupported escape \\{nxt}")
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_items(body: str) -> list[str]:
    items, depth, in_str, cur = [], 0, False, ""
    for ch in body:
        if in_str:
            cur += ch
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur += ch
        elif ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur.strip())
    return items
