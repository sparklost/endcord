## FAQ

### Obtaining your Discord token
1. Open Discord in browser.
2. Open developer tools (`F12` or `Ctrl+Shift+I` on Chrome and Firefox).
3. Go to the `Network` tab then refresh the page.
4. In the 'Filter URLs' text box, search `discord.com/api`.
5. Click on any filtered entry. On the right side, switch to `Header` tab, look for `Authorization`.
6. Copy value of `Authorization: ...` found under `Request Headers` (right click -> Copy Value)
7. This is your discord token. **Do not share it!**

### To further decrease probability of getting banned
Endcord does its best to avoid causing any suspicious activity, so using it as-is is pretty much enough.
- Do not use endcord to perform any out-of-ordinary actions (i.e. self-bots). Third party clients can sometimes trip anti-spam heuristic algorithm for catching self-bots.
- Do not use endcord at the same time with the client from which you copied token from, it might be suspicious to have 2 clients using same token at the same time.
- Do not use same token across different third party clients.
- Do not use `--token` flag, endcord automatically refreshes token stored with profile manager, so there is no need to update it manually.
- `anonymous` mode in `client_properties` setting might be more risky than `default` mode.
- Do not set invalid `custom_user_agent` setting, and try to match it with your OS.
- Do not use public proxy, like VPN or especially TOR.
- Try to not send messages that will be blocked by filter rules in servers.
- Try to not get blocked by someone.

### What if you get banned?
You can write to Discord Support team: https://dis.gd/request.
If you did something particular with endcord that caused the ban, open an issue describing what that is. Maybe that can be prevented or other users can be warned.

### Note on Python performance misconceptions
Python is slower than languages like C or Rust, but in this use case it does not affect performance. Endcord is event-driven and network-bound, not CPU-bound, so Python’s overhead is negligible (significantly reduced when built with nuitka).
All CPU-critical components are implemented in Cython with minimal python calls, resulting in near-C speeds.
Python was chosen because it enables rapid development.
Additionally if built with nuitka, custom Python is compiled from source (preferably with clang) with custom compiler flags for performance.
Full list of optimizations made for endcord can be found [here](.github/CONTRIBUTING.md#list-of-optimizations).

### Running multiple endcord instances
To run multiple endcord instances at the same time, while keeping them completely separated, run endcord with `ENDCORD_APP_NAME` environment variable set to something else. This will change "endcord" everywhere: in config and cache paths, notifications, keyring...

### No emoji
If emoji are drawn as empty box or similar, it means emoji font is not installed, its misconfigured in terminal config, or emoji are not supported by this terminal.
If case emoji cant be configured, enable `emoji_as_text` in eendcord `config.ini`.

### Sticker cannot be opened
This is very likely "lottie sticker". These stickers have special vector way of drawing (not svg) and will not be supported.

### Restore locally hidden channels
Locally hidden channels can be restored by removing them in `hidden_channels.json`, see [Configuration](#configuration) for path.

### Must send at least N messages in official client
The client will refuse to send message in newly-created DM channels. This measure is to prevent triggering discords spam filter.

### Messages wont send and in log it says error code 400
In config, disable `send_x_super_properties`. These properties may be used in spam detection so disable them only if necessary, and report the issue.

### Higher voice call quality
To increase quality of the sound sent from this device, set `ENDCORD_VOICE_OPUS_MODE=audio` environment variable. (options: `audio`, `voip`, `lowdelay`). This will make it encode opus with 96KB/s instead default 64KB/s (variable bitrate), but use more bandwidth.

### No notification sound
Custom notification sound can be set in config: `custom_notification_sound = /path/to/file.mp3`.

### Some keybindings don't work with non-Latin keyboard layouts (non-ascii)
This happens with `Alt+Key` keybindings, but may happen with other modifiers too. That happens because terminal emulators translate pressed keys with non-Latin keyboard layouts to Unicode. So when `ALT+B` is pressed, but Greek layout is used, endcord will receive `ALT+β`.
But this also means that it can be added to keybinding config. See [keybinding](#keybinding) for instructions on how to add multiple keybindings for same action.

### If some keybindings still don't work
Its probably terminal emulator sending different key codes than those in default settings. Check this by running endcord with `-k` or `--keybinding` to start keybinding resolver.
Then press key combination and see printed code. Put this code in settings to use it.
This mostly happens with `Ctrl+Arrow` and `Alt+Arrow` combinations, on some non-standard terminal emulators like kitty or cmder.
As a last resort try setting `fallback_keybinding_parser = True` in config.

### Running in tty
Linux tty doesnt support 256-colors endcord will try to run anyways (and may still crash) but UI lines will be drawn wrongly and colors will be mixed up.
But endcord can run inside [fbterm](https://salsa.debian.org/debian/fbterm), [kmscon](https://github.com/kmscon/kmscon) or [yaft](https://github.com/uobikiemukot/yaft).
Follow [fbterm setup instructions](https://wiki.archlinux.org/title/Fbterm#Installation), then set environment variable: `export TERM=fbterm` and run endcord.
Some characters may fail to render so set `emoji_as_text = True` in config and `compact = True` in theme. Some theme characters should also be tweaked.
Note: keybinding `Ctrl+Up/Down/Left/Right` does not work in tty, either rebind them or add custom keymap, or in `/etc/vconsole.conf`.

### Keybindings dont work in tmux
Add this to tmux.conf: `set -g default-terminal xterm-256color`

### RAM usage
RAM usage greatly depends on multiple factors:
- Full binary version uses few MB more than lite.
- Number of servers and channels: each avg server is ~1MB.
- If using terminal media player it will permanently increase RAM usage by ~20MB on firs media play.
- Voice calls will also permanently increase RAM usage by ~5MB on first connected/initialized call (+ ~20MB for media if not already). With few extra MB if using noise suppression with RNNoise.

### GUI (Windowed) mode
This mode entirely replaces curses and the need for terminal emulator, using GTK3 window, UI still remains terminal-like.
As as dependency, GTK3 must be installed. It is NOT required ONLY on windows (build script will set everything up when toggling gui mode).
Tray icon will be enabled, so closing window will only minimize it to tray.
If using external editor, use editor with graphical interface. TUI editors will not work, as this is no longer in terminal.
Also, endcord built-in media player will not work because its standalone TUI thats not using curses. All media will be opened in native player.
Building with pyinstaller is not recommended because it generates huge binary.
You can toggle windowed mode by running: `python build.py --toggle-windowed`.
Then run endcord from source: `uv run main.py`.
After first run in windowed mode, extra config will be generated in endcord config path, a file called `gtkcurses.json`. More info in [configuration](configuration.md).
All keybindings will work in windowed mode, even those not available in terminal (eg. `Shift+Enter`, `Ctrl+Z`).
Note: To have tray icon on Linux, [ayatana appindicator](https://github.com/AyatanaIndicators/libayatana-appindicator) must be installed.

### Legacy theme
Endcord default theme uses non-standard characters to display some TUI elements, and these characters may not work on some terminals, or look weird wit
h some fonts.
If that happens, use [legacy theme](themes/legacy.ini). It is used by default on windows.

### Virus scanners are flagging endcord binaries as malware
These are false positives. Binaries are built using nuitka, the problem is that its regularly used by other people to distribute malware. So some AVs flag all nuitka-built binaries as malware. [Ref](https://nuitka.net/user-documentation/common-issue-solutions.html#windows-virus-scanners).
Its the same with all other python freezing tools, like pyinstaller, cx-freeze...
So to run endcord, either allow it in anti-virus/windows-defender or run it from source.

### Debug mode
Debug mode can be enabled with `-d` flag.
It will cause extra messages to be written to log file (in config directory).
Endcord will periodically write to drive and log file will quickly grow in size.
Log from previous run is renamed to `-prev` on each run and it is replaced.
Live log can be accessed with `show_log`.

### Debug files
Anonymized data that might help in debugging is saved in `Debug` directory (generated only in debug mode), see [Configuration](#configuration) for path. It is never automatically uploaded.
All channel and server names, topics, descriptions are replaced. All channel and server IDs are added to random number and hashed, so they are irreversible changed, and will be different on each run.

### Support
Open an issue in [issue tracker](https://github.com/sparklost/endcord/issues).
Or, if you don't have a github account, want more interactive support, to share a theme or have a question: [Endcord Discord Server](https://discord.gg/judQSxw5K2).

### Creating and maintaining package for Linux distribution
Because endcord build script requires network access in order to install dependencies, it is impossible to use it to build endcord packages for many distributions/repositories, because they require build-time network isolation.
Because of that package maintainers are strongly encouraged to instead create "endcord-installer" package, that will install official bash installer script.
More info can be found in [contributing.md](.github/CONTRIBUTING.md#recommended-easy-route-for-package-maintainers), along with instructions for actual manual building.
After submitting a package to distributions package repository, open an issue here, so I can review it and add it to readme after it gets accepted.

### Source-available license
Endcord is licensed under source-available license. This means it is **NOT OPEN SOURCE**. How this affects you:
If you are a user, this **doesn't affect** the slightest how you are using endcord.
If you are a developer, you are **NOT ALLOWED TO PUBLICLY MODIFY THE CODE**.
If you are a package maintainer, license specifically allows you to distribute **binaries built from unmodified source code**.
Slightly longer and more detailed explanation is available in the [license file](LICENSE).
Why? Because this is one-person project, and this person is greedily taking all the fun of programming for themselves. And as bonus they will ensure that this project stays 100% human made, forever.
