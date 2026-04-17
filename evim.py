#!/usr/bin/env python3
import curses
import curses.ascii
import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import termios
import threading
import time
import traceback
from pathlib import Path

OPTION_ALIASES = {
    "tabstop": "tabsize", "ts": "tabsize",
    "nu": "number", "numbers": "number",
    "rnu": "relativenumber",
}

CONFIG_FILES = [".evimrc.py", "evimrc.py", ".evimrc", "evimrc"]

class Editor:
    def __init__(self, filepath=None):
        self.filepath = filepath
        self.lines = [""]
        self.cx = 0
        self.cy = 0
        self.mode = "overlay"
        self.command = ""
        self.message = "EVim - normal mode"
        self.bindings = {}
        self.options = {
            "tabsize": 4,
            "number": False,
            "relativenumber": False,
            "show_command": True,
            "theme": "classic_blue",
            "indent_guides": False,
            "cursorline": False,
            "mouse": True,
            "wrap": False,
        }
        self.python_env = {"editor": self}
        self.start_hooks = []
        # Plugin system
        self.plugins = {}  # {name: {"name": str, "version": str, "setup": fn, "enabled": bool, ...}}
        self.event_hooks = {}  # {event_name: [callback, ...]}
        self.plugin_dirs = [
            Path.home() / ".config" / "evim" / "plugins",
            Path.cwd() / ".evim" / "plugins",
        ]
        self.autocommands = {}  # {event: [(pattern, callback), ...]}
        self.should_exit = False
        self.history = []
        self.clipboard = ""
        self.completion_index = 0
        self.completion_prefix = ""
        self.selection = None
        self.pending_normal = ""
        self.last_search = ""
        self.search_direction = 1
        self.show_welcome = True
        self.colors_initialized = False
        self.config_loading = True
        self.filetype = None
        self.syntax_language = None
        self.dirty = False
        self.cached_variables = set()
        self.buffers = {}  # NEW: For multi-file support {filepath: lines}
        self.buffer_order = []  # NEW: Track buffer open order
        self.current_buffer_idx = 0  # NEW: Current active buffer
        self.lsp_enabled = False
        self.lsp_diagnostics = []  # [(line, col, severity, message), ...]
        self.lsp_process = None
        self.lsp_request_id = 0
        self.lsp_responses = {}  # {id: response}
        self.lsp_capabilities = {}
        self.lsp_initialized = False
        self.lsp_lock = threading.Lock()
        self.lsp_hover_text = None
        self.lsp_completions = []  # [(label, detail), ...]
        self.lsp_completion_active = False
        self.lsp_completion_idx = 0
        self.lsp_server_cmd = None  # e.g. ['pyright-langserver', '--stdio']
        self.redo_stack = []  # For redo functionality
        self.scroll_top = 0  # Viewport top line
        self.scroll_left = 0  # Horizontal scroll offset
        # Dot repeat
        self.last_edit = None  # (action_name, args) for . repeat
        self.recording_edit = False
        self.edit_keys = []  # Keys captured during an edit for dot repeat
        # Macros
        self.macro_recording = None  # Register name currently recording, or None
        self.macro_keys = []  # Keys captured during macro recording
        self.macros = {}  # {register: [keys]}
        self.macro_playing = False
        # Marks
        self.marks = {}  # {char: (cy, cx)}
        # Registers
        self.registers = {}  # {char: text}  ('"' is default)
        self.pending_register = None  # Set by " key
        # Jump list
        self.jumplist = []  # [(cy, cx), ...]
        self.jumplist_pos = -1
        # Split panes
        self.splits = []  # List of pane dicts
        self.active_split = 0
        # Git gutter
        self.git_diff_lines = {}  # {lineno: 'added'|'modified'|'deleted'}
        # Persistent undo
        self.undo_file = None
        # Terminal panel
        self.term_visible = False
        self.term_fd = None
        self.term_pid = None
        self.term_lines = [""]  # scrollback buffer
        self.term_scroll = 0  # scroll offset within terminal output
        self.term_col = 0  # cursor column within current line (for \r handling)
        # Precompiled ANSI escape regex for terminal output stripping
        self._ansi_re = re.compile(
            r'\x1b\[[0-9;]*[a-zA-Z]'
            r'|\x1b\][^\x07]*\x07'
            r'|\x1b[()][AB012]'
            r'|\x1b\[\?[0-9;]*[a-zA-Z]'
        )
        # File explorer
        self.explorer_visible = False
        self.explorer_width = 25
        self.explorer_entries = []  # [(depth, name, fullpath, is_dir, expanded), ...]
        self.explorer_cursor = 0  # selected entry index
        self.explorer_scroll = 0  # scroll offset
        self.explorer_expanded = set()  # expanded directory paths
        self.explorer_cwd = str(Path.cwd())
        self._explorer_last_click = -1
        self._explorer_click_time = 0
        # Minimap
        self.minimap_visible = False
        self.minimap_width = 20
        self.read_file()
        if self.filepath:
            self.buffers[self.filepath] = self.lines[:]
            self.buffer_order = [self.filepath]
            self.current_buffer_idx = 0
        self.load_config()
        self.config_loading = False

    def read_file(self):
        if not self.filepath:
            self.filetype = None
            self.syntax_language = None
            self.dirty = False
            return
        path = Path(self.filepath)
        self.filetype = path.suffix.lower()
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            self.lines = text.splitlines() or [""]
        else:
            self.lines = [""]
        self.detect_syntax()
        self.dirty = False

    def write_file(self):
        if not self.filepath:
            self.message = "No filename. Use :w <name> to save."
            return
        self.emit("before_save", filepath=self.filepath)
        try:
            data = "\n".join(self.lines) + ("\n" if self.lines else "")
            Path(self.filepath).write_text(data, encoding="utf-8")
            self.message = f"Saved {self.filepath}"
            self.dirty = False
            try:
                self.update_git_gutter()
            except Exception:
                pass
            self.emit("after_save", filepath=self.filepath)
        except Exception as exc:
            self.message = f"Save failed: {exc}"

    def do_completion(self):
        line = self.lines[self.cy]
        if self.cx == 0 or not self.syntax_language:
            self.snapshot()
            self.insert_char('\t')
            return
        word_start = self.cx
        while word_start > 0 and (line[word_start-1].isalnum() or line[word_start-1] == '_'):
            word_start -= 1
        prefix = line[word_start:self.cx]
        if not prefix or len(prefix) < 2:
            self.snapshot()
            self.insert_char('\t')
            return
        if prefix != self.completion_prefix:
            self.completion_index = 0
            self.completion_prefix = prefix
        candidates = []
        if prefix in self.snippets:
            candidates = [self.snippets[prefix]]
        else:
            keywords = set(self._cached_keywords)
            if not self.cached_variables:
                for l in self.lines:
                    words = re.findall(r'\b[a-zA-Z_]\w*\b', l)
                    self.cached_variables.update(words)
                self.cached_variables -= keywords
            variables = list(self.cached_variables)
            candidates = list(dict.fromkeys([k for k in list(keywords) + variables if k.startswith(prefix)]))
        if candidates:
            completion = candidates[self.completion_index % len(candidates)]
            self.completion_index += 1
            # Handle multi-line snippets
            if '\n' in completion:
                lines = completion.split('\n')
                # Insert first line at current position
                self.lines[self.cy] = line[:word_start] + lines[0] + line[self.cx:]
                self.cx = word_start + len(lines[0])
                # Insert remaining lines
                for i, l in enumerate(lines[1:], 1):
                    self.lines.insert(self.cy + i, l)
                # Move cursor to first placeholder or end
                if '|' in lines[0]:
                    pos = lines[0].find('|')
                    self.cx = word_start + pos
                    self.lines[self.cy] = self.lines[self.cy].replace('|', '')
                else:
                    self.cy += len(lines) - 1
                    self.cx = len(lines[-1])
            else:
                self.lines[self.cy] = line[:word_start] + completion + line[self.cx:]
                self.cx = word_start + len(completion)
            self.mark_dirty()
        else:
            self.snapshot()
            self.insert_char('\t')

    def run_mapped_action(self, action):
        cmd = action.strip()
        if cmd.endswith("<CR>"):
            cmd = cmd[:-4].strip()
        if cmd.startswith(":"):
            cmd = cmd[1:]
        self.run_ex(cmd)

    def load_config(self):
        selected = None
        app_dir = Path(__file__).resolve().parent
        search_paths = [Path.cwd() / name for name in CONFIG_FILES]
        search_paths.extend(app_dir / name for name in CONFIG_FILES)
        search_paths.append(Path.home() / ".evimrc.py")
        search_paths.append(Path.home() / ".evimrc")
        for candidate in search_paths:
            if candidate.exists():
                selected = candidate
                break
        if not selected:
            return
        content = selected.read_text(encoding="utf-8")
        old_loading = self.config_loading
        self.config_loading = True
        try:
            exec(compile(content, str(selected), "exec"), {"editor": self, "__builtins__": __builtins__})
        except Exception as exc:
            self.message = f"Config error: {exc}"
        finally:
            self.config_loading = old_loading
        self.message = f"Loaded {selected.name}"

    def init_colors(self):
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            # Not in a curses screen yet; keep config value and apply later.
            self.colors_initialized = False
            return
        theme = self.options.get("theme", "classic_blue")
        # (fg, bg, attr) for each syntax element
        B = curses.A_BOLD
        N = 0
        BLK = curses.COLOR_BLACK
        RED = curses.COLOR_RED
        GRN = curses.COLOR_GREEN
        YEL = curses.COLOR_YELLOW
        BLU = curses.COLOR_BLUE
        MAG = curses.COLOR_MAGENTA
        CYN = curses.COLOR_CYAN
        WHT = curses.COLOR_WHITE
        D = -1  # default bg
        # Each theme: (keyword, type, comment, string, number, preproc,
        #              text_fg, text_bg, lineno_fg, lineno_bg,
        #              status_fg, status_bg, cmdline_fg, cmdline_bg)
        THEMES = {
            "classic_blue":     ((BLU,BLK,B),  (YEL,BLK,B),  (CYN,BLK,N),  (GRN,BLK,N),  (MAG,BLK,N),  (RED,BLK,B),   WHT,BLK, CYN,BLK, WHT,BLU, WHT,BLK),
            "neon_nights":      ((MAG,BLK,B),  (CYN,BLK,B),  (GRN,BLK,N),  (YEL,BLK,N),  (WHT,BLK,B),  (RED,BLK,B),   WHT,BLK, MAG,BLK, BLK,MAG, MAG,BLK),
            "desert_storm":     ((YEL,BLK,B),  (RED,BLK,N),  (GRN,BLK,N),  (CYN,BLK,N),  (MAG,BLK,N),  (RED,BLK,B),   YEL,BLK, RED,BLK, BLK,YEL, YEL,BLK),
            "sunny_meadow":     ((GRN,BLK,B),  (YEL,BLK,B),  (WHT,BLK,N),  (CYN,BLK,N),  (MAG,BLK,N),  (RED,BLK,N),   GRN,BLK, YEL,BLK, BLK,GRN, GRN,BLK),
            "vampire_castle":   ((RED,BLK,B),  (MAG,BLK,B),  (WHT,BLK,N),  (GRN,BLK,N),  (CYN,BLK,N),  (YEL,BLK,N),   RED,BLK, MAG,BLK, WHT,RED, RED,BLK),
            "arctic_aurora":    ((CYN,BLK,B),  (GRN,BLK,B),  (WHT,BLK,N),  (MAG,BLK,N),  (YEL,BLK,N),  (BLU,BLK,B),   CYN,BLK, BLU,BLK, BLK,CYN, CYN,BLK),
            "forest_grove":     ((GRN,BLK,B),  (CYN,BLK,N),  (YEL,BLK,N),  (RED,BLK,N),  (MAG,BLK,N),  (BLU,BLK,B),   GRN,BLK, GRN,BLK, WHT,GRN, GRN,BLK),
            "golden_wheat":     ((YEL,BLK,B),  (WHT,BLK,B),  (GRN,BLK,N),  (CYN,BLK,N),  (RED,BLK,N),  (MAG,BLK,N),   YEL,BLK, YEL,BLK, BLK,YEL, YEL,BLK),
            "midnight_sky":     ((BLU,BLK,B),  (CYN,BLK,B),  (WHT,BLK,N),  (MAG,BLK,N),  (GRN,BLK,N),  (RED,BLK,B),   BLU,BLK, BLU,BLK, CYN,BLU, BLU,BLK),
            "cloudy_day":       ((BLU,WHT,N),  (BLK,WHT,B),  (GRN,WHT,N),  (RED,WHT,N),  (MAG,WHT,N),  (CYN,WHT,N),   BLK,WHT, BLU,WHT, BLK,WHT, BLU,WHT),
            "city_lights":      ((CYN,BLK,B),  (YEL,BLK,B),  (WHT,BLK,N),  (GRN,BLK,N),  (MAG,BLK,N),  (RED,BLK,N),   CYN,BLK, WHT,BLK, BLK,CYN, CYN,BLK),
            "creamy_latte":     ((MAG,WHT,B),  (BLU,WHT,B),  (GRN,WHT,N),  (RED,WHT,N),  (CYN,WHT,N),  (MAG,WHT,N),   BLK,WHT, MAG,WHT, BLK,WHT, MAG,WHT),
            "deep_space":       ((BLU,BLK,B),  (MAG,BLK,B),  (CYN,BLK,N),  (GRN,BLK,N),  (YEL,BLK,N),  (RED,BLK,B),   WHT,BLK, BLU,BLK, WHT,BLK, BLU,BLK),
            "fresh_breeze":     ((CYN,WHT,N),  (GRN,WHT,B),  (BLK,WHT,N),  (RED,WHT,N),  (BLU,WHT,N),  (MAG,WHT,N),   BLK,WHT, CYN,WHT, BLK,CYN, CYN,WHT),
            "matrix_code":      ((GRN,BLK,B),  (GRN,BLK,B),  (GRN,BLK,N),  (GRN,BLK,N),  (GRN,BLK,N),  (GRN,BLK,B),   GRN,BLK, GRN,BLK, GRN,BLK, GRN,BLK),
            "ocean_blue":       ((CYN,BLU,B),  (WHT,BLU,B),  (WHT,BLU,N),  (YEL,BLU,N),  (GRN,BLU,N),  (RED,BLU,B),   WHT,BLU, CYN,BLU, WHT,BLU, CYN,BLU),
            "fire_red":         ((RED,BLK,B),  (YEL,BLK,B),  (WHT,BLK,N),  (GRN,BLK,N),  (CYN,BLK,N),  (MAG,BLK,B),   RED,BLK, YEL,BLK, YEL,RED, RED,BLK),
            "forest_green":     ((GRN,BLK,B),  (YEL,BLK,B),  (CYN,BLK,N),  (RED,BLK,N),  (MAG,BLK,N),  (BLU,BLK,B),   GRN,BLK, GRN,BLK, BLK,GRN, GRN,BLK),
            "purple_haze":      ((MAG,BLK,B),  (BLU,BLK,B),  (CYN,BLK,N),  (GRN,BLK,N),  (RED,BLK,N),  (YEL,BLK,B),   MAG,BLK, MAG,BLK, WHT,MAG, MAG,BLK),
            "sunset_orange":    ((YEL,BLK,B),  (RED,BLK,B),  (CYN,BLK,N),  (MAG,BLK,N),  (GRN,BLK,N),  (BLU,BLK,B),   YEL,BLK, RED,BLK, BLK,YEL, YEL,BLK),
            "arctic_white":     ((BLK,WHT,B),  (BLU,WHT,B),  (GRN,WHT,N),  (RED,WHT,N),  (MAG,WHT,N),  (CYN,WHT,B),   BLK,WHT, BLU,WHT, BLK,WHT, BLU,WHT),
            "midnight_purple":  ((MAG,BLK,B),  (CYN,BLK,B),  (YEL,BLK,N),  (GRN,BLK,N),  (WHT,BLK,B),  (RED,BLK,B),   MAG,BLK, CYN,BLK, CYN,MAG, MAG,BLK),
            "desert_gold":      ((YEL,BLK,B),  (RED,BLK,N),  (GRN,BLK,N),  (CYN,BLK,N),  (WHT,BLK,N),  (MAG,BLK,B),   YEL,BLK, YEL,BLK, BLK,YEL, YEL,BLK),
            "cyber_pink":       ((MAG,BLK,B),  (CYN,BLK,B),  (GRN,BLK,N),  (YEL,BLK,N),  (WHT,BLK,B),  (RED,BLK,B),   MAG,BLK, MAG,BLK, BLK,MAG, MAG,BLK),
        }
        t = THEMES.get(theme, THEMES["classic_blue"])
        kw, ty, co, st, nu, pp = t[0], t[1], t[2], t[3], t[4], t[5]
        txt_fg, txt_bg = t[6], t[7]
        ln_fg, ln_bg = t[8], t[9]
        sfg, sbg = t[10], t[11]
        cmd_fg, cmd_bg = t[12], t[13]
        curses.init_pair(1, kw[0], kw[1])
        curses.init_pair(2, co[0], co[1])
        curses.init_pair(3, st[0], st[1])
        curses.init_pair(4, nu[0], nu[1])
        curses.init_pair(5, ty[0], ty[1])
        curses.init_pair(6, pp[0], pp[1])
        curses.init_pair(7, sfg, sbg)
        curses.init_pair(8, txt_fg, txt_bg)
        curses.init_pair(9, ln_fg, ln_bg)
        curses.init_pair(10, cmd_fg, cmd_bg)
        self.color_keyword = curses.color_pair(1) | kw[2]
        self.color_type = curses.color_pair(5) | ty[2]
        self.color_comment = curses.color_pair(2) | co[2]
        self.color_string = curses.color_pair(3) | st[2]
        self.color_number = curses.color_pair(4) | nu[2]
        self.color_preprocessor = curses.color_pair(6) | pp[2]
        self.color_status = curses.color_pair(7) | curses.A_BOLD
        self.color_default = curses.color_pair(8)
        self.color_lineno = curses.color_pair(9)
        self.color_cmdline = curses.color_pair(10)
        self.color_bg = curses.color_pair(8)
        self.colors_initialized = True

    def detect_syntax(self):
        extension_map = {
            ".c": "c", ".h": "c",
            ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
            ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
            ".cs": "csharp", ".rs": "rust", ".py": "python", ".lua": "lua",
            ".pas": "pascal", ".pp": "pascal", ".pascal": "pascal",
            ".f": "fortran", ".for": "fortran", ".f90": "fortran",
            ".f95": "fortran", ".f03": "fortran", ".f77": "fortran",
            ".evimrc": "evimlang",
            ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
            ".ts": "typescript", ".tsx": "typescript",
            ".java": "java", ".go": "go",
            ".rb": "ruby", ".php": "php",
            ".pl": "perl", ".pm": "perl",
            ".swift": "swift",
            ".kt": "kotlin", ".kts": "kotlin",
            ".scala": "scala", ".sc": "scala",
            ".sh": "shell", ".bash": "shell", ".zsh": "shell",
            ".asm": "assembly", ".s": "assembly", ".nasm": "assembly", ".inc": "assembly",
            ".r": "r", ".R": "r",
            ".zig": "zig",
            ".nim": "nim",
            ".dart": "dart",
            ".ex": "elixir", ".exs": "elixir",
            ".erl": "erlang", ".hrl": "erlang",
            ".hs": "haskell", ".lhs": "haskell",
            ".ml": "ocaml", ".mli": "ocaml",
            ".clj": "clojure", ".cljs": "clojure", ".cljc": "clojure",
            ".lisp": "lisp", ".cl": "lisp", ".el": "lisp",
            ".vue": "vue",
            ".svelte": "svelte",
            ".yaml": "yaml", ".yml": "yaml",
            ".toml": "toml",
            ".json": "json",
            ".xml": "xml", ".xsl": "xml", ".xsd": "xml",
            ".html": "html", ".htm": "html",
            ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
            ".sql": "sql",
            ".md": "markdown", ".markdown": "markdown",
            ".cmake": "cmake",
            ".dockerfile": "dockerfile",
            ".proto": "protobuf",
            ".v": "v", ".vsh": "v",
            ".d": "dlang",
            ".m": "objectivec", ".mm": "objectivec",
            ".jl": "julia",
            ".ps1": "powershell", ".psm1": "powershell",
            ".tf": "terraform", ".hcl": "terraform",
            ".sol": "solidity",
            ".groovy": "groovy", ".gradle": "groovy",
        }
        self.syntax_language = extension_map.get(self.filetype, None)
        self.snippets = {}
        self.cached_variables.clear()
        if self.syntax_language == "python":
            self.snippets = {
                "if": "if :",
                "for": "for  in :",
                "def": "def ():",
                "class": "class :",
                "try": "try:\n\t\nexcept :",
            }
        elif self.syntax_language in ("c", "cpp", "csharp"):
            self.snippets = {
                "if": "if () {}",
                "for": "for (;;) {}",
                "while": "while () {}",
                "switch": "switch () {\n\tcase :\n\t\tbreak;\n}",
                "class": "class  {\n\t\n};",
                "struct": "struct  {\n\t\n};",
            }
        elif self.syntax_language == "rust":
            self.snippets = {
                "fn": "fn () {}",
                "if": "if  {}",
                "for": "for  in  {}",
                "struct": "struct  {\n\t\n}",
                "impl": "impl  {\n\t\n}",
            }
        elif self.syntax_language == "lua":
            self.snippets = {
                "if": "if  then\n\t\nend",
                "for": "for  do\n\t\nend",
                "function": "function ()\n\t\nend",
                "while": "while  do\n\t\nend",
            }
        elif self.syntax_language in ("java", "javascript", "typescript", "kotlin", "scala"):
            self.snippets = {
                "if": "if () {}", "for": "for (;;) {}",
                "while": "while () {}", "class": "class  {\n\t\n}",
            }
        elif self.syntax_language == "go":
            self.snippets = {
                "if": "if  {}", "for": "for  {}",
                "func": "func () {\n\t\n}", "struct": "type  struct {\n\t\n}",
            }
        elif self.syntax_language == "ruby":
            self.snippets = {
                "if": "if \n\t\nend", "def": "def \n\t\nend",
                "class": "class \n\t\nend",
            }
        elif self.syntax_language == "swift":
            self.snippets = {
                "if": "if  {}", "for": "for  in  {}",
                "func": "func () {\n\t\n}", "guard": "guard  else {\n\treturn\n}",
            }
        elif self.syntax_language == "shell":
            self.snippets = {
                "if": "if [[ ]]; then\n\t\nfi",
                "for": "for  in ; do\n\t\ndone",
                "while": "while [[ ]]; do\n\t\ndone",
                "function": "function () {\n\t\n}",
            }
        elif self.syntax_language == "php":
            self.snippets = {
                "if": "if () {}", "for": "for (;;) {}",
                "while": "while () {}", "function": "function () {\n\t\n}",
                "class": "class  {\n\t\n}",
            }
        # Add more as needed
        # Cache keyword and type sets for syntax highlighting performance
        self._cached_keywords = set(self.get_keyword_sets(self.syntax_language)) if self.syntax_language else set()
        self._cached_types = set(self.get_type_words(self.syntax_language)) if self.syntax_language else set()

    def get_keyword_sets(self, lang):
        default = {
            "if", "else", "for", "while", "return", "break", "continue",
            "switch", "case", "default", "do", "struct", "union", "enum",
            "const", "static", "volatile", "extern", "goto", "sizeof",
            "namespace", "using", "new", "delete", "try", "catch", "finally",
            "class", "public", "private", "protected", "override", "virtual",
            "template", "typename", "this", "throw", "operator", "inline",
            "auto", "constexpr", "decltype", "typedef", "friend", "mutable",
            "namespace", "using", "nullptr", "constexpr", "noexcept", "offsetof",
        }
        python = {
            "def", "class", "import", "from", "as", "if", "elif", "else",
            "for", "while", "return", "break", "continue", "try", "except",
            "finally", "with", "lambda", "yield", "global", "nonlocal",
            "assert", "pass", "raise", "del", "True", "False", "None",
            "async", "await", "in", "is", "and", "or", "not",
        }
        lua = {
            "and", "break", "do", "else", "elseif", "end", "false", "for",
            "function", "if", "in", "local", "nil", "not", "or", "repeat",
            "return", "then", "true", "until", "while", "goto",
        }
        pascal = {
            "begin", "end", "var", "const", "type", "record", "procedure",
            "function", "program", "if", "then", "else", "for", "to", "downto",
            "while", "repeat", "until", "case", "of", "uses", "unit", "interface",
            "implementation", "with", "nil", "in", "out", "div", "mod",
            "packed", "set", "array", "string", "object", "class", "constructor",
            "destructor", "inline", "override", "virtual", "absolute", "reintroduce",
        }
        fortran = {
            "program", "end", "function", "subroutine", "integer", "real",
            "double", "complex", "logical", "character", "if", "then", "else",
            "elseif", "do", "continue", "goto", "stop", "return", "call",
            "module", "use", "contains", "interface", "enddo", "endif",
            "implicit", "none", "parameter", "real", "doubleprecision", "allocate",
            "deallocate", "type", "kind", "dimension", "intent",
        }
        rust = {
            "fn", "let", "mut", "pub", "crate", "mod", "use", "impl", "trait",
            "struct", "enum", "match", "if", "else", "loop", "while", "for",
            "in", "as", "const", "static", "ref", "return", "break", "continue",
            "unsafe", "async", "await", "dyn", "where", "move", "type", "self",
            "super", "extern", "crate", "macro_rules", "impl", "trait", "override",
        }
        csharp = default | {"namespace", "using", "async", "await", "var", "new", "get", "set", "sealed", "readonly", "event", "delegate", "base", "override", "partial", "virtual", "abstract", "checked", "unchecked", "fixed", "unsafe", "stackalloc"}
        cpp = default | {"nullptr", "template", "typename", "using", "static_cast", "dynamic_cast", "reinterpret_cast", "noexcept", "final", "decltype", "constexpr", "mutable", "friend", "export", "explicit", "alignas", "alignof", "thread_local"}
        c = default | {"inline", "restrict", "signed", "unsigned", "short", "long", "void", "char", "int", "float", "double", "bool", "wchar_t", "size_t", "ptrdiff_t"}
        rust_k = rust
        evimlang = {"set", "map", "python"}
        javascript = {
            "var", "let", "const", "function", "return", "if", "else", "for",
            "while", "do", "switch", "case", "default", "break", "continue",
            "new", "delete", "typeof", "instanceof", "in", "of", "this",
            "class", "extends", "super", "import", "export", "from", "as",
            "try", "catch", "finally", "throw", "async", "await", "yield",
            "void", "with", "debugger", "true", "false", "null", "undefined",
        }
        typescript = javascript | {
            "type", "interface", "enum", "namespace", "module", "declare",
            "abstract", "implements", "readonly", "keyof", "infer", "is",
            "asserts", "any", "never", "unknown",
        }
        java_k = {
            "abstract", "assert", "break", "case", "catch", "class", "const",
            "continue", "default", "do", "else", "enum", "extends", "final",
            "finally", "for", "goto", "if", "implements", "import",
            "instanceof", "interface", "native", "new", "package", "private",
            "protected", "public", "return", "static", "strictfp", "super",
            "switch", "synchronized", "this", "throw", "throws", "transient",
            "try", "void", "volatile", "while", "true", "false", "null",
        }
        go_k = {
            "break", "case", "chan", "const", "continue", "default", "defer",
            "else", "fallthrough", "for", "func", "go", "goto", "if",
            "import", "interface", "map", "package", "range", "return",
            "select", "struct", "switch", "type", "var", "true", "false",
            "nil", "iota",
        }
        ruby_k = {
            "def", "class", "module", "end", "if", "elsif", "else", "unless",
            "case", "when", "while", "until", "for", "do", "begin", "rescue",
            "ensure", "raise", "return", "yield", "next", "break", "redo",
            "retry", "in", "then", "self", "super", "nil", "true", "false",
            "and", "or", "not", "require", "include", "attr_reader",
            "attr_writer", "attr_accessor", "puts", "print", "lambda", "proc",
            "private", "public", "protected",
        }
        php_k = {
            "if", "else", "elseif", "while", "do", "for", "foreach", "as",
            "switch", "case", "default", "break", "continue", "return",
            "function", "class", "new", "try", "catch", "finally", "throw",
            "namespace", "use", "extends", "implements", "interface",
            "abstract", "public", "private", "protected", "static", "const",
            "var", "echo", "print", "isset", "unset", "empty", "array",
            "list", "global", "true", "false", "null", "yield", "match",
            "enum", "readonly", "fn",
        }
        perl_k = {
            "my", "our", "local", "sub", "if", "elsif", "else", "unless",
            "while", "until", "for", "foreach", "do", "return", "last",
            "next", "redo", "use", "require", "package", "BEGIN", "END",
            "die", "warn", "print", "say", "chomp", "push", "pop", "shift",
            "unshift", "defined", "undef", "eval", "grep", "map", "sort",
            "keys", "values", "exists", "delete", "bless", "ref",
        }
        swift_k = {
            "import", "class", "struct", "enum", "protocol", "extension",
            "func", "var", "let", "if", "else", "guard", "switch", "case",
            "default", "for", "in", "while", "repeat", "break", "continue",
            "return", "throw", "throws", "rethrows", "try", "catch", "do",
            "as", "is", "self", "Self", "super", "init", "deinit", "nil",
            "true", "false", "public", "private", "internal", "open",
            "fileprivate", "static", "override", "final", "lazy", "weak",
            "unowned", "mutating", "inout", "where", "async", "await",
        }
        kotlin_k = {
            "fun", "val", "var", "if", "else", "when", "for", "while", "do",
            "return", "break", "continue", "class", "object", "interface",
            "enum", "sealed", "data", "open", "abstract", "override",
            "private", "protected", "public", "internal", "companion",
            "init", "this", "super", "package", "import", "as", "is", "in",
            "try", "catch", "finally", "throw", "null", "true", "false",
            "it", "by", "lazy", "lateinit", "suspend", "inline", "typealias",
        }
        scala_k = {
            "def", "val", "var", "class", "object", "trait", "extends",
            "with", "import", "package", "if", "else", "match", "case",
            "for", "while", "do", "return", "yield", "throw", "try",
            "catch", "finally", "new", "this", "super", "override",
            "abstract", "final", "sealed", "private", "protected",
            "implicit", "lazy", "type", "true", "false", "null",
        }
        shell_k = {
            "if", "then", "else", "elif", "fi", "case", "esac", "for",
            "while", "until", "do", "done", "in", "function", "return",
            "exit", "break", "continue", "local", "export", "readonly",
            "declare", "typeset", "unset", "shift", "source", "eval",
            "exec", "trap", "set", "echo", "printf", "read", "test",
            "true", "false",
        }
        assembly_k = {
            "mov", "add", "sub", "mul", "div", "push", "pop", "call", "ret",
            "jmp", "je", "jne", "jz", "jnz", "jg", "jge", "jl", "jle",
            "ja", "jb", "jae", "jbe", "cmp", "test", "and", "or", "xor",
            "not", "shl", "shr", "sal", "sar", "rol", "ror", "lea", "nop",
            "int", "syscall", "hlt", "inc", "dec", "imul", "idiv", "neg",
            "movzx", "movsx", "cdq", "rep", "movs", "stos",
            "ldr", "str", "ldm", "stm", "bl", "bx", "blx", "svc",
            "li", "la", "lw", "sw", "lb", "sb", "beq", "bne", "addi",
            "jal", "jr", "lui", "auipc", "ecall",
            "section", "segment", "global", "extern", "db", "dw", "dd",
            "dq", "resb", "resw", "resd", "resq", "equ", "times", "org",
            "align", "bits", "include",
        }
        if lang == "python":
            return python
        if lang == "lua":
            return lua
        if lang == "pascal":
            return pascal
        if lang == "fortran":
            return fortran
        if lang == "rust":
            return rust_k
        if lang == "csharp":
            return csharp
        if lang == "cpp":
            return cpp
        if lang == "c":
            return c
        if lang == "evimlang":
            return evimlang
        if lang == "javascript":
            return javascript
        if lang == "typescript":
            return typescript
        if lang == "java":
            return java_k
        if lang == "go":
            return go_k
        if lang == "ruby":
            return ruby_k
        if lang == "php":
            return php_k
        if lang == "perl":
            return perl_k
        if lang == "swift":
            return swift_k
        if lang == "kotlin":
            return kotlin_k
        if lang == "scala":
            return scala_k
        if lang == "shell":
            return shell_k
        if lang == "assembly":
            return assembly_k
        # New languages - use C-like defaults with language-specific additions
        zig_k = {"fn", "pub", "const", "var", "comptime", "inline", "extern", "export", "try", "catch", "unreachable", "defer", "errdefer", "orelse", "if", "else", "while", "for", "switch", "break", "continue", "return", "struct", "enum", "union", "error", "test", "async", "await", "suspend", "resume", "threadlocal", "usingnamespace"}
        nim_k = {"proc", "func", "method", "var", "let", "const", "type", "object", "ref", "ptr", "import", "include", "from", "export", "template", "macro", "iterator", "converter", "when", "case", "of", "if", "elif", "else", "while", "for", "in", "block", "try", "except", "finally", "raise", "yield", "discard", "return", "result", "nil"}
        dart_k = {"abstract", "as", "assert", "async", "await", "break", "case", "catch", "class", "const", "continue", "covariant", "default", "deferred", "do", "dynamic", "else", "enum", "export", "extends", "extension", "external", "factory", "final", "finally", "for", "get", "if", "implements", "import", "in", "interface", "is", "late", "library", "mixin", "new", "null", "on", "operator", "part", "required", "rethrow", "return", "sealed", "set", "show", "static", "super", "switch", "sync", "this", "throw", "try", "typedef", "var", "void", "while", "with", "yield"}
        elixir_k = {"def", "defp", "defmodule", "defmacro", "defstruct", "defprotocol", "defimpl", "do", "end", "if", "else", "unless", "case", "cond", "when", "with", "fn", "raise", "rescue", "try", "catch", "after", "for", "in", "import", "use", "alias", "require", "and", "or", "not", "true", "false", "nil", "pipe", "send", "receive"}
        haskell_k = {"module", "where", "import", "qualified", "as", "hiding", "data", "type", "newtype", "class", "instance", "deriving", "if", "then", "else", "case", "of", "let", "in", "do", "where", "forall", "infixl", "infixr", "infix"}
        ocaml_k = {"let", "in", "if", "then", "else", "match", "with", "function", "fun", "rec", "and", "or", "not", "type", "module", "struct", "sig", "end", "val", "open", "include", "exception", "try", "raise", "begin", "for", "while", "do", "done", "to", "downto", "mutable", "ref"}
        clojure_k = {"def", "defn", "defmacro", "fn", "let", "loop", "recur", "if", "when", "cond", "case", "do", "try", "catch", "finally", "throw", "ns", "require", "import", "use", "in-ns", "refer", "atom", "deref", "swap!", "reset!", "assoc", "dissoc", "conj", "cons", "map", "filter", "reduce"}
        lisp_k = {"defun", "defvar", "defparameter", "defconstant", "defmacro", "lambda", "let", "let*", "setq", "setf", "if", "when", "unless", "cond", "case", "progn", "loop", "do", "dolist", "dotimes", "return", "nil", "t", "and", "or", "not", "car", "cdr", "cons", "list", "append", "funcall", "apply", "format"}
        julia_k = {"function", "end", "if", "elseif", "else", "for", "while", "try", "catch", "finally", "return", "break", "continue", "begin", "let", "local", "global", "const", "struct", "mutable", "abstract", "primitive", "type", "module", "import", "using", "export", "macro", "do", "in", "isa", "where"}
        r_k = {"function", "if", "else", "for", "while", "repeat", "in", "next", "break", "return", "library", "require", "source", "TRUE", "FALSE", "NULL", "NA", "Inf", "NaN"}
        sql_k = {"SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "ALTER", "DROP", "INDEX", "VIEW", "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "ON", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION", "AND", "OR", "NOT", "NULL", "AS", "DISTINCT", "COUNT", "SUM", "AVG", "MIN", "MAX", "LIKE", "IN", "BETWEEN", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END", "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "CONSTRAINT", "DEFAULT", "CHECK", "UNIQUE", "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION"}
        html_k = {"html", "head", "body", "div", "span", "p", "a", "img", "table", "tr", "td", "th", "form", "input", "button", "select", "option", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style", "link", "meta", "title", "section", "article", "nav", "header", "footer", "main", "aside"}
        css_k = {"color", "background", "margin", "padding", "border", "display", "position", "top", "left", "right", "bottom", "width", "height", "font", "text", "flex", "grid", "align", "justify", "overflow", "opacity", "transform", "transition", "animation", "z-index", "cursor", "visibility", "important"}
        yaml_k = {"true", "false", "null", "yes", "no", "on", "off"}
        solidity_k = {"pragma", "solidity", "contract", "interface", "library", "function", "modifier", "event", "emit", "mapping", "struct", "enum", "if", "else", "for", "while", "do", "break", "continue", "return", "require", "revert", "assert", "payable", "view", "pure", "external", "internal", "public", "private", "virtual", "override", "memory", "storage", "calldata"}
        terraform_k = {"resource", "data", "variable", "output", "locals", "module", "provider", "terraform", "required_providers", "backend", "for_each", "count", "depends_on", "lifecycle", "provisioner", "dynamic", "content"}
        dlang_k = {"module", "import", "class", "struct", "interface", "enum", "union", "void", "auto", "if", "else", "while", "for", "foreach", "do", "switch", "case", "default", "break", "continue", "return", "scope", "delegate", "function", "lazy", "template", "mixin", "alias", "typeof", "typeid", "is", "assert", "throw", "try", "catch", "finally", "immutable", "const", "shared", "pure", "nothrow", "override", "abstract", "final", "synchronized"}
        v_k = {"fn", "pub", "mut", "const", "struct", "enum", "union", "interface", "type", "import", "module", "if", "else", "for", "in", "match", "or", "and", "not", "return", "defer", "go", "spawn", "shared", "lock", "rlock", "unsafe", "assert", "none", "true", "false"}
        groovy_k = default | {"def", "as", "in", "trait", "with", "assert", "println"}
        powershell_k = {"function", "param", "begin", "process", "end", "if", "elseif", "else", "switch", "while", "for", "foreach", "do", "until", "try", "catch", "finally", "throw", "return", "break", "continue", "exit", "Write-Host", "Write-Output", "Get-Item", "Set-Item", "New-Object", "Import-Module"}
        if lang == "zig":
            return zig_k
        if lang == "nim":
            return nim_k
        if lang == "dart":
            return dart_k
        if lang == "elixir":
            return elixir_k
        if lang == "haskell":
            return haskell_k
        if lang == "ocaml":
            return ocaml_k
        if lang in ("clojure",):
            return clojure_k
        if lang == "lisp":
            return lisp_k
        if lang == "julia":
            return julia_k
        if lang == "r":
            return r_k
        if lang == "sql":
            return sql_k
        if lang in ("html", "xml", "vue", "svelte"):
            return html_k
        if lang in ("css", "scss", "sass", "less"):
            return css_k
        if lang in ("yaml", "toml", "json"):
            return yaml_k
        if lang == "solidity":
            return solidity_k
        if lang == "terraform":
            return terraform_k
        if lang == "dlang":
            return dlang_k
        if lang == "v":
            return v_k
        if lang == "groovy":
            return groovy_k
        if lang == "powershell":
            return powershell_k
        if lang == "erlang":
            return elixir_k
        if lang == "objectivec":
            return default
        if lang in ("markdown", "protobuf", "cmake", "dockerfile"):
            return default
        return default

    def get_type_words(self, lang):
        base_types = {"int", "float", "double", "char", "bool", "long", "short", "void", "wchar_t", "size_t", "ptrdiff_t", "auto"}
        pascal_types = {"integer", "real", "boolean", "string", "char", "text", "byte", "word", "longint", "qword"}
        fortran_types = {"integer", "real", "complex", "logical", "character", "doubleprecision"}
        python_types = {"int", "float", "bool", "str", "list", "dict", "set", "tuple", "None", "True", "False"}
        lua_types = {"nil", "table", "userdata", "function"}
        rust_types = {"i32", "i64", "u32", "u64", "usize", "isize", "f32", "f64", "String", "str", "bool", "char"}
        if lang == "pascal":
            return pascal_types
        if lang == "fortran":
            return fortran_types
        if lang == "python":
            return python_types
        if lang == "lua":
            return lua_types
        if lang == "rust":
            return rust_types
        javascript_types = {"Number", "String", "Boolean", "Object", "Array", "Function", "Symbol", "BigInt", "Map", "Set", "Promise", "Date", "RegExp", "Error"}
        typescript_types = javascript_types | {"string", "number", "boolean", "void", "any", "never", "unknown", "object", "symbol", "bigint", "undefined"}
        java_types = {"int", "long", "short", "byte", "float", "double", "char", "boolean", "void", "String", "Integer", "Long", "Float", "Double", "Boolean", "Object", "List", "Map", "Set"}
        go_types = {"int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "float32", "float64", "complex64", "complex128", "string", "bool", "byte", "rune", "error", "any"}
        ruby_types = {"String", "Integer", "Float", "Array", "Hash", "Symbol", "Regexp", "Range", "Proc", "IO", "File", "NilClass"}
        php_types = {"int", "float", "string", "bool", "array", "object", "callable", "void", "mixed", "never", "null", "self", "static", "iterable"}
        swift_types = {"Int", "Int8", "Int16", "Int32", "Int64", "UInt", "Float", "Double", "Bool", "String", "Character", "Array", "Dictionary", "Set", "Optional", "Any", "Void", "Never"}
        kotlin_types = {"Int", "Long", "Short", "Byte", "Float", "Double", "Boolean", "Char", "String", "Unit", "Nothing", "Any", "Array", "List", "Map", "Set", "Pair"}
        scala_types = {"Int", "Long", "Short", "Byte", "Float", "Double", "Boolean", "Char", "String", "Unit", "Nothing", "Any", "AnyRef", "List", "Map", "Set", "Option", "Vector", "Seq"}
        assembly_types = {
            "eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp",
            "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
            "ax", "bx", "cx", "dx", "al", "bl", "cl", "dl",
            "sp", "lr", "pc", "xmm0", "xmm1", "xmm2", "xmm3",
        }
        if lang == "javascript":
            return javascript_types
        if lang == "typescript":
            return typescript_types
        if lang == "java":
            return java_types
        if lang == "go":
            return go_types
        if lang == "ruby":
            return ruby_types
        if lang == "php":
            return php_types
        if lang == "swift":
            return swift_types
        if lang == "kotlin":
            return kotlin_types
        if lang == "scala":
            return scala_types
        if lang == "assembly":
            return assembly_types
        if lang in ("perl", "shell"):
            return set()
        zig_types = {"u8", "u16", "u32", "u64", "u128", "i8", "i16", "i32", "i64", "i128", "f16", "f32", "f64", "f128", "usize", "isize", "bool", "void", "anytype", "noreturn", "type", "comptime_int", "comptime_float"}
        nim_types = {"int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "float", "float32", "float64", "bool", "char", "string", "seq", "array", "set", "tuple", "void", "auto", "any"}
        dart_types = {"int", "double", "num", "String", "bool", "List", "Map", "Set", "Future", "Stream", "Iterable", "dynamic", "void", "Object", "Null", "Never", "Function", "Type", "Symbol"}
        elixir_types = {"integer", "float", "atom", "string", "list", "tuple", "map", "pid", "port", "reference", "binary", "function", "boolean"}
        haskell_types = {"Int", "Integer", "Float", "Double", "Char", "String", "Bool", "IO", "Maybe", "Either", "Monad", "Functor", "Applicative", "Show", "Eq", "Ord", "Num"}
        ocaml_types = {"int", "float", "bool", "char", "string", "unit", "list", "array", "option", "ref", "exn", "bytes"}
        julia_types = {"Int", "Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64", "Float16", "Float32", "Float64", "Bool", "Char", "String", "Array", "Dict", "Set", "Tuple", "Nothing", "Any", "Number", "Real", "Complex", "Vector", "Matrix"}
        r_types = {"numeric", "integer", "double", "complex", "character", "logical", "raw", "list", "vector", "matrix", "data.frame", "factor", "array"}
        dlang_types = {"int", "uint", "long", "ulong", "short", "ushort", "byte", "ubyte", "float", "double", "real", "bool", "char", "wchar", "dchar", "string", "wstring", "dstring", "size_t", "ptrdiff_t", "void"}
        v_types = {"int", "i8", "i16", "i64", "u8", "u16", "u32", "u64", "f32", "f64", "bool", "string", "rune", "byte", "voidptr", "any"}
        solidity_types = {"uint", "uint8", "uint16", "uint32", "uint64", "uint128", "uint256", "int256", "address", "bool", "string", "bytes", "bytes32", "mapping"}
        if lang == "zig":
            return zig_types
        if lang == "nim":
            return nim_types
        if lang == "dart":
            return dart_types
        if lang in ("elixir", "erlang"):
            return elixir_types
        if lang == "haskell":
            return haskell_types
        if lang == "ocaml":
            return ocaml_types
        if lang in ("clojure", "lisp"):
            return set()
        if lang == "julia":
            return julia_types
        if lang == "r":
            return r_types
        if lang == "dlang":
            return dlang_types
        if lang == "v":
            return v_types
        if lang == "solidity":
            return solidity_types
        if lang in ("sql", "html", "xml", "css", "scss", "sass", "less", "yaml", "toml", "json", "markdown", "vue", "svelte", "protobuf", "cmake", "dockerfile", "terraform", "groovy", "powershell", "objectivec"):
            return base_types
        return base_types

    def highlight_line(self, stdscr, y, line, prefix, width, cursor_col=None, x_offset=0):
        """Draw one line with syntax highlighting. Returns the x position after drawing."""
        x = x_offset
        if prefix:
            ln_attr = getattr(self, 'color_lineno', self.color_default)
            self.draw_segment(stdscr, y, x, prefix, ln_attr, cursor_col=None, base=0)
            x += len(prefix)
        content = line
        lang = self.syntax_language
        pos = 0
        while pos < len(content):
            if lang in ("c", "cpp", "csharp", "rust"):
                if content[pos:].lstrip().startswith("#") and content[:pos].strip() == "":
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_preprocessor, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("//", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("/*", pos):
                    end = content.find("*/", pos + 2)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 2
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang in ("java", "go", "javascript", "typescript", "swift", "kotlin", "scala"):
                if content.startswith("//", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("/*", pos):
                    end = content.find("*/", pos + 2)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 2
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang == "python":
                if content.startswith("#", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("@", pos):
                    end = pos + 1
                    while end < len(content) and (content[end].isalnum() or content[end] in "_."):
                        end += 1
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_preprocessor, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang == "lua":
                if content.startswith("--", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
            elif lang == "pascal":
                if content.startswith("//", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("{", pos):
                    end = content.find("}", pos + 1)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 1
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
                if content.startswith("(*", pos):
                    end = content.find("*)", pos + 2)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 2
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang == "fortran":
                if content.startswith("!", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
            elif lang in ("ruby", "shell", "perl"):
                if content.startswith("#", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
            elif lang == "php":
                if content.startswith("//", pos) or content.startswith("#", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("/*", pos):
                    end = content.find("*/", pos + 2)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 2
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang == "assembly":
                if content[pos] in (";", "#", "@"):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content.startswith("/*", pos):
                    end = content.find("*/", pos + 2)
                    if end < 0:
                        end = len(content)
                    else:
                        end += 2
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_comment, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
                if content[pos] == ".":
                    end = pos + 1
                    while end < len(content) and (content[end].isalnum() or content[end] == "_"):
                        end += 1
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_preprocessor, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            elif lang == "evimlang":
                if content.startswith("\"", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    x += len(content) - pos; break
                if content[pos] in ('"', "'"):
                    delim = content[pos]
                    end = pos + 1
                    while end < len(content):
                        if content[end] == "\\" and end + 1 < len(content):
                            end += 2
                            continue
                        if content[end] == delim:
                            end += 1
                            break
                        end += 1
                    self.draw_segment(stdscr, y, x, content[pos:end], self.color_string, cursor_col, base=pos)
                    x += end - pos
                    pos = end
                    continue
            if content[pos] in ('"', "'"):
                delim = content[pos]
                end = pos + 1
                while end < len(content):
                    if content[end] == "\\" and end + 1 < len(content):
                        end += 2
                        continue
                    if content[end] == delim:
                        end += 1
                        break
                    end += 1
                self.draw_segment(stdscr, y, x, content[pos:end], self.color_string, cursor_col, base=pos)
                x += end - pos
                pos = end
                continue
            if content[pos].isdigit():
                start = pos
                if content.startswith("0x", pos) or content.startswith("0X", pos):
                    pos += 2
                    while pos < len(content) and (content[pos].isdigit() or content[pos].lower() in "abcdef"):
                        pos += 1
                else:
                    while pos < len(content) and (content[pos].isdigit() or content[pos] in ".eE+-"):
                        pos += 1
                self.draw_segment(stdscr, y, x, content[start:pos], self.color_number, cursor_col, base=start)
                x += pos - start
                continue
            if content[pos].isalpha() or content[pos] == "_":
                start = pos
                while pos < len(content) and (content[pos].isalnum() or content[pos] == "_"):
                    pos += 1
                token = content[start:pos]
                token_key = token if lang not in ("fortran", "pascal") else token.lower()
                attr = self.color_default
                if token_key in self._cached_keywords:
                    attr = self.color_keyword
                elif token_key in self._cached_types:
                    attr = self.color_type
                self.draw_segment(stdscr, y, x, token, attr, cursor_col, base=start)
                x += len(token)
                continue
            self.draw_segment(stdscr, y, x, content[pos], self.color_default, cursor_col, base=pos)
            x += 1
            pos += 1
        if cursor_col is not None and cursor_col == len(content) and x < x_offset + width:
            self.draw_segment(stdscr, y, x, " ", curses.A_REVERSE, cursor_col, base=cursor_col)
            x += 1
        return x

    def draw_segment(self, stdscr, y, x, text, attr, cursor_col=None, base=0):
        for i, ch in enumerate(text):
            if x + i >= stdscr.getmaxyx()[1] - 1:
                break
            ch_attr = attr
            if cursor_col is not None and base + i == cursor_col:
                ch_attr |= curses.A_REVERSE
            try:
                stdscr.addstr(y, x + i, ch, ch_attr)
            except curses.error:
                pass

    def _set_cursor_shape(self, beam=False):
        try:
            import sys
            sys.stdout.write("\033[6 q" if beam else "\033[2 q")
            sys.stdout.flush()
        except Exception:
            pass

    def register_key(self, mode, key, fn):
        self.bindings[(mode, key)] = fn

    def set_option(self, name, value):
        self.options[name] = value

    def on_start(self, fn):
        self.start_hooks.append(fn)

    def set_message(self, text):
        self.message = str(text)

    def run_python(self, source):
        old_msg = self.message
        try:
            exec(source, self.python_env)
            if self.message == old_msg:
                self.message = "Python executed"
        except Exception as exc:
            self.message = f"Python error: {exc}"

    # ── Plugin System ──────────────────────────────────────────────

    def emit(self, event, **kwargs):
        """Fire an event, calling all registered hooks. Returns list of results."""
        results = []
        for cb in self.event_hooks.get(event, []):
            try:
                r = cb(self, **kwargs)
                results.append(r)
            except Exception as exc:
                self.message = f"Event hook error ({event}): {exc}"
        # Autocommands: check pattern against filepath
        for pattern, cb in self.autocommands.get(event, []):
            try:
                fp = self.filepath or ""
                if pattern == "*" or (self.filepath and Path(fp).match(pattern)):
                    r = cb(self, **kwargs)
                    results.append(r)
            except Exception as exc:
                self.message = f"Autocmd error ({event}): {exc}"
        return results

    def on(self, event, callback):
        """Register a callback for an event. Returns callback for use as decorator."""
        self.event_hooks.setdefault(event, []).append(callback)
        return callback

    def off(self, event, callback=None):
        """Remove a callback (or all callbacks) for an event."""
        if callback is None:
            self.event_hooks.pop(event, None)
        elif event in self.event_hooks:
            self.event_hooks[event] = [cb for cb in self.event_hooks[event] if cb is not callback]

    def autocmd(self, event, pattern, callback):
        """Register an autocommand: callback fires when event matches glob pattern."""
        self.autocommands.setdefault(event, []).append((pattern, callback))

    def plugin_register(self, name, *, version="0.1", setup=None, description="", **extra):
        """Register a plugin. setup(editor) is called to initialize."""
        info = {"name": name, "version": version, "description": description,
                "enabled": True, "setup": setup, **extra}
        self.plugins[name] = info
        if setup:
            try:
                setup(self)
            except Exception as exc:
                info["enabled"] = False
                self.message = f"Plugin '{name}' setup error: {exc}"
                return False
        self.emit("plugin_loaded", plugin=name)
        return True

    def plugin_load_file(self, path):
        """Load a plugin from a Python file. The file should call editor.plugin_register(...)."""
        path = Path(path)
        if not path.exists():
            self.message = f"Plugin not found: {path}"
            return False
        try:
            code = path.read_text(encoding="utf-8")
            exec(compile(code, str(path), "exec"),
                 {"editor": self, "__builtins__": __builtins__})
            return True
        except Exception as exc:
            self.message = f"Plugin load error ({path.name}): {exc}"
            return False

    def plugin_load_dir(self, directory):
        """Load all *.py plugins from a directory."""
        d = Path(directory)
        if not d.is_dir():
            return 0
        count = 0
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_"):
                continue
            if self.plugin_load_file(f):
                count += 1
        return count

    def plugin_load_all(self):
        """Load plugins from all configured plugin directories."""
        total = 0
        for d in self.plugin_dirs:
            total += self.plugin_load_dir(d)
        if total:
            self.message = f"Loaded {total} plugin(s)"
        return total

    def plugin_disable(self, name):
        """Disable a plugin by name."""
        if name in self.plugins:
            self.plugins[name]["enabled"] = False
            teardown = self.plugins[name].get("teardown")
            if teardown:
                try:
                    teardown(self)
                except Exception:
                    pass
            self.message = f"Plugin '{name}' disabled"
        else:
            self.message = f"Plugin '{name}' not found"

    def plugin_enable(self, name):
        """Re-enable a disabled plugin."""
        if name in self.plugins:
            info = self.plugins[name]
            info["enabled"] = True
            setup = info.get("setup")
            if setup:
                try:
                    setup(self)
                except Exception as exc:
                    info["enabled"] = False
                    self.message = f"Plugin '{name}' enable error: {exc}"
                    return
            self.message = f"Plugin '{name}' enabled"
        else:
            self.message = f"Plugin '{name}' not found"

    def plugin_list(self):
        """Return a list of (name, version, enabled, description) tuples."""
        return [(p["name"], p["version"], p["enabled"], p.get("description", ""))
                for p in self.plugins.values()]

    # ── Jump List ──────────────────────────────────────────────────

    def jumplist_push(self):
        """Record current position in the jump list."""
        pos = (self.cy, self.cx)
        if self.jumplist and self.jumplist[-1] == pos:
            return
        # Truncate forward history if we moved
        if self.jumplist_pos >= 0 and self.jumplist_pos < len(self.jumplist) - 1:
            self.jumplist = self.jumplist[:self.jumplist_pos + 1]
        self.jumplist.append(pos)
        if len(self.jumplist) > 100:
            self.jumplist = self.jumplist[-100:]
        self.jumplist_pos = len(self.jumplist) - 1

    def jumplist_back(self):
        """Go to previous position in jump list (Ctrl+o)."""
        if not self.jumplist:
            self.message = "Jump list empty"
            return
        if self.jumplist_pos < 0:
            self.jumplist_pos = len(self.jumplist) - 1
        # Save current position if at end
        if self.jumplist_pos == len(self.jumplist) - 1:
            cur = (self.cy, self.cx)
            if not self.jumplist or self.jumplist[-1] != cur:
                self.jumplist.append(cur)
                self.jumplist_pos = len(self.jumplist) - 1
        if self.jumplist_pos > 0:
            self.jumplist_pos -= 1
            y, x = self.jumplist[self.jumplist_pos]
            if y < len(self.lines):
                self.cy = y
                self.cx = min(x, len(self.lines[y]))

    def jumplist_forward(self):
        """Go to next position in jump list (Ctrl+i / Tab in normal)."""
        if not self.jumplist:
            self.message = "Jump list empty"
            return
        if self.jumplist_pos < len(self.jumplist) - 1:
            self.jumplist_pos += 1
            y, x = self.jumplist[self.jumplist_pos]
            if y < len(self.lines):
                self.cy = y
                self.cx = min(x, len(self.lines[y]))

    # ── QoL: Join Lines ────────────────────────────────────────────

    def join_lines(self):
        """Join current line with next line (J key)."""
        if self.cy >= len(self.lines) - 1:
            return
        current = self.lines[self.cy].rstrip()
        next_line = self.lines[self.cy + 1].lstrip()
        sep = " " if current and next_line else ""
        self.cx = len(current)
        self.lines[self.cy] = current + sep + next_line
        del self.lines[self.cy + 1]
        self.dirty = True

    # ── QoL: Toggle Case ──────────────────────────────────────────

    def toggle_case(self):
        """Toggle case of character under cursor (~ key)."""
        line = self.current_line()
        if self.cx < len(line):
            ch = line[self.cx]
            toggled = ch.lower() if ch.isupper() else ch.upper()
            self.lines[self.cy] = line[:self.cx] + toggled + line[self.cx + 1:]
            self.cx = min(self.cx + 1, len(self.lines[self.cy]))
            self.dirty = True

    # ── QoL: Ctrl+s Save ──────────────────────────────────────────

    def quick_save(self):
        """Quick save (Ctrl+s)."""
        self.emit("before_save", filepath=self.filepath)
        self.write_file()
        self.emit("after_save", filepath=self.filepath)

    def run_file(self):
        """Run the current file using language-appropriate command."""
        if not self.filepath:
            self.message = "No file to run"
            return
        if self.dirty:
            self.write_file()
        fp = self.filepath
        lang = self.syntax_language
        run_commands = {
            "python": f"python3 {fp}",
            "javascript": f"node {fp}",
            "typescript": f"npx ts-node {fp}",
            "lua": f"lua {fp}",
            "ruby": f"ruby {fp}",
            "perl": f"perl {fp}",
            "php": f"php {fp}",
            "shell": f"bash {fp}",
            "go": f"go run {fp}",
            "rust": f"cargo run",
            "c": f"gcc -o /tmp/evim_run {fp} && /tmp/evim_run",
            "cpp": f"g++ -o /tmp/evim_run {fp} && /tmp/evim_run",
            "java": f"javac {fp} && java {os.path.splitext(os.path.basename(fp))[0]}",
            "kotlin": f"kotlinc {fp} -include-runtime -d /tmp/evim_run.jar && java -jar /tmp/evim_run.jar",
            "swift": f"swift {fp}",
            "scala": f"scala {fp}",
            "pascal": f"fpc {fp} -o/tmp/evim_run && /tmp/evim_run",
            "fortran": f"gfortran -o /tmp/evim_run {fp} && /tmp/evim_run",
            "csharp": f"dotnet-script {fp}",
            "assembly": f"nasm -f elf64 {fp} -o /tmp/evim_run.o && ld /tmp/evim_run.o -o /tmp/evim_run && /tmp/evim_run",
            "r": f"Rscript {fp}",
            "zig": f"zig run {fp}",
            "nim": f"nim r {fp}",
            "dart": f"dart run {fp}",
            "elixir": f"elixir {fp}",
            "erlang": f"escript {fp}",
            "haskell": f"runghc {fp}",
            "ocaml": f"ocaml {fp}",
            "clojure": f"clojure {fp}",
            "lisp": f"sbcl --script {fp}",
            "julia": f"julia {fp}",
            "dlang": f"dmd -run {fp}",
            "v": f"v run {fp}",
            "groovy": f"groovy {fp}",
            "powershell": f"pwsh {fp}",
            "sql": f"sqlite3 < {fp}",
            "html": f"xdg-open {fp}",
        }
        cmd = run_commands.get(lang)
        if not cmd:
            self.message = f"No run command for {lang or 'unknown'} files"
            return
        self.message = f"Running: {cmd}"
        if not self.term_visible:
            self.term_toggle()
        self.term_write(cmd + "\n")

    def start(self, stdscr):
        curses.curs_set(1)
        self._set_cursor_shape(beam=False)
        self._inject_key = None
        stdscr.keypad(True)
        curses.raw()
        stdscr.timeout(100)
        self.init_colors()
        self.load_undo_history()
        self.update_git_gutter()
        self._last_click_time = 0
        self._last_click_pos = (-1, -1)
        self._mouse_dragging = False
        if self.options.get("mouse"):
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        # Load plugins from plugin directories
        self.plugin_load_all()
        for fn in self.start_hooks:
            try:
                fn(self)
            except Exception as exc:
                self.message = f"Start hook error: {exc}"
        self.emit("startup")
        while not self.should_exit:
            if self.mode == "overlay":
                self.draw_overlay(stdscr)
            else:
                self.redraw(stdscr)
            self.handle_key(stdscr)
        self.lsp_stop()
        self.save_undo_history()
        self._set_cursor_shape(beam=False)

    def redraw(self, stdscr):
        height, width = stdscr.getmaxyx()
        bg = getattr(self, 'color_bg', curses.A_NORMAL)
        stdscr.bkgd(' ', bg)
        stdscr.erase()
        # Draw side panels and compute editor area
        editor_left = 0
        editor_right = width
        if self.explorer_visible:
            ew = self.draw_file_explorer(stdscr, height, width)
            editor_left = ew
        if self.minimap_visible and width - editor_left > 40:
            top_for_minimap = max(0, self.cy - height + 4)
            mw = self.draw_minimap(stdscr, height, width, top_for_minimap)
            editor_right = width - mw
        editor_w = editor_right - editor_left
        if editor_w < 10:
            editor_w = width
            editor_left = 0
            editor_right = width
        top = max(0, self.cy - height + 4)
        num_file_lines = len(self.lines[top: top + height - 2])
        has_gutter = bool(self.git_diff_lines)
        gutter_w = 2 if has_gutter else 0
        for idx, line in enumerate(self.lines[top: top + height - 2]):
            lineno = top + idx
            x_off = editor_left
            # Git gutter
            if has_gutter:
                diff_type = self.git_diff_lines.get(lineno)
                gutter_ch = " "
                gutter_attr = bg
                if diff_type == 'added':
                    gutter_ch = "+"
                    gutter_attr = self.color_keyword
                elif diff_type == 'modified':
                    gutter_ch = "~"
                    gutter_attr = self.color_string
                elif diff_type == 'deleted':
                    gutter_ch = "-"
                    gutter_attr = self.color_preprocessor
                try:
                    stdscr.addstr(idx, x_off, gutter_ch + " ", gutter_attr)
                except curses.error:
                    pass
            prefix = ""
            if self.options.get("number") or self.options.get("relativenumber"):
                if self.options.get("relativenumber"):
                    if lineno == self.cy:
                        num = lineno + 1
                    else:
                        num = abs(lineno - self.cy)
                else:
                    num = lineno + 1
                prefix = f"{num:4} "
            display_line = line.replace("\t", " " * self.options["tabsize"])
            # Horizontal scroll
            scroll_left = self.scroll_left if not self.options.get("wrap") else 0
            if scroll_left > 0 and scroll_left < len(display_line):
                display_line = display_line[scroll_left:]
            elif scroll_left >= len(display_line):
                display_line = ""
            cursor_col = None
            if lineno == self.cy:
                display_cx = 0
                for i in range(min(self.cx, len(line))):
                    if line[i] == '\t':
                        display_cx += self.options["tabsize"]
                    else:
                        display_cx += 1
                cursor_col = display_cx - scroll_left
            # Offset x by gutter width
            full_prefix = " " * gutter_w + prefix if gutter_w else prefix
            # Truncate line to editor area width
            avail_w = editor_w
            drawn = self.highlight_line(stdscr, idx, display_line, full_prefix, avail_w, cursor_col, x_off)
            # Cursorline highlight
            if self.options.get("cursorline") and lineno == self.cy:
                if drawn < x_off + editor_w - 1:
                    try:
                        stdscr.addstr(idx, drawn, " " * (x_off + editor_w - 1 - drawn), curses.A_UNDERLINE | bg)
                    except curses.error:
                        pass
                try:
                    stdscr.chgat(idx, x_off + gutter_w, editor_w - 1 - gutter_w, curses.A_UNDERLINE | bg)
                except curses.error:
                    pass
            elif drawn < x_off + editor_w - 1:
                try:
                    stdscr.addstr(idx, drawn, " " * (x_off + editor_w - 1 - drawn), bg)
                except curses.error:
                    pass
            # Draw indent guides at each tab stop within the leading whitespace
            if self.options.get("indent_guides", False):
                prefix_len = len(full_prefix)
                indent = len(display_line) - len(display_line.lstrip())
                tab = self.options.get("tabsize", 4)
                if indent > 0 and tab > 0:
                    for col in range(0, indent, tab):
                        gx = x_off + prefix_len + col
                        if x_off <= gx < x_off + editor_w - 1:
                            try:
                                stdscr.addstr(idx, gx, "│", curses.A_DIM | bg)
                            except curses.error:
                                pass
        # Fill empty rows below file content with tilde markers
        ln_attr = getattr(self, 'color_lineno', bg)
        for idx in range(num_file_lines, height - 2):
            try:
                stdscr.addstr(idx, editor_left, "~".ljust(editor_w - 1), ln_attr)
            except curses.error:
                pass
        # Enhanced status bar
        dirty_marker = "[+]" if self.dirty else ""
        ft = self.syntax_language or "plain"
        linecol = f"Ln {self.cy + 1}/{len(self.lines)}, Col {self.cx + 1}"
        left_status = f" {self.mode.upper()} | {self.filepath or '[no file]'} {dirty_marker}"
        run_btn = " \u25b6 Run " if self.filepath else ""
        right_status = f"{ft} | {linecol} "
        mid = self.message
        gap = width - 1 - len(left_status) - len(run_btn) - len(right_status)
        if gap > len(mid) + 2:
            center = f" {mid} "
            pad = gap - len(center)
            status = left_status + run_btn + " " * (pad // 2) + center + " " * (pad - pad // 2) + right_status
        else:
            status = (left_status + run_btn + " " + mid)[:width - 1 - len(right_status)] + right_status
        status_attr = getattr(self, 'color_status', curses.A_REVERSE)
        status_padded = status[:width - 1].ljust(width - 1)
        try:
            stdscr.addstr(height - 2, 0, status_padded, status_attr)
        except curses.error:
            pass
        # Highlight the Run button in green
        if run_btn and self.filepath:
            run_col = len(left_status)
            if run_col + len(run_btn) < width - 1:
                run_attr = curses.A_BOLD | curses.color_pair(4)
                try:
                    stdscr.addstr(height - 2, run_col, run_btn, run_attr)
                except curses.error:
                    pass
        cmd_attr = getattr(self, 'color_cmdline', curses.A_NORMAL)
        if self.mode == "command" and self.options.get("show_command"):
            command_line = ":" + self.command
            cmdline_padded = command_line[:width - 1].ljust(width - 1)
            try:
                stdscr.addstr(height - 1, 0, cmdline_padded, cmd_attr)
            except curses.error:
                pass
        elif self.macro_recording:
            rec = f"recording @{self.macro_recording}"
            try:
                stdscr.addstr(height - 1, 0, rec[:width - 1].ljust(width - 1), cmd_attr)
            except curses.error:
                pass
        else:
            hint = "Press : for commands, i for insert, ESC to return."
            try:
                stdscr.addstr(height - 1, 0, hint[:width - 1].ljust(width - 1), cmd_attr)
            except curses.error:
                pass
        line = self.lines[self.cy] if self.cy < len(self.lines) else ""
        display_cx = 0
        for i in range(min(self.cx, len(line))):
            if line[i] == '\t':
                display_cx += self.options["tabsize"]
            else:
                display_cx += 1
        prefix_w = gutter_w + (5 if self.options.get("number") or self.options.get("relativenumber") else 0)
        scroll_left = self.scroll_left if not self.options.get("wrap") else 0
        # Draw terminal panel if visible
        if self.term_visible:
            self.draw_terminal_panel(stdscr)
        # Draw LSP diagnostic markers in gutter
        if self.lsp_enabled and self.lsp_diagnostics:
            for dline, dcol, sev, dmsg in self.lsp_diagnostics:
                row = dline - top
                if 0 <= row < height - 2:
                    marker = "●"
                    if sev == 1:
                        attr = curses.color_pair(2) | curses.A_BOLD
                    elif sev == 2:
                        attr = curses.color_pair(8) | curses.A_BOLD
                    else:
                        attr = curses.color_pair(5) | curses.A_BOLD
                    try:
                        stdscr.addstr(row, editor_left, marker, attr)
                    except curses.error:
                        pass
        # Draw LSP completion popup
        if self.lsp_completion_active:
            self.draw_lsp_completion_popup(stdscr, height, width)
        # Draw LSP status indicator
        if self.lsp_enabled:
            lsp_ind = " LSP"
            lsp_col = width - len(lsp_ind) - 1
            if lsp_col > 0:
                lsp_attr = curses.color_pair(4) | curses.A_BOLD
                try:
                    stdscr.addstr(height - 2, lsp_col, lsp_ind, lsp_attr)
                except curses.error:
                    pass
        if self.mode != "terminal" and self.mode != "explorer":
            curses.setsyx(self.cy - top, display_cx - scroll_left + prefix_w + editor_left)
        curses.doupdate()

    def draw_overlay(self, stdscr):
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if self.show_welcome:
            lines = [
                "Welcome to EVim",
                "Uganda Fund Edition",
                "",
                "Normal mode commands:",
                "  h/j/k/l  - move",
                "  i        - insert mode",
                "  dd       - delete line",
                "  dw       - delete word",
                "  yy       - yank line",
                "  p        - paste",
                "  u        - undo",
                "  /pattern - search forward",
                "  n/N      - next/previous search",
                "  :w, :q, :wq, :source, :help",
                "",
                "Press any key to continue...",
            ]
        else:
            lines = [
                "EVim Help",
                "",
                "── Movement ──",
                "  h/j/k/l     move left/down/up/right",
                "  w/b/e       word forward/backward/end",
                "  0 / $       line start / line end",
                "  gg / G      file start / file end",
                "  %           match bracket",
                "  Ctrl+d/u    scroll half page down/up",
                "  Ctrl+f/b    scroll full page down/up",
                "",
                "── Editing ──",
                "  i / I       insert / insert at start",
                "  a / A       (append) / append at end",
                "  o / O       open line below / above",
                "  x           delete char",
                "  dd / dw     delete line / word",
                "  cw          change word",
                "  yy          yank line",
                "  p           paste",
                "  J           join lines",
                "  ~           toggle case",
                "  u / Ctrl+r  undo / redo",
                "  .           repeat last edit",
                "  Ctrl+/      toggle comment",
                "  Ctrl+s      quick save",
                "",
                "── Search & Selection ──",
                "  /pattern    search forward",
                "  ?pattern    search backward",
                "  n / N       next / prev match",
                "  v           toggle visual selection",
                "  d / y       delete / yank selection",
                "",
                "── Navigation ──",
                "  Ctrl+o      jump back",
                "  Ctrl+i      jump forward",
                "  Ctrl+p      fuzzy file finder",
                "  Ctrl+n      toggle terminal",
                "  Ctrl+e      toggle file explorer",
                "  Ctrl+m      toggle minimap",
                "  Ctrl+Up/Dn  fast scroll (5 lines)",
                "  Ctrl+g      file info",
                "",
                "── Macros / Marks / Registers ──",
                "  q{a-z}      record macro",
                "  @{a-z}      play macro",
                "  m{a-z}      set mark",
                "  '{a-z}      goto mark",
                "  \"{a-z}      select register",
                "",
                "── Commands ──",
                "  :w :q :wq :q!   save/quit",
                "  :e <file>       open buffer",
                "  :bn :bp :ls     buffer navigation",
                "  :cd :pwd :!cmd  directory/shell",
                "  :sort :noh      sort lines / clear search",
                "  :reg :marks     show registers/marks",
                "  :explorer       toggle file explorer",
                "  :minimap        toggle minimap",
                "  :theme <name>   change theme",
                "  :source <file>  source a script",
                "  :help           this help",
                "",
                "── Plugins ──",
                "  :PluginLoad     load all plugins",
                "  :PluginList     list loaded plugins",
                "  :PluginDisable  disable a plugin",
                "  :PluginEnable   enable a plugin",
                "  Dirs: ~/.config/evim/plugins/",
                "        .evim/plugins/",
                "",
                "── LSP ──",
                "  :lsp            start language server",
                "  :lsp stop       stop language server",
                "  :lsp restart    restart server",
                "  :lsp status     show server info",
                "  gd              go to definition",
                "  gr              find references",
                "  K               hover info",
                "  Tab (insert)    LSP completion",
                "",
                "Press any key to return...",
            ]
        box_top = max(0, (height - len(lines)) // 2 - 1)
        box_left = max(0, (width - 60) // 2)
        for idx, text in enumerate(lines):
            if box_top + idx >= height - 1:
                break
            if box_left >= width:
                continue
            text_width = max(0, width - box_left - 1)
            if text_width == 0:
                continue
            try:
                stdscr.addstr(box_top + idx, box_left, text[:text_width])
            except curses.error:
                pass
        curses.setsyx(0, 0)
        curses.doupdate()

    def handle_key(self, stdscr):
        if hasattr(self, '_inject_key') and self._inject_key is not None:
            ch = self._inject_key
            self._inject_key = None
        else:
            ch = stdscr.getch()
        if ch < 0:
            return
        # Record macro keys (but not the q that stops recording)
        if self.macro_recording and not self.macro_playing:
            if ch != ord('q') or self.mode != "normal":
                self.macro_keys.append(ch)
        if self.mode == "overlay":
            self.mode = "normal"
            self.show_welcome = False
            self.message = "EVim - normal mode"
            return
        # Mouse handling
        if ch == curses.KEY_MOUSE and self.options.get("mouse"):
            try:
                _, mx, my, _, bstate = curses.getmouse()
                height, width = stdscr.getmaxyx()
                top = max(0, self.cy - height + 4)
                has_nums = self.options.get("number") or self.options.get("relativenumber")
                prefix_w = (2 if self.git_diff_lines else 0) + (5 if has_nums else 0)
                scroll_left = self.scroll_left if not self.options.get("wrap") else 0
                now = time.time()
                # Check if click is inside the file explorer
                if self.explorer_visible:
                    ew = min(self.explorer_width, width // 3)
                    if mx < ew:
                        if bstate & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                            if self.mode != "explorer":
                                self.mode = "explorer"
                                self.message = "EXPLORER (Ctrl+e to close)"
                            # Click on entry
                            if my >= 1 and my < height - 2:
                                clicked_idx = self.explorer_scroll + (my - 1)
                                if 0 <= clicked_idx < len(self.explorer_entries):
                                    self.explorer_cursor = clicked_idx
                                    # Double-click opens
                                    if hasattr(self, '_explorer_last_click') and self._explorer_last_click == clicked_idx and (now - self._explorer_click_time) < 0.4:
                                        self.explorer_handle_key(10)  # simulate Enter
                                        self._explorer_last_click = -1
                                    else:
                                        self._explorer_last_click = clicked_idx
                                        self._explorer_click_time = now
                        # Scroll wheel in explorer
                        if bstate & (curses.BUTTON4_PRESSED if hasattr(curses, 'BUTTON4_PRESSED') else 0):
                            self.explorer_scroll = max(0, self.explorer_scroll - 3)
                            return
                        if bstate & (curses.BUTTON5_PRESSED if hasattr(curses, 'BUTTON5_PRESSED') else 0):
                            self.explorer_scroll += 3
                            return
                        return
                # Check if click is inside the terminal panel
                if self.term_visible:
                    panel_h = max(5, height // 2)
                    panel_w = max(20, width // 2)
                    panel_y = height - panel_h - 2
                    panel_x = width - panel_w
                    if panel_y <= my < panel_y + panel_h and panel_x <= mx < panel_x + panel_w:
                        if bstate & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                            if self.mode != "terminal":
                                self.mode = "terminal"
                                self.message = "TERMINAL (Ctrl+n to return)"
                            return
                        # Scroll wheel inside terminal panel
                        if bstate & (curses.BUTTON4_PRESSED if hasattr(curses, 'BUTTON4_PRESSED') else 0):
                            self.term_scroll = min(self.term_scroll + 3, max(0, len(self.term_lines) - 3))
                            return
                        if bstate & (curses.BUTTON5_PRESSED if hasattr(curses, 'BUTTON5_PRESSED') else 0):
                            self.term_scroll = max(0, self.term_scroll - 3)
                            return
                        return
                # Click on Run button in status bar
                if my == height - 2 and self.filepath:
                    run_label = " \u25b6 Run "
                    run_col = len(f" {self.mode.upper()} | {self.filepath or '[no file]'} {'[+]' if self.dirty else ''}")
                    if bstate & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                        if run_col <= mx < run_col + len(run_label):
                            self.run_file()
                            return
                # Scroll wheel (works in any mode in the editor area)
                if bstate & (curses.BUTTON4_PRESSED if hasattr(curses, 'BUTTON4_PRESSED') else 0):
                    self.scroll_half_up(height)
                    return
                if bstate & (curses.BUTTON5_PRESSED if hasattr(curses, 'BUTTON5_PRESSED') else 0):
                    self.scroll_half_down(height)
                    return
                # Click / drag in the editor text area
                if my < height - 2:
                    target_line = top + my
                    editor_left_off = min(self.explorer_width, width // 3) if self.explorer_visible else 0
                    target_col = max(0, mx - prefix_w - editor_left_off + scroll_left)
                    if 0 <= target_line < len(self.lines):
                        target_col = min(target_col, len(self.lines[target_line]))
                        # Double-click: select word under cursor
                        if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                            same_pos = (self._last_click_pos == (target_line, target_col))
                            if same_pos and (now - self._last_click_time) < 0.4:
                                # Double click — select word
                                line = self.lines[target_line]
                                wstart = target_col
                                wend = target_col
                                while wstart > 0 and (line[wstart - 1].isalnum() or line[wstart - 1] == '_'):
                                    wstart -= 1
                                while wend < len(line) and (line[wend].isalnum() or line[wend] == '_'):
                                    wend += 1
                                if wend > wstart:
                                    self.selection = (target_line, wstart)
                                    self.cy = target_line
                                    self.cx = wend
                                    if self.mode not in ("visual", "command"):
                                        self.mode = "normal"
                                    self.message = "Word selected"
                                self._last_click_time = 0
                                self._last_click_pos = (-1, -1)
                                return
                            # Single click — position cursor
                            self._last_click_time = now
                            self._last_click_pos = (target_line, target_col)
                            # If in terminal mode, switch back to normal
                            if self.mode == "terminal":
                                self.mode = "normal"
                                self.message = "EVim - normal mode"
                            # Clear selection on plain click
                            self.selection = None
                            self._mouse_dragging = True
                            self.cy = target_line
                            self.cx = target_col
                        # Drag (button1 held + motion) — visual selection
                        elif bstate & curses.REPORT_MOUSE_POSITION or bstate & curses.BUTTON1_RELEASED:
                            if self._mouse_dragging:
                                if self.selection is None:
                                    self.selection = (self.cy, self.cx)
                                self.cy = target_line
                                self.cx = target_col
                                if bstate & curses.BUTTON1_RELEASED:
                                    self._mouse_dragging = False
                                    sy, sx = self.selection
                                    if sy == self.cy and sx == self.cx:
                                        self.selection = None
            except curses.error:
                pass
            return
        # Ctrl+n - toggle terminal panel (works from any mode except command)
        if ch == 14 and self.mode != "command":
            if self.mode == "terminal":
                self.term_visible = False
                self.mode = "normal"
                self.message = "EVim - normal mode"
                self._set_cursor_shape(beam=False)
            else:
                self.term_toggle()
            return
        # Ctrl+e - toggle file explorer (works from any mode except command)
        if ch == 5 and self.mode != "command":
            if self.mode == "explorer":
                self.explorer_visible = False
                self.mode = "normal"
                self.message = "EVim - normal mode"
            else:
                self.explorer_toggle()
            return
        # Ctrl+m - toggle minimap
        if ch == 13 and self.mode not in ("command", "insert"):
            self.minimap_toggle()
            return
        # F5 - run file
        if ch == curses.KEY_F5:
            self.run_file()
            return
        # Explorer mode input handling
        if self.mode == "explorer":
            self.explorer_handle_key(ch)
            return
        # Terminal mode input handling — keystrokes go directly to pty
        if self.mode == "terminal":
            if ch in (curses.KEY_EXIT, 27):
                self.term_visible = False
                self.mode = "normal"
                self.message = "EVim - normal mode"
                self._set_cursor_shape(beam=False)
                return
            if ch in (curses.KEY_ENTER, 10, 13):
                self.term_write("\n")
                return
            if ch in (curses.KEY_BACKSPACE, 127, curses.ascii.DEL):
                self.term_write("\x7f")
                return
            if ch == curses.KEY_UP:
                self.term_write("\x1b[A")
                return
            if ch == curses.KEY_DOWN:
                self.term_write("\x1b[B")
                return
            if ch == curses.KEY_LEFT:
                self.term_write("\x1b[D")
                return
            if ch == curses.KEY_RIGHT:
                self.term_write("\x1b[C")
                return
            if ch == curses.KEY_HOME:
                self.term_write("\x1b[H")
                return
            if ch == curses.KEY_END:
                self.term_write("\x1b[F")
                return
            if ch == curses.KEY_DC:  # Delete key
                self.term_write("\x1b[3~")
                return
            if ch == curses.KEY_PPAGE:  # Page Up — scroll terminal
                self.term_scroll = min(self.term_scroll + 5, max(0, len(self.term_lines) - 5))
                return
            if ch == curses.KEY_NPAGE:  # Page Down — scroll terminal
                self.term_scroll = max(0, self.term_scroll - 5)
                return
            # Send control characters and printable chars directly
            if 0 <= ch < 256:
                self.term_write(chr(ch))
                return
            return
        if self.mode == "insert":
            # LSP completion navigation
            if self.lsp_completion_active:
                if ch == 9:  # Tab - next completion
                    self.lsp_completion_idx = (self.lsp_completion_idx + 1) % len(self.lsp_completions)
                    return
                if ch == curses.KEY_UP:
                    self.lsp_completion_idx = (self.lsp_completion_idx - 1) % len(self.lsp_completions)
                    return
                if ch == curses.KEY_DOWN:
                    self.lsp_completion_idx = (self.lsp_completion_idx + 1) % len(self.lsp_completions)
                    return
                if ch in (curses.KEY_ENTER, 10, 13):
                    self.lsp_apply_completion()
                    return
                if ch == 27:  # ESC dismisses completion
                    self.lsp_completion_active = False
                    self.lsp_completions = []
                    self.mode = "normal"
                    self.message = "EVim - normal mode"
                    self._set_cursor_shape(beam=False)
                    return
                # Any other key dismisses completion and processes normally
                self.lsp_completion_active = False
                self.lsp_completions = []
            if ch in (curses.KEY_EXIT, 27):
                self.mode = "normal"
                self.message = "EVim - normal mode"
                self._set_cursor_shape(beam=False)
                return
            if ch in (curses.KEY_BACKSPACE, 127, curses.ascii.DEL):
                self.snapshot()
                self.backspace()
                return
            if ch in (curses.KEY_ENTER, 10, 13):
                self.snapshot()
                self.newline()
                return
            if ch == curses.KEY_DC:
                self.snapshot()
                self.delete_char()
                return
            if ch == curses.KEY_LEFT:
                self.move_left()
                return
            if ch == curses.KEY_RIGHT:
                self.move_right()
                return
            if ch == curses.KEY_UP:
                self.move_up()
                return
            if ch == curses.KEY_DOWN:
                self.move_down()
                return
            if ch == 9:
                if self.lsp_enabled and self.lsp_initialized:
                    self.lsp_completion()
                else:
                    self.do_completion()
                return
            if ch == 19:  # Ctrl+s in insert mode
                self.quick_save()
                return
            if curses.ascii.isprint(ch):
                self.snapshot()
                char = chr(ch)
                if self.syntax_language and self.try_skip_closing(char):
                    self.lsp_did_change()
                    return
                if self.syntax_language and self.try_insert_pair(char):
                    self.lsp_did_change()
                    return
                self.insert_char(char)
                self.lsp_did_change()
                return
            return
        if self.mode == "command":
            if ch in (curses.KEY_ENTER, 10, 13):
                self.run_command()
                self.command = ""
                self.mode = "normal"
                return
            if ch in (curses.KEY_BACKSPACE, 127, curses.ascii.DEL):
                self.command = self.command[:-1]
                return
            if ch == 27:
                self.mode = "normal"
                self.command = ""
                self.message = "EVim - normal mode"
                return
            if 0 <= ch < 256:
                self.command += chr(ch)
            return
        key = self.key_name(ch)
        if self.pending_normal:
            combo = self.pending_normal + key
            self.pending_normal = ""
            if combo == "dd":
                self.snapshot()
                self.delete_line()
                self.last_edit = ("delete_line", ())
                return
            if combo == "dw":
                self.snapshot()
                self.delete_word()
                self.last_edit = ("delete_word", ())
                return
            if combo == "yy":
                self.yank_line()
                return
            if combo == "cw":
                self.snapshot()
                self.change_word()
                return
            if combo == "vv":
                self.toggle_selection()
                return
            # failed combo, continue processing key normally
        if self.selection and key == "d":
            self.snapshot()
            self.delete_selection()
            return
        if self.selection and key == "y":
            self.yank_selection()
            return
        if key == "d":
            self.pending_normal = "d"
            return
        if key == "y":
            self.pending_normal = "y"
            return
        binding = self.bindings.get(("normal", key))
        if binding:
            self.call_binding(binding)
            return
        if key == "i":
            self.mode = "insert"
            self.message = "EVim - insert mode"
            self._set_cursor_shape(beam=True)
            self.pending_normal = ""
            return
        if key == ":":
            self.mode = "command"
            self.command = ""
            self.pending_normal = ""
            return
        if key == "h":
            self.move_left()
            self.pending_normal = ""
            return
        if key == "j":
            self.move_down()
            self.pending_normal = ""
            return
        if key == "k":
            self.move_up()
            self.pending_normal = ""
            return
        if key == "l":
            self.move_right()
            self.pending_normal = ""
            return
        if key == "x":
            self.snapshot()
            self.delete_char()
            self.last_edit = ("delete_char", ())
            self.pending_normal = ""
            return
        if key == "p":
            self.snapshot()
            self.paste_after()
            self.pending_normal = ""
            return
        if key == "u":
            self.undo()
            self.pending_normal = ""
            return
        if ch == 18:  # Ctrl+r for redo
            self.redo()
            self.pending_normal = ""
            return
        if key == "n":
            self.find_again(1)
            self.pending_normal = ""
            return
        if key == "N":
            self.find_again(-1)
            self.pending_normal = ""
            return
        if key == "v":
            self.toggle_selection()
            self.pending_normal = ""
            return
        if key == "0":
            self.cx = 0
            self.pending_normal = ""
            return
        if key == "$":
            self.cx = len(self.current_line())
            self.pending_normal = ""
            return
        if key == "G":
            self.jumplist_push()
            self.cy = len(self.lines) - 1
            self.cx = min(self.cx, len(self.current_line()))
            self.pending_normal = ""
            return

        # gg - go to top
        if self.pending_normal == "g" and key == "g":
            self.jumplist_push()
            self.cy = 0
            self.cx = 0
            self.pending_normal = ""
            return
        # gd - go to definition (LSP)
        if self.pending_normal == "g" and key == "d":
            self.pending_normal = ""
            self.lsp_goto_definition()
            return
        # gr - find references (LSP)
        if self.pending_normal == "g" and key == "r":
            self.pending_normal = ""
            self.lsp_references()
            return
        if key == "g":
            self.pending_normal = "g"
            return
        if self.pending_normal == "g":
            self.pending_normal = ""

        # Word motions
        if key == "w":
            self.word_forward()
            return
        if key == "b":
            self.word_backward()
            return
        if key == "e":
            self.word_end()
            return

        # o/O - open line
        if key == "o":
            self.open_line_below()
            return
        if key == "O":
            self.open_line_above()
            return

        # A/I - insert at end/start
        if key == "A":
            self.insert_at_end()
            return
        if key == "I":
            self.insert_at_start()
            return

        # % - bracket match
        if key == "%":
            self.match_bracket()
            return

        # Scroll: Ctrl+d, Ctrl+u, Ctrl+f, Ctrl+b
        if ch == 4:  # Ctrl+d
            height = stdscr.getmaxyx()[0]
            self.scroll_half_down(height)
            return
        if ch == 21:  # Ctrl+u
            height = stdscr.getmaxyx()[0]
            self.scroll_half_up(height)
            return
        if ch == 6:  # Ctrl+f
            height = stdscr.getmaxyx()[0]
            self.scroll_page_down(height)
            return
        if ch == 2:  # Ctrl+b
            height = stdscr.getmaxyx()[0]
            self.scroll_page_up(height)
            return

        # Ctrl+/ - toggle comment (sends 31 on most terminals)
        if ch == 31:
            self.toggle_comment()
            return

        # Ctrl+Up / Ctrl+Down - fast scroll (5 lines)
        if ch == 566 or ch == curses.KEY_SR:  # Ctrl+Up
            self.cy = max(0, self.cy - 5)
            self.cx = min(self.cx, len(self.current_line()))
            return
        if ch == 525 or ch == curses.KEY_SF:  # Ctrl+Down
            self.cy = min(len(self.lines) - 1, self.cy + 5)
            self.cx = min(self.cx, len(self.current_line()))
            return

        # . - dot repeat (replay last edit action keys)
        if key == ".":
            if self.last_edit:
                name, args = self.last_edit
                method = getattr(self, name, None)
                if method:
                    self.snapshot()
                    method(*args)
            return

        # Macros: q to toggle recording, @ to play
        if key == "q":
            if self.macro_recording:
                self.stop_macro()
            else:
                self.pending_normal = "q"
            return
        if self.pending_normal == "q":
            self.start_macro(key)
            self.pending_normal = ""
            return
        if key == "@":
            self.pending_normal = "@"
            return
        if self.pending_normal == "@":
            self.play_macro(key, stdscr)
            self.pending_normal = ""
            return

        # Marks: m to set, ' to jump
        if key == "m":
            self.pending_normal = "m"
            return
        if self.pending_normal == "m":
            self.set_mark(key)
            self.pending_normal = ""
            return
        if key == "'":
            self.pending_normal = "'"
            return
        if self.pending_normal == "'":
            self.goto_mark(key)
            self.pending_normal = ""
            return

        # Registers: " to select register
        if key == '"':
            self.pending_normal = '"'
            return
        if self.pending_normal == '"':
            self.pending_register = key
            self.pending_normal = ""
            return

        # Ctrl+p - fuzzy file finder
        if ch == 16:  # Ctrl+p
            self.fuzzy_find(stdscr)
            return

        # J - join lines
        if key == "J":
            self.snapshot()
            self.join_lines()
            self.last_edit = ("join_lines", ())
            return

        # K - LSP hover info
        if key == "K":
            self.lsp_hover()
            return

        # ~ - toggle case
        if key == "~":
            self.snapshot()
            self.toggle_case()
            return

        # Ctrl+s - quick save
        if ch == 19:  # Ctrl+s
            self.quick_save()
            return

        # Ctrl+o - jump back
        if ch == 15:  # Ctrl+o
            self.jumplist_back()
            return

        # Ctrl+i - jump forward (Tab in normal mode)
        if ch == 9:  # Ctrl+i / Tab
            self.jumplist_forward()
            return

        # Ctrl+g - file info
        if ch == 7:  # Ctrl+g
            total = len(self.lines)
            pct = int((self.cy + 1) / total * 100) if total else 0
            fname = self.filepath or "[No Name]"
            mod = " [Modified]" if self.dirty else ""
            self.message = f'"{fname}"{mod} {total} lines --{pct}%-- Ln {self.cy + 1}, Col {self.cx + 1}'
            return

        # / and ? for search
        if key == "/":
            self.mode = "command"
            self.command = "/"
            self.pending_normal = ""
            return
        if key == "?":
            self.mode = "command"
            self.command = "?"
            self.pending_normal = ""
            return

    def key_name(self, ch):
        if ch == curses.KEY_LEFT:
            return "LEFT"
        if ch == curses.KEY_RIGHT:
            return "RIGHT"
        if ch == curses.KEY_UP:
            return "UP"
        if ch == curses.KEY_DOWN:
            return "DOWN"
        if ch == curses.KEY_F1:
            return "<F1>"
        try:
            return chr(ch)
        except Exception:
            return str(ch)

    def call_binding(self, binding):
        try:
            if callable(binding):
                try:
                    binding(self)
                except TypeError:
                    binding()
        except Exception as exc:
            self.message = f"Binding error: {exc}"

    def current_line(self):
        return self.lines[self.cy]

    def set_cursor(self):
        self.cx = max(0, min(self.cx, len(self.current_line())))
        self.cy = max(0, min(self.cy, len(self.lines) - 1))

    def current_char(self):
        line = self.current_line()
        return line[self.cx] if self.cx < len(line) else ""

    def try_skip_closing(self, char):
        closings = {')': '(', ']': '[', '}': '{', '"': '"', "'": "'"}
        line = self.current_line()
        if char in closings and self.cx < len(line) and line[self.cx] == char:
            self.cx += 1
            return True
        return False

    def try_insert_pair(self, char):
        pairs = {'(': ')', '[': ']', '{': '}', '"': '"', "'": "'"}
        if char not in pairs:
            return False
        line = self.current_line()
        closing = pairs[char]
        self.lines[self.cy] = line[:self.cx] + char + closing + line[self.cx:]
        self.cx += 1
        self.mark_dirty()
        return True

    def move_left(self):
        if self.cx > 0:
            self.cx -= 1
        elif self.cy > 0:
            self.cy -= 1
            self.cx = len(self.current_line())

    def move_right(self):
        if self.cx < len(self.current_line()):
            self.cx += 1
        elif self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0

    def move_up(self):
        if self.cy > 0:
            self.cy -= 1
            self.cx = min(self.cx, len(self.current_line()))

    def move_down(self):
        if self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = min(self.cx, len(self.current_line()))

    def insert_char(self, ch):
        line = self.current_line()
        self.lines[self.cy] = line[:self.cx] + ch + line[self.cx:]
        self.cx += 1
        self.mark_dirty()

    def newline(self):
        line = self.current_line()
        before = line[: self.cx]
        after = line[self.cx :]
        self.lines[self.cy] = before
        self.lines.insert(self.cy + 1, after)
        self.cy += 1
        indent = self.calculate_indent(self.cy - 1)
        self.lines[self.cy] = " " * indent + self.lines[self.cy].lstrip(" ")
        self.cx = indent
        self.mark_dirty()

    def calculate_indent(self, line_no):
        if not self.syntax_language:
            return 0
        if line_no < 0 or line_no >= len(self.lines):
            return 0
        line = self.lines[line_no]
        stripped = line.strip()
        base = len(line) - len(line.lstrip(" "))
        if not stripped:
            return base
        if stripped.endswith(("{", "(", "[", ":")):
            return base + self.options.get("tabsize", 4)
        if self.syntax_language in ("ruby",) and stripped.endswith(("do", "then", "|")):
            return base + self.options.get("tabsize", 4)
        if self.syntax_language == "shell" and stripped.endswith(("then", "do", "else")):
            return base + self.options.get("tabsize", 4)
        return base

    def backspace(self):
        if self.cx > 0:
            line = self.current_line()
            self.lines[self.cy] = line[: self.cx - 1] + line[self.cx :]
            self.cx -= 1
            self.mark_dirty()
        elif self.cy > 0:
            prev = self.lines[self.cy - 1]
            self.cx = len(prev)
            self.lines[self.cy - 1] = prev + self.current_line()
            del self.lines[self.cy]
            self.cy -= 1
            self.mark_dirty()

    def delete_char(self):
        line = self.current_line()
        if self.cx < len(line):
            self.lines[self.cy] = line[: self.cx] + line[self.cx + 1 :]
            self.mark_dirty()
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] += self.lines[self.cy + 1]
            del self.lines[self.cy + 1]
            self.mark_dirty()

    def run_command(self):
        command = self.command.strip()
        if not command:
            self.message = ""
            return
        self.run_ex(command)

    def _resolve_option(self, name):
        """Map option aliases to internal names."""
        return OPTION_ALIASES.get(name, name)

    def run_ex(self, command):
        """Execute a single ex command."""
        command = command.strip()
        if command.startswith(":"):
            command = command[1:].strip()
        if not command:
            return

        # Search / replace shortcuts
        if command.startswith("/") or command.startswith("?"):
            self.search_command(command)
            return
        if command.startswith("s/") or command.startswith("%s/"):
            self.replace_command(command)
            return

        # Parse command name and rest
        parts = command.split(None, 1)
        cmd = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        # set
        if cmd == "set":
            self._ex_set(rest)
            return

        # write
        if cmd in ("w", "write"):
            if rest:
                self.filepath = rest
            self.write_file()
            return

        # quit
        if cmd == "q":
            if self.dirty:
                self.message = "Unsaved changes! Use :q! to force quit."
            else:
                self.should_exit = True
            return
        if cmd == "q!":
            self.should_exit = True
            return
        if cmd == "wq":
            self.write_file()
            self.should_exit = True
            return

        # edit / open buffer
        if cmd in ("e", "edit"):
            if rest:
                self.open_buffer(rest)
            else:
                self.message = "Usage: :e <filename>"
            return

        # buffers
        if cmd in ("bn", "bnext"):
            self.next_buffer()
            return
        if cmd in ("bp", "bprev"):
            self.prev_buffer()
            return
        if cmd in ("ls", "buffers"):
            self.list_buffers()
            return

        # echo
        if cmd == "echo":
            self.message = rest
            return

        # theme / colorscheme
        if cmd in ("theme", "colorscheme"):
            if rest:
                self.set_theme(rest.strip())
            else:
                self.message = f"Current theme: {self.options.get('theme', 'default')}"
            return

        # syntax
        if cmd == "syntax":
            val = rest.strip().lower()
            if val == "on":
                self.options["syntax"] = True
                self.detect_syntax()
                self.message = "Syntax highlighting enabled"
            elif val == "off":
                self.options["syntax"] = False
                self.syntax_language = None
                self.message = "Syntax highlighting disabled"
            else:
                self.message = "Usage: :syntax on|off"
            return

        # help
        if cmd == "help":
            self.mode = "overlay"
            self.show_welcome = False
            self.message = "EVim help"
            return

        # map commands
        if cmd == "map":
            mparts = rest.split(None, 2)
            if len(mparts) >= 3:
                mode, key, action = mparts[0], mparts[1], mparts[2]
                self.register_key(mode, key,
                    lambda e, a=action: e.run_mapped_action(a))
                self.message = f"Mapped [{mode}] {key} -> {action}"
            else:
                self.message = "Usage: :map <mode> <key> <action>"
            return
        if cmd in ("nmap", "nnoremap"):
            mparts = rest.split(None, 1)
            if len(mparts) >= 2:
                key, action = mparts[0], mparts[1]
                self.register_key("normal", key,
                    lambda e, a=action: e.run_mapped_action(a))
                self.message = f"Mapped [normal] {key} -> {action}"
            else:
                self.message = f"Usage: :{cmd} <key> <action>"
            return
        if cmd in ("imap", "inoremap"):
            mparts = rest.split(None, 1)
            if len(mparts) >= 2:
                key, action = mparts[0], mparts[1]
                self.register_key("insert", key,
                    lambda e, a=action: e.run_mapped_action(a))
                self.message = f"Mapped [insert] {key} -> {action}"
            else:
                self.message = f"Usage: :{cmd} <key> <action>"
            return
        if cmd in ("vmap", "vnoremap"):
            mparts = rest.split(None, 1)
            if len(mparts) >= 2:
                key, action = mparts[0], mparts[1]
                self.register_key("visual", key,
                    lambda e, a=action: e.run_mapped_action(a))
                self.message = f"Mapped [visual] {key} -> {action}"
            else:
                self.message = f"Usage: :{cmd} <key> <action>"
            return

        # python / py
        if cmd in ("python", "py"):
            if rest:
                self.run_python(rest)
            else:
                self.message = "Usage: :python <code>"
            return

        # source
        if cmd == "source":
            if rest:
                path = Path(rest.strip())
                if path.exists():
                    content = path.read_text(encoding="utf-8")
                    try:
                        exec(compile(content, str(path), "exec"),
                             {"editor": self, "__builtins__": __builtins__})
                        self.message = f"Sourced {path.name}"
                    except Exception as exc:
                        self.message = f"Source error: {exc}"
                else:
                    self.message = f"File not found: {rest}"
            else:
                self.message = "Usage: :source <file>"
            return

        # Jump to line number (:42)
        try:
            lineno = int(cmd)
            self.cy = max(0, min(lineno - 1, len(self.lines) - 1))
            self.cx = 0
            self.message = f"Line {self.cy + 1}"
            return
        except ValueError:
            pass

        # ── Plugin commands ──
        if cmd in ("PluginLoad", "pluginload", "plugin-load"):
            if rest:
                if self.plugin_load_file(rest.strip()):
                    self.message = f"Plugin loaded: {rest.strip()}"
            else:
                n = self.plugin_load_all()
                self.message = f"Loaded {n} plugin(s) from plugin dirs"
            return
        if cmd in ("PluginList", "pluginlist", "plugin-list", "plugins"):
            plist = self.plugin_list()
            if plist:
                lines = [f"  {'✓' if e else '✗'} {n} v{v} — {d}" for n, v, e, d in plist]
                self.message = f"{len(plist)} plugin(s): " + "; ".join(
                    f"{n} v{v}" for n, v, e, d in plist)
            else:
                self.message = "No plugins loaded"
            return
        if cmd in ("PluginDisable", "plugindisable", "plugin-disable"):
            if rest:
                self.plugin_disable(rest.strip())
            else:
                self.message = "Usage: :PluginDisable <name>"
            return
        if cmd in ("PluginEnable", "pluginenable", "plugin-enable"):
            if rest:
                self.plugin_enable(rest.strip())
            else:
                self.message = "Usage: :PluginEnable <name>"
            return

        # ── File Explorer / Minimap ──
        if cmd == "explorer":
            self.explorer_toggle()
            return
        if cmd == "minimap":
            self.minimap_toggle()
            return
        if cmd == "run":
            self.run_file()
            return
        if cmd == "lsp":
            arg = rest.strip().lower()
            if arg == "stop":
                self.lsp_stop()
            elif arg == "restart":
                self.lsp_stop()
                self.lsp_start()
            elif arg == "status":
                if self.lsp_enabled:
                    srv = self.lsp_server_cmd[0] if self.lsp_server_cmd else "?"
                    diag_count = len(self.lsp_diagnostics)
                    self.message = f"LSP: {srv} | {diag_count} diagnostics"
                else:
                    self.message = "LSP: not running"
            else:
                self.lsp_start()
            return

        # ── QoL: cd, pwd ──
        if cmd == "cd":
            target = rest.strip() if rest else str(Path.home())
            target = os.path.expanduser(target)
            try:
                os.chdir(target)
                self.message = f"cd {os.getcwd()}"
            except Exception as exc:
                self.message = f"cd failed: {exc}"
            return
        if cmd == "pwd":
            self.message = os.getcwd()
            return

        # ── QoL: shell command ──
        if cmd == "!" or command.startswith("!"):
            shell_cmd = (rest if cmd == "!" else command[1:]).strip()
            if not shell_cmd:
                self.message = "Usage: :! <command>"
                return
            try:
                result = subprocess.run(shell_cmd, shell=True, capture_output=True,
                                        text=True, timeout=10)
                out = result.stdout.strip() or result.stderr.strip()
                self.message = out[:200] if out else f"Exit {result.returncode}"
            except subprocess.TimeoutExpired:
                self.message = "Command timed out (10s)"
            except Exception as exc:
                self.message = f"Shell error: {exc}"
            return

        # ── QoL: sort ──
        if cmd == "sort":
            if self.selection:
                sy, sx = self.selection
                ey = self.cy
                if sy > ey:
                    sy, ey = ey, sy
                self.snapshot()
                self.lines[sy:ey + 1] = sorted(self.lines[sy:ey + 1])
                self.selection = None
                self.message = f"Sorted lines {sy + 1}-{ey + 1}"
            else:
                self.snapshot()
                self.lines.sort()
                self.message = f"Sorted all {len(self.lines)} lines"
            self.dirty = True
            return

        # ── QoL: nohlsearch ──
        if cmd in ("noh", "nohlsearch"):
            self.last_search = ""
            self.message = "Search cleared"
            return

        # ── QoL: registers / marks display ──
        if cmd in ("reg", "registers"):
            if self.registers:
                lines = [f'  "{k}: {v[:40]}' for k, v in self.registers.items()]
                self.message = " | ".join(f'"{k}:{v[:20]}' for k, v in self.registers.items())
            else:
                self.message = "No registers set"
            return
        if cmd == "marks":
            if self.marks:
                self.message = " | ".join(f"'{k}:{y+1},{x}" for k, (y, x) in self.marks.items())
            else:
                self.message = "No marks set"
            return

        # ── QoL: only (close all other buffers) ──
        if cmd == "only":
            if self.filepath:
                self.buffers = {self.filepath: self.lines[:]}
                self.buffer_order = [self.filepath]
                self.current_buffer_idx = 0
                self.message = "Closed other buffers"
            return

        self.message = f"Unknown command: {cmd}"

    def _ex_set(self, rest):
        """Handle :set commands."""
        if not rest:
            self.message = f"Options: {self.options}"
            return
        # set option=value
        if "=" in rest:
            name, _, val = rest.partition("=")
            name = name.strip()
            val = val.strip()
            mapped = self._resolve_option(name)
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            self.options[mapped] = val
            if mapped == "theme":
                self.set_theme(str(val))
                return
            self.message = f"{name}={val}"
            return
        parts = rest.split()
        name = parts[0]
        # set nooption
        if name.startswith("no"):
            canon = name[2:]
            mapped = self._resolve_option(canon)
            self.options[mapped] = False
            self.message = f"{canon} disabled"
            return
        mapped = self._resolve_option(name)
        # set option value  (e.g. set theme matrix_code, set tabstop 4)
        if len(parts) >= 2:
            val = parts[1]
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            self.options[mapped] = val
            if mapped == "theme":
                self.set_theme(str(val))
                return
            self.message = f"{name}={val}"
            return
        # set option (boolean toggle on)
        self.options[mapped] = True
        self.message = f"{name} enabled"

    def search_command(self, command):
        if len(command) < 2:
            self.message = "Use /pattern or ?pattern"
            return
        direction = 1 if command[0] == "/" else -1
        pattern = command[1:]
        if not pattern:
            self.message = "Empty search pattern"
            return
        self.last_search = pattern
        self.search_direction = direction
        found = self.find_pattern(pattern, direction)
        if found:
            self.move_to_search(found)
            self.message = f"Found '{pattern}'"
        else:
            self.message = f"Pattern not found: {pattern}"

    def open_help(self):
        self.mode = "overlay"
        self.message = "EVim help: press any key"

    def snapshot(self):
        self.history.append((list(self.lines), self.cx, self.cy))
        if len(self.history) > 50:
            self.history.pop(0)

    def mark_dirty(self):
        self.dirty = True

    def undo(self):
        if not self.history:
            self.message = "Nothing to undo"
            return
        self.redo_stack.append((list(self.lines), self.cx, self.cy))  # Save for redo
        self.lines, self.cx, self.cy = self.history.pop()
        self.message = "Undo"
        self.set_cursor()

    def redo(self):
        if not self.redo_stack:
            self.message = "Nothing to redo"
            return
        self.history.append((list(self.lines), self.cx, self.cy))  # Save for undo
        self.lines, self.cx, self.cy = self.redo_stack.pop()
        self.message = "Redo"
        self.set_cursor()

    def delete_line(self):
        if not self.lines:
            return
        del self.lines[self.cy]
        if not self.lines:
            self.lines = [""]
            self.cy = 0
            self.cx = 0
        else:
            self.cy = min(self.cy, len(self.lines) - 1)
            self.cx = min(self.cx, len(self.current_line()))
        self.dirty = True
        self.message = "Deleted line"

    def delete_word(self):
        line = self.current_line()
        if self.cx >= len(line):
            self.delete_char()
            return
        end = self.cx
        while end < len(line) and not line[end].isspace():
            end += 1
        self.lines[self.cy] = line[: self.cx] + line[end:]
        self.dirty = True
        self.message = "Deleted word"

    def change_word(self):
        self.delete_word()
        self.mode = "insert"
        self._set_cursor_shape(beam=True)
        self.message = "Change word"

    def yank_line(self):
        text = self.current_line() + "\n"
        self.yank_to_register(text)
        self.message = "Yanked line"

    def paste_after(self):
        text = self.paste_from_register()
        if not text:
            self.message = "Nothing to paste"
            return
        if text.endswith("\n"):
            self.lines.insert(self.cy + 1, text[:-1])
            self.cy += 1
            self.cx = 0
        else:
            line = self.current_line()
            self.lines[self.cy] = line[: self.cx] + text + line[self.cx :]
            self.cx += len(text)
        self.dirty = True
        self.message = "Pasted"

    def toggle_selection(self):
        if self.selection is None:
            self.selection = (self.cy, self.cx)
            self.message = "Visual selection started"
        else:
            self.selection = None
            self.message = "Visual selection cleared"

    def yank_selection(self):
        if self.selection is None:
            self.message = "No selection"
            return
        sy, sx = self.selection
        ey, ex = self.cy, self.cx
        if sy > ey or (sy == ey and sx > ex):
            sy, sx, ey, ex = ey, ex, sy, sx
        lines = self.lines[sy:ey+1]
        if sy == ey:
            copied = lines[0][sx:ex]
        else:
            copied = lines[0][sx:] + "\n"
            for mid in lines[1:-1]:
                copied += mid + "\n"
            copied += lines[-1][:ex]
        self.yank_to_register(copied)
        self.selection = None
        self.message = "Yanked selection"

    def delete_selection(self):
        if self.selection is None:
            self.message = "No selection"
            return
        sy, sx = self.selection
        ey, ex = self.cy, self.cx
        if sy > ey or (sy == ey and sx > ex):
            sy, sx, ey, ex = ey, ex, sy, sx
        if sy == ey:
            line = self.lines[sy]
            self.lines[sy] = line[:sx] + line[ex:]
        else:
            first = self.lines[sy][:sx]
            last = self.lines[ey][ex:]
            self.lines[sy:ey + 1] = [first + last]
        self.cy = sy
        self.cx = sx
        self.selection = None
        self.dirty = True
        self.message = "Deleted selection"

    # ── Word motions ──

    def word_forward(self):
        line = self.current_line()
        if self.cx >= len(line):
            if self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = 0
                line = self.current_line()
                while self.cx < len(line) and line[self.cx].isspace():
                    self.cx += 1
            return
        pos = self.cx
        if pos < len(line) and (line[pos].isalnum() or line[pos] == '_'):
            while pos < len(line) and (line[pos].isalnum() or line[pos] == '_'):
                pos += 1
        elif pos < len(line) and not line[pos].isspace():
            while pos < len(line) and not line[pos].isspace() and not (line[pos].isalnum() or line[pos] == '_'):
                pos += 1
        while pos < len(line) and line[pos].isspace():
            pos += 1
        if pos >= len(line) and self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0
            line = self.current_line()
            while self.cx < len(line) and line[self.cx].isspace():
                self.cx += 1
        else:
            self.cx = pos

    def word_backward(self):
        line = self.current_line()
        if self.cx <= 0:
            if self.cy > 0:
                self.cy -= 1
                self.cx = len(self.current_line())
                line = self.current_line()
            else:
                return
        pos = self.cx - 1
        while pos > 0 and line[pos].isspace():
            pos -= 1
        if pos >= 0 and (line[pos].isalnum() or line[pos] == '_'):
            while pos > 0 and (line[pos - 1].isalnum() or line[pos - 1] == '_'):
                pos -= 1
        elif pos >= 0 and not line[pos].isspace():
            while pos > 0 and not line[pos - 1].isspace() and not (line[pos - 1].isalnum() or line[pos - 1] == '_'):
                pos -= 1
        self.cx = max(0, pos)

    def word_end(self):
        line = self.current_line()
        pos = self.cx + 1
        if pos >= len(line):
            if self.cy < len(self.lines) - 1:
                self.cy += 1
                line = self.current_line()
                pos = 0
            else:
                return
        while pos < len(line) and line[pos].isspace():
            pos += 1
        if pos < len(line) and (line[pos].isalnum() or line[pos] == '_'):
            while pos < len(line) - 1 and (line[pos + 1].isalnum() or line[pos + 1] == '_'):
                pos += 1
        elif pos < len(line):
            while pos < len(line) - 1 and not line[pos + 1].isspace() and not (line[pos + 1].isalnum() or line[pos + 1] == '_'):
                pos += 1
        self.cx = min(pos, len(line))

    # ── Open line ──

    def open_line_below(self):
        self.snapshot()
        indent = len(self.current_line()) - len(self.current_line().lstrip(' '))
        self.lines.insert(self.cy + 1, " " * indent)
        self.cy += 1
        self.cx = indent
        self.mode = "insert"
        self._set_cursor_shape(beam=True)
        self.mark_dirty()
        self.message = "EVim - insert mode"

    def open_line_above(self):
        self.snapshot()
        indent = len(self.current_line()) - len(self.current_line().lstrip(' '))
        self.lines.insert(self.cy, " " * indent)
        self.cx = indent
        self.mode = "insert"
        self._set_cursor_shape(beam=True)
        self.mark_dirty()
        self.message = "EVim - insert mode"

    # ── Insert at start/end ──

    def insert_at_end(self):
        self.cx = len(self.current_line())
        self.mode = "insert"
        self._set_cursor_shape(beam=True)
        self.message = "EVim - insert mode"

    def insert_at_start(self):
        line = self.current_line()
        self.cx = len(line) - len(line.lstrip())
        self.mode = "insert"
        self._set_cursor_shape(beam=True)
        self.message = "EVim - insert mode"

    # ── Match bracket ──

    def match_bracket(self):
        line = self.current_line()
        if self.cx >= len(line):
            return
        ch = line[self.cx]
        pairs = {'(': ')', ')': '(', '[': ']', ']': '[', '{': '}', '}': '{'}
        if ch not in pairs:
            for i, c in enumerate(line[self.cx:]):
                if c in pairs:
                    self.cx += i
                    ch = c
                    break
            else:
                return
        target = pairs[ch]
        forward = ch in ('(', '[', '{')
        depth = 0
        if forward:
            for row in range(self.cy, len(self.lines)):
                start = self.cx + 1 if row == self.cy else 0
                for col in range(start, len(self.lines[row])):
                    c = self.lines[row][col]
                    if c == ch:
                        depth += 1
                    elif c == target:
                        if depth == 0:
                            self.cy, self.cx = row, col
                            return
                        depth -= 1
        else:
            for row in range(self.cy, -1, -1):
                end = self.cx - 1 if row == self.cy else len(self.lines[row]) - 1
                for col in range(end, -1, -1):
                    c = self.lines[row][col]
                    if c == ch:
                        depth += 1
                    elif c == target:
                        if depth == 0:
                            self.cy, self.cx = row, col
                            return
                        depth -= 1

    # ── Scroll commands ──

    def scroll_half_down(self, height):
        half = max(1, (height - 2) // 2)
        self.cy = min(self.cy + half, len(self.lines) - 1)
        self.cx = min(self.cx, len(self.current_line()))

    def scroll_half_up(self, height):
        half = max(1, (height - 2) // 2)
        self.cy = max(self.cy - half, 0)
        self.cx = min(self.cx, len(self.current_line()))

    def scroll_page_down(self, height):
        page = max(1, height - 3)
        self.cy = min(self.cy + page, len(self.lines) - 1)
        self.cx = min(self.cx, len(self.current_line()))

    def scroll_page_up(self, height):
        page = max(1, height - 3)
        self.cy = max(self.cy - page, 0)
        self.cx = min(self.cx, len(self.current_line()))

    # ── Line commenting ──

    def toggle_comment(self):
        comment_map = {
            "c": "// ", "cpp": "// ", "csharp": "// ", "rust": "// ",
            "java": "// ", "go": "// ", "javascript": "// ", "typescript": "// ",
            "swift": "// ", "kotlin": "// ", "scala": "// ",
            "python": "# ", "ruby": "# ", "perl": "# ", "shell": "# ", "php": "// ",
            "lua": "-- ", "fortran": "! ", "pascal": "// ",
            "assembly": "; ",
        }
        prefix = comment_map.get(self.syntax_language, "# ")
        self.snapshot()
        line = self.current_line()
        stripped = line.lstrip()
        if stripped.startswith(prefix):
            indent = len(line) - len(stripped)
            self.lines[self.cy] = line[:indent] + stripped[len(prefix):]
            self.message = "Uncommented"
        else:
            indent = len(line) - len(stripped)
            self.lines[self.cy] = line[:indent] + prefix + stripped
            self.message = "Commented"
        self.mark_dirty()

    # ── Macros ──

    def start_macro(self, reg):
        self.macro_recording = reg
        self.macro_keys = []
        self.message = f"Recording @{reg}..."

    def stop_macro(self):
        if self.macro_recording:
            self.macros[self.macro_recording] = self.macro_keys[:]
            self.message = f"Recorded @{self.macro_recording} ({len(self.macro_keys)} keys)"
            self.macro_recording = None
            self.macro_keys = []

    def play_macro(self, reg, stdscr):
        if not hasattr(self, '_macro_depth'):
            self._macro_depth = 0
        if self._macro_depth > 100:
            self.message = "Macro recursion limit reached"
            return
        keys = self.macros.get(reg)
        if not keys:
            self.message = f"Empty macro @{reg}"
            return
        self._macro_depth += 1
        self.macro_playing = True
        for k in keys:
            self._inject_key = k
            self.handle_key(stdscr)
        self._macro_depth -= 1
        if self._macro_depth == 0:
            self.macro_playing = False
        self._inject_key = None
        self.message = f"Played @{reg}"

    # ── Marks ──

    def set_mark(self, ch):
        self.marks[ch] = (self.cy, self.cx)
        self.message = f"Mark '{ch}' set"

    def goto_mark(self, ch):
        if ch in self.marks:
            self.cy, self.cx = self.marks[ch]
            self.set_cursor()
            self.message = f"Jump to mark '{ch}'"
        else:
            self.message = f"Mark '{ch}' not set"

    # ── Registers ──

    def get_register(self):
        reg = self.pending_register or '"'
        self.pending_register = None
        return reg

    def yank_to_register(self, text):
        reg = self.get_register()
        self.registers[reg] = text
        self.clipboard = text

    def paste_from_register(self):
        reg = self.get_register()
        text = self.registers.get(reg, self.clipboard)
        return text

    # ── Git gutter ──

    def update_git_gutter(self):
        self.git_diff_lines = {}
        if not self.filepath:
            return
        try:
            result = subprocess.run(
                ["git", "diff", "--unified=0", "--no-color", "--", self.filepath],
                capture_output=True, text=True, timeout=2, cwd=str(Path(self.filepath).resolve().parent)
            )
            if result.returncode != 0:
                return
            for m in re.finditer(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', result.stdout):
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count == 0:
                    self.git_diff_lines[start - 1] = 'deleted'
                else:
                    for i in range(count):
                        self.git_diff_lines[start - 1 + i] = 'added'
        except Exception:
            pass

    # ── Persistent undo ──

    def save_undo_history(self):
        if not self.filepath:
            return
        undo_dir = Path.home() / ".evim_undo"
        undo_dir.mkdir(exist_ok=True)
        safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(Path(self.filepath).resolve()))
        path = undo_dir / safe
        data = {"history": [(l, cx, cy) for l, cx, cy in self.history[-20:]]}
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def load_undo_history(self):
        if not self.filepath:
            return
        undo_dir = Path.home() / ".evim_undo"
        safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(Path(self.filepath).resolve()))
        path = undo_dir / safe
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("history", []):
                lines, cx, cy = item
                self.history.append((lines, cx, cy))
        except Exception:
            pass

    # ── Fuzzy file finder ──

    def fuzzy_find(self, stdscr):
        query = ""
        selected = 0
        # Scan files once before the input loop
        try:
            all_files = sorted(
                str(p) for p in Path('.').rglob('*')
                if p.is_file() and not any(part.startswith('.') for part in p.parts)
            )
        except Exception:
            all_files = []
        while True:
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            stdscr.addstr(0, 0, f"Find file: {query}_"[:width-1], curses.A_BOLD)
            try:
                if query:
                    ql = query.lower()
                    scored = []
                    for f in all_files:
                        fl = f.lower()
                        if ql in fl:
                            scored.append((fl.index(ql), f))
                    scored.sort()
                    files = [s[1] for s in scored]
                else:
                    files = all_files
                files = files[:height - 3]
            except Exception:
                files = []
            selected = min(selected, max(0, len(files) - 1))
            for i, f in enumerate(files):
                attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
                try:
                    stdscr.addstr(i + 2, 2, f[:width - 4], attr)
                except curses.error:
                    pass
            curses.doupdate()
            ch = stdscr.getch()
            if ch in (27,):
                return
            if ch in (curses.KEY_ENTER, 10, 13):
                if files:
                    self.open_buffer(files[selected])
                return
            if ch in (curses.KEY_BACKSPACE, 127, curses.ascii.DEL):
                query = query[:-1]
            elif ch == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif ch == curses.KEY_DOWN:
                selected += 1
            elif 0 <= ch < 256 and curses.ascii.isprint(ch):
                query += chr(ch)

    # ── LSP (Language Server Protocol) ──

    LSP_SERVERS = {
        "python": ["pyright-langserver", "--stdio"],
        "javascript": ["typescript-language-server", "--stdio"],
        "typescript": ["typescript-language-server", "--stdio"],
        "c": ["clangd"],
        "cpp": ["clangd"],
        "rust": ["rust-analyzer"],
        "go": ["gopls"],
        "lua": ["lua-language-server"],
        "java": ["jdtls"],
        "ruby": ["solargraph", "stdio"],
        "php": ["phpactor", "language-server"],
        "csharp": ["omnisharp", "--languageserver"],
        "zig": ["zls"],
        "dart": ["dart", "language-server"],
        "haskell": ["haskell-language-server-wrapper", "--lsp"],
        "elixir": ["elixir-ls"],
        "kotlin": ["kotlin-language-server"],
        "scala": ["metals"],
        "swift": ["sourcekit-lsp"],
        "html": ["vscode-html-language-server", "--stdio"],
        "css": ["vscode-css-language-server", "--stdio"],
        "json": ["vscode-json-language-server", "--stdio"],
        "yaml": ["yaml-language-server", "--stdio"],
        "vue": ["vue-language-server", "--stdio"],
        "svelte": ["svelteserver", "--stdio"],
        "nim": ["nimlangserver"],
        "ocaml": ["ocamllsp"],
        "clojure": ["clojure-lsp"],
        "julia": ["julia", "--startup-file=no", "-e", "using LanguageServer; runserver()"],
        "terraform": ["terraform-ls", "serve"],
    }

    def lsp_start(self):
        """Start the LSP server for the current language."""
        lang = self.syntax_language
        if not lang:
            self.message = "No language detected for LSP"
            return
        cmd = self.LSP_SERVERS.get(lang)
        if not cmd:
            self.message = f"No LSP server configured for {lang}"
            return
        # Check if server binary exists
        import shutil
        if not shutil.which(cmd[0]):
            self.message = f"LSP server not found: {cmd[0]} (install it first)"
            return
        try:
            self.lsp_process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0
            )
            self.lsp_server_cmd = cmd
            self.lsp_request_id = 0
            self.lsp_initialized = False
            self.lsp_enabled = True
            # Start reader thread
            t = threading.Thread(target=self._lsp_reader_loop, daemon=True)
            t.start()
            # Send initialize request
            root_uri = f"file://{os.path.abspath('.')}"
            self._lsp_send_request("initialize", {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "completion": {"completionItem": {"snippetSupport": False}},
                        "hover": {"contentFormat": ["plaintext"]},
                        "publishDiagnostics": {"relatedInformation": True},
                        "definition": {},
                    }
                },
            })
            self.message = f"LSP: starting {cmd[0]}..."
        except Exception as exc:
            self.message = f"LSP start error: {exc}"
            self.lsp_enabled = False

    def lsp_stop(self):
        """Stop the LSP server."""
        if self.lsp_process:
            try:
                self._lsp_send_request("shutdown", {})
                self._lsp_send_notification("exit", None)
                self.lsp_process.terminate()
                self.lsp_process.wait(timeout=3)
            except Exception:
                if self.lsp_process:
                    self.lsp_process.kill()
            # Close pipes to avoid resource leaks
            for pipe in (self.lsp_process.stdin, self.lsp_process.stdout, self.lsp_process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            self.lsp_process = None
        self.lsp_enabled = False
        self.lsp_initialized = False
        self.lsp_diagnostics = []
        self.lsp_hover_text = None
        self.lsp_completions = []
        self.lsp_completion_active = False
        self.message = "LSP stopped"

    def _lsp_send_request(self, method, params):
        """Send a JSON-RPC request to the LSP server."""
        if not self.lsp_process or not self.lsp_process.stdin:
            return -1
        self.lsp_request_id += 1
        rid = self.lsp_request_id
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = f"Content-Length: {len(body)}\r\n\r\n{body}"
        try:
            self.lsp_process.stdin.write(msg.encode("utf-8"))
            self.lsp_process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.lsp_enabled = False
        return rid

    def _lsp_send_notification(self, method, params):
        """Send a JSON-RPC notification (no id)."""
        if not self.lsp_process or not self.lsp_process.stdin:
            return
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        msg = f"Content-Length: {len(body)}\r\n\r\n{body}"
        try:
            self.lsp_process.stdin.write(msg.encode("utf-8"))
            self.lsp_process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.lsp_enabled = False

    def _lsp_reader_loop(self):
        """Background thread: read LSP server stdout and dispatch responses."""
        buf = b""
        while self.lsp_process and self.lsp_process.poll() is None:
            try:
                chunk = self.lsp_process.stdout.read(1)
                if not chunk:
                    break
                buf += chunk
                # Parse Content-Length header
                while b"\r\n\r\n" in buf:
                    header_end = buf.index(b"\r\n\r\n")
                    header = buf[:header_end].decode("utf-8", errors="replace")
                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":")[1].strip())
                    body_start = header_end + 4
                    if len(buf) < body_start + content_length:
                        break  # Wait for more data
                    body = buf[body_start:body_start + content_length]
                    buf = buf[body_start + content_length:]
                    try:
                        msg = json.loads(body.decode("utf-8"))
                        self._lsp_handle_message(msg)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                break
        with self.lsp_lock:
            self.lsp_enabled = False

    def _lsp_handle_message(self, msg):
        """Handle an incoming LSP message."""
        with self.lsp_lock:
            if "id" in msg and "method" not in msg:
                # Response to our request
                rid = msg["id"]
                self.lsp_responses[rid] = msg
                # Prune old responses to prevent memory leak
                if len(self.lsp_responses) > 50:
                    oldest_keys = sorted(self.lsp_responses.keys())[:25]
                    for k in oldest_keys:
                        self.lsp_responses.pop(k, None)
                result = msg.get("result", {})
                # Handle initialize response
                if result and "capabilities" in result:
                    self.lsp_capabilities = result["capabilities"]
                    self.lsp_initialized = True
                    self._lsp_send_notification("initialized", {})
                    self._lsp_did_open()
                    self.message = f"LSP: {self.lsp_server_cmd[0]} ready"
            elif "method" in msg:
                method = msg["method"]
                params = msg.get("params", {})
                if method == "textDocument/publishDiagnostics":
                    self._lsp_handle_diagnostics(params)
                elif method == "window/logMessage":
                    pass  # Ignore log messages

    def _lsp_handle_diagnostics(self, params):
        """Process diagnostics from the LSP server."""
        diags = []
        for d in params.get("diagnostics", []):
            rng = d.get("range", {})
            start = rng.get("start", {})
            line = start.get("line", 0)
            col = start.get("character", 0)
            severity = d.get("severity", 1)  # 1=error, 2=warning, 3=info, 4=hint
            message = d.get("message", "")
            diags.append((line, col, severity, message))
        self.lsp_diagnostics = diags

    def _lsp_did_open(self):
        """Notify LSP that a document was opened."""
        if not self.filepath or not self.lsp_initialized:
            return
        lang_id = self.syntax_language or "plaintext"
        text = "\n".join(self.lines)
        self._lsp_send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": f"file://{os.path.abspath(self.filepath)}",
                "languageId": lang_id,
                "version": 1,
                "text": text,
            }
        })

    def lsp_did_change(self):
        """Notify LSP that the document changed (full sync)."""
        if not self.lsp_enabled or not self.lsp_initialized or not self.filepath:
            return
        text = "\n".join(self.lines)
        self._lsp_send_notification("textDocument/didChange", {
            "textDocument": {
                "uri": f"file://{os.path.abspath(self.filepath)}",
                "version": self.lsp_request_id,
            },
            "contentChanges": [{"text": text}],
        })

    def lsp_goto_definition(self):
        """Request go-to-definition from LSP."""
        if not self.lsp_enabled or not self.lsp_initialized or not self.filepath:
            self.message = "LSP not active"
            return
        rid = self._lsp_send_request("textDocument/definition", {
            "textDocument": {"uri": f"file://{os.path.abspath(self.filepath)}"},
            "position": {"line": self.cy, "character": self.cx},
        })
        # Wait briefly for response
        for _ in range(50):
            with self.lsp_lock:
                if rid in self.lsp_responses:
                    resp = self.lsp_responses.pop(rid)
                    result = resp.get("result")
                    if result:
                        loc = result if isinstance(result, dict) else result[0] if isinstance(result, list) and result else None
                        if loc:
                            uri = loc.get("uri", "")
                            rng = loc.get("range", {}).get("start", {})
                            target_line = rng.get("line", 0)
                            target_col = rng.get("character", 0)
                            fpath = uri.replace("file://", "")
                            if fpath != os.path.abspath(self.filepath):
                                self.jumplist_push()
                                self.open_file(fpath)
                            else:
                                self.jumplist_push()
                            self.cy = target_line
                            self.cx = target_col
                            self.message = f"Definition: line {target_line + 1}"
                            return
                    self.message = "No definition found"
                    return
            time.sleep(0.02)
        self.message = "LSP: definition request timed out"

    def lsp_hover(self):
        """Request hover info from LSP."""
        if not self.lsp_enabled or not self.lsp_initialized or not self.filepath:
            self.message = "LSP not active"
            return
        rid = self._lsp_send_request("textDocument/hover", {
            "textDocument": {"uri": f"file://{os.path.abspath(self.filepath)}"},
            "position": {"line": self.cy, "character": self.cx},
        })
        for _ in range(50):
            with self.lsp_lock:
                if rid in self.lsp_responses:
                    resp = self.lsp_responses.pop(rid)
                    result = resp.get("result")
                    if result:
                        contents = result.get("contents", "")
                        if isinstance(contents, dict):
                            text = contents.get("value", str(contents))
                        elif isinstance(contents, list):
                            text = " | ".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
                        else:
                            text = str(contents)
                        # Strip markdown fences
                        text = re.sub(r'```\w*\n?', '', text).strip()
                        self.lsp_hover_text = text
                        self.message = text[:200]
                    else:
                        self.message = "No hover info"
                    return
            time.sleep(0.02)
        self.message = "LSP: hover request timed out"

    def lsp_completion(self):
        """Request completions from LSP."""
        if not self.lsp_enabled or not self.lsp_initialized or not self.filepath:
            return
        rid = self._lsp_send_request("textDocument/completion", {
            "textDocument": {"uri": f"file://{os.path.abspath(self.filepath)}"},
            "position": {"line": self.cy, "character": self.cx},
        })
        for _ in range(50):
            with self.lsp_lock:
                if rid in self.lsp_responses:
                    resp = self.lsp_responses.pop(rid)
                    result = resp.get("result")
                    items = []
                    if isinstance(result, dict):
                        items = result.get("items", [])
                    elif isinstance(result, list):
                        items = result
                    self.lsp_completions = [(it.get("label", ""), it.get("detail", "")) for it in items[:50]]
                    if self.lsp_completions:
                        self.lsp_completion_active = True
                        self.lsp_completion_idx = 0
                    else:
                        self.message = "No completions"
                    return
            time.sleep(0.02)
        self.message = "LSP: completion request timed out"

    def lsp_apply_completion(self):
        """Apply the selected LSP completion."""
        if not self.lsp_completions or not self.lsp_completion_active:
            return
        label, _ = self.lsp_completions[self.lsp_completion_idx]
        # Find the word prefix to replace
        line = self.lines[self.cy]
        start = self.cx
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] == '_'):
            start -= 1
        self.snapshot()
        self.lines[self.cy] = line[:start] + label + line[self.cx:]
        self.cx = start + len(label)
        self.lsp_completion_active = False
        self.lsp_completions = []
        self.dirty = True
        self.lsp_did_change()

    def lsp_references(self):
        """Request references from LSP."""
        if not self.lsp_enabled or not self.lsp_initialized or not self.filepath:
            self.message = "LSP not active"
            return
        rid = self._lsp_send_request("textDocument/references", {
            "textDocument": {"uri": f"file://{os.path.abspath(self.filepath)}"},
            "position": {"line": self.cy, "character": self.cx},
            "context": {"includeDeclaration": True},
        })
        for _ in range(50):
            with self.lsp_lock:
                if rid in self.lsp_responses:
                    resp = self.lsp_responses.pop(rid)
                    result = resp.get("result", [])
                    if result:
                        refs = []
                        for r in result[:20]:
                            uri = r.get("uri", "").replace("file://", "")
                            ln = r.get("range", {}).get("start", {}).get("line", 0)
                            fname = os.path.basename(uri)
                            refs.append(f"{fname}:{ln + 1}")
                        self.message = f"Refs({len(result)}): " + ", ".join(refs[:5])
                    else:
                        self.message = "No references found"
                    return
            time.sleep(0.02)
        self.message = "LSP: references request timed out"

    def draw_lsp_diagnostics_gutter(self, stdscr, line_idx, gutter_x, y):
        """Draw LSP diagnostic markers in the gutter."""
        for dline, dcol, severity, dmsg in self.lsp_diagnostics:
            if dline == line_idx:
                marker = "●"
                if severity == 1:
                    attr = curses.color_pair(2) | curses.A_BOLD  # red for error
                elif severity == 2:
                    attr = curses.color_pair(8) | curses.A_BOLD  # yellow for warning
                else:
                    attr = curses.color_pair(5) | curses.A_BOLD  # blue for info
                try:
                    stdscr.addstr(y, gutter_x, marker, attr)
                except curses.error:
                    pass
                return

    def draw_lsp_completion_popup(self, stdscr, height, width):
        """Draw LSP completion popup near the cursor."""
        if not self.lsp_completion_active or not self.lsp_completions:
            return
        top = max(0, self.cy - (max(0, self.cy - height + 4)))
        has_nums = self.options.get("number") or self.options.get("relativenumber")
        prefix_w = (2 if self.git_diff_lines else 0) + (5 if has_nums else 0)
        editor_left = 0
        if self.explorer_visible:
            editor_left = min(self.explorer_width, width // 3)
        popup_x = self.cx + prefix_w + editor_left + 1
        popup_y = top + 1
        max_items = min(len(self.lsp_completions), 10, height - popup_y - 3)
        if max_items <= 0:
            return
        popup_w = min(40, width - popup_x - 2)
        if popup_w < 10:
            return
        for i in range(max_items):
            label, detail = self.lsp_completions[i]
            text = label[:popup_w - 2]
            if detail:
                remaining = popup_w - 2 - len(text) - 1
                if remaining > 3:
                    text += " " + detail[:remaining]
            text = text[:popup_w - 2].ljust(popup_w - 2)
            attr = curses.A_REVERSE if i == self.lsp_completion_idx else curses.color_pair(1)
            try:
                stdscr.addstr(popup_y + i, popup_x, " " + text + " ", attr)
            except curses.error:
                pass

    def term_spawn(self, rows=24, cols=80):
        if self.term_fd is not None:
            return
        shell = os.environ.get('SHELL', '/bin/bash')
        pid, fd = pty.openpty()
        # Set initial terminal size so shell/programs know the dimensions
        try:
            fcntl.ioctl(pid, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass
        try:
            self.term_pid = os.fork()
        except OSError:
            os.close(pid)
            os.close(fd)
            self.message = "Failed to fork terminal"
            return
        if self.term_pid == 0:
            try:
                os.close(pid)
                os.setsid()
                os.dup2(fd, 0)
                os.dup2(fd, 1)
                os.dup2(fd, 2)
                if fd > 2:
                    os.close(fd)
                os.environ['TERM'] = 'dumb'
                os.environ['COLUMNS'] = str(cols)
                os.environ['LINES'] = str(rows)
                os.execvp(shell, [shell, '--noediting'])
            except Exception:
                os._exit(127)
        else:
            os.close(fd)
            self.term_fd = pid
            fl = fcntl.fcntl(self.term_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.term_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    def term_set_size(self, rows, cols):
        if self.term_fd is not None:
            try:
                fcntl.ioctl(self.term_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            except OSError:
                pass

    def term_read(self):
        if self.term_fd is None:
            return
        try:
            while True:
                r, _, _ = select.select([self.term_fd], [], [], 0)
                if not r:
                    break
                data = os.read(self.term_fd, 4096)
                if not data:
                    self.term_close()
                    return
                text = data.decode('utf-8', errors='replace')
                # Strip ANSI escape sequences (we don't emulate a full VT)
                text = self._ansi_re.sub('', text)
                # Normalize line endings: \r\n -> \n first
                text = text.replace('\r\n', '\n')
                if not self.term_lines:
                    self.term_lines = [""]
                for ch in text:
                    if ch == '\n':
                        self.term_lines.append("")
                        self.term_col = 0
                    elif ch == '\r':
                        # Carriage return: cursor back to column 0
                        self.term_col = 0
                    elif ch == '\x08':  # backspace
                        if self.term_col > 0:
                            self.term_col -= 1
                            line = self.term_lines[-1]
                            if self.term_col < len(line):
                                self.term_lines[-1] = line[:self.term_col] + line[self.term_col + 1:]
                    elif ch == '\x07':  # bell - ignore
                        pass
                    elif ch == '\t':
                        spaces = 8 - (self.term_col % 8)
                        line = self.term_lines[-1]
                        line = line[:self.term_col].ljust(self.term_col) + ' ' * spaces + line[self.term_col + spaces:] if self.term_col < len(line) else line.ljust(self.term_col) + ' ' * spaces
                        self.term_lines[-1] = line
                        self.term_col += spaces
                    elif ord(ch) >= 32 or ch not in '\x00\x01\x02\x03\x04\x05\x06\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f':
                        line = self.term_lines[-1]
                        # Overwrite at cursor column
                        if self.term_col < len(line):
                            line = line[:self.term_col] + ch + line[self.term_col + 1:]
                        else:
                            line = line.ljust(self.term_col) + ch
                        self.term_lines[-1] = line
                        self.term_col += 1
                # Auto-scroll to bottom on new output
                self.term_scroll = 0
                # Cap scrollback at 1000 lines
                if len(self.term_lines) > 1000:
                    self.term_lines = self.term_lines[-1000:]
        except OSError:
            pass

    def term_write(self, text):
        if self.term_fd is None:
            return
        try:
            os.write(self.term_fd, text.encode('utf-8'))
        except OSError:
            self.term_close()

    def term_close(self):
        if self.term_fd is not None:
            try:
                os.close(self.term_fd)
            except OSError:
                pass
            self.term_fd = None
        if self.term_pid and self.term_pid > 0:
            import signal
            try:
                os.kill(self.term_pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                os.waitpid(self.term_pid, 0)
            except ChildProcessError:
                pass
            self.term_pid = None

    def term_toggle(self):
        self.term_visible = not self.term_visible
        if self.term_visible:
            self.mode = "terminal"
            self.message = "TERMINAL (Ctrl+n to return)"
        else:
            self.mode = "normal"
            self.message = "EVim - normal mode"

    # ── File Explorer ──────────────────────────────────────────────

    def explorer_build_entries(self):
        """Build the flat list of entries from the directory tree."""
        self.explorer_entries = []
        root = self.explorer_cwd
        self._explorer_scan(root, 0)

    def _explorer_scan(self, dirpath, depth):
        """Recursively scan directory and populate explorer_entries."""
        try:
            items = sorted(os.listdir(dirpath))
        except OSError:
            return
        # Directories first, then files
        dirs = [i for i in items if os.path.isdir(os.path.join(dirpath, i)) and not i.startswith('.')]
        files = [i for i in items if not os.path.isdir(os.path.join(dirpath, i)) and not i.startswith('.')]
        for name in dirs:
            fullpath = os.path.join(dirpath, name)
            expanded = fullpath in self.explorer_expanded
            self.explorer_entries.append((depth, name, fullpath, True, expanded))
            if expanded:
                self._explorer_scan(fullpath, depth + 1)
        for name in files:
            fullpath = os.path.join(dirpath, name)
            self.explorer_entries.append((depth, name, fullpath, False, False))

    def explorer_toggle(self):
        """Toggle the file explorer panel."""
        self.explorer_visible = not self.explorer_visible
        if self.explorer_visible:
            self.explorer_cwd = str(Path.cwd())
            self.explorer_build_entries()
            self.mode = "explorer"
            self.message = "EXPLORER (Ctrl+b to close)"
        else:
            if self.mode == "explorer":
                self.mode = "normal"
                self.message = "EVim - normal mode"

    def explorer_handle_key(self, ch):
        """Handle key input when explorer is focused."""
        if ch in (curses.KEY_EXIT, 27):  # ESC
            self.explorer_visible = False
            self.mode = "normal"
            self.message = "EVim - normal mode"
            return
        entries = self.explorer_entries
        if not entries:
            return
        if ch == curses.KEY_UP or ch == ord('k'):
            self.explorer_cursor = max(0, self.explorer_cursor - 1)
            return
        if ch == curses.KEY_DOWN or ch == ord('j'):
            self.explorer_cursor = min(len(entries) - 1, self.explorer_cursor + 1)
            return
        if ch in (curses.KEY_ENTER, 10, 13, ord('l'), curses.KEY_RIGHT):
            if 0 <= self.explorer_cursor < len(entries):
                depth, name, fullpath, is_dir, expanded = entries[self.explorer_cursor]
                if is_dir:
                    if expanded:
                        self.explorer_expanded.discard(fullpath)
                    else:
                        self.explorer_expanded.add(fullpath)
                    self.explorer_build_entries()
                    # Keep cursor in bounds
                    self.explorer_cursor = min(self.explorer_cursor, len(self.explorer_entries) - 1)
                else:
                    # Open file
                    self.open_buffer(fullpath)
                    self.explorer_visible = False
                    self.mode = "normal"
                    self.message = f"Opened {name}"
            return
        if ch == ord('h') or ch == curses.KEY_LEFT:
            # Collapse directory or go to parent
            if 0 <= self.explorer_cursor < len(entries):
                depth, name, fullpath, is_dir, expanded = entries[self.explorer_cursor]
                if is_dir and expanded:
                    self.explorer_expanded.discard(fullpath)
                    self.explorer_build_entries()
                    self.explorer_cursor = min(self.explorer_cursor, len(self.explorer_entries) - 1)
            return
        if ch == ord('r') or ch == ord('R'):
            self.explorer_build_entries()
            self.message = "Explorer refreshed"
            return

    def draw_file_explorer(self, stdscr, height, width):
        """Draw the file explorer panel on the left side."""
        ew = min(self.explorer_width, width // 3)
        bg = getattr(self, 'color_bg', curses.A_NORMAL)
        border_attr = getattr(self, 'color_status', curses.A_REVERSE)
        # Title bar
        title = " Explorer "
        title_line = title + "─" * max(0, ew - len(title) - 1) + "│"
        try:
            stdscr.addstr(0, 0, title_line[:ew], border_attr)
        except curses.error:
            pass
        # Entries
        entries = self.explorer_entries
        visible_h = height - 3  # title + status + cmdline
        # Auto-scroll to keep cursor visible
        if self.explorer_cursor < self.explorer_scroll:
            self.explorer_scroll = self.explorer_cursor
        if self.explorer_cursor >= self.explorer_scroll + visible_h:
            self.explorer_scroll = self.explorer_cursor - visible_h + 1
        for i in range(visible_h):
            row = i + 1
            eidx = self.explorer_scroll + i
            if row >= height - 2:
                break
            if eidx < len(entries):
                depth, name, fullpath, is_dir, expanded = entries[eidx]
                indent = "  " * depth
                if is_dir:
                    icon = "▼ " if expanded else "▶ "
                else:
                    icon = "  "
                text = indent + icon + name
                attr = bg
                if eidx == self.explorer_cursor and self.mode == "explorer":
                    attr = border_attr
                line_text = text[:ew - 1].ljust(ew - 1) + "│"
            else:
                line_text = " " * (ew - 1) + "│"
                attr = bg
            try:
                stdscr.addstr(row, 0, line_text[:ew], attr)
            except curses.error:
                pass
        return ew

    # ── Minimap ────────────────────────────────────────────────────

    def minimap_toggle(self):
        """Toggle the minimap panel."""
        self.minimap_visible = not self.minimap_visible
        if self.minimap_visible:
            self.message = "Minimap ON"
        else:
            self.message = "Minimap OFF"

    def draw_minimap(self, stdscr, height, width, editor_top):
        """Draw a minimap (code overview) on the right side."""
        mw = min(self.minimap_width, width // 4)
        mx = width - mw
        bg = getattr(self, 'color_bg', curses.A_NORMAL)
        dim_attr = curses.A_DIM | bg
        border_attr = getattr(self, 'color_status', curses.A_REVERSE)
        visible_h = max(1, height - 2)  # rows for minimap content
        total = len(self.lines)
        # Each minimap row represents `ratio` source lines
        if total <= visible_h:
            ratio = 1
        else:
            ratio = max(1, total // visible_h)
        # Title
        title = "│ Map "
        try:
            stdscr.addstr(0, mx, title[:mw], border_attr)
        except curses.error:
            pass
        # Draw compressed lines
        for row in range(1, visible_h):
            src_line_idx = (row - 1) * ratio
            if src_line_idx >= total:
                # Empty row
                try:
                    stdscr.addstr(row, mx, "│" + " " * (mw - 1), dim_attr)
                except curses.error:
                    pass
                continue
            line = self.lines[src_line_idx]
            # Compress: take every other char, show structure
            compressed = ""
            for ci, ch in enumerate(line.replace("\t", " ")):
                if ci >= (mw - 2) * 2:
                    break
                if ci % 2 == 0:
                    if ch == ' ':
                        compressed += ' '
                    elif ch.isalpha():
                        compressed += '░'
                    elif ch.isdigit():
                        compressed += '▒'
                    else:
                        compressed += '·'
            text = "│" + compressed[:mw - 1].ljust(mw - 1)
            # Highlight if this range includes the current cursor line
            attr = dim_attr
            range_start = src_line_idx
            range_end = min(src_line_idx + ratio, total)
            if range_start <= self.cy < range_end:
                attr = border_attr
            # Highlight if in viewport
            elif editor_top <= src_line_idx < editor_top + visible_h:
                attr = curses.A_NORMAL | bg
            try:
                stdscr.addstr(row, mx, text[:mw], attr)
            except curses.error:
                pass
        return mw

    def draw_terminal_panel(self, stdscr):
        height, width = stdscr.getmaxyx()
        # Panel: bottom-right, half width, half height
        panel_h = max(5, height // 2)
        panel_w = max(20, width // 2)
        panel_y = height - panel_h - 2  # above status bar
        panel_x = width - panel_w
        content_w = panel_w - 2  # inside borders
        content_h = panel_h - 2  # inside borders
        # Spawn shell sized to panel if needed
        if self.term_fd is None:
            self.term_spawn(content_h, content_w)
        else:
            self.term_set_size(content_h, content_w)
        bg = getattr(self, 'color_bg', curses.A_NORMAL)
        border_attr = getattr(self, 'color_status', curses.A_REVERSE)
        # Draw border top
        title = " Terminal "
        border_top = "┌" + title + "─" * max(0, panel_w - 2 - len(title)) + "┐"
        try:
            stdscr.addstr(panel_y, panel_x, border_top[:panel_w], border_attr)
        except curses.error:
            pass
        # Read any pending output from the shell
        self.term_read()
        # Draw terminal content lines (shell output includes prompt + typed text)
        if self.term_scroll > 0:
            end = len(self.term_lines) - self.term_scroll
            start = max(0, end - content_h)
            visible = self.term_lines[start:end]
        else:
            visible = self.term_lines[-content_h:]
        for i in range(content_h):
            row = panel_y + 1 + i
            if row >= height - 2:
                break
            text = visible[i] if i < len(visible) else ""
            line_content = "│" + text[:content_w].ljust(content_w) + "│"
            try:
                stdscr.addstr(row, panel_x, line_content[:panel_w], bg)
            except curses.error:
                pass
        # Draw border bottom
        border_bottom = "└" + "─" * max(0, panel_w - 2) + "┘"
        bottom_row = panel_y + panel_h - 1
        if bottom_row < height - 2:
            try:
                stdscr.addstr(bottom_row, panel_x, border_bottom[:panel_w], border_attr)
            except curses.error:
                pass
        # Place cursor at the shell cursor position (end of last visible line)
        if self.mode == "terminal" and self.term_scroll == 0:
            last_line = self.term_lines[-1] if self.term_lines else ""
            cursor_col = min(self.term_col, content_w - 1)
            # Cursor row: position within visible area
            n_visible = min(len(self.term_lines), content_h)
            cursor_row = panel_y + n_visible
            if cursor_row < height - 2:
                curses.setsyx(cursor_row, panel_x + 1 + cursor_col)

    def find_pattern(self, pattern, direction=1):
        if direction == 1:
            for row in range(self.cy, len(self.lines)):
                offset = self.cx + 1 if row == self.cy else 0
                idx = self.lines[row].find(pattern, offset)
                if idx >= 0:
                    return (row, idx)
            for row in range(0, self.cy):
                idx = self.lines[row].find(pattern)
                if idx >= 0:
                    return (row, idx)
        else:
            for row in range(self.cy, -1, -1):
                end = self.cx if row == self.cy else len(self.lines[row])
                idx = self.lines[row].rfind(pattern, 0, end)
                if idx >= 0:
                    return (row, idx)
            for row in range(len(self.lines) - 1, self.cy, -1):
                idx = self.lines[row].rfind(pattern)
                if idx >= 0:
                    return (row, idx)
        return None

    def find_again(self, direction=1):
        if not self.last_search:
            self.message = "No previous search"
            return
        result = self.find_pattern(self.last_search, direction)
        if result:
            self.move_to_search(result)
            self.message = f"Found '{self.last_search}'"
        else:
            self.message = f"Pattern not found: {self.last_search}"

    def move_to_search(self, found):
        self.jumplist_push()
        row, col = found
        self.cy = row
        self.cx = col
        self.set_cursor()

    def set_theme(self, theme_name):
        valid_themes = [
            "classic_blue", "neon_nights", "desert_storm", "sunny_meadow",
            "vampire_castle", "arctic_aurora", "forest_grove", "golden_wheat",
            "midnight_sky", "cloudy_day", "city_lights", "creamy_latte",
            "deep_space", "fresh_breeze", "matrix_code", "ocean_blue",
            "fire_red", "forest_green", "purple_haze", "sunset_orange",
            "arctic_white", "midnight_purple", "desert_gold", "cyber_pink",
        ]
        if theme_name in valid_themes:
            self.options["theme"] = theme_name
            if self.colors_initialized and not self.config_loading:
                self.init_colors()
            self.message = f"Theme set to {theme_name.replace('_', ' ').title()}"
        else:
            self.message = f"Unknown theme: {theme_name}. Available: {', '.join(t.replace('_', ' ').title() for t in valid_themes)}"

    def replace_command(self, command):
        """Handle :s/old/new/, :s/old/new/g, :%s/old/new/g for search & replace"""
        try:
            all_lines = command.startswith("%s/")
            cmd = command[1:] if all_lines else command  # strip leading %
            parts = cmd.split("/")
            if len(parts) < 3:
                self.message = "Use :s/old/new/ or :%s/old/new/g"
                return
            old = parts[1]
            new = parts[2]
            global_flag = len(parts) > 3 and "g" in parts[3]
            self.snapshot()
            total = 0
            if all_lines:
                for i in range(len(self.lines)):
                    if old in self.lines[i]:
                        if global_flag:
                            total += self.lines[i].count(old)
                            self.lines[i] = self.lines[i].replace(old, new)
                        else:
                            total += 1
                            self.lines[i] = self.lines[i].replace(old, new, 1)
            else:
                line = self.current_line()
                if global_flag:
                    total = line.count(old)
                    self.lines[self.cy] = line.replace(old, new)
                else:
                    total = 1 if old in line else 0
                    self.lines[self.cy] = line.replace(old, new, 1)
            if total > 0:
                self.mark_dirty()
                self.message = f"Replaced {total} occurrence(s)"
            else:
                self.message = f"Pattern not found: {old}"
        except Exception as e:
            self.message = f"Replace error: {e}"

    def open_buffer(self, filepath):
        """Open a new file in a buffer"""
        try:
            # Save current buffer
            if self.filepath and self.filepath in self.buffers:
                self.buffers[self.filepath] = self.lines[:]
            if filepath not in self.buffers:
                path = Path(filepath)
                if path.exists():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    self.buffers[filepath] = text.splitlines() or [""]
                else:
                    self.buffers[filepath] = [""]
                self.buffer_order.append(filepath)
            self.current_buffer_idx = self.buffer_order.index(filepath)
            self.filepath = filepath
            self.lines = self.buffers[filepath][:]
            self.cx = 0
            self.cy = 0
            self.detect_syntax()
            self.emit("buffer_open", filepath=filepath)
            self.message = f"Opened buffer: {filepath}"
        except Exception as e:
            self.message = f"Error opening buffer: {e}"

    def next_buffer(self):
        """Switch to next buffer (:bn)"""
        if not self.buffer_order:
            self.message = "No buffers open"
            return
        self.current_buffer_idx = (self.current_buffer_idx + 1) % len(self.buffer_order)
        self.filepath = self.buffer_order[self.current_buffer_idx]
        self.lines = self.buffers[self.filepath][:]
        self.cy = min(self.cy, max(0, len(self.lines) - 1))
        self.cx = min(self.cx, max(0, len(self.lines[self.cy]) - 1)) if self.lines[self.cy] else 0
        self.message = f"Buffer: {self.filepath}"

    def prev_buffer(self):
        """Switch to previous buffer (:bp)"""
        if not self.buffer_order:
            self.message = "No buffers open"
            return
        self.current_buffer_idx = (self.current_buffer_idx - 1) % len(self.buffer_order)
        self.filepath = self.buffer_order[self.current_buffer_idx]
        self.lines = self.buffers[self.filepath][:]
        self.cy = min(self.cy, max(0, len(self.lines) - 1))
        self.cx = min(self.cx, max(0, len(self.lines[self.cy]) - 1)) if self.lines[self.cy] else 0
        self.message = f"Buffer: {self.filepath}"

    def list_buffers(self):
        """List all open buffers (:ls, :buffers)"""
        if not self.buffer_order:
            self.message = "No buffers open"
            return
        buf_list = ", ".join(self.buffer_order)
        self.message = f"Buffers: {buf_list}"


def main():
    import sys
    args = sys.argv[1:]
    filepath = None
    for arg in args:
        if arg in ("-h", "--help"):
            print("Usage: evim [file]")
            print("  file    File to open (optional)")
            print("\nKeyboard shortcuts:")
            print("  :help   Show full help inside editor")
            print("  :lsp    Start language server")
            print("  :q      Quit")
            sys.exit(0)
        elif arg in ("-v", "--version"):
            print("EVim 1.0")
            sys.exit(0)
        elif not arg.startswith("-"):
            filepath = arg
    editor = Editor(filepath)
    curses.wrapper(editor.start)

if __name__ == "__main__":
    main()
