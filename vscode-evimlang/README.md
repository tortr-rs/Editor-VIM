# evimlang

Syntax highlighting for evimlang configuration files used by the evim text editor.

## Installation

1. Copy the `vscode-evimlang` folder to your VS Code extensions directory (`~/.vscode/extensions/`).
2. Reload VS Code.
3. Open `.evimrc` files - they should now have syntax highlighting.

## Features

- Syntax highlighting for evimlang commands (`set`, `map`, `python`)
- Comments starting with `"`
- Strings in double quotes
- Auto-closing brackets and quotes

## evimlang Syntax

```
" This is a comment
set number
set theme matrix_code
map normal <F1> :help
python print("Hello")
```