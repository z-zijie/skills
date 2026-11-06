"""A small YAML subset: enough for every file this framework owns.

Why not a YAML dependency: the framework has to run under whatever Python the target
machine already has, and a scaffold that cannot start because a wheel is missing is a
scaffold that does not exist. The subset covers exactly what RSI files use:

* block maps and block sequences, nested by indentation
* flow maps ``{a: 1, b: 2}`` and flow sequences ``[a, b]``
* plain, single-quoted and double-quoted scalars
* block scalars ``|`` and ``>`` with optional chomping
* ``#`` comments and blank lines
* ``null``/``~``, booleans, integers, floats

Anything outside the subset raises ``YamlError`` with the line number instead of
guessing, so a malformed hypothesis card fails loudly at validation time.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple


class YamlError(ValueError):
    """Raised when a document leaves the supported YAML subset."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


_NULL_TOKENS = {"null", "Null", "NULL", "~"}
_TRUE_TOKENS = {"true", "True", "TRUE", "yes", "Yes", "on", "On"}
_FALSE_TOKENS = {"false", "False", "FALSE", "no", "No", "off", "Off"}
_INT_RE = re.compile(r"^[-+]?(0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _strip_comment(text: str) -> str:
    """Drop a trailing ``#`` comment that is not inside quotes."""
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def _parse_scalar(token: str, line: int) -> Any:
    token = token.strip()
    if token == "":
        return None
    if token in _NULL_TOKENS:
        return None
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    if token[0] == '"':
        if not token.endswith('"') or len(token) < 2:
            raise YamlError("unterminated double-quoted scalar", line)
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token[1:-1]
    if token[0] == "'":
        if not token.endswith("'") or len(token) < 2:
            raise YamlError("unterminated single-quoted scalar", line)
        return token[1:-1].replace("''", "'")
    if token[0] == "{" or token[0] == "[":
        value, rest = _parse_flow(token, 0, line)
        if token[rest:].strip():
            raise YamlError(f"trailing text after flow collection: {token[rest:]!r}", line)
        return value
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    lowered = token.lower()
    if lowered in {"nan", ".nan"}:
        return math.nan
    if lowered in {"inf", ".inf", "+inf"}:
        return math.inf
    if lowered in {"-inf", ".inf-"}:
        return -math.inf
    return token


def _parse_flow(text: str, start: int, line: int) -> Tuple[Any, int]:
    """Parse a flow collection starting at ``start``; return (value, next index)."""
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    index = start + 1
    if opener == "{":
        result: Dict[str, Any] = {}
        while True:
            while index < len(text) and text[index] in " \t,":
                index += 1
            if index >= len(text):
                raise YamlError("unterminated flow mapping", line)
            if text[index] == closer:
                return result, index + 1
            key_start = index
            quote = ""
            while index < len(text):
                char = text[index]
                if quote:
                    if char == quote:
                        quote = ""
                elif char in "'\"":
                    quote = char
                elif char in ":,}":
                    break
                index += 1
            key = text[key_start:index].strip().strip("'\"")
            if index < len(text) and text[index] == ":":
                index += 1
            index = _skip_space(text, index)
            value, index = _parse_flow_value(text, index, closer, line)
            result[key] = value
    result_list: List[Any] = []
    while True:
        while index < len(text) and text[index] in " \t,":
            index += 1
        if index >= len(text):
            raise YamlError("unterminated flow sequence", line)
        if text[index] == closer:
            return result_list, index + 1
        value, index = _parse_flow_value(text, index, closer, line)
        result_list.append(value)


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _parse_flow_value(text: str, index: int, closer: str, line: int) -> Tuple[Any, int]:
    if index < len(text) and text[index] in "{[":
        return _parse_flow(text, index, line)
    quote = ""
    value_start = index
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in f",{closer}":
            break
        index += 1
    return _parse_scalar(text[value_start:index], line), index


def _split_key(text: str, line: int) -> Optional[Tuple[str, str]]:
    """Split ``key: value`` at the first top-level colon, or return None."""
    quote = ""
    depth = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char in "{[":
            depth += 1
            continue
        if char in "}]":
            depth -= 1
            continue
        if char == ":" and depth == 0:
            if index + 1 < len(text) and text[index + 1] not in " \t":
                continue
            return text[:index].rstrip(), text[index + 1:].strip()
    return None


class _Parser:
    def __init__(self, text: str) -> None:
        self.raw_lines: List[str] = text.splitlines()
        self.lines: List[Tuple[int, str, int]] = []
        for number, raw in enumerate(self.raw_lines, start=1):
            if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
                raise YamlError("tab indentation is not supported", number)
            stripped = _strip_comment(raw)
            if not stripped.strip():
                continue
            if stripped.strip() in {"---", "..."}:
                continue
            indent = len(stripped) - len(stripped.lstrip(" "))
            self.lines.append((number, stripped[indent:], indent))
        self.position = 0
        #: The line number the current scalar's content starts on, set by _parse_value.
        self._scalar_body_start: Optional[int] = None

    def parse(self) -> Any:
        if not self.lines:
            return None
        value = self._parse_block(self.lines[0][2])
        if self.position < len(self.lines):
            number, text, _ = self.lines[self.position]
            raise YamlError(f"unexpected content {text!r}", number)
        return value

    def _peek(self) -> Optional[Tuple[int, str, int]]:
        if self.position >= len(self.lines):
            return None
        return self.lines[self.position]

    def _parse_block(self, indent: int) -> Any:
        head = self._peek()
        if head is None:
            return None
        if head[1] == "-" or head[1].startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_sequence(self, indent: int) -> List[Any]:
        items: List[Any] = []
        while True:
            head = self._peek()
            if head is None or head[2] != indent:
                break
            if not (head[1] == "-" or head[1].startswith("- ")):
                break
            number, text, _ = head
            rest = text[2:] if head[1].startswith("- ") else ""
            self.position += 1
            if rest == "":
                child = self._peek()
                if child is None or child[2] <= indent:
                    items.append(None)
                    continue
                items.append(self._parse_block(child[2]))
                continue
            split = _split_key(rest, number)
            if split is not None and not rest.lstrip().startswith(("{", "[")):
                key, value = split
                mapping: Dict[str, Any] = {}
                inner_indent = indent + 2
                if value == "":
                    child = self._peek()
                    if child is not None and child[2] > indent:
                        mapping[key] = self._parse_block(child[2])
                    else:
                        mapping[key] = None
                else:
                    mapping[key] = self._parse_value(value, number, inner_indent)
                # Continuation keys of the same sequence item.
                while True:
                    head = self._peek()
                    if head is None or head[2] <= indent or head[1] == "-" or head[1].startswith("- "):
                        break
                    if head[2] != inner_indent:
                        raise YamlError("inconsistent indentation in sequence item", head[0])
                    self.position += 1
                    item_split = _split_key(head[1], head[0])
                    if item_split is None:
                        raise YamlError(f"expected 'key: value', got {head[1]!r}", head[0])
                    sub_key, sub_value = item_split
                    if sub_value == "":
                        child = self._peek()
                        if child is not None and child[2] > head[2]:
                            mapping[sub_key] = self._parse_block(child[2])
                        else:
                            mapping[sub_key] = None
                    else:
                        mapping[sub_key] = self._parse_value(sub_value, head[0], inner_indent)
                items.append(mapping)
                continue
            items.append(self._parse_value(rest, number, indent))
        return items

    def _parse_mapping(self, indent: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while True:
            head = self._peek()
            if head is None or head[2] < indent:
                break
            if head[2] > indent:
                raise YamlError("unexpected indentation", head[0])
            if head[1] == "-" or head[1].startswith("- "):
                break
            number, text, _ = head
            split = _split_key(text, number)
            if split is None:
                raise YamlError(f"expected 'key: value', got {text!r}", number)
            key, value = split
            self.position += 1
            if value == "":
                child = self._peek()
                if child is not None and child[2] > indent:
                    result[key] = self._parse_block(child[2])
                else:
                    result[key] = None
            else:
                result[key] = self._parse_value(value, number, indent)
        return result

    def _parse_value(self, token: str, line: int, parent_indent: int) -> Any:
        if token in {"|", "|-", "|+", ">", ">-", ">+"}:
            return self._parse_block_scalar(token, line, parent_indent)
        if token[:1] in "{[\"'":
            return _parse_scalar(token, line)
        return self._fold_plain(_parse_scalar(token, line), token, parent_indent)

    def _fold_plain(self, value: Any, token: str, parent_indent: int) -> Any:
        """Fold the continuation lines of a plain multi-line scalar.

        A wrapped description is ordinary YAML and is how the repository's skills are written:

            description: Measure the candidate and return its signed result
              with correctness and timing.

        A continuation line never starts a new mapping entry or sequence item, so a line that
        looks like ``key: value`` or ``- item`` is left alone and still raises as an
        indentation error rather than being swallowed into the scalar.
        """
        if not isinstance(value, str):
            return value
        parts = [token.strip()]
        while True:
            head = self._peek()
            if head is None or head[2] <= parent_indent:
                break
            number, text, _ = head
            if text == "-" or text.startswith("- "):
                break
            if _split_key(text, number) is not None:
                break
            parts.append(text.strip())
            self.position += 1
        if len(parts) == 1:
            return value
        return " ".join(part for part in parts if part)

    def _parse_block_scalar(self, header: str, line: int, parent_indent: int) -> str:
        """Read a ``|`` or ``>`` block verbatim from the raw source lines.

        The pre-pass strips comments and blank lines because they are noise between scalars, but
        inside a block scalar they are content: an inline diff is mostly blank context lines and
        ``#`` comment lines, and a folded code sample loses its shape without them. So this reads
        the original lines, keeps them as written, and advances the parse position past them.
        """
        fold = header[0] == ">"
        chomp = header[1] if len(header) > 1 else ""
        collected: List[str] = []
        block_indent: Optional[int] = None
        index = line  # raw_lines is zero-based and `line` is one-based
        consumed_until = line
        while index < len(self.raw_lines):
            raw = self.raw_lines[index]
            if not raw.strip():
                # A blank line inside the block is content; one after a dedent ends the block.
                if block_indent is None:
                    index += 1
                    continue
                collected.append("")
                index += 1
                consumed_until = index
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent <= parent_indent:
                break
            if block_indent is None:
                block_indent = indent
            if indent < block_indent:
                break
            collected.append(raw[block_indent:])
            index += 1
            consumed_until = index
        # Drop trailing blank lines that belong to the block's chomping, not to its content.
        while collected and collected[-1] == "":
            collected.pop()
        # Move the structural position past every line this scalar consumed.
        while self.position < len(self.lines) and self.lines[self.position][0] <= consumed_until:
            self.position += 1
        if fold:
            joined: List[str] = []
            for entry in collected:
                if entry and joined and joined[-1] and not joined[-1].endswith((":", "-", "\n")):
                    joined[-1] = joined[-1] + " " + entry
                else:
                    joined.append(entry)
            text_out = "\n".join(joined)
        else:
            text_out = "\n".join(collected)
        if chomp != "-":
            text_out += "\n"
        return text_out


def loads(text: str) -> Any:
    """Parse a YAML subset document."""
    return _Parser(text).parse()


def load(path) -> Any:
    """Parse a YAML subset file."""
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())


def dumps(value: Any, indent: int = 0) -> str:
    """Emit a document this module can read back. Stable key order is the caller's job."""
    raise NotImplementedError("dumps is intentionally not implemented: RSI stores YAML hand-written or JSON")


def safe_dump_json_style(value: Any) -> str:
    """Emit a YAML document using JSON-compatible scalars (the format RSI writes)."""
    lines: List[str] = []
    _emit(value, 0, lines)
    return "\n".join(lines) + "\n"


def _emit(value: Any, indent: int, lines: List[str]) -> None:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(pad + "{}")
            return
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                _emit(item, indent + 2, lines)
            elif isinstance(item, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                first_key = next(iter(item))
                first_value = item[first_key]
                if not isinstance(first_value, (dict, list)) or not first_value:
                    head = _scalar(first_value)
                    if head and "\n" not in head:
                        lines.append(f"{pad}- {first_key}: {head}")
                        remainder = {key: other for key, other in item.items() if key != first_key}
                        if remainder:
                            _emit(remainder, indent + 2, lines)
                        continue
                lines.append(f"{pad}-")
                _emit(item, indent + 2, lines)
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                _emit(item, indent + 2, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(pad + _scalar(value))


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text == "" or any(char in text for char in ":#{}[],&*!|>'\"%@`\n") or text != text.strip():
        return json.dumps(text)
    return text
