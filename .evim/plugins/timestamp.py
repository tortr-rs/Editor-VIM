# timestamp.py — Insert current timestamp with :timestamp or :ts

import datetime


def setup(editor):
    editor._ts_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        stripped = cmd.strip()
        if stripped in ("timestamp", "ts"):
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = editor.lines[editor.cy]
            editor.lines[editor.cy] = line[:editor.cx] + now + line[editor.cx:]
            editor.cx += len(now)
            editor.message = f"[timestamp] Inserted {now}"
            return
        if stripped in ("date",):
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            line = editor.lines[editor.cy]
            editor.lines[editor.cy] = line[:editor.cx] + today + line[editor.cx:]
            editor.cx += len(today)
            editor.message = f"[timestamp] Inserted {today}"
            return
        return editor._ts_original_run_ex(cmd)

    editor.run_ex = patched_run_ex


def teardown(editor):
    if hasattr(editor, '_ts_original_run_ex'):
        editor.run_ex = editor._ts_original_run_ex
        del editor._ts_original_run_ex


editor.plugin_register(
    "timestamp",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Insert timestamps with :timestamp, :ts, or :date",
)
