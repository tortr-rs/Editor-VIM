#!/usr/bin/env python3
import curses
import curses.ascii
import os
import re
from pathlib import Path

CONFIG_FILE = "evimrc"

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
        }
        self.python_env = {}
        self.start_hooks = []
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
        self.filetype = None
        self.syntax_language = None
        self.dirty = False
        self.cached_variables = set()
        self.read_file()
        self.load_config()

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
        try:
            data = "\n".join(self.lines) + ("\n" if self.lines else "")
            Path(self.filepath).write_text(data, encoding="utf-8")
            self.message = f"Saved {self.filepath}"
            self.dirty = False
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
            keywords = set(self.get_keyword_sets(self.syntax_language))
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
        if action.startswith(":"):
            self.run_command(action[1:])
        else:
            # Simulate key presses, but for simplicity, just run as command
            self.run_command(action)

    def parse_evimlang(self, content):
        lines = content.splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('"'):
                continue
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "set":
                if len(parts) >= 2:
                    option = parts[1]
                    if option == "numbers":
                        option = "number"
                    elif option == "nonumbers":
                        option = "nonumber"
                    if option.startswith("no"):
                        self.set_option(option[2:], False)
                    else:
                        value = " ".join(parts[2:]) if len(parts) > 2 else True
                        self.set_option(option, value)
            elif cmd == "map":
                if len(parts) >= 3:
                    mode = parts[1]
                    key = parts[2]
                    action = " ".join(parts[3:])
                    self.register_key(mode, key, lambda e, a=action: self.run_mapped_action(a))
            elif cmd == "python":
                code = " ".join(parts[1:])
                try:
                    exec(code, {"editor": self, "set_option": self.set_option, "register_key": self.register_key})
                except Exception as exc:
                    self.message = f"Python error in config: {exc}"
            else:
                self.message = f"Unknown evimlang command: {cmd}"

    def load_config(self):
        path = Path(CONFIG_FILE)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        self.parse_evimlang(content)

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        theme = self.options.get("theme", "default")
        themes = {
            "classic_blue": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_CYAN, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_RED, -1, curses.A_BOLD),
            },
            "neon_nights": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_CYAN, -1, 0),
                "preprocessor": (curses.COLOR_RED, -1, curses.A_BOLD),
            },
            "desert_storm": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "sunny_meadow": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "vampire_castle": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "arctic_aurora": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "forest_grove": {
                "keyword": (curses.COLOR_RED, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_CYAN, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "golden_wheat": {
                "keyword": (curses.COLOR_RED, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_CYAN, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "midnight_sky": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "cloudy_day": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "city_lights": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "creamy_latte": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_CYAN, -1, 0),
                "preprocessor": (curses.COLOR_RED, -1, curses.A_BOLD),
            },
            "deep_space": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "fresh_breeze": {
                "keyword": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "matrix_code": {
                "keyword": (curses.COLOR_GREEN, -1, curses.A_BOLD),
                "type": (curses.COLOR_GREEN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_GREEN, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_GREEN, -1, 0),
                "preprocessor": (curses.COLOR_GREEN, -1, curses.A_BOLD),
            },
            "ocean_blue": {
                "keyword": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "type": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "comment": (curses.COLOR_WHITE, -1, 0),
                "string": (curses.COLOR_YELLOW, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_RED, -1, curses.A_BOLD),
            },
            "fire_red": {
                "keyword": (curses.COLOR_RED, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_WHITE, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_CYAN, -1, 0),
                "preprocessor": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
            },
            "forest_green": {
                "keyword": (curses.COLOR_GREEN, -1, curses.A_BOLD),
                "type": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "comment": (curses.COLOR_CYAN, -1, 0),
                "string": (curses.COLOR_RED, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "purple_haze": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_YELLOW, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "sunset_orange": {
                "keyword": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "type": (curses.COLOR_RED, -1, curses.A_BOLD),
                "comment": (curses.COLOR_CYAN, -1, 0),
                "string": (curses.COLOR_MAGENTA, -1, 0),
                "number": (curses.COLOR_GREEN, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "arctic_white": {
                "keyword": (curses.COLOR_BLACK, -1, curses.A_BOLD),
                "type": (curses.COLOR_BLUE, -1, curses.A_BOLD),
                "comment": (curses.COLOR_CYAN, -1, 0),
                "string": (curses.COLOR_RED, -1, 0),
                "number": (curses.COLOR_MAGENTA, -1, 0),
                "preprocessor": (curses.COLOR_GREEN, -1, curses.A_BOLD),
            },
            "midnight_purple": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_YELLOW, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "desert_gold": {
                "keyword": (curses.COLOR_YELLOW, -1, curses.A_BOLD),
                "type": (curses.COLOR_RED, -1, curses.A_BOLD),
                "comment": (curses.COLOR_CYAN, -1, 0),
                "string": (curses.COLOR_MAGENTA, -1, 0),
                "number": (curses.COLOR_GREEN, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
            "cyber_pink": {
                "keyword": (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
                "type": (curses.COLOR_CYAN, -1, curses.A_BOLD),
                "comment": (curses.COLOR_YELLOW, -1, 0),
                "string": (curses.COLOR_GREEN, -1, 0),
                "number": (curses.COLOR_RED, -1, 0),
                "preprocessor": (curses.COLOR_BLUE, -1, curses.A_BOLD),
            },
        }
        t = themes.get(theme, themes["classic_blue"])
        curses.init_pair(1, t["keyword"][0], t["keyword"][1])
        curses.init_pair(2, t["comment"][0], t["comment"][1])
        curses.init_pair(3, t["string"][0], t["string"][1])
        curses.init_pair(4, t["number"][0], t["number"][1])
        curses.init_pair(5, t["type"][0], t["type"][1])
        curses.init_pair(6, t["preprocessor"][0], t["preprocessor"][1])
        self.color_keyword = curses.color_pair(1) | t["keyword"][2]
        self.color_type = curses.color_pair(5) | t["type"][2]
        self.color_comment = curses.color_pair(2) | t["comment"][2]
        self.color_string = curses.color_pair(3) | t["string"][2]
        self.color_number = curses.color_pair(4) | t["number"][2]
        self.color_preprocessor = curses.color_pair(6) | t["preprocessor"][2]
        self.color_default = curses.A_NORMAL

    def detect_syntax(self):
        extension_map = {
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".hpp": "cpp",
            ".hh": "cpp",
            ".hxx": "cpp",
            ".cs": "csharp",
            ".rs": "rust",
            ".py": "python",
            ".lua": "lua",
            ".pas": "pascal",
            ".pp": "pascal",
            ".pascal": "pascal",
            ".f": "fortran",
            ".for": "fortran",
            ".f90": "fortran",
            ".f95": "fortran",
            ".f03": "fortran",
            ".f77": "fortran",
            ".evimrc": "evimlang",
        }
        self.syntax_language = extension_map.get(self.filetype, None)
        self.snippets = {}
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
        # Add more as needed

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
        return base_types

    def highlight_line(self, stdscr, y, line, prefix, width, cursor_col=None):
        x = 0
        if prefix:
            self.draw_segment(stdscr, y, x, prefix, self.color_default, cursor_col=None, base=0)
            x += len(prefix)
        content = line
        lang = self.syntax_language
        pos = 0
        while pos < len(content):
            if lang in ("c", "cpp", "csharp", "rust"):
                if content[pos:].lstrip().startswith("#") and content[:pos].strip() == "":
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_preprocessor, cursor_col, base=pos)
                    return
                if content.startswith("//", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    return
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
                    return
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
                    return
            elif lang == "pascal":
                if content.startswith("//", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    return
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
                    return
            elif lang == "evimlang":
                if content.startswith("\"", pos):
                    self.draw_segment(stdscr, y, x, content[pos:], self.color_comment, cursor_col, base=pos)
                    return
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
                if token_key in self.get_keyword_sets(lang):
                    attr = self.color_keyword
                elif token_key in self.get_type_words(lang):
                    attr = self.color_type
                self.draw_segment(stdscr, y, x, token, attr, cursor_col, base=start)
                x += len(token)
                continue
            self.draw_segment(stdscr, y, x, content[pos], self.color_default, cursor_col, base=pos)
            x += 1
            pos += 1
        if cursor_col is not None and cursor_col == len(content) and x < width:
            self.draw_segment(stdscr, y, x, " ", curses.A_REVERSE, cursor_col, base=cursor_col)

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

    def register_key(self, mode, key, fn):
        self.bindings[(mode, key)] = fn

    def set_option(self, name, value):
        self.options[name] = value

    def on_start(self, fn):
        self.start_hooks.append(fn)

    def set_message(self, text):
        self.message = str(text)

    def run_python(self, source):
        try:
            exec(source, self.python_env)
            self.message = "Python executed"
        except Exception as exc:
            self.message = f"Python error: {exc}"

    def start(self, stdscr):
        curses.curs_set(1)
        stdscr.keypad(True)
        curses.raw()
        stdscr.timeout(100)
        self.init_colors()
        for fn in self.start_hooks:
            try:
                fn(self)
            except Exception as exc:
                self.message = f"Start hook error: {exc}"
        while not self.should_exit:
            if self.mode == "overlay":
                self.draw_overlay(stdscr)
            else:
                self.redraw(stdscr)
            self.handle_key(stdscr)

    def redraw(self, stdscr):
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        top = max(0, self.cy - height + 4)
        for idx, line in enumerate(self.lines[top: top + height - 2]):
            lineno = top + idx
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
            cursor_col = None
            if lineno == self.cy:
                display_cx = 0
                for i in range(min(self.cx, len(line))):
                    if line[i] == '\t':
                        display_cx += self.options["tabsize"]
                    else:
                        display_cx += 1
                cursor_col = display_cx
            self.highlight_line(stdscr, idx, display_line, prefix, width, cursor_col)
            # Draw indent guides
            if self.options.get("indent_guides", False) and self.syntax_language:
                prefix_len = len(prefix)
                indent = len(display_line) - len(display_line.lstrip())
                if indent > 0:
                    guide_x = indent + prefix_len
                    if guide_x < width - 1:
                        stdscr.addstr(idx, guide_x, "│", curses.A_DIM)
        status = f"{self.mode.upper()} | {self.filepath or '[no file]'} | {self.message}"
        if len(status) > width - 1:
            status = status[:width - 1]
        stdscr.addstr(height - 2, 0, status, curses.A_REVERSE)
        if self.mode == "command" and self.options.get("show_command"):
            command_line = ":" + self.command
            stdscr.addstr(height - 1, 0, command_line[:width - 1])
        else:
            stdscr.addstr(height - 1, 0, "Press : for commands, i for insert, ESC to return.")
        line = self.lines[self.cy] if self.cy < len(self.lines) else ""
        display_cx = 0
        for i in range(min(self.cx, len(line))):
            if line[i] == '\t':
                display_cx += self.options["tabsize"]
            else:
                display_cx += 1
        curses.setsyx(self.cy - top, display_cx + (5 if self.options.get("number") or self.options.get("relativenumber") else 0))
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
                "Commands:",
                "  i        - insert mode",
                "  dd       - delete line",
                "  dw       - delete word",
                "  yy       - yank line",
                "  p        - paste",
                "  u        - undo",
                "  /pattern - search forward",
                "  ?pattern - search backward",
                "  n/N      - next/previous search",
                "  :w, :q, :wq, :source, :help",
                "  v        - enter visual selection",
                "  d        - delete selection",
                "",
                "Press any key to return...",
            ]
        box_top = max(0, (height - len(lines)) // 2 - 1)
        box_left = max(0, (width - 60) // 2)
        for idx, text in enumerate(lines):
            stdscr.addstr(box_top + idx, box_left, text[:width - box_left - 1])
        curses.setsyx(0, 0)
        curses.doupdate()

    def handle_key(self, stdscr):
        ch = stdscr.getch()
        if ch < 0:
            return
        if self.mode == "overlay":
            self.mode = "normal"
            self.show_welcome = False
            self.message = "EVim - normal mode"
            return
        if self.mode == "insert":
            if ch in (curses.KEY_EXIT, 27):
                self.mode = "normal"
                self.message = "EVim - normal mode"
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
                self.do_completion()
                return
            if curses.ascii.isprint(ch):
                self.snapshot()
                char = chr(ch)
                if self.syntax_language and self.try_skip_closing(char):
                    return
                if self.syntax_language and self.try_insert_pair(char):
                    return
                self.insert_char(char)
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
                return
            if combo == "dw":
                self.snapshot()
                self.delete_word()
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
            self.cy = len(self.lines) - 1
            self.cx = min(self.cx, len(self.current_line()))
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

    def calculate_indent(self, line_idx):
        if line_idx == 0 or not self.syntax_language:
            return 0
        prev_line = self.lines[line_idx - 1]
        indent = len(prev_line) - len(prev_line.lstrip())
        stripped = prev_line.rstrip()
        if self.syntax_language in ("c", "cpp", "csharp", "rust", "java"):
            if stripped.endswith("{"):
                indent += self.options["tabsize"]
        elif self.syntax_language == "python":
            if stripped.endswith(":"):
                indent += self.options["tabsize"]
        elif self.syntax_language == "lua":
            if stripped.endswith("then") or stripped.endswith("do") or stripped.endswith("function"):
                indent += self.options["tabsize"]
        elif self.syntax_language == "pascal":
            if stripped.endswith("begin"):
                indent += self.options["tabsize"]
        return indent

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
        if command == "q!":
            self.should_exit = True
            return
        if command == "q":
            if self.dirty:
                self.message = "Unsaved changes. Use :w or :wq"
                return
            self.should_exit = True
            return
        if command == "w":
            self.write_file()
            return
        if command.startswith("w "):
            self.filepath = command[2:].strip()
            self.detect_syntax()
            self.write_file()
            return
        if command == "wq":
            self.write_file()
            self.should_exit = True
            return
        if command == "help":
            self.open_help()
            return
        if command == "evimh":
            self.open_help()
            return
        if command.startswith("python ") or command.startswith("py "):
            code = command.split(" ", 1)[1]
            self.run_python(code)
            return
        if command.startswith("set "):
            self.parse_set(command[4:].strip())
            return
        if command == "source" or command == "source " + CONFIG_FILE:
            self.load_config()
            self.message = f"Sourced {CONFIG_FILE}"
            return
        if command.startswith("/") or command.startswith("?"):
            self.search_command(command)
            return
        if command.startswith("theme "):
            theme_name = command[6:].strip()
            self.set_theme(theme_name)
            return
        if command.startswith("evimlang "):
            evim_code = command[9:].strip()
            self.run_evimlang(evim_code)
            return
        self.message = f"Unknown command: {command}"

    def parse_set(self, option):
        if option == "number":
            self.options["number"] = True
            self.message = "Line numbers enabled"
        elif option == "nonumber":
            self.options["number"] = False
            self.message = "Line numbers disabled"
        elif option == "relativenumber":
            self.options["relativenumber"] = True
            self.message = "Relative line numbers enabled"
        elif option == "norelativenumber":
            self.options["relativenumber"] = False
            self.message = "Relative line numbers disabled"
        elif option == "indent_guides":
            self.options["indent_guides"] = True
            self.message = "Indent guides enabled"
        elif option == "noindent_guides":
            self.options["indent_guides"] = False
            self.message = "Indent guides disabled"
        else:
            self.message = f"Unknown option: {option}"

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
        self.cached_variables.clear()

    def undo(self):
        if not self.history:
            self.message = "Nothing to undo"
            return
        self.lines, self.cx, self.cy = self.history.pop()
        self.message = "Undo"
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
        self.message = "Change word"

    def yank_line(self):
        self.clipboard = self.current_line() + "\n"
        self.message = "Yanked line"

    def paste_after(self):
        if not self.clipboard:
            self.message = "Nothing to paste"
            return
        line = self.current_line()
        self.lines[self.cy] = line[: self.cx] + self.clipboard + line[self.cx :]
        self.cx += len(self.clipboard)
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
        self.clipboard = copied
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
        row, col = found
        self.cy = row
        self.cx = col
        self.set_cursor()

    def run_evimlang(self, code):
        """Execute evimlang code on the fly"""
        try:
            self.parse_evimlang(code)
            self.message = "Evimlang executed"
        except Exception as e:
            self.message = f"Evimlang error: {e}"
        valid_themes = [
            "classic_blue", "neon_nights", "desert_storm", "sunny_meadow", "vampire_castle",
            "arctic_aurora", "forest_grove", "golden_wheat", "midnight_sky", "cloudy_day",
            "city_lights", "creamy_latte", "deep_space", "fresh_breeze", "matrix_code"
        ]
        if theme_name in valid_themes:
            self.options["theme"] = theme_name
            self.init_colors()
            self.message = f"Theme set to {theme_name.replace('_', ' ').title()}"
        else:
            self.message = f"Unknown theme: {theme_name}. Available: {', '.join(t.replace('_', ' ').title() for t in valid_themes)}"


def main():
    import sys
    filepath = sys.argv[1] if len(sys.argv) > 1 else None
    editor = Editor(filepath)
    curses.wrapper(editor.start)

if __name__ == "__main__":
    main()
