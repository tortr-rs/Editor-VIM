# git_branch.py — Show current git branch in the status message on startup/save

import subprocess


def _get_branch():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _show_branch(editor, **kwargs):
    branch = _get_branch()
    if branch:
        editor.message = f"[git] Branch: {branch}"


def setup(editor):
    editor.on("startup", _show_branch)
    editor.on("after_save", _show_branch)


def teardown(editor):
    editor.off("startup", _show_branch)
    editor.off("after_save", _show_branch)


editor.plugin_register(
    "git_branch",
    version="1.0",
    setup=setup,
    teardown=teardown,
    description="Display current git branch in status bar",
)
