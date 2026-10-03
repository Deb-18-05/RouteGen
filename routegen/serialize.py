"""
Shared writer for the human-readable TrainGo database text format
used by STB, PTB and RTB.

    HEADER
    {
        KEY = value
        BLOCK
        {
            ...
        }
        LIST
        {
            1 = "scalar"
            ITEM 2
            {
                ...
            }
        }
    }
"""

import math
import os
import re
from pathlib import Path


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Blocks whose keys come from OSM and must keep their original case.
PRESERVE_KEY_BLOCKS = {"tags", "names"}


def format_scalar(value):
    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NULL"
        return repr(round(value, 9))

    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )

    return f'"{text}"'


def _is_scalar(value):
    return value is None or isinstance(value, (bool, int, float, str))


def _key(key, preserve):
    key = str(key)

    if _IDENTIFIER.match(key):
        return key if preserve else key.upper()

    return format_scalar(key)


def _write(lines, level, key, value, preserve=False):
    pad = "    " * level

    if isinstance(value, dict):
        lines.append(f"{pad}{key}")
        lines.append(f"{pad}{{")

        child_preserve = (
            preserve
            or key.strip('"').lower() in PRESERVE_KEY_BLOCKS
        )

        for child_key, child_value in value.items():
            _write(
                lines,
                level + 1,
                _key(child_key, child_preserve),
                child_value,
                child_preserve
            )

        lines.append(f"{pad}}}")
        return

    if isinstance(value, (list, tuple, set)):
        items = (
            sorted(value, key=str)
            if isinstance(value, set)
            else list(value)
        )

        lines.append(f"{pad}{key}")
        lines.append(f"{pad}{{")

        for index, item in enumerate(items, start=1):
            if _is_scalar(item):
                lines.append(
                    f"{pad}    {index} = {format_scalar(item)}"
                )
            else:
                _write(
                    lines,
                    level + 1,
                    f"ITEM {index}",
                    item,
                    preserve
                )

        lines.append(f"{pad}}}")
        return

    lines.append(f"{pad}{key} = {format_scalar(value)}")


def serialize_database(header, database):
    lines = [header, "{"]

    for key, value in database.items():
        _write(lines, 1, _key(key, False), value)

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def write_database(output_path, header, database):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary = output_path.with_suffix(output_path.suffix + ".tmp")

    with open(temporary, "w", encoding="utf-8") as file:
        file.write(serialize_database(header, database))

    os.replace(temporary, output_path)

    return output_path
