# evim

**Editor_VIM** – A modal CLI text editor inspired by Vim, built with Python and curses. Features syntax highlighting, themes, auto-completion, and a custom scripting language called evimlang.

## Features

- **Modal Editing**: Normal, Insert, and Command modes with Vim-like keybindings
- **Syntax Highlighting**: Support for C, C++, C#, Pascal, Fortran, Python, Lua, Rust, and evimlang
- **Themes**: 15 unique themes (switch with `:theme <name>`), including a retro green-on-black
- **Auto-Pair & Indent**: Smart auto-pairing for `()`, `{}`, `[]`, quotes, and indentation for code files only
- **IntelliSense Completion**: Tab-based completion for keywords and snippets
- **AI Snippets**: Auto-suggestions for code snippets
- **evimlang Scripting**: Custom config language for settings, mappings, and Python integration (like Vim's vimrc)
- **VS Code Extension**: Syntax highlighting for evimlang in VS Code (local extension)

## Installation

Requires Python 3 and the `curses` library (usually included with Python).

```bash
# Clone the repo
git clone https://github.com/yourusername/evim.git
cd evim

# Run
python3 evim.py [filename]
```

## Usage

- Start editing: `python3 evim.py myfile.py`
- Modes:
  - **Normal**: Navigate with `h/j/k/l`, enter commands with `:`
  - **Insert**: Type text with `i/a/o`
  - **Command**: `:w` save, `:q` quit, `:theme <name>` change theme
- evimlang config: Edit `evimrc` for custom settings (e.g., `set theme retro`, `map <key> <command>`)

## Configuration

Use `evimrc` for startup config. Examples:

```
set theme retro
map <C-s> :w
python print("Hello from evim!")
```

## Contributing

Feel free to open issues or PRs on GitHub. BSD 3-Clause licensed.

## License

BSD 3-Clause License - see [LICENSE](LICENSE) for details.