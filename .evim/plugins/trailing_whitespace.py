# trailing_whitespace.py — Strip trailing whitespace on save

def _strip_trailing(editor, filepath=None, **kwargs):
    changed = False
    for i, line in enumerate(editor.lines):
        stripped = line.rstrip()
        if stripped != line:
            editor.lines[i] = stripped
            changed = True
    if changed:
        editor.message = "[trailing_whitespace] Stripped trailing whitespace"


def setup(editor):
    editor.on("before_save", _strip_trailing)


def teardown(editor):
    editor.off("before_save", _strip_trailing)


editor.plugin_register(
    "trailing_whitespace",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Strip trailing whitespace on save",
)
