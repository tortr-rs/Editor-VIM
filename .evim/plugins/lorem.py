# lorem.py — Generate lorem ipsum text with :lorem [count]

_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum."
)


def setup(editor):
    editor._lorem_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        parts = cmd.strip().split()
        if parts and parts[0] == "lorem":
            count = 1
            if len(parts) > 1:
                try:
                    count = max(1, min(int(parts[1]), 50))
                except ValueError:
                    count = 1
            text = (" ".join([_LOREM] * count)).strip()
            line = editor.lines[editor.cy]
            editor.lines[editor.cy] = line[:editor.cx] + text + line[editor.cx:]
            editor.cx += len(text)
            editor.message = f"[lorem] Inserted {count} paragraph(s)"
            return
        return editor._lorem_original_run_ex(cmd)

    editor.run_ex = patched_run_ex


def teardown(editor):
    if hasattr(editor, '_lorem_original_run_ex'):
        editor.run_ex = editor._lorem_original_run_ex
        del editor._lorem_original_run_ex


editor.plugin_register(
    "lorem",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Insert lorem ipsum text with :lorem [count]",
)
