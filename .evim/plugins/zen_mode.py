# zen_mode.py — Distraction-free writing mode via :zen

_ZEN_OPTS = ('number', 'relativenumber', 'cursorline', 'indent_guides')


def _toggle_zen(editor):
    if getattr(editor, '_zen_active', False):
        for k, v in editor._zen_saved.items():
            editor.options[k] = v
        editor._zen_active = False
        editor.message = "[zen] Zen mode off"
    else:
        editor._zen_saved = {k: editor.options.get(k, False) for k in _ZEN_OPTS}
        for k in _ZEN_OPTS:
            editor.options[k] = False
        editor._zen_active = True
        for attr in ('show_explorer', 'show_minimap'):
            if getattr(editor, attr, False):
                setattr(editor, attr, False)
        editor.message = "[zen] Zen mode on"


def setup(editor):
    editor._zen_active = False
    editor._zen_saved = {}
    editor._zen_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        if cmd.strip() == "zen":
            _toggle_zen(editor)
            return
        return editor._zen_original_run_ex(cmd)

    editor.run_ex = patched_run_ex


def teardown(editor):
    if getattr(editor, '_zen_active', False):
        _toggle_zen(editor)
    if hasattr(editor, '_zen_original_run_ex'):
        editor.run_ex = editor._zen_original_run_ex
        del editor._zen_original_run_ex
    for attr in ('_zen_active', '_zen_saved'):
        if hasattr(editor, attr):
            delattr(editor, attr)


editor.plugin_register(
    "zen_mode",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Distraction-free writing mode via :zen",
)
