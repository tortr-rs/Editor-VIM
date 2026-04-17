# bracket_highlight.py — Highlight matching brackets in the status message

_PAIRS = {'(': ')', '[': ']', '{': '}'}
_CLOSE = {v: k for k, v in _PAIRS.items()}


def _find_match(lines, row, col):
    """Find matching bracket position. Returns (row, col) or None."""
    if row >= len(lines) or col >= len(lines[row]):
        return None
    ch = lines[row][col]

    if ch in _PAIRS:
        target = _PAIRS[ch]
        depth = 0
        r, c = row, col
        while r < len(lines):
            line = lines[r]
            start = c if r == row else 0
            for ci in range(start, len(line)):
                if line[ci] == ch:
                    depth += 1
                elif line[ci] == target:
                    depth -= 1
                    if depth == 0:
                        return (r, ci)
            r += 1
        return None

    if ch in _CLOSE:
        opener = _CLOSE[ch]
        depth = 0
        r, c = row, col
        while r >= 0:
            line = lines[r]
            start = c if r == row else len(line) - 1
            for ci in range(start, -1, -1):
                if line[ci] == ch:
                    depth += 1
                elif line[ci] == opener:
                    depth -= 1
                    if depth == 0:
                        return (r, ci)
            r -= 1
        return None

    return None


def _show_match(editor, **kwargs):
    match = _find_match(editor.lines, editor.cy, editor.cx)
    if match:
        r, c = match
        editor.message = f"[brackets] Match at Ln {r + 1}, Col {c + 1}"


def setup(editor):
    editor.on("startup", _show_match)
    editor._bracket_find_match = _find_match


def teardown(editor):
    editor.off("startup", _show_match)
    if hasattr(editor, '_bracket_find_match'):
        del editor._bracket_find_match


editor.plugin_register(
    "bracket_highlight",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Show matching bracket position in status bar",
)
