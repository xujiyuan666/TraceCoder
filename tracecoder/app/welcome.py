"""Welcome screen renderables."""

from __future__ import annotations

from rich.align import Align
from rich.text import Text

WELCOME_LOGO_PALETTE = {
    "A": "#5c2a0a",
    "B": "#a84a10",
    "C": "#e07a2a",
    "D": "#f0a060",
    "E": "#f8d0a0",
    "F": "#fff0e0",
    "P": "#f0a060",
    "Q": "#f8d0a0",
}

WELCOME_LOGO_PIXELS = (
    "..........CCDDDDDDCC............",
    ".......CCDDDDDDDDDDDDDDCC.......",
    "..CCCDDDDDDDDDDDDDDDDDDDDDDCCC..",
    "CCCBDDDAAADDDDDDDDDDDDAAADDDDBCC",
    ".CCDDDDDDDDDDDDDDDDDDDDDDDDDDCC.",
    "..CDDDDDFFAFFDDDDDDFFAFFDDDDDC..",
    "...DDDDDDDDDDDDDDDDDDDDDDDDDD...",
    "....DDDDDDDDEEEEEEEEDDDDDDDD....",
    "....DDDDDDDEEEAEEEEAEEEDDDDD....",
    "....DDDDDDDEEEEEEEEEEDDDDDDD....",
    ".....DDDDDEEEAAAAAAAAEEEDDD.....",
    ".....DDDDDDEEEEEEEEEEDDDDDD.....",
    "......DDDDDEEEEEEEEEEEDDDD......",
    "....CCCDDDDEEEEEEEEEEEDDDCCC....",
    "..CCCDDDDDDDDDDDDDDDDDDDDDDCCC..",
)

WELCOME_PARTICLE_FRAMES = (
    ((0, 2, "P"), (2, 0, "Q"), (8, 1, "P"), (13, 30, "Q")),
    ((1, 2, "Q"), (5, 0, "P"), (10, 31, "Q"), (14, 1, "P")),
    ((0, 30, "P"), (4, 0, "Q"), (8, 2, "P"), (12, 29, "Q")),
    ((2, 0, "Q"), (6, 31, "P"), (11, 1, "Q"), (14, 30, "P")),
)


def welcome_renderable(*, compact: bool = False, particle_frame: int = 0) -> Align:
    """Render the animated logo, or a small-screen wordmark when space is tight."""
    if compact:
        return Align.center(
            Text.assemble(
                ("first", "#81e8bb bold"),
                ("coder", "#18cfcb bold"),
                ("\nlocal coding agent", "#6e6d72"),
            )
        )
    rows = [list(row) for row in WELCOME_LOGO_PIXELS]
    frame = WELCOME_PARTICLE_FRAMES[particle_frame % len(WELCOME_PARTICLE_FRAMES)]
    for row_index, column_index, pixel in frame:
        if not 0 <= row_index < len(rows):
            continue
        row = rows[row_index]
        if column_index >= len(row):
            row.extend("." for _ in range(column_index - len(row) + 1))
        if row[column_index] == ".":
            row[column_index] = pixel

    text = Text()
    for row_index, row in enumerate(rows):
        if row_index:
            text.append("\n")
        for pixel in row:
            color = WELCOME_LOGO_PALETTE.get(pixel)
            text.append("██" if color else "  ", style=color)
    return Align.center(text)
