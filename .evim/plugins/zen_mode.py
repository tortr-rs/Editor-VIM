# zen_mode.py — Distraction-free writing mode via :zen

_original_opts = {}


def _toggle_zen(editor):
    if getattr(editor, '_zen_active', False):
        # Restore
        for k, v in _original_opts.items():
            editor.options[k] = v
        editor._zen_active = False
        editor.message = "[zen] Zen mode off"
    else:
        # Save and set minimal options
        _original_opts.clear()
        for k in ('number', 'relativenumber', 'cursorline', 'indent_guides'):
            _original_opts[k] = editor.options.get(k, False)
            editor.options[k] = False
        editor._zen_active = True
        # Hide explorer and minimap if visible
        if getattr(editor, 'show_explorer', False):
            editor.show_explorer = False
        if getattr(editor, 'show_minimap', False):
            editor.show_minimap = False
        editor.message = "[zen] Zen mode on — distraction-free writing"


def setup(editor):
    editor._zen_active = False
    editor._zen_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        if cmd.strip() == "zen":
            _toggle_zen(editor)
            return
        return editor._zen_original_run_ex(cmd)

    editor.run_ex = patched_run_ex
    editor.message = "[zen] Zen mode available — use :zen to toggle"


def teardown(editor):
    if getattr(editor, '_zen_active', False):
        _toggle_zen(editor)
    if hasattr(editor, '_zen_original_run_ex'):
        editor.run_ex = editor._zen_original_run_ex
        del editor._zen_original_run_ex
    if hasattr(editor, '_zen_active'):
        del editor._zen_active


editor.plugin_register(
    "zen_mode",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Distraction-free writing mode via :zen",
)
