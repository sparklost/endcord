## Default config values with explanations:
Note: always put string in `""`. To use `"` inside the string escape it like this: `\"`. To use `\` escape it like this: `\\`. These are counted as single character.

### Main
- `theme = None`  
    Custom theme path, or name of file in `Themes` directory.  Set to `None` to use theme from `config.ini` `[theme]` section or defaults.
- `extensions = True`  
    Enable extensions.
- `rpc = True`  
    Enable RPC server.
- `game_detection = True`  
    Enable game detection service.
- `vim_mode = False`  
    Enable vim-like mode.
- `limit_chat_buffer = 100`  
    Number of messages kept in chat buffer. Initial buffer is 50 messages and is expanded in scroll direction. Limit: 50-1000. Larger value will cause longer chat updates.  
- `limit_channel_cache = 10`  
    How many previous channel chats are kept in cache. For each channel `download_msg` number of messages are kept.  Set to `0` to disable caching.  
    Tabbed channels are counted as "pinned" cached channels.  
    Larger limit_channel_cache value will cause more RAM and CPU usage.a
- `download_msg = 25`  
    Number of messages downloaded in chunks for updating chat. Discord default is 25. Limit: 20-100.  
    Larger values will cause longer waiting time when switching channel and loading chat chunks.
- `convert_timezone = True`  
    Use local time. If set to False, will show UTC time.
- `send_typing = True`  
    Allow `[your_username] is typing...` to be sent.
- `desktop_notifications = True`  
    Allow sending desktop notifications when user is pinged/mentioned.  
    Set to `2` to disable notification but keep sound, only `custom_notification_sound` will be used.
- `idle_timeout = 10`  
    After how many **minutes** to automatically switch to idle status. Set to `None` or `0` to disable.  
    Note: this only detects when window is **not focused**.
- `notification_when_focused = False`  
    Whether to receive desktop notifications when terminal is not focused.
- `remove_previous_notification = True`  
    Remove previous desktop notification that's coming from same DM/Channel.
- `ack_throttling = 5`  
    Delay in seconds between each ack send. Minimum is 3s. The larger it is, the longer will `[N+ unreads]` stay in status line.
- `member_list = True`  
    Whether to download member activities. Disable for lower CPU, RAM and network usage.  
    If disabled, member list will be empty and there will be no presences in profile view screen.
- `member_list_auto_open = False`  
    Automatically open member list on startup and on channel switch to different guild, if enough space.  
    Will disable persisting member list state between sessions.
- `use_nick_when_avail = True`  
    Replace global_name with nick when it is available.
- `remember_state = True`  
    Remember previous state across session. This include: last opened channel, tree layout, muted state, folder names, tabs, games blacklist.
- `remember_tabs = True`  
    Remember tabbed channels across sessions. Only if `remember_state` is True.
- `new_tab_at_end = False`  
    Add tabbed channels at end of the tab line, or insert them after currently active tab.
- `reply_mention = True`  
    Ping someone by default when replying.
- `cache_typed = True`  
    Save unsent message when switching channel, and load it when re-opening that channel.
- `show_pending_messages = True`  
    Show pending messages in chat with `color_chat_pending` until they are sent. Disable to save some CPU usage when sending messages.
- `assist = True`  
    Assist when typing @username, @role, #channel, :emoji:, ;sticker;
 - `assist_swap_binding = True`  
    Whether to swap `chat_up` and `chat_down` with `extra_up` and `extra_down` when popup window is open.
- `cursor_on_time = 0.7`  
    Time in seconds the cursor stays ON. Set to `None` or `0` to disable cursor blinking.
- `cursor_off_time = 0.5`  
    Time in seconds the cursor stays OFF. Set to `None` or `0` to disable cursor blinking.
- `tab_spaces = 4`  
    How many spaces are inserted when pressing tab button or loading tab character from external editor.
- `blocked_mode = 1`  
    What to do with blocked/ignored messages:  
    0 - No blocking  
    1 - Mask blocked messages  
    2 - Hide blocked messages
- `hide_spam = True`  
    Whether to hide or show spam DM request channels in DM list.
- `keep_deleted = False`  
    Whether to keep deleted messages in the chat, with different color, or remove them.
- `limit_cache_deleted = 30`  
    Limit number of cached deleted messages per channel.
- `max_thumb_cache_age = 7`  
    How long to keep cached thumbs for inline images, in days. Set `0` to clear on each new run.  
- `syntax_highlight = True`  
    Whether to enable code block syntax highlighting. Code block needs to have language prefix.  
    For this to work, either GNU `source-highlight` or `python-pygments` needs to be installed.
- `tree_show_folders = True`  
    Whether to show or hide server folders in tree.
- `wrap_around = True`  
    Wrap around selection in tree and extra window, i.e. go to first when moving selection past last item and opposite.
- `mouse = True`  
    Disable if there are issues with mouse controls.  
- `mouse_scroll_sensitivity = 3`  
    How many lines are scrolled at once when scrolling with mouse.
- `mouse_scroll_selection = False`  
    Scroll selection instead content, disables mouse_scroll_sensitivity.  
- `draw_scrollbar = True`  
    Whether to draw scrollbar at the right side of the chat.
- `screen_update_delay = 0.01`  
    Delay in seconds before screen is updated. Limited to min 0.01.  
    Too low value will cause visual "glitches". Increasing value will add latency between performed action and visual feedback.
- `extra_line_delay = 5`  
    How long will temporary extra line pop-ups remain before they are auto-removed.
- `gif_download_type = 1`  
    Type of the media when gif is downloaded from tenor (klippy and giphy cant be changed):  
    0 - gif HD  
    1 - gif UHD  
    2 - mp4 Video  
- `trim_embed_url_size = 40`  
    Length to which to trim embed url, appended with `...`. Set to `None` to disable. Minimum is 20.
- `aspell_mode = "normal"`  
    [Aspell](http://aspell.net/) filter mode.  
    Available options: `ultra` / `fast` / `normal` / `slow` / `bad-spellers`  
    Set to `None` to disable spell checking.  
    More info [here](http://aspell.net/man-html/Notes-on-the-Different-Suggestion-Modes.html#Notes-on-the-Different-Suggestion-Modes).  
- `aspell_lang = "en_US"`  
    Language dictionary for aspell.  
    To list all installed languages, run `aspell dump dicts`.  
    Additional dictionaries can be installed with package manager or downloaded [here](https://ftp.gnu.org/gnu/aspell/dict/0index.html) (extract archive and run "configure" script).  
- `media_mute = False`  
    Whether to mute video in media player or not. If true, will not initialize audio at all.
- `media_cap_fps = 30`  
    Maximum framerate when playing videos.
- `media_font_aspect_ratio = None`  
    Font height/width ratio is automatically queried from terminal. But it may fail, in that case default `2.25` value is used.  
    If pictures appear wider or narrower change this to value = `char_w / char_h`, eg. `18 / 8 = 2.25`.
- `inline_media = False`  
    Draw inline images in chat. Requires media support (not endcord-lite). Currently does nothing.
- `inline_media_height = 14`  
    Height of DISPLAYED image in chat. In number of characters.
- `inline_media_quality = "low"`  
    Quality of downloaded image thumb for chat inline media. Has no effect in endcord-lite. Possible options are: `lossless`, `high`, `'low`.
- `rpc_external = True`  
    Whether to use external resources for Rich Presence (like custom pictures).
- `emoji_as_text = False`  
    Will convert emoji characters to their names. Enable if emoji are not supported by terminal.
- `message_spacing = True`  
    Will add one line space between messages not belonging to same user.
- `message_grouping = True`  
    Will replace `format_message` with `format_message_grouped` (same with color format) if consecutive messages are belonging to the same user in short period of time.
- `native_media_player = False`  
    Use system native media player instead in-terminal ASCII art.
- `native_file_dialog = "Auto"`  
    Use system native file dialog when uploading files.  
    Available options: `True` - use system native file picker, `False` - use internal file picker, `"Auto"` - use yazi if available, fallback to system native.
- `save_summaries = True`  
    Whether to save summaries to disk. Disable to save RAM and reduce disk writes.
- `default_stickers = True`  
    Download discord default stickers and add them to sticker search. Disable to save some RAM.
- `only_one_open_server = False`  
    Force only one open server at a time in tree. When one is opened other is closed, excluding DMs.
- `remember_collapsed_channels = True`  
    Whether to persist collapsed state for forums and channels with threads.
- `assist_skip_app_command = False`  
    Skip assist for app_name when typing app command. Instead, show all app commands and insert app_name with selected command.
- `fallback_keybinding_parser = False`  
    Use fallback keybinding resolver. Some keybindings may not work, but should be more stable on exotic terminals. On windows, this is forced ON.
- `assist_limit = 50`  
    Maximum number of results when showing assist.
- `assist_score_cutoff = 15`  
    Cutoff for assist match score. Lower value will result in more results.
- `limit_command_history = 50`  
    Maximum number of commands stored in history. File is `command_history.json` in config dir.
- `game_detection_download_delay = 7`  
    How often detectable games list will be checked for updates. Value is in days. Set to `0` to check on each run.
- `external_editor = None`  
    Command or path to executable for launching external editor. Set to `None` to use system default.
- `calls = True`  
    If disabled, wont be able to receive nor start calls and calls wont ring.
- `call_silence_threshold = -30`  
    This is silence detection threshold for sound transmitted in voice calls. Value is in dB.  
    If system mic volume is too low, actual sound may be detected as silence, so decrease this value until voice can be heard properly.  
    Increase it if endcord is constantly sending noise when its silence. Or decrease mic volume and increase input volume in endcord.  
    Set to `0` to disable silence detection (sound will be constantly sent and will use more bandwidth).
- `call_opus_mode = "voip"`  
    This setting changes quality of the sent audio, higher quality means more network usage and higher sound delay. Options: `lowdelay`, `voip`, `audio`.  
    Lowdelay has the worst quality. Voip is good for voice calls. Audio mode is useful when streaming music.  
- `call_fast_mixer = False`  
    This option only has effect on mixing received audio from multiple users speaking at the same time.  
    If True, call will use faster mixer that uses less CPU but might result in lower audio quality.  
- `downloads_path = None`  
    Path to custom downloads directory. Set to `None` to use system default.
- `notifications_pfp = True`  
    Whether to include profile pictures in notifications.  
    If enabled will slightly delay notification if picture is being downloaded. Pictures are cached in system default cache dir.  
    Set to number to change maximum picture resolution, eg. `notifications_pfp = 128` will always download image with resolution 128x128 or worse.
- `linux_notification_sound = "message"`  
    Sound played when notification is displayed. Linux only. Set to `None` to disable. Sound names can be found in `/usr/share/sounds/freedesktop/stereo`, without extension.
- `custom_notification_sound = None`  
    Path to audio file played when notification is sent. Set to `None` to disable.
- `linux_ringtone_incoming = "phone-incoming-call"`  
    Sound played when there is incoming call. Linux only. Set to `None` to disable. Sound names can be found in `/usr/share/sounds/freedesktop/stereo`, without extension. Set this and custom_ringtone_incoming to `None` to disable sound entirely.
- `custom_ringtone_incoming = None`  
    Path to audio file played when there is incoming call. Set to `None` to disable. The file will be played in loop.
- `linux_ringtone_outgoing = "phone-outgoing-call"`  
    Sound played when there is outgoing call. Linux only. Set to `None` to disable. Sound names can be found in `/usr/share/sounds/freedesktop/stereo`, without extension.
- `custom_ringtone_outgoing = None`  
    Path to audio file played when there is outgoing call. Set to `None` to disable. The file will be played in loop.
- `custom_media_player = None`  
    Custom script, command, or path to executable that will be used to play media. Dont forget to make it executable. Can also run `bash path/to/file.sh`.  
    Prebuilt script already exists [here](https://github.com/sparklost/endcord/blob/main/tools/endcord-custom-media.sh), configuration instructions are in the script.  
    File path will be passed as first argument. Do not use custom arguments, instead write bash wrapper script that will pass file to specific command. If paired with `custom_media_hint`, the script can be made to decide between multiple media player based on media type.
- `custom_media_blacklist = None`  
    List of media types to be ignored for custom media player and will be played in endcord builtin or with native media player. Available options: `"img"`, `"gif"`, `"video"`, `"audio"`, `"URL"`, `"YT"`. Example to allow only images and gifs: `["img", "gif"]`.
- `custom_media_terminal = False`  
    If custom media player is supposed to be opened in terminal, set this to `True`, so endcord drawing will be "paused" while media is playing.
- `custom_media_hint = False`  
    Whether to pass media type (same as in `custom_media_blacklist`) to the `custom_media_player` command as a second argument, after file path. This is useful for custom scripts.
- `yt_dlp_path = "yt-dlp"`  
    Path to [yt-dlp](https://github.com/yt-dlp/yt-dlp) executable or command. Used for playing youtube videos.
- `yt_dlp_format = 18`  
    [Format code](https://github.com/yt-dlp/yt-dlp?tab=readme-ov-file#format-selection) for youtube video to play.
- `mpv_path = "mpv"`  
    Path to [mpv](https://mpv.io/) executable or command. Used for playing youtube videos without ascii art.
- `yt_in_mpv = False`  
    Whether to open youtube links in mpv or in browser.  
- `check_for_updates = 1`  
    Whether to check for endcord and all extensions updates.
    0 - Do not check  
    1 - Show popup if there are updates  
    2 - Show popup and send desktop notification  
    3 - Only check endcord, and don't send desktop notification  
    4 - Only check endcord, and send desktop notification
- `check_update_interval = 1`  
    How often to check for endcord and extensions updates, in days.
-  `client_properties = "default"`  
    Client properties are used by discord in spam detection system.  
    They contain various system information like operating system and browser user agent. Available options:
    - `"default"` - Approximately what official desktop client sends. Includes: OS version, architecture, Linux window manager, locale.
    - `"anonymous"` - Approximately what official web client sends. But there is higher risk to trigger spam heuristics.
    - `"android"` - Default but modified to look like android device. Other users will see this client as mobile phone device.
- `custom_user_agent = None`  
    Custom [user agent string](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/User-Agent) for `client_properties`.  
    Default user agent is Firefox for `"anonymous"` and discord desktop client for `default` client properties.  
    User agent should not be changed unless the [default ones](https://github.com/sparklost/endcord/blob/main/endcord/client_properties.py) are very outdated.  
    Setting wrong user agent can make you more suspicious to discord spam filter! Make sure user agent string matches your OS.
- `send_x_super_properties = True`  
    Enable sending X-Super-Properties header containing system information. May reduce risk suspicion of client.  
    Disabling this may solve message sending issues ("returned error code 400" in log).  
- `proxy = None`  
    Proxy URL to use, it must be this format: `protocol://host:port`, example: `socks5://localhost:1080`. Or `protocol://user:password@host:port`.  
    Supported proxy protocols: `http`, `https`, `socks5`.  
    WARNING: Using proxy (especially TOR) might make you more suspicious to discord.  
    Voice and video calls will only work with socks5 proxy and it must support UDP ASSOCIATE.  
- `custom_host = None`  
    Custom host to connect to, like `old.server.spacebar.chat`. Set to `None` to use default host (`discord.com`)
- `capabilities = None`  
    DO NOT CHANGE, unless you know what you're doing. This might cause some features to not work or client to get unstable.  
    If bot token is used, then this will be used as `intents` parameter instead. See [discord bot documentation](https://discord.com/developers/docs/events/gateway#gateway-intents).  
    Default capabilities: `30717`, default intents: `50364033`.  
- `easter_eggs = True`  
    In case some easter egg is annoying.
- `debug = False`  
    Enable debug mode.

### Theme
- `compact = True`  
    Compact mode that is more space-efficient, has less borders between windows.
- `tree_width = 32`  
    Width of channel tree in characters.
- `extra_window_height = 6`  
    Height of extra window drawn above status line. Window title line not included.
- `member_list_width = 20`  
    Width of member list. It won't be drawn if remaining screen width for chat is less than 32 characters.
- `format_message = "[%timestamp] <%global_name> %app%edited\n%content"`  
    Formatting for message base string. See [format_message](#format_message) for more info.
- `format_message_grouped = " ├  %content %edited"`  
    Formatting for message base string when this message is belonging to the same user as previous, in short period of time. See [format_message_grouped](#format_message_grouped) for more info.
- `format_newline = " │  %content"`  
    Formatting for each newline string after message base. See [format_newline](#format_newline) for more info.
- `format_reply = " ╭──🡲 [%timestamp] <%global_name>: %content"`  
    Formatting for replied message string. It is above message base. See [format_reply](#format_reply) for more info.
- `format_reactions = " ╰──⤙ %reactions"`  
    Formatting for message reactions string. It is bellow last newline string. See [format_reactions](#format_reactions) for more info.
- `format_reactions_newline = "      %reactions"`  
    Formatting for each next reactions string. Uses same [format_reactions](#format_reactions).
- `format_interaction = " ╭──⤙ %global_name used [%command]"`  
    Formatting for bot interaction string. It is above message base. See [format_interaction](#format_interaction)
- `format_one_reaction = "[]%count:%reaction]"`  
    Formatting for single reaction string. Reactions string is assembled by joining these strings with `reactions_separator` in between. See [format_one_reaction](#format_one_reaction) for more info.
- `format_timestamp = "%H:%M"`  
    Format for timestamps in messages. Same as [datetime format codes](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes)
- `format_status_line_l = " ⠀%status_dot %nick %warn_state %unreads %action %typing"`  
    Formatting for left side of status line. See [format_status](#format_status) for more info. Set to `None` to disable.  
    This and other line formats will replace trailing, heading and 3 or more space characters with "middle line" if used in standard mode (not compact). This can be avoided by using Braille Pattern Blank character: `⠀`.  
- `format_status_line_r = "%vim_mode %slowmode"`  
    Formatting for right side of status line. See [format_status](#format_status) for more info.
- `format_title_line_l = " %server: %channel_no_tab"`  
    Formatting for left side of title line. See [format_status](#format_status) for more info. Set to `None` to disable.
- `format_title_line_r = None`  
    Formatting for right side of title line. See [format_status](#format_status) for more info.
- `format_subtitle_line = "─%tabs"`  
    Formatting for subtitle line - drawn bellow title line, only if there is content. See [format_status](#format_status) for more info.
- `format_title_tree = " endcord  %task"`  
    Formatting for channel tree title line. See [format_status](#format_status) for more info. Set to `None` to disable.
- `format_rich = "%type %name - %state - %details"`  
    Formatting for rich presence string used in `format_status`. See [format_rich](#format_rich) for more info.
- `format_tabs = "%name"`  
    Formatting for tabs list string used in `format_status`. See [format_tabs](#format_tabs) for more info.
- `format_prompt = "[%channel] > "`  
    Formatting for prompt line. See [format_prompt](#format_prompt) for more info.
- `format_forum = "[%timestamp] - <%msg_count> - %thread_name"`  
    Formatting for each thread in forum. One line per thread. See [format_forum](#format_status) for more info.
- `format_forum_timestamp = "%Y-%m-%d"`  
    Format for timestamps in forum. Same as [datetime format codes](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes)
- `format_search_message = "%channel: [%date] <%global_name> | %content"`  
    Formatting for message line in extra window when searching. See [format_search_message](#format_search_message) for more info.
- `edited_string = "(edited)"`  
    A string that replaces `%edited` in the message format when the message is edited.
 - `app_string = "- (%app)"`  
    A string that replaces `%app` in the message format when the message is sent from app or webhook.
- `quote_character = "║"`  
    A character that is prefixed to each line of single or multiline quote.
- `scrollbar_character = "┃"`  
    A character used to draw scrollbar on the right side of the chat.
- `reactions_separator = " "`  
    A string placed between two reactions.
- `tabs_separator = "|"`  
    A string placed between two tabs.
- `chat_date_separator = "─"`  
    A single character used to draw horizontal line for separating messages sent on different days. Set to `None` to disable date separator.
- `format_date = " %B %d, %Y "`  
    Format for timestamps in `chat_date_separator`. Same as [datetime format codes](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes).
- `limit_username = 10`  
    Limit to the username and global name string length.
- `limit_channel_name = 15`  
    Limit to the channel name string length.
- `limit_typing_string = 32`  
    Limit to the typing string length. Also limits `%details` and `%state` in `format_rich`.
- `limit_prompt = 15`  
    Limit to the thread name string length.
- `limit_thread_name = 0`  
    Limit to `%username`, `%global_name`, `%server` and `%channel` length in `format_prompt`.
- `limit_tab_len = 20`  
    Limit to individual tab length. Set to `0` to disable.
- `limit_tabs_string = 0`  
    Limit to `%tabs` length in `format_status`. Set to `0` to disable.
- `tree_drop_down_vline = "│"`  
    A single character used to draw vertical line in tree drop down menus. Also used to draw vertical line separating channel tree and the chat in compact mode.
- `tree_drop_down_hline = "─"`  
    A single character used to draw horizontal line in tree drop down menus.
- `tree_drop_down_intersect = "├"`  
    A single character used to draw intersections in tree drop down menus.
- `tree_drop_down_corner = "╰"`  
    A single character used to draw corners in tree drop down menus.
- `tree_drop_down_pointer = "🡲"`  
    A single character used to draw pointer in tree drop down menus. Pointer is used to designate categories and servers.
- `tree_drop_down_thread = "⤙"`  
    A single character used to draw thread pointer in tree drop down menus.
- `tree_drop_down_forum = "◆"`  
    A single character used to draw forum pointer in tree drop down menus.
- `tree_drop_down_folder = "+"`  
    A single character used to draw folder pointer in tree drop down menus.
- `tree_drop_down_voice = "○"`  
    A single character used to draw voice channel pointer in tree drop down menus.
- `tree_dm_status = "●"`  
    A single character prefixed to DM name in tree drop down, to indicate status: online/away/dnd. Also used in member list.
- `border_corners = "╭╰╮╯"`  
    Characters used to draw corners in bordered mode.
- `activity_icons = "🎮︎📺︎♪📺︎🎮︎"`  
    Characters used to indicate rich presence activity in member list.  
    Order of characters: Playing, Streaming, Listening, Watching, Competing.
- `smart_chat_lines = True`  
    Whether to extend lines in chat, left of reactions, in grouped messages only. Will use `tree_drop_down_intersect` and `tree_drop_down_vline`.
- `username_role_colors = True`  
    Allow `%username` and `%global_name` to have color of primary role.
- `dynamic_name_len = True`  
    Use dynamic name length for usernames and global_names in `format_message` and `format_reply` only.  
    Overrides `limit_username` option and name length is limited to `chat_width - 15`.  
    If this is ON, `color_format_message` still works same as with `dynamic_name_len = False`, and it will internally correct for dynamic name difference.  
    Eg.: if color format has red character set to be at index 20, and `limit_username = 10`, `format_message = "abcd %username1234X67` but dynamic name is ON, then red character will always be 5 characters after `%username`. because `20 - length("abcd ") - 10 = 5`.
- `media_use_blocks = True`  
    Whether to use "block" characters (`▀`) for drawing media instead traditional characters from `media_ascii_palette`.  
    This will result in clearer picture with pixel-like characters, vertical resolution will be doubled.  
    `media_ascii_palette` and media_saturation have no effect when this is ON.
- `media_truecolor = True`  
    Use truecolor for media rendering. Works only for `media_use_blocks`.
- `media_ascii_palette = "  ..',;:c*loexk#O0XNW"`  
    Characters used to draw in terminal. From darkest to brightest. Same character can be repeated. Number of characters is not fixed.
- `media_saturation = 1.2`  
    Saturation correction applied to image in order to make colors more visible. Adjust if changing `ascii_palette` or media_color_bg.
- `media_bar_ch = "━"`  
    A single character used to draw progress bar in media player when playing video or audio.

### Colors and attributes
Colors are part of the theme, configured as 2 or 3 values in a list: `[foreground, background, attribute]`  
Foreground and background are ANSI color codes. To print all available colors with codes run: `endcord --colors`.  
Colors can also be set as RGB like this `(0, 124, 255)`. Alternatively hex color code can be used too, make sure its in `"` quotes and is starting with `#.`  
-1 is terminal default color (bg or fg individually). Set entire color pair to `None` to use terminal default fg and bg colors.  
Attribute is optional string: `"b"/"bold"`, `"u"/"underline"`, `"i"/"italic"`
Example: `[209, 234, "u"]` - 209 is foreground, 234 is background, "u" is underline.  
All colors starting with `color_format` are formatted like this:  
`[[fg, bg, attr], [fg, bg, attr, start, end], [...]...]`  
First `[fg, bg, attr]` is base color for whole context. If `bg` is -1, `bg` from `color_chat_default` and `color_chat_mention` is used. Same for `fg`.  
Every next list has additional `start` and `end`- indexes on a line where color is applied. If `bg` is -2, `bg` from base color is used. -1 is terminal default color. Same for `fg`.  
- `color_default = [-1, -1]`  
    Base color formatting for text. No attribute.
- `color_green = [46, -1]`  
- `color_orange = [208, -1]`  
- `color_red = [196, -1]`  
    Colors for green/orange/red elements in various windows and lines.
- `color_chat_mention = [-2, 234]`  
    Color for highlighted messages containing mentions (reply with ping included) and mention roles.  
    If fg is `-2` then it will not overwrite message element original fg.  
- `color_chat_blocked = [242, -1]`  
    Color for blocked messages if `block_mode = 1`.
- `color_chat_deleted = [95, -1]`  
    Color for deleted messages when `keep_deleted = True`.
- `color_chat_pending = [242, -1]`  
    Color for pending messages in the chat.
- `color_chat_selected = [233, 255]`  
    Color for selected line in the chat.
- `color_chat_separator = [242, -1, "i"]`  
    Color for date separator line in the chat.
- `color_chat_standout = [153, 234]`  
    Color for chat elements that should standout, like `@mentons`, `#channels`, timestamps, custom emoji.
- `color_chat_edited = [241, -1]`  
    Color for `edited_string`.
- `color_chat_url = [153, -1, "u"]`  
    Color for urls in message content and embeds.
- `color_chat_spoiler = [239, 239]`  
    Color for spoilers in message.
- `color_chat_code = [250, 233]`  
    Color for code snippets and blocks.
- `color_status_line = [233, 255]`  
    Color for status line.
- `color_extra_line = [233, 245]`  
    Color for extra line, drawn above status line. Used only in compact mode.
- `color_title_line = [233, 255]`  
    Color for chat title line and tree title line. Used only in compact mode.
- `color_subtitle_line = [245, -1]`  
    Color for subtitle line.
- `color_extra_window = [-1, -1]`  
    Color for extra window body.
- `color_extra_window_low = [245, -1]`  
    Color with lower visibility in extra window.
- `color_extra_window_standout = [153, -1]`  
    Color for standout visibility in extra window.
- `color_prompt = [255, -1]`  
    Color for prompt line.
- `color_input_line = [255, -1]`  
    Base color for input line.
- `color_cursor = [233, 255]`  
    Color for cursor in input line.
- `color_scrollbar = [-1, -1]`
    Color for scrollbars.
- `color_misspelled = [222, -1]`  
    Color for misspelled words in input line.
- `color_tree_default = [255, -1]`  
    Base color for tree components. No attribute.
- `color_tree_category = [153, -1]`
- `color_tree_server = [153, -1]`
- `color_tree_selected = [233, 255]`  
- `color_tree_muted = [242, -1]`  
- `color_tree_active = [255, 234]`  
- `color_tree_unseen = [-2, -2, "b"]`  
    Set fg and bg to -2 so it only adds attribute to color_tree_category and color_tree_server, instead of replacing color.
- `color_tree_mentioned = [197, -1]`  
- `color_tree_active_mentioned = [197, 234]`  
- `color_tree_selected_mentioned = [124, 255]`  
- `color_format_message = [[-1, -1], [242, -2, 0, 0, 7], [25, -2, 0, 8, 9], [25, -2, 0, 19, 20]]`  
    Color format for message base string. Corresponding to `format_message`.
- `color_format_message_grouped = [[-1, -1], [242, -2, 0, 1, 2]]`  
    Color format for message base string when this message is belonging to the same user as previous, in short period of time.
- `color_format_newline = [[-1, -1], [242, -2, 0, 1, 2]]`  
    Color format for each newline string after message base. Corresponding to `format_newline`.
- `color_format_reply = [[245, -1], [242, -2, 0, 1, 5], [25, -2, 0, 14, 15], [25, -2, 0, 25, 26]]`  
    Color format for replied message string. Corresponding to `format_reply`.
- `color_format_reactions = [[245, -1], [242, -2, 0, 1, 5]]`  
    Color format for message reactions string. Corresponding to `format_reactions` and `format_reactions_newline`.
- `color_format_interaction = [[245, -1], [242, -2, 0, 1, 5]]`  
    Color format for message app interaction string. Corresponding to `format_interaction`.
- `color_format_forum = [[-1, -1], [242, -2, 0, 0, 12], [25, -2, 0, 15, 20]]`  
    Color format for threads in forum. Corresponding to `format_forum`.
- `"syntax_token_colors = [133, 185, 102, 173, 133, 173, 111, -1, 167, 111, -1, -1]`  
    A list of foreground colors for code block syntax. Must have all 12 values. Values correspond to (in order):  
    keyword, string, comment, number, type, classname, function, variable, preproc/builtin, specialchar, symbol, cbracket.  
    Set to negative value to disable this token.  
- `media_color_bg = -1`  
    Single color value for background color when showing media.

### format_message
- `%content` - message text; to make text appear on next line (`format_newline`), add `\n` just before `%content`, this will ensure `%edited` stays at the end of content
- `%username` - of message author
- `%global_name` - of message author
- `%timestamp` - formatted with `format_timestamp`
- `%edited` - replaced with `edited_string`
- `%app` - replaced with `app_string` if this message is sent by app or webhook
Note: everything after `%content` may be pushed to newline.

### format_message_grouped
- `%content` - this is remainder of previous line
- `%timestamp` - formatted with `format_timestamp`
- `%edited` - replaced with `edited_string`, can be placed only after `% content`

### format_newline
- `%content` - this is remainder of previous line
- `%timestamp` - formatted with `format_timestamp`

### format_reply
- `%content` - of replied message
- `%username` - of replied message author
- `%global_name` - of replied message author
- `%timestamp` - of replied message, formatted with `format_timestamp`

### format_reactions
- `%timestamp` - of base message, formatted with `format_timestamp`
- `%reactions` - all reactions formatted with `format_one_reaction` then joined with `reactions_separator`

### format_one_reaction
- `%reaction` - reaction emoji or emoji name
- `%count` - count of this same reaction

### format_interaction
- `%username` - of the user who used the command
- `%global_name` - of the user who used the command
- `%command` - the command that user executed

### format_status
- `%global_name` - my global name
- `%username` - my username
- `%status` - Discord status if online, otherwise 'connecting', 'offline' or 'error'
- `status_dot` - Colored character from `tree_dm_status`
- `%warn_state` - `[connecting]`, `[OFFLINE]`, `[ERROR]`, `[AFK]`, or nothing
- `%custom_status` - custom status string
- `%custom_status_emoji` - custom status emoji or emoji name
- `%pronouns` - my pronouns
- `%unreads` - `[N+ unreads]` if in this channel has unread messages
- `%typing` - typing string
- `%rich` - my rich presence, replaced with `format_rich`
- `%server` - currently viewed server
- `%channel` - currently viewed channel
- `%channel_no_tab` - currently viewed channel, shown only if there are no tabs
- `%action` - warning for replying/editing/deleting message
- `%task` - currently running slow task (reconnecting, downloading chat...)
- `%tabs` - all tabs formatted with `format_tabs` then joined with `tabs_separator`
- `%slowmode` - `Slowmode: hh:mm:ss` if slowmode is enabled, otherwise its hidden
- `%app_name` - default is `endcord`, can be changed with `ENDCORD_APP_NAME` environment variable
- `%vim_mode` - `[--INSERT--]` or `[--NORMAL--]` when `vim_mode` is ON

### format_rich
- `%type` - type of rich presence: "Playing" or "Listening to"
- `%name` - name of the rich presence app
- `%state` - rich presence state
- `%details` - rich presence details
- `%small_text` - rich presence small text (disabled)
- `%large_text` - rich presence large text (disabled)

### format_tabs
- `%num` - number of the tab
- `%name` - name of the tabbed channel, limited with `limit_channel_name`
- `%server` - name of the server

### format_prompt
- `%global_name` - my global name
- `%username` - my username
- `%server` - currently viewed server
- `%channel` - currently viewed channel
- `%vim_mode` - `INSERT` or `NORMAL` when `vim_mode` is ON

### format_forum
- `%thread_name` - name of a thread
- `%timestamp` - date a thread is created, formatted with `format_forum_timestamp`
- `%msg_count` - number of messages send in a thread

### format_search_message
- `%content` - message text
- `%username` - of message author
- `%global_name` - of message author
- `%date` - formatted same as `format_forum_timestamp`
- `%channel` - to which channel in this server the message belongs, limited with `limit_channel_name`

### app_string
- `%app` - either `App`, `App - Ephemeral` or `Webhook`


## pgcurses.json - config for experimental windowed mode
- `window_size: [900, 600]`  
    Initial window width and height in pixels.
- `maximized: false`  
    Initial window maximized state.
- `font_size: 12`  
    Size of the font.
- `font_name: "Source Code Pro"`  
    Name of the font installed on the system.
- `app_name: "Endcord"`  
    Only changes title of the window.
- `repeat_delay: 400`  
    Delay before held key will start repeating, in ms.
- `repeat_interval: 25`  
    Delay between each key repeat when holding key, in ms.
- `ctrl_v_paste: false`  
    If `true` will use `Ctrl+V` instead `Ctrl+Shift+V` for pasting.
- `enable_tray: true`  
    Enable tray icon. closing window will minimize to tray.
- `tray_icon_normal: null`  
    Path to tray icon file. Its supposed to be png with size of 32x32 or 64x64, but other formats and sizes should work too. Set to `null` to use default icons.
- `tray_icon_unread: null`  
    Path to tray icon file shown when there are unread messages. Set to `null` to disable.
- `tray_icon_unread: null`  
    Path to tray icon file shown when there are unread messages that are mentioning this user. Set to `null` to disable.
- `default_color_pair: [...]`  
    Default color pair used for drawing, first color is foreground, and second is background, colors are in `[R, G, B]` format.
- `color_palette: [...]`  
    First 16 colors of xterm256 color palette are user configurable. Colors are in `[R, G, B]` format.
