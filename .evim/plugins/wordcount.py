# wordcount.py — Show word/line/char count in the status bar

def _update_count(editor, **kwargs):
    text = "\n".join(editor.lines)
    lines = len(editor.lines)
    words = len(text.split())
    chars = len(text)
    editor.message = f"[wordcount] {lines} lines, {words} words, {chars} chars"


def setup(editor):
    editor.on("after_save", _on_save)
    # Register a custom command :wordcount
    editor._wordcount_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        if cmd.strip() in ("wordcount", "wc"):
            _update_count(editor)
            return
        return editor._wordcount_original_run_ex(cmd)

    editor.run_ex = patched_run_ex


def _on_save(editor, **kwargs):
    _update_count(editor)


def teardown(editor):
    editor.off("after_save", _on_save)
    if hasattr(editor, '_wordcount_original_run_ex'):
        editor.run_ex = editor._wordcount_original_run_ex
        del editor._wordcount_original_run_ex


editor.plugin_register(
    "wordcount",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Show word/line/char count on save or via :wordcount",
)
