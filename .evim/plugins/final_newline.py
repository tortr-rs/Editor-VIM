# final_newline.py — Ensure files end with a single newline on save

def _ensure_final_newline(editor, filepath=None, **kwargs):
    if not editor.lines:
        editor.lines = [""]
        return
    # Remove trailing empty lines, keep exactly one
    while len(editor.lines) > 1 and editor.lines[-1] == "" and editor.lines[-2] == "":
        editor.lines.pop()
    if editor.lines[-1] != "":
        editor.lines.append("")


def setup(editor):
    editor.on("before_save", _ensure_final_newline)


def teardown(editor):
    editor.off("before_save", _ensure_final_newline)


editor.plugin_register(
    "final_newline",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Ensure files end with a single trailing newline",
)
