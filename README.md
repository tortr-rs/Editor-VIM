# EVim

A modal CLI text editor inspired by Vim, built with Python and curses. 57 languages, 24 themes, built-in LSP client, file explorer, minimap, terminal, and plugin system — with Emacs-style IDE features.
https://github.com/tortr-rs/evim-plugins for my official plugins!
and visit https://editor-vim.netlify.app/ for the official website
![Python](https://img.shields.io/badge/Python-3.x-blue)
![License](https://img.shields.io/badge/License-BSD--3--Clause-green)

## Features

- **Modal Editing** — Normal, Insert, Command, Visual, Terminal, and Explorer modes
- **57 Languages** — Syntax highlighting with keywords and type annotations
- **24 Color Themes** — Switch instantly with `:theme <name>`
- **Built-in LSP Client** — Go-to-definition, hover, references, completion (30 pre-configured servers)
- **File Explorer** — Tree-view sidebar with expand/collapse (`Ctrl+e`)
- **Minimap** — Scrollable code overview with viewport indicator (`Ctrl+m`)
- **Built-in Terminal** — PTY-based terminal panel (`Ctrl+n`)
- **Run Button** — Execute code in 37 languages (`F5` or click `▶ Run`)
- **Plugin System** — Event hooks, auto-loading from `~/.config/evim/plugins/`
- **Git Gutter** — Shows added/modified/deleted lines
- **Fuzzy Finder** — Quick file navigation (`Ctrl+p`)
- **Macros, Marks, Registers** — Full Vim-style text manipulation
- **Mouse Support** — Click, drag, double/triple-click, scroll, right-click context menu, clickable tab bar & minimap
- **Smart Editing** — Auto-pairs, auto-indent, dot-repeat, snippets
- **Python-Scriptable Config** — `.evimrc` with full Python access
- **Command Palette** — Emacs M-x style (`Ctrl+x` / `Alt+x`) with fuzzy filtering
- **Kill Ring** — Multi-clipboard with rotate (`Ctrl+y` paste, `Alt+y` rotate)
- **Incremental Search** — Live match highlighting as you type
- **Code Folding** — `za`/`zo`/`zc`/`zM`/`zR` fold commands
- **Project Grep** — `:grep` with rg/grep fallback and interactive results
- **Symbol Outline** — `:outline` / `Alt+o` for function/class jump
- **Recent Files** — `:recent` with persistent history
- **Surround Editing** — `cs`/`ds`/`S` to change, delete, add surrounds

## Installation

Requires Python 3 with `curses` (included on Linux/macOS).

### Debian / Ubuntu (apt)

```bash
curl -fsSL https://tortr-rs.github.io/evim-repo/public.key | sudo gpg --dearmor -o /usr/share/keyrings/evim.gpg
echo "deb [signed-by=/usr/share/keyrings/evim.gpg] https://tortr-rs.github.io/evim-repo stable main" | sudo tee /etc/apt/sources.list.d/evim.list
sudo apt update && sudo apt install evim-editor
```

### Fedora / RHEL (dnf)

```bash
sudo tee /etc/yum.repos.d/evim.repo <<EOF
[evim]
name=EVim Editor Repository
baseurl=https://tortr-rs.github.io/evim-repo/rpm
enabled=1
gpgcheck=1
gpgkey=https://tortr-rs.github.io/evim-repo/public.key
EOF
sudo dnf install evim-editor
```

### openSUSE (zypper)

```bash
sudo zypper addrepo https://tortr-rs.github.io/evim-repo/rpm evim
sudo zypper refresh && sudo zypper install evim-editor
```

### Arch Linux (pacman)

```bash
sudo pacman -U https://github.com/tortr-rs/Editor-VIM/releases/download/v1.0.0/evim-editor-1.0.0-1-any.pkg.tar.zst
```

### macOS (Homebrew)

```bash
brew tap tortr-rs/evim && brew install evim-editor
```

### Flatpak

```bash
# Download from GitHub Releases
flatpak install evim-editor-1.0.0.flatpak
```

### pip

```bash
pip install evim-editor
```

### From source

```bash
git clone https://github.com/tortr-rs/Editor-VIM.git
cd Editor-VIM
chmod +x evim
ln -s "$(pwd)/evim" ~/.local/bin/evim
#then run evim 
```

## Keybindings

### Normal Mode

| Key | Action |
|-----|--------|
| `h` `j` `k` `l` | Move left/down/up/right |
| `w` `b` `e` | Word forward/backward/end |
| `0` `$` | Line start/end |
| `gg` `G` | File top/bottom |
| `i` `a` `o` `O` | Enter insert mode |
| `dd` `dw` `x` | Delete line/word/char |
| `yy` `p` | Yank line / paste |
| `cw` | Change word |
| `u` / `Ctrl+r` | Undo / redo |
| `.` | Repeat last change |
| `/` `?` `n` `N` | Search forward/backward/next/prev |
| `gd` | LSP go-to-definition |
| `gr` | LSP references |
| `K` | LSP hover info |
| `Ctrl+d` `Ctrl+u` | Scroll half-page down/up |
| `Ctrl+e` | Toggle file explorer |
| `Ctrl+m` | Toggle minimap |
| `Ctrl+n` | Toggle terminal |
| `Ctrl+p` | Fuzzy file finder |
| `Ctrl+s` | Quick save |
| `Ctrl+/` | Toggle comment |
| `F5` | Run file |
| `F10` | Settings menu |
| `q<reg>` `@<reg>` | Record / play macro |
| `m<char>` `'<char>` | Set / go to mark |
| `Ctrl+x` | Command palette (M-x) |
| `Ctrl+y` | Kill ring paste |
| `Alt+y` | Kill ring rotate |
| `Alt+o` | Symbol outline |
| `za` `zo` `zc` | Fold toggle/open/close |
| `zM` `zR` | Fold all / unfold all |
| `cs<old><new>` | Change surround pair |
| `ds<char>` | Delete surround |
| `S<char>` | Surround selection (visual) |

### Insert Mode

| Key | Action |
|-----|--------|
| `Esc` | Back to normal mode |
| `Tab` | LSP / keyword completion |
| Auto-pairs | `()` `[]` `{}` `""` `''` |

### Command Mode

| Command | Action |
|---------|--------|
| `:w` | Save |
| `:q` `:q!` | Quit / force quit |
| `:wq` | Save and quit |
| `:e <file>` | Open file |
| `:bn` `:bp` | Next / previous buffer |
| `:theme <name>` | Change theme |
| `:set <opt>=<val>` | Set option |
| `:%s/old/new/g` | Search and replace |
| `:lsp` | Start LSP server |
| `:run` | Run current file |
| `:help` | Show help |
| `:! <cmd>` | Shell command |
| `:grep <pattern>` | Project-wide search |
| `:outline` | Symbol outline |
| `:recent` | Recent files |
| `:palette` | Command palette |
| `:fold` `:foldall` | Toggle fold / fold all |
| `:unfoldall` | Unfold all |
| `:sort` | Sort lines |
| `:killring` | Show kill ring |
| `:menu` | Settings menu |
| `:zen` | Zen mode (plugin) |
| `:lorem [n]` | Lorem ipsum (plugin) |

## Mouse

| Action | Effect |
|--------|--------|
| Click | Position cursor |
| Double-click | Select word |
| Triple-click | Select line |
| Drag | Visual selection |
| Right-click | Context menu |
| Scroll wheel | Scroll up/down |
| Click tab bar | Switch buffer |
| Click minimap | Jump to position |
| Click mode indicator | Toggle insert/normal |
| Click ▶ Run | Run file |

## Themes

`classic_blue` · `neon_nights` · `desert_storm` · `sunny_meadow` · `vampire_castle` · `arctic_aurora` · `forest_grove` · `golden_wheat` · `midnight_sky` · `cloudy_day` · `city_lights` · `creamy_latte` · `deep_space` · `fresh_breeze` · `matrix_code` · `ocean_blue` · `fire_red` · `forest_green` · `purple_haze` · `sunset_orange` · `arctic_white` · `midnight_purple` · `desert_gold` · `cyber_pink`

## Supported Languages

C, C++, C#, Rust, Python, Lua, Pascal, Fortran, JavaScript, TypeScript, Java, Go, Ruby, PHP, Perl, Swift, Kotlin, Scala, Shell, Assembly, R, Zig, Nim, Dart, Elixir, Erlang, Haskell, OCaml, Clojure, Lisp, Vue, Svelte, YAML, TOML, JSON, XML, HTML, CSS, SCSS, Sass, Less, SQL, Markdown, CMake, Dockerfile, Protobuf, V, D, Objective-C, Julia, PowerShell, Terraform, Solidity, Groovy, evimlang

## LSP Support

30 pre-configured language servers. Start with `:lsp` or `:lsp start`.

| Language | Server |
|----------|--------|
| Python | `pyright-langserver` |
| TypeScript/JS | `typescript-language-server` |
| C/C++ | `clangd` |
| Rust | `rust-analyzer` |
| Go | `gopls` |
| Lua | `lua-language-server` |
| Java | `jdtls` |
| Ruby | `solargraph` |
| PHP | `phpactor` |
| C# | `omnisharp` |
| Zig | `zls` |
| Dart | `dart language-server` |
| Haskell | `haskell-language-server-wrapper` |
| Elixir | `elixir-ls` |
| Kotlin | `kotlin-language-server` |
| Scala | `metals` |
| Swift | `sourcekit-lsp` |
| HTML/CSS/JSON | `vscode-*-language-server` |
| YAML | `yaml-language-server` |
| Vue | `vue-language-server` |
| Svelte | `svelteserver` |
| Nim | `nimlangserver` |
| OCaml | `ocamllsp` |
| Clojure | `clojure-lsp` |
| Julia | `LanguageServer.jl` |
| Terraform | `terraform-ls` |

## Configuration

EVim loads config from `.evimrc`, `.evimrc.py`, `evimrc.py`, or `evimrc` in the working directory.

```python
editor.set_theme('matrix_code')
editor.options['number'] = True
editor.options['relativenumber'] = True
editor.register_key('normal', '<F1>', lambda e: e.run_ex('help'))
editor.message = 'Config Loaded'
```

## Plugins

Place Python files in `~/.config/evim/plugins/` or `.evim/plugins/`:

```python
def on_load(editor):
    editor.message = "Plugin loaded!"

def on_save(editor):
    editor.message = f"Saved {editor.filepath}"
```

Manage with `:PluginList`, `:PluginLoad`, `:PluginEnable`, `:PluginDisable`.

## License

BSD 3-Clause License — see [LICENSE](LICENSE) for details.
