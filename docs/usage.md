## Basic Usage

### Profile manager
Profile manager is used for login and easier switching between multiple accounts.
It allows logging in with standard email and password, 2fa, QR code, or to paste token.
Each account is saved as "profile", it has custom "name" as identifier, and last date of usage is also stored.
Profiles can be saved either in keyring or as plaintext json.
Keyring is OS managed secure storage for secrets (like passwords and tokens) - recommended.
Plaintext means that tokens will be saved non-encrypted in `profiles.json` file in endcord settings directory.
Use plaintext mode only if keyring is not working.
If keyring is not available or not working, plaintext mode is used by default.
Profile manager will always automatically load last used profile.
Unless other profile is selected in manager TUI, or profile name is provided with `--profile` flag.
Manager can be re-opened using `--manager` flag.

### Logging in
- Email/phone number and password - credentials are used only while logging in and will never be kept in storage
- Scan QR code - note that some terminals may fail to render QR code
- Token - used to access Discord through your account without logging-in
- Token as an argument: `endcord -t [YOUR_TOKEN]`, but note that it might get saved in your terminal history file

Email or QR code login may fail because captcha is requested by Discord. In that case first login and complete captcha through official client, from same IP address, then try again. If it still fails, then you'll have to use token method.
If you want to check what is endcord doing with credentials, look in profile_manager.py and auth.py.
**Do not share your token!**

### Channel Tree
If tree object has `>` before the object name, it means it has sub-objects (its drop-down).
Objects can be un/collapsed with `Ctrl+Space`. Channels with threads can be un/collapsed on `Alt+E`.
Channel with threads are collapsed by default.
Thread can be joined or left (toggle) on `Alt+J`.

### Newline
Newline can be added to input line by pressing `Ctrl+N`.
To keep text in one line it is represented as `␤` only in input line.
When message is sent, it will be split in newlines properly.

### Chat scrolling
When last message in chat buffer is selected, buffer will be extended with older messages.
If number of messages in buffer exceeds `limit_chat_buffer` value in config, chat will be trimmed on the opposite side.
If latest message is missing, then buffer can be extended with newer messages by selecting first message in buffer.

### Downloading / Open in browser
Downloading and opening links have similar mechanism:
If there is one item, download will start immediately / open in browser.
If there are multiple items, it will prompt for an item index (starts from 1).
Items can be:
- Links and attachments for 'open in browser'
- Only attachments for 'download'.
Links are counted first. Items are counted from start to end of the message, in order.
Downloads are parallel. `Ctrl+X` will cancel ALL downloads and attachments, with a confirmation prompt.

### Uploading
Uploading is initiated by pressing `Ctrl+U`. Previously typed content will be cached.
If `native_file_dialog` is set to `True` in config, it will open system native file dialog instead.
Type path to file that should be uploaded and press enter. Cached content will be restored.
Wait until file is uploaded and then send the message. Multiple files can be added this way.
Path can be absolute or relative, and has autocomplete on `tab` key.
If file size exceeds Discords limit it will not be added to the sent message.
Attachments can be navigated with `Ctrl+Left/Right` in popup line (above status line).
`Ctrl+X` will cancel ALL downloads and attachments, with a confirmation prompt.
`Ctrl+K` will cancel selected attachment (and stop upload) and remove it from attachments list.

### Popup window and member list navigation
Same controls for navigating in popup window and member list:
`Alt+Up/Down` - Go up/down
`Alt+Enter` - perform action on selected line (depends on context)
Popup window navigation is prioritized over member list.
Popup window is drawn for viewing:
- User profile info - from selected message (`Alt+P`)
- Channel/server info - from selected tree channel (`Alt+I`)
- Summaries for currently open channel (`Alt+S`), `Alt+Enter` will jump to first message in chat on that summary topic.
- Search and search results (`Ctrl+F`), `Alt+Enter` or enter with no typed text will jump to selected message.
- Pinned messages (`Alt+N`),  `Alt+Enter` or enter with no typed text will jump to selected message.
`Alt+Enter` in member list will show user profile of the selected member.

### Assist with mention, role, channel, emoji, sticker
When typing eg. username prefixed with `@`, assist will open as popup window with search results for typed text after `@` (search is case-insensitive).
Assist triggers are (the first character): `@username`, `@role`, `#channel`, `:emoji:`, `;sticker;`.
Press `Esc` to stop assist. Re-type trigger to start it again.
Navigation: `Alt+Up/Down` - Go up/down, `Alt+Enter` or `Enter` - insert selected item.
When inserted in input line, item will usually be shown as `<some_numbers>` - that is intended - do not alter it.
Stickers and emoji are sorted into packs, and will be shown as `pack name - emoji/sticker name`, and search is performed on that string.
Emoji assist has special features:
- Type  `:**` to search all favorited emojis.
- Type `+:` at the start of input line, and when eg `+:sparkles:` is sent, it will react to selected message instead.

### Adding/Removing reactions
To start reaction assist, press `Alt+E`.
Then type emoji name prefixed with `:`, just like with regular assist then enter.
If reaction is already present it will be +1. Reaction is removed if current account already reacted (reactions are toggled).
To add/remove one of already present reactions, only type its index (starts from 1).
If this account reacted to the message, that reaction will have `*` prefixed to reaction count.

### Escape key priority order
Stop recording, close popup window, stop replying, everything else.

### Client commands
Press `Ctrl+/` to switch to command mode. Command mode has its own assist but can also trigger regular assist.
Last 10 commands can be cycled with `Arrow Up/Down` bindings while in command mode.
[Commands list](docs/commands.md).

### App commands
App commands assist is initiated by typing `/` at the start of input line.
Pressing Enter will insert current assist selection.
If selection is `EXECUTE` then pressing enter will try to send that command.
App commands format is like this:
`/[bot_name] [command] [subcommand/group] [subcommand_after_group] --[option_name]=[option_value]`
`[bot_name]` is case-insensitive, and `_` must be used instead space.
Space is used only to separate command segments, except in option_value with quotes.
`[subcommand/group]` is either single subcommand or subcommand group, and is optional.
Subcommand group must be followed by `[subcommand_after_group]`, which is subcommand selected from this group.
`[option_value]` can be put in `""`, useful when it has spaces in the value. Options are sometimes not required.
If option is of type "attachment", it can be left without value, but attachment must be provided (with `Ctrl+U`) before sending the command.
If `skip_app_command_assist = True` in config, then all app commands are shown in initial assist after `/`. Then, inserting command will also insert bot_name.

### Emoji
To add default emoji in message just type its name or alias, like this: `:thumbs_up:`
For now, there is no emoji assist, but it is planned.
Emoji names can be found [here](https://unicode.org/emoji/charts/full-emoji-list.html) and aliases [here](https://www.webfx.com/tools/emoji-cheat-sheet/).

### s/ replacements with regex
Type `s/old/new` as a message and send it to edit your last message by replacing `old` with `new`.
Note that this is using regex. And is very similar to how sed works.
Features other than full regex support:
- Full regex support
- Append `/g` to repeat for all matches
- Append `/i` to enable case-insensitive search
- `&` in the replacement portion represents the entire matched pattern from the search portion
- `\NUMBER` in the replacement portion represents capture groups in the search portion

### Tabs
Tabs are "pinned" channels in channel cache, and are counted in channel cache limit.
Currently active channel can be un/tabbed with `Alt+B`.
To switch to any tabbed channel use `Alt+NUM`, where `NUM` is tab number (use keys in number row).

### Forums
Forums in tree can be opened just like a channel (`Ctrl+Space`).
Select post and `Enter` to open it. While in a thread run `toggle_thread` command to join it.
Posts are treated same as threads in channel tree, but only participated posts will be shown.
If there are no posts in the forum (this will happen when switching to forum in never opened server), switch to some channel in the same server, (client must subscribe to some channel so discord can send thread list sync).

### Redraw
If UI ever gets messed up, redraw it with command: press `Ctrl+/` then type `redraw`, and press `Enter` to execute it.

### Terminal size
Recommended terminal size for proper viewing is minimum 120 columns and 32 rows, for default theme.

### RPC
For now RPC only supports Rich Presence over IPC, which means no subscriptions, join requests, lobby, etc.
Because of this, some apps may not connect, misbehave or even error. If that happens, disable RPC in config.
If this happens, more info about what is going on can be found in log, when endcord is in debug mode.

### Game detection
Game detection service is ON by default and can be disabled in config.
Occasionally, it will download ~10MB json file, clean it up, and save it as ~1MB file. When this happens there will be small spike in CPU usage at startup.
Games can be blacklisted using `game_detection_blacklist` command.

### Theming
Custom theme path can be provided with `-c [PATH_TO_THEME]` flag or in `config.ini`.
Theme can also be changed in `config.ini` under section `[theme]`.
Loading order: argument theme -> `config.ini` theme -> builtin default theme. There can be missing settings.
If theme is not found at provided path, default theme will be written to it.
If only file name is provided, without `.ini` extension, theme will be searched in `Themes` directory, see [Configuration](docs/configuration.md) for path.
Default themes are assumed to be drawn on dark terminal background (preferably black).

### Vim mode
Vim-like mode can be enabled in the config by setting `vim_mode = True`. Keybindings list can be found [here](docs/keybindings.md#vim-mode-keybindings).
In vim mode there are currently 2 modes: "normal" and "insert".
Normal mode is the default mode and allows using many "single-key" keybindings, fully disabling the input line.
As usual, press `i` to switch to insert mode and `Esc` to leave it. Insert mode will allow typing in input line.
Pressing enter in insert mode will not send the message but insert newline instead.
Note that original keybindings are not disabled and can be used from any mode. Any keybinding collisions are resolved in favor of vim mode bindings.

### Media support
Very large number of image and video formats are supported thanks to pillow and PyAV.
All the visual media is converted to ASCII art that can be additionally configured in [theme](docs/configuration.md#theme).
But there is also setting in config to open media in external app (cross-system, will use default system app for that file format).
"endcord-lite" (without voice calls and ASCII media support), can be built by specifying `--lite` flag to build script. Lite version is significantly smaller, cant make voice calls, but still can open media in external app.

### Custom hosts
Connecting to other discord-like instance can be configured in `config.ini` by setting `custom_host = ` to preferred host domain. Set to `None` to use default host (`discord.com`) or use `--custom-host=` command argument.
But endcord may crash at any time. Further, each host may have different spam filters, so **use at your own risk** still applies.
Whether endcord will work or crash depends on hosts api implementation, the more different from discord it is, greater is the risk of a crash. If endcord crashes - its hosts fault. Do not report bugs related to this.

### Termux (android)
Endcord can be run on android through termux app, but it cant be built.
To run it: first install python >= 3.12 and `uv`, then clone this repo, setup env to "MICRO" level: `python --nobuild --level=MICRO` (one time), and run it: `uv run main.py`.
Setting up environment for higher levels will probably fail because these dependencies will have to be built for arm architecture.
To enable android notifications simply run `pkg install termux-api` and install Termux:API app. Vibration is disabled by default, to enable it: run endcord at least once, then in Termux:Api notification settings enable vibration for endcord notifications.
Notifications will work as ling as endcord is running, so it might be necessary for termux to "Acquire wakelock".

### Extensions warning
Extensions are enabled by default, and can be toggled in settings. But extension can modify almost everything in endcord, and can access your tokens. **Use extensions at your own risk!**
To prevent extension injection (malware can modify endcord config and inject extension in extensions directory) - which is very unlikely, there is build script option: `--disable-extensions` which disables extension loading in the code itself, overriding config.
