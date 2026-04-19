# autosave.py — Auto-save files on a timer after edits

import threading

_INTERVAL = 30


def _auto_save(editor):
    if getattr(editor, '_autosave_dirty', False) and editor.filepath and hasattr(editor, 'write_file'):
        try:
            editor.write_file()
            editor.message = f"[autosave] Saved {editor.filepath}"
            editor._autosave_dirty = False
        except Exception:
            pass
    t = threading.Timer(_INTERVAL, _auto_save, args=[editor])
    t.daemon = True
    t.start()
    editor._autosave_timer = t


def _mark_dirty(editor, **kwargs):
    editor._autosave_dirty = True


def _clear_dirty(editor, **kwargs):
    editor._autosave_dirty = False


def setup(editor):
    editor._autosave_dirty = False
    editor.on("after_save", _clear_dirty)
    editor.on("buffer_open", _mark_dirty)
    _auto_save(editor)


def teardown(editor):
    t = getattr(editor, '_autosave_timer', None)
    if t:
        t.cancel()
    editor.off("after_save", _clear_dirty)
    editor.off("buffer_open", _mark_dirty)


editor.plugin_register(
    "autosave",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Auto-save files periodically after edits",
)
