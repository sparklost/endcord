## Commands
- `react` / `react [reaction]`  
    Prompt to type reaction or send provided reaction to selected message.
- `status` / `status [type]`, types: 1 - "online", 2 - "idle", 3 - "dnd", 4 - "invisible"  
    Cycle statuses, or set it by specifying its type name or index.  
- `download` / `download [num]`  
    Download selected attachment.
- `open_link` / `open_link [num]`  
    Open selected url in browser, prompt if there are multiple on same line.
- `copy_link` / `copy_link [num]`  
    Copy selected url to clipboard, prompt if there are multiple on same line.
- `play` / `play [num]`  
    Play selected attachment.
- `search` / `search [query]`  
    Show message search prompt or perform search with provided string.
- `gif` / `gif [query]`  
    Show gif search prompt or perform gif search with provided string.
- `record` / `record cancel`  
    Toggle recording, will send when stopped.
- `upload` / `upload [path]`  
    Prompt for upload path, or provide it in command and start uploading.
- `profile` / `profile <@[user_id]>`  
    View profile info of user from currently selected message or specified user.
- `channel` / `channel <#[channel_id]>`  
    View info of currently selected channel in tree or specified channel.
- `summaries` / `summaries <#[channel_id]>`  
    View summaries of currently active channel or specified channel.
- `copy_message`  
    Copy selected message contents to clipboard.
- `spoil`  
    Reveal one-by-one spoiler in selected message.
- `link_channel` / `link_channel <#[channel_id]>`  
    Copy link of selected channel in tree to clipboard, or from provided channel id.
- `link_message`  
    Copy link of selected message to clipboard,
- `cancel *[download/1/uploa/2/attachment/3]`  
    Cancel all downloads and/or uploads. Can use `cancel 1` or `cancel download`... Use just `cancel` to cancel both downloads and uploads.
- `external_edit`  
    Open external editor to type message in it.
- `member_list`  
    Toggle member list.
- `toggle_thread`  
    Join/leave currently open thread.
- `toggle_thread_tree`  
    Join/Leave selected thread in tree.
- `bottom`  
    Go to chat bottom.
- `goto_reply`  
    Go to message that selected message is replying to.
- `show_reactions`  
    Show reactions details for selected message.
- `toggle_tab`  
    Toggle tabbed (pinned) state of currently active channel.
- `switch_tab [num/next/prev]`  
    Switch to specified tab by its number. Type `prev` or `next` to incrementally switch tab.
- `remove_all_tabs`  
    Remove all tabbed channels.
- `show_pinned`  
    Show pinned messages for current channel.
- `quit`  
    Quit endcord.


## Special commands (no keybinding)
- `goto <#[channel_id]>`  
    Go to specified channel/category/server from anywhere. If server or category is specified, they will be selected in tree.  
- `view_pfp` / `view_pfp <@[user_id]>`  
    View profile picture of user from currently selected message or specified user.
- `paste`  
    Try to paste image or file as attachemnt, or text to input line. Requires either media suport (all platforms, no text) or `xclip`/`wl-clipboard` (linux only).  
- `check_standing`  
    Check account standing. 0-100 value, anything non-100 is concerning.  
- `hide` / `hide <#[channel_id]>`  
    Prompt to hide selected channel in tree or specified channel.
- `toggle_mute` / `toggle_mute <#[channel_id]>`  
    Mute/unmute selected item in tree or specified channel/category/guild.
- `mark_as_read` / `mark_as_read <#[channel_id]>`  
    Mark selected or specified channel/category/guild/* item in tree as read. use `mark_as_read *` to mark entire current server as read.
- `mark_as_unread`  
    Mark selected message as unread.
- `insert_timestamp [time]`  
    Insert timestamp in input line, `[time]` can be of formats: `YYYY-MM-DD-HH-mm`, `YYYY-MM-DD`, `HH:mm`, `HH:mm:SS`.
- `vote [num]`  
    If selected message is ongoing poll, vote for specified answer index.
- `pin_message`  
    Pin selected message to current channel.
- `push_button [num/name]`  
    Push button on interactive app message. Specify either button number or button name (case-insensitive).
- `dump_chat`  
    Dump current chat to unique json, saved in Debug folder found inside config location.
- `string_select [string]` / `string_select [num] [string]`  
    Select a string on interactive app message. Strings are provided in assist window. Specify `[num]` if there are multiple string selects.
- `set_notifications ...` / `set_notifications <#[channel_id]> ...`  
    Show and modify server/channel notification settings.
- `custom_status [text]`  
    Set custom status text.
- `custom_status_emoji [emoji]`  
    Set custom status emoji.
- `custom_status_remove`  
    Remove custom status.
- `toggle_afk`  
    Toggle afk state. If afk, mobile devices will receive notifications.
- `block *ignore <@[user_id]>`  
    Block user. `ignore` is optional.
- `unblock *ignore <@[user_id]>`  
    Unblock user. `ignore` is optional.
- `toggle_blocked_messages`  
    Toggle showing messages from blocked users in chat. Toggles between `blocked_mode` setting and fully shown messages.
- `view_emoji *[emoji/num]`  
    Download specified custom emoji and show it in media player. If number or nothing is specified it will show emoji from selected line in chat.
- `goto_mention` / `goto_mention [num]`  
    Go to channel/message mentioned in selected message.
- `voice_start_call`  
    Start voice call in currently open DM.
- `voice_accept_call`  
    Accept incoming voice call.
- `voice_leave_call`  
    Leave current voice call.
- `voice_reject_call`  
    Silence incoming call or cancel outgoing call.
- `voice_list_call`  
    Show all call participants and their states in an updated list. Must be in the call to use this.
- `voice_toggle_mute`  
    Toggle mute state before joining a call. Persisted across sessions.
- `generate_invite *[duration] *[limit]`  
    Generate invite to current server with custom expiration `duration` and uses `limit`. Set to 0 for infinite. Invite URL will be copied to clipboard.
    `duration` can be: `4w3d5h30m10s` where `w`is weeks, `d` is days..., can be used partially and mixed: `5h1w`. Default is 7 days and infinite uses.
- `rename_folder [name]`  
    Locally rename currently selected folder in tree. Custom names are kept in state_profile_name.json in config dir.
- `collapse_all_except *[option]`  
    Collapse all servers in tree except specific `[option]`: `current` (default), `selected`, `above`, `bellow`.  
- `tree_select *[server/channel], *next/prev`  
    Select next or previous server or channel in tree. Default is next channel.  
- `send_message --channel_id=[channel_id]/<#[channel_id]> --reply_id=[message_id] --ping=[True/False] --attachment=[path] [message_content]`  
    Try not to abuse this command, as its run in a thread and can abuse API!  
    `--channel_id` - accepts both channel_id and formatted channel_id from assist, only last channel_id will be used. Default: current channel.  
    `--reply_id` - mesage id of message that is being replied to.  
    `--ping` - If this messahe is a reply, should it ping. Default=`True`.  
    `--attachment` - Absolute path to file to be uploaded as an attachment, can be multiple.  
    Everything after these flags is considered as message content.  
- `redraw`  
    Redraw UI if it ever gets messed up.
- `open_config_dir`  
    Open config directory with system file manager.
- `show_log`  
    Show live log.
- `game_detection_blacklist [game_name]`  
    Toggle blacklisted state for games detected in past 7 days, with assist.
- `set [key] = [value]` / `set [key]=[value]`  
    Change settings and save them. Usually restart is required.  
    External theme won't be changed, and it can override changed settings.  


## Command-bindings only commands
Special commands availabe only for command-bindings (macros).
- `sleep [seconds]`  
    Sleep for specified numbr of seconds. If some macro fails to execute, adding `sleep 0.1` between the two comands may help.
- `type [text]`  
    "Type" specifid text at cursor position in input line in current context.


## Command-bindings (macros)
- `Ctrl+K` - `"11" = "command_palette; type 'goto '"`  
    Open command palette and type `goto `.
- `Shift+Down` - `"336" = "tree_select server; collapse_all_except selected"`  
    Select next server in tree and collapse all other servers except it.
- `Shift+Up` - `"337" = "tree_select server prev; collapse_all_except selected"`  
    Select previous server in tree and collapse all other servers except it.
