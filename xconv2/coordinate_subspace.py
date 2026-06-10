"""Coordinate subspace command parsing shared by GUI and worker helpers."""

from __future__ import annotations

import ast


def parse_coordinate_subspace_commands(commands_text: str) -> dict[str, tuple[object, object]]:
    """Parse newline-delimited coordinate bound commands into a selection mapping.

    Accepted line formats (comments begin with ``#``):
    - ``coord = lo:hi``
    - ``coord: lo, hi``
    - ``coord lo hi``
    - ``coord value`` (interpreted as ``value:value``)
    """

    def _parse_atom(text: str) -> object:
        token = text.strip()
        if not token:
            raise ValueError("Empty bound value in coordinate command")

        try:
            return ast.literal_eval(token)
        except (ValueError, SyntaxError):
            pass

        try:
            if "." not in token and "e" not in token.lower():
                return int(token)
        except ValueError:
            pass

        try:
            return float(token)
        except ValueError:
            return token

    def _parse_line(line: str) -> tuple[str, tuple[object, object]]:
        if "=" in line:
            lhs, rhs = line.split("=", 1)
            coord = lhs.strip()
            bounds_text = rhs.strip()
        elif ":" in line:
            lhs, rhs = line.split(":", 1)
            coord = lhs.strip()
            bounds_text = rhs.strip()
        else:
            parts = line.split()
            if len(parts) == 2:
                coord = parts[0].strip()
                value = _parse_atom(parts[1])
                return coord, (value, value)
            if len(parts) == 3:
                coord = parts[0].strip()
                return coord, (_parse_atom(parts[1]), _parse_atom(parts[2]))
            raise ValueError(
                "Expected 'coord=lo:hi', 'coord: lo,hi', or 'coord lo hi' format"
            )

        if not coord:
            raise ValueError("Coordinate name is missing")

        if ":" in bounds_text:
            lo_text, hi_text = bounds_text.split(":", 1)
        elif "," in bounds_text:
            lo_text, hi_text = bounds_text.split(",", 1)
        else:
            value = _parse_atom(bounds_text)
            return coord, (value, value)

        return coord, (_parse_atom(lo_text), _parse_atom(hi_text))

    selections: dict[str, tuple[object, object]] = {}
    for line_no, raw_line in enumerate(commands_text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        try:
            coord, bounds = _parse_line(line)
        except ValueError as exc:
            raise ValueError(f"Invalid bounds command on line {line_no}: {exc}") from exc

        selections[coord] = bounds

    return selections
