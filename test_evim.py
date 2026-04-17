#!/usr/bin/env python3
"""Test evim ex commands without curses."""
import sys, importlib.util

spec = importlib.util.spec_from_file_location("evim", "evim.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Editor = mod.Editor
OPTION_ALIASES = mod.OPTION_ALIASES

print("PASS: Module loads OK")
assert OPTION_ALIASES["tabstop"] == "tabsize"
assert OPTION_ALIASES["nu"] == "number"
assert OPTION_ALIASES["rnu"] == "relativenumber"
print("PASS: OPTION_ALIASES correct")


class FakeEditor:
    def __init__(self):
        self.options = {
            "tabsize": 4, "number": False, "relativenumber": False,
            "show_command": True, "theme": "classic_blue", "indent_guides": False,
        }
        self.message = ""
        self.bindings = {}
        self.mode = "normal"
        self.dirty = False
        self.should_exit = False
        self.filepath = "test.txt"
        self.lines = ["hello world"]
        self.cx = 0
        self.cy = 0
        self.syntax_language = None
        self.last_search = ""
        self.search_direction = 1
        self.show_welcome = True
        self.colors_initialized = False
        self.config_loading = False
        self.python_env = {"editor": None}
        self.buffers = {}
        self.buffer_order = []
        self.current_buffer_idx = 0
        self.history = []
        self.filetype = None
        self.cached_variables = set()
        self.snippets = {}
        self.python_env["editor"] = self
        self.event_hooks = {}
        self.autocommands = {}
        self.plugins = {}
        self.jumplist = []
        self.jumplist_pos = -1

    def init_colors(self):
        pass


# Bind Editor methods onto FakeEditor
for name in (
    "run_ex", "_ex_set", "_resolve_option", "run_mapped_action",
    "register_key", "set_theme", "search_command", "replace_command",
    "write_file", "run_python", "open_buffer", "next_buffer",
    "prev_buffer", "list_buffers", "detect_syntax", "find_pattern",
    "move_to_search", "set_cursor", "current_line", "snapshot",
    "mark_dirty", "emit", "jumplist_push",
):
    setattr(FakeEditor, name, getattr(Editor, name))


e = FakeEditor()
p, f = 0, 0


def test(desc, cmd, check):
    global p, f
    e.message = ""
    try:
        e.run_ex(cmd)
        if check():
            p += 1
            print(f"  PASS: {desc}")
        else:
            f += 1
            print(f"  FAIL: {desc} | msg={e.message!r} opts={e.options}")
    except Exception as ex:
        f += 1
        import traceback
        traceback.print_exc()
        print(f"  FAIL: {desc} | {ex}")


print("\n=== Set commands ===")
test("set number", "set number", lambda: e.options["number"] is True)
test("set nonumber", "set nonumber", lambda: e.options["number"] is False)
test("set nu", "set nu", lambda: e.options["number"] is True)
test("set nonu", "set nonu", lambda: e.options["number"] is False)
test("set relativenumber", "set relativenumber", lambda: e.options["relativenumber"] is True)
test("set rnu", "set rnu", lambda: e.options["relativenumber"] is True)
test("set tabstop=8", "set tabstop=8", lambda: e.options["tabsize"] == 8)
test("set tabstop 4", "set tabstop 4", lambda: e.options["tabsize"] == 4)
test("set theme matrix_code", "set theme matrix_code", lambda: e.options["theme"] == "matrix_code")

print("\n=== File commands ===")
test("w", "w", lambda: "Saved" in e.message)
test("w newfile.txt", "w newfile.txt", lambda: e.filepath == "newfile.txt")
e.filepath = "test.txt"
e.dirty = False
test("q clean", "q", lambda: e.should_exit)
e.should_exit = False
e.dirty = True
test("q dirty", "q", lambda: "Unsaved" in e.message and not e.should_exit)
e.dirty = False
test("q!", "q!", lambda: e.should_exit)
e.should_exit = False
test("wq", "wq", lambda: e.should_exit)
e.should_exit = False

print("\n=== Other commands ===")
test("echo hello world", "echo hello world", lambda: e.message == "hello world")
test("help", "help", lambda: e.mode == "overlay")
e.mode = "normal"
test("theme classic_blue", "theme classic_blue", lambda: e.options["theme"] == "classic_blue")
test("colorscheme neon_nights", "colorscheme neon_nights", lambda: e.options["theme"] == "neon_nights")
test("syntax off", "syntax off", lambda: e.options.get("syntax") is False)
test("syntax on", "syntax on", lambda: e.options.get("syntax") is True)
test("python code", "python editor.message = 'from py'", lambda: e.message == "from py")

print("\n=== Map commands ===")
test("map normal", "map normal <F2> :help", lambda: ("normal", "<F2>") in e.bindings)
test("nmap", "nmap <F3> :set number", lambda: ("normal", "<F3>") in e.bindings)

e.options["number"] = False
e.bindings[("normal", "<F3>")](e)
if e.options["number"] is True:
    p += 1
    print("  PASS: mapped action executes")
else:
    f += 1
    print(f"  FAIL: mapped action | number={e.options['number']}")

print("\n=== Search/replace ===")
test("/hello", "/hello", lambda: "Found" in e.message or "not found" in e.message.lower())
test("?hello", "?hello", lambda: "not found" in e.message.lower() or "Found" in e.message)
e.lines = ["old text here"]
test("s/old/new/", "s/old/new/", lambda: "Replaced" in e.message)

print("\n=== Colon prefix ===")
test(":set number", ":set number", lambda: e.options["number"] is True)

print("\n=== .evimrc loading ===")
e2 = FakeEditor()
content = open(".evimrc").read()
try:
    exec(compile(content, ".evimrc", "exec"), {"editor": e2, "__builtins__": __builtins__})
    ok = (
        e2.options["theme"] == "matrix_code"
        and e2.options["number"] is True
        and e2.options["relativenumber"] is True
        and ("normal", "<F1>") in e2.bindings
    )
    if ok:
        p += 1
        print("  PASS: .evimrc config")
    else:
        f += 1
        print(f"  FAIL: .evimrc | {e2.options} bindings={list(e2.bindings.keys())}")
except Exception as ex:
    f += 1
    import traceback
    traceback.print_exc()
    print(f"  FAIL: .evimrc | {ex}")

print(f"\n=== Results: {p} passed, {f} failed ===")
sys.exit(0 if f == 0 else 1)
