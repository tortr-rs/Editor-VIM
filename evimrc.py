# evim config file — this is Python!
# The 'editor' object is available for customization.

# Theme
editor.set_theme('ocean_dark')

# Line numbers
editor.options['number'] = True
editor.options['relativenumber'] = True
editor.options['indent_guides'] = True

# Key mappings
editor.register_key('normal', '<F1>', lambda e: e.run_ex('help'))

editor.message = 'Config Loaded'