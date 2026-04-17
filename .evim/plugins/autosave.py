# autosave.py — Auto-save files on a timer after edits
# Saves the current file every N seconds if modified.

import threading

_timer = None
_dirty = False
_interval = 30  # seconds


def _auto_save(editor):
    global _dirty, _timer
    if _dirty and editor.filepath and hasattr(editor, 'write_file'):
        try:
            editor.write_file()
            editor.message = f"[autosave] Saved {editor.filepath}"
            _dirty = False
        except Exception:
            pass
    _timer = threading.Timer(_interval, _auto_save, args=[editor])
    _timer.daemon = True
    _timer.start()


def _mark_dirty(editor, **kwargs):
    global _dirty
    _dirty = True


def setup(editor):
    editor.on("after_save", _on_save)
    editor.on("buffer_open", _mark_dirty)
    global _timer
    _timer = threading.Timer(_interval, _auto_save, args=[editor])
    _timer.daemon = True
    _timer.start()
    editor.message = f"[autosave] Enabled ({_interval}s interval)"


def _on_save(editor, **kwargs):
    global _dirty
    _dirty = False


def teardown(editor):
    global _timer
    if _timer:
        _timer.cancel()
        _timer = None
    editor.off("after_save", _on_save)
    editor.off("buffer_open", _mark_dirty)


editor.plugin_register(
    "autosave",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Auto-save files periodically after edits",
)
