## Keybindings

### Tree
- `Ctrl+Up/Down` - Navigating channel tree
- `Ctrl+Space` - Expand selected categories and servers or enter selected channel
- `Alt+H` - Un/collapse channel with threads in tree
- `Alt+J` - Join/leave selected thread in tree
- `Alt+I` - View channel info (selected in tree)
- `Alt+Shift+U`- Copy selected channel (in tree) URL to clipboard
- `Shift+Up/Down` - Select prev/next server and collapse all other servers

### Input line
- `Left/Right` - character left/right
- `Ctrl+Left/Right` - word left/right
- `HOME/END` - go to start/end of input line
- `Shift+Left/Right` - select character left/right
- `Ctrl+Shift+Left/Right` - select word left/right
- `Shift+HOME/END` - select to start/end of input line
- `Ctrl+N` - Insert newline in input line (warning `Shift+Enter` doesn't work in terminals)
- `Alt+Z` - Undo
- `Alt+Shift+Z` - Redo
- `Alt+A` - Select all
- `Alt+C` - Copy selection in input line, otherwise copy selected message in the chat
- `Alt+X` - Cut selection
- `Ctrl+V` - Smart paste - paste text or file as attachment
- `Ctrl+Backspace` - Delete word backwards
- `Ctrl+Del` - Delete word forwards

### Chat
- `Enter` - Send message
- `Arrow-Up/Down` - Navigating messages / popup window if its open and setting `assist_swap_bindings = True`
- `Ctrl+R` - Reply to selected message
- `Ctrl+E` - Edit selected message
- `Ctrl+D` - Delete selected message
- `Ctrl+P` - Toggle reply ping when replying
- `Ctrl+B` - Scroll back to chat bottom
- `Ctrl+G` - Go to replied message from selected message
- `Ctrl+L` - Download selected attachment
- `Ctrl+U` Upload attachments
- `Ctrl+O` - Open selected link in browser
- `Ctrl+W` - View selected attachment (image, gif, video, audio) in media player
- `Alt+S` - Reveal one spoiler in selected messages
- `Ctrl+F` - Search messages in current server
- `Alt+F` - Search gifs
- `Alt+P` - View user profile (selected message)
- `Alt+U` - Copy selected message URL to clipboard
- `Alt+R` - Add reaction to selected message
- `Alt+W` - Show reactions details for selected message
- `Alt+N` - Show pinned messages in current channel
- `Ctrl+Shift+V` - This is common terminal binding to paste text, better use: `Ctrl+V` binding for smart paste

### Popup line
- `Alt+<` - Previous uploaded/uploading attachment
- `Alt+>` - Next uploaded/uploading attachment

### Popup window
- `Alt+Up/Down` - navigate in popup window / member list if open and no popup window / chat if popup window is open and setting `assist_swap_bindings = True`
- `Alt+Enter` - select in popup window / member list
- `Alt+V` - Preview selected file in upload assist or when searching gif or attachments ready to send
- `Ctrl+Y-Up/Down` - Resize popup window +/- 1

### Other
- `Ctrl+/` - Open command palette
- `Ctrl+X` - Cancel all downloads and uploads
- `Alt+D` - Cycle user status (online/away/DnD/invisible)
- `Alt+M` - Toggle member list
- `Ctrl+T` - Toggle tabbed state for selected channel in tree
- `Alt+NUM`- Switch to tab, `NUM` is 1-9 in number row, not numeric keypad!
- `Alt+E` - Open external editor to type message in it
- `Ctrl+K` - Open command palette and type `goto ` and show recent channels
- `Alt+Left/Right` - Switch tabs incrementally (next/previous)
- `Ctrl+C` - Quit
- `Enter` - Open selected post in forum
- `Escape` - Close assist, exit command mode, cancel...

### Media player controls
- `escape` - quit
- `Space` - pause
- `Left/Right` - seek
- `Up/Down` - volume
- `Z` - replay

### Default command-bindings (macros)
- `Alt+Left/Right` -`"M-LEFT" = "switch_tab prev"` and `"M-RIGHT" = "switch_tab next"`  
    Switch to previous/next tabbed channel.
- `Ctrl+K` - `"C-k" = "command_palette; type 'goto '"`  
    Open command palette and type `goto `.
- `Shift+Up` - `"S-UP" = "tree_select server prev; collapse_all_except selected"`  
    Select previous server in tree and collapse all other servers except it.
- `Shift+Down` - `"S-DOWN" = "tree_select server; collapse_all_except selected"`  
    Select next server in tree and collapse all other servers except it.
- `Ctrl+Y Up/Down` - `"C-y UP" = "resize_popup_window +1"` and `"C-y DOWN" = "resize_popup_window -1"`  
    Increase/Decrease vertical size of popup window by 1.

### OS specific keybindings
Some keybindings are used by terminals or OS itself, so they are by default rebound to something else.

### Windows:
- `Ctrl+A` - Expand selected categories and servers

### macOS:
- `Alt+O` - Open link in browser


## Mouse controls

### Single click on:
- All windows, in tree also: un/collapse
- Tabs (subtitle) line to switch tabs (only if exactly `format_title_line_r = "%tabs"` in config)
- Popup window title and drag it to resize it
- Scrollbar to move it there, or drag it
- Tree border to toggle its minimized state
- Member list border to toggle its minimized state
- Buttons in call UI, click on input/output volume values to toggle mute each

### Double click on:
- Tree - un/collapse category/server or enter channel
- Popup window - select item
- Member list - view member profile
- Input line - select a word
- Tabs (subtitle) line - if tab is temporary (italics) will be made permanent

### Double click in chat on:
- Message time - start replying to message
- Message reply line - go to that message
- Username - view profile
- Reaction - toggle that reaction
- URL - open media / download file / open in browser
- Spoiler - reveal that spoiler
- User mention - view profile of mentioned user
- Channel - go to that channel
- Custom discord emoji - view that emoji in media player
- Message attachment - download attachment
- Inline image embed - open / play media

### Other
- Scroll up/down in all windows
- Middle click on channel in tree to add it to new tab
- Middle click on tab (in subtitle line) to remove it

On Windows, double click isn't working, use triple click instead.


## Configuring keybindings
### Standard keybindings
Keybindings are configured in separate sections in `config.ini`.  Main keybindings section is `[keybindings]`.  
Key combinations are saved as custom syntax (very similar to emacs), that can be generated by running `endcord -k`.  
Syntax: `Mod1-Mod2-Mod3-Key`. Mod2 and Mod3 are optional.  
Modifiers: `C` - Ctrl, `M` - Alt, `S` - Shift. Keys can be uppercase and lowercase, uppercase doesn't indicate shift.  
Special keys: `UP`/`DOWN`/`LEFT`/`RIGHT` - arrow keys, `"ENTER"`.  
`Ctrl+Shift+Key` combinations are not supported by most terminal emulators, but `Alt+Shift+Key` are.  
Keybindings can also be chained like this (maximum 2 bindings in chain, separated with space ` `):  
`"C-y M-e"` which means: press `Ctrl+Y` then `Alt+E`, or `"C-y S-UP"`...  
To specify multiple keybindings for same action put them in a tuple, eg.: `("C-y", "C-y M-e")`.  
Switch tab keybinding is special - `NUM` is placeholder for 1-9 number keys, eg.:`M-NUM` or `M-y NUM`.  

### Configuring vim mode keybindings
Keybindings for vim mode are configured in section `[vim_mode_bindings]` can be typed as characters but they must be in "". Eg.: `"edit" = "e"`.  
In vim mode bindings, lone capital letter means Shift+key.  
There is one special keybinding, used only in vim mode: `"insert_mode" = "i"`.  
Command bindings can also use vim mode bindings, but they must be typed as key codes in quotes!  

### Command keybinding (macros)
There is additional section `[command_bindings]`, used to make custom client command string or even macros executed when keybinding is pressed.  
Command keybinding is added like this: `"C-x" = "send_message Hello World!"`. This will execute that command when `Ctrl+X` is pressed.  
Note that all bindings must be inside quotes, even a single integer. To use same binding as standard keybindings, set standard keybinding to `None`.  
Alongside commands, standard keybinding names can be used here too. Eg. `"C-x" = "tree_up; tree_up"` will "press" tree_up binding twice.  
To execute multiple commands in a sequence, type them separated with `;` character. To use actual `;` character in command type it as `\;`.  
Special commands available only for command-bindings are documented in [Commands list](commands.md#command-bindings-only-commands).  


## Vim mode keybindings
- `i` - Enter insert mode

### tree
- `K/J` - Navigating channel tree
- `Space` - Expand selected categories and servers or enter selected channel
- `W` - Un/collapse channel with threads in tree
- `O` - Join/leave selected thread in tree
- `I` - View channel info (selected in tree)
- `C`- Copy selected channel (in tree) URL to clipboard

### input line
- `h/l` - character left/right
- `b/w` - word left/right
- `Ctrl+h/l` - select left/right
- `H/L` - select word left/right
- `Ctrl+N` - Insert newline in input line (warning `Shift+Enter` doesn't work in terminals)
- `u` - Undo
- `Ctrl+R` - Redo
- `a` - Select all
- `y` - Copy selection in input line, otherwise copy selected message in the chat
- `Y` - Cut selection
- `X` - Delete word
- `p` - Smart paste - paste text or file as attachment

### chat
- `Enter` - Send message
- `k/j` - Navigating messages
- `r` - Reply to selected message
- `e` - Edit selected message
- `d` - Delete selected message
- `P` - Toggle reply ping when replying
- `B` - Scroll back to chat bottom
- `g` - Go to replied message from selected message
- `D` - Download selected attachment
- `U` - Upload attachments
- `o` - Open selected link in browser
- `v` - View selected attachment (image, gif, video, audio) in media player
- `S` - Reveal one spoiler in selected messages
- `f` - Search messages in current server
- `F` - Search gifs
- `c` - View user profile (selected message)
- `M` - Copy selected message URL to clipboard
- `R` - Add reaction to selected message
- `A` - Show reactions details for selected message
- `n` - Show pinned messages in current channel
- `Ctrl+Shift+V` - This is common terminal binding to paste text, better use: `paste` command

### popup line
- `<` - Previous uploaded/uploading attachment
- `>` - Next uploaded/uploading attachment

### popup window
- `,/.` - navigate in popup window / member list
- `q` - select in popup window / member list
- `V` - Preview selected file in upload assist or when searching gif or attachments ready to send

### other
- `:` - Open command palette
- `X` - Cancel all downloads and uploads
- `s` - Cycle user status (online/away/DnD/invisible)
- `m` - Toggle member list
- `t` - Toggle tabbed state for selected channel in tree
- `NUM`- Switch to tab, `NUM` is 1-9.
- `E` - Open external editor to type message in it
- `Ctrl+K` - Open command palette and type `goto ` and show recent channels
- `Alt+Left/Right` - Switch tabs incrementally (next/previous)
- `Q` - Quit
