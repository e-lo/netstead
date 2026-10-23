"""An offline planner for the assistant (``select.provider = "stub"``): a small grammar, no model, for tests and demos.

It answers with the same tool calls a model would (``select`` in the selection tool's shape, ``style``, ``filter``,
``navigate``, ``save_selection``, ``edit_cells`` with ``on_selection``), so everything after the model runs the same.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

from netstead.llm.types import ToolCall

__all__ = ["StubPlan", "StubPlanner"]

_OPS = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt", "=": "eq", "!=": "ne"}
_HELP = (
    "The offline assistant understands: select <street> (optionally between <street> and <street>); "
    "color by <column>; width by <column>; filter <column> <op> <value>; clear the filter; zoom to the selection; "
    "zoom to link <id>; save the selection as <name>; set <column> to <value> on the selection. Join steps with "
    '"and" or "then". Choose a language model in Settings for more.'
)
#: Where a request splits into steps. A street name with "and" in it is put back together (see ``plan``).
_SEPARATOR = re.compile(r"\s+(?:and then|and|then)\s+", re.IGNORECASE)
#: More parts than this is not a request the grammar was meant for.
_MAX_PARTS = 16


@dataclass(frozen=True)
class StubPlan:
    """The calls the grammar found (tool names and arguments) and a sentence for the user."""

    calls: tuple[ToolCall, ...]
    text: str


def _value(text: str) -> int | float | str:
    """``text`` as an int or float when it reads as one.

    >>> _value("3"), _value("-1.5"), _value("primary")
    (3, -1.5, 'primary')
    """
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d*\.\d+", text):
        return float(text)
    return text


class StubPlanner:
    """Turn simple commands, joined by "and" / "then", into tool calls; anything else gets the help text."""

    def plan(self, text: str) -> StubPlan:
        """The calls for ``text``; an empty plan with the help text when a part isn't understood.

        Steps split on "and" / "then"; when a split breaks a street name ("between A and B", "Lewis and Clark
        Way"), the shortest rejoining that the grammar understands wins.
        """
        parts = [p.strip() for p in _SEPARATOR.split(text.strip().rstrip(".")) if p.strip()]
        if not parts or len(parts) > _MAX_PARTS:
            return StubPlan((), _HELP)

        @functools.cache
        def from_part(i: int) -> tuple[ToolCall, ...] | None:
            if i == len(parts):
                return ()
            for j in range(i + 1, len(parts) + 1):
                call = self._one(" and ".join(parts[i:j]))
                if call is not None and (rest := from_part(j)) is not None:
                    return (call, *rest)
            return None

        calls = from_part(0)
        return StubPlan(calls, "") if calls is not None else StubPlan((), _HELP)

    @staticmethod
    def _one(part: str) -> ToolCall | None:
        """One step, matched without regard to case; names and values keep the case they were typed in."""
        i = re.IGNORECASE
        if m := re.fullmatch(r"select (.+)", part, i):
            return _select(m.group(1).strip())
        if m := re.fullmatch(r"colou?r by (\w+)", part, i):
            return ToolCall("style", {"color_by": m.group(1)})
        if m := re.fullmatch(r"(?:width|size) by (\w+)", part, i):
            return ToolCall("style", {"width_by": m.group(1)})
        if re.fullmatch(r"clear (?:the )?filter", part, i):
            return ToolCall("filter", {})
        if m := re.fullmatch(r"filter (\w+) (>=|<=|!=|>|<|=) (\S+)", part, i):
            col, op, val = m.groups()
            return ToolCall("filter", {"where": [{"col": col, "op": _OPS[op], "val": _value(val)}]})
        if re.fullmatch(r"zoom to (?:the )?selection", part, i):
            return ToolCall("navigate", {"to_selection": True})
        if m := re.fullmatch(r"zoom to (link|node) (\S+)", part, i):
            return ToolCall("navigate", {"record": {"table": m.group(1).lower(), "id": _value(m.group(2))}})
        if m := re.fullmatch(r"save (?:the )?selection as (.+)", part, i):
            return ToolCall("save_selection", {"name": m.group(1).strip()})
        if m := re.fullmatch(r"set (\w+) to (\S+) on (?:the )?selection", part, i):
            return ToolCall("edit_cells", {"on_selection": True, "values": {m.group(1): _value(m.group(2))}})
        return None


def _select(phrase: str) -> ToolCall | None:
    """A ``select`` call in the selection tool's shape; ``None`` for "between A" with no "and B" yet."""
    args: dict[str, Any] = {"utterance": phrase}
    if m := re.fullmatch(r"(.+?) between (.+?) and (.+)", phrase, re.IGNORECASE):
        name, start, end = m.groups()
        args.update(facility={"name": name.strip()}, from_anchor=start.strip(), to_anchor=end.strip())
    elif re.search(r"\sbetween\s", phrase, re.IGNORECASE):
        return None  # the "and" was split off: let ``plan`` rejoin it
    else:
        args["facility"] = {"name": phrase}
    return ToolCall("select", args)
