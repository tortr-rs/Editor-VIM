# line_sorter.py — Sort selected lines or all lines with :sortlines

def setup(editor):
    editor._sorter_original_run_ex = editor.run_ex

    def patched_run_ex(cmd):
        parts = cmd.strip().split()
        if parts and parts[0] in ("sortlines", "sortl"):
            reverse = "--reverse" in parts or "-r" in parts
            editor.lines.sort(reverse=reverse)
            direction = "descending" if reverse else "ascending"
            editor.message = f"[line_sorter] Sorted {len(editor.lines)} lines ({direction})"
            return
        if parts and parts[0] in ("uniqlines", "uniql"):
            seen = set()
            result = []
            for line in editor.lines:
                if line not in seen:
                    seen.add(line)
                    result.append(line)
            removed = len(editor.lines) - len(result)
            editor.lines[:] = result
            editor.message = f"[line_sorter] Removed {removed} duplicate line(s)"
            return
        return editor._sorter_original_run_ex(cmd)

    editor.run_ex = patched_run_ex


def teardown(editor):
    if hasattr(editor, '_sorter_original_run_ex'):
        editor.run_ex = editor._sorter_original_run_ex
        del editor._sorter_original_run_ex


editor.plugin_register(
    "line_sorter",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Sort or deduplicate lines with :sortlines and :uniqlines",
)
