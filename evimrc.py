# evim config file — this is Python!
# Auto-saved by EVim settings menu (F10)
# The 'editor' object is available for customization.
editor = None  # Set by evim.py when loading

# Theme
editor.set_theme('golden_wheat')

# Display options
editor.options['number'] = True
editor.options['relativenumber'] = True
editor.options['indent_guides'] = True
editor.options['cursorline'] = False
editor.options['wrap'] = False
editor.options['statusline'] = True
editor.options['tabline'] = True
editor.options['show_command'] = True

# Editor options
editor.options['mouse'] = True
editor.options['error_lens'] = True
editor.options['bracket_highlight'] = True
editor.options['word_highlight'] = False
editor.options['autosave'] = True
editor.options['tabsize'] = 4
editor.options['autosave_delay'] = 2

# Panel options
editor.options['explorer'] = True
editor.options['minimal'] = False

editor.message = 'Config Loaded'

