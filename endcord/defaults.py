settings = {
    "theme": None,
    "extensions": True,
    "rpc": True,
    "game_detection": True,
    "downloads_path": None,
    "limit_chat_buffer": 100,
    "limit_channel_cache": 5,
    "download_msg": 25,
    "convert_timezone": True,
    "send_typing": True,
    "desktop_notifications": True,
    "notification_in_active": True,
    "remove_previous_notification": True,
    "ack_throttling": 5,
    "member_list": True,
    "member_list_auto_open": False,
    "use_nick_when_available": True,
    "remember_state": True,
    "remember_tabs": True,
    "reply_mention": True,
    "cache_typed": True,
    "show_pending_messages": True,
    "cursor_on_time": 0.7,
    "cursor_off_time": 0.5,
    "tab_spaces": 4,
    "blocked_mode": 2,
    "hide_spam": True,
    "keep_deleted": False,
    "limit_cache_deleted": 30,
    "tree_show_folders": True,
    "wrap_around": True,
    "mouse": True,
    "mouse_scroll_sensitivity": 3,
    "mouse_scroll_selection": False,
    "screen_update_delay": 0.01,
    "extra_line_delay": 5,
    "tenor_gif_type": 1,
    "trim_embed_url_size": 40,
    "aspell_mode": "normal",
    "aspell_lang": "en_US",
    "media_mute": False,
    "media_cap_fps": 30,
    "rpc_external": True,
    "emoji_as_text": False,
    "message_spacing": True,
    "native_media_player": False,
    "native_file_dialog": "auto",
    "save_summaries": True,
    "default_stickers": True,
    "only_one_open_server": False,
    "assist": True,
    "assist_skip_app_command": False,
    "assist_limit": 50,
    "assist_score_cutoff": 15,
    "limit_command_history": 50,
    "game_detection_download_delay": 7,
    "linux_notification_sound": "message",
    "custom_notification_sound": None,
    "linux_ringtone_incoming": "phone-incoming-call",
    "custom_ringtone_incoming": None,
    "linux_ringtone_outgoing": "phone-outgoing-calling",
    "custom_ringtone_outgoing": None,
    "external_editor": None,
    "calls": True,
    "yt_dlp_path": "yt-dlp",
    "yt_dlp_format": 18,
    "mpv_path": "mpv",
    "yt_in_mpv": False,
    "client_properties": "default",
    "custom_user_agent": None,
    "send_x_super_properties": True,
    "proxy": None,
    "custom_host": None,
    "capabilities": None,
    "easter_eggs": True,
    "debug": False,
}
theme = {
    "compact": False,
    "tree_width": 32,
    "extra_window_height": 6,
    "member_list_width": 20,
    "format_message": "[%timestamp] <%global_name> | %content %edited",
    "format_newline": "                       %content",
    "format_reply": "[REPLY] <%global_name> | ╭──🡲 [%timestamp] %content",
    "format_reactions": "[REACT]                ╰──⤙ %reactions",
    "format_interaction": "                       ╭──⤙ %global_name used [%command]",
    "format_one_reaction": "%count:%reaction",
    "format_timestamp": "%H:%M",
    "format_status_line_l": " %global_name (%username) - %status %afk %unreads %action %typing",
    "format_status_line_r": "%slowmode",
    "format_title_line_l": " %server: %channel",
    "format_title_line_r": "%tabs",
    "format_title_tree": " endcord  %task",
    "format_rich": "%type %name - %state - %details",
    "format_tabs": "%num - %name",
    "format_prompt": "[%channel] > ",
    "format_forum": "[%timestamp] - <%msg_count> - %thread_name",
    "format_forum_timestamp": "%Y-%m-%d",
    "format_search_message": "%channel: [%date] <%global_name> | %content",
    "edited_string": "(edited)",
    "quote_character": "║",
    "reactions_separator": "; ",
    "tabs_separator": " | ",
    "chat_date_separator": "─",
    "format_date": " %B %d, %Y ",
    "limit_username": 10,
    "limit_channel_name": 15,
    "limit_typing_string": 30,
    "limit_prompt": 15,
    "limit_thread_name": 0,
    "limit_tab_len": 12,
    "limit_tabs_string": 60,
    "tree_vert_line": "│",
    "tree_drop_down_vline": "│",
    "tree_drop_down_hline": "─",
    "tree_drop_down_intersect": "├",
    "tree_drop_down_corner": "╰",
    "tree_drop_down_pointer": "🡲",
    "tree_drop_down_thread": "⤙",
    "tree_drop_down_forum": "◆",
    "tree_drop_down_folder": "+",
    "tree_dm_status": "●",
    "border_corners": "╭╰╮╯",
    "username_role_colors": True,
    "color_default": [-1, -1],
    "color_chat_mention": [223, 234],
    "color_chat_blocked": [242, -1],
    "color_chat_deleted": [95, -1],
    "color_chat_pending": [242, -1],
    "color_chat_selected": [233, 255],
    "color_chat_separator": [242, -1, "i"],
    "color_status_line": [233, 255],
    "color_extra_line": [233, 245],
    "color_title_line": [233, 255],
    "color_extra_window": [-1, -1],
    "color_prompt": [255, -1],
    "color_input_line": [255, -1],
    "color_cursor": [233, 255],
    "color_misspelled": [222, -1],
    "color_tree_default": [255, -1],
    "color_tree_selected": [233, 255],
    "color_tree_muted": [242, -1],
    "color_tree_active": [255, 234],
    "color_tree_unseen": [255, -1, "b"],
    "color_tree_mentioned": [197, -1],
    "color_tree_active_mentioned": [197, 234],
    "color_format_message": [[-1, -1], [242, -2, 0, 0, 7], [25, -2, 0, 8, 9], [25, -2, 0, 19, 20], [-1, -2, 0, 21, 22]],
    "color_format_newline": None,
    "color_format_reply": [[245, -1], [67, -2, 0, 0, 7], [25, -2, 0, 8, 9], [25, -2, 0, 19, 20], [-1, -2, 0, 21, 27]],
    "color_format_reactions": [[245, -1], [131, -2, 0, 0, 7], [-1, -2, 0, 23, 27]],
    "color_format_forum": [[-1, -1], [242, -2, 0, 0, 12], [25, -2, 0, 15, 20]],
    "color_chat_standout": [153, 234],
    "color_chat_edited": [241, -1],
    "color_chat_url": [153, -1, "u"],
    "color_chat_spoiler": [245, -1],
    "color_chat_code": [250, 233],
    "media_use_blocks": True,
    "media_ascii_palette": "  ..',;:c*loexk#O0XNW",
    "media_saturation": 1.2,
    "media_font_aspect_ratio": 2.25,
    "media_color_bg": 16,
    "media_bar_ch": "━",
}


keybindings = {
    # tree
    "tree_up": 575,   # Ctrl+Up
    "tree_down": 534,   # Ctrl+Down
    "tree_select": 0,   # Ctrl+Space
    "tree_collapse_threads": "ALT+104",   # Alt+H
    "tree_join_thread": "ALT+106",   # Alt+J
    "channel_info": "ALT+105",   # Alt+I
    "copy_channel_link": "ALT+85",   # Alt+Shift+U
    # input line
    "input_left": 260,   # Left
    "input_right": 261,   # Right
    "word_left": 554,   # Ctrl+Left
    "word_right": 569,   # Ctrl+Right
    "insert_newline": 14,   # Ctrl+N
    "undo": "ALT+122",   # Alt+Z
    "redo": "ALT+90",   # Alt+Shift+Z
    "select_left": 393,   # Shift+Left
    "select_right": 402,   # Shift+Right
    "select_word_left": 555,   # Ctrl+Shift+Left
    "select_word_right": 570,   # Ctrl+Shift+Right
    "select_all": "ALT+97",   # Alt+A
    "copy_sel": "ALT+99",   # Alt+C
    "cut_sel": "ALT+120",   # Alt+X
    "delete_word": 8, # Ctrl+Backspace/Ctrl+H
    # chat
    "send_message": 10,   # Enter
    "chat_up": 259,   # Up
    "chat_down": 258,   # Down
    "reply": 18,   # Ctrl+R
    "edit": 5,   # Ctrl+E
    "delete": 4,   # Ctrl+D
    "toggle_ping": 16,   # Ctrl+P
    "scroll_bottom": 2,   # Ctrl+B
    "go_replied": 7,   # Ctrl+G
    "download": 23,   # Ctrl+W
    "upload": 21,   # Ctrl+U
    "browser": 15,   # Ctrl+O
    "copy_msg": 12,   # Ctrl+L
    "view_media": 22,   # Ctrl+V
    "spoil": "ALT+116",   # Alt+T
    "search": 6,   # Ctrl+F
    "profile_info": "ALT+112",   # Alt+P
    "show_summaries": "ALT+115",   # Alt+S
    "copy_message_link": "ALT+117",   # Alt+U
    "add_reaction": "ALT+114",   # Alt+R
    "search_gif": "ALT+102",   # Alt+F
    "show_reactions": "ALT+119",   # Alt+W
    "show_pinned": "ALT+110",   # Alt+N
    # extra line
    "attach_prev": "ALT+44",   # Alt+<
    "attach_next": "ALT+46",   # Alt+>
    # extra window
    "extra_up": 573,   # Alt+Up
    "extra_down": 532,   # Alt+Down
    "extra_select": "ALT+10",   # Alt+Enter
    "preview_upload": "ALT+118",   # Alt+V
    # media
    "media_pause": 32,   # C
    "media_replay": 122,   # Z
    "media_seek_forward": 261,   # Right
    "media_seek_backward": 260,   # Left
    # other
    "cancel": 24,   # Ctrl+X
    "cycle_status": "ALT+100",   # Alt+D
    "toggle_member_list": "ALT+109",   # Alt+M
    "toggle_tab": 20,   # Ctrl+T
    "switch_tab_modifier": "ALT+NUM",   # Alt+Num
    "command_palette": 31,   # Ctrl+/
    "open_external_editor": "ALT+101",   # Alt+E
    "quit": None,   # already bound to Ctrl+C
}


command_bindings = {
    "552": "switch_tab prev",
    "567": "switch_tab next",
    "11": "command_palette; type 'goto '",
    "336": "tree_select server; collapse_all_except selected",
    "337": "tree_select server prev; collapse_all_except selected",
}


windows_override_keybindings = {
    "command_palette": 28,   # Ctrl+\
    "tree_up": 480,   # Ctrl+Up
    "tree_down": 481,   # Ctrl+Down
    "tree_select": 1,   # Ctrl+A
    "word_left": 443,   # Ctrl+Left
    "word_right": 444,   # Ctrl+Right
    "view_media": "ALT+121",   # Alt+Y
}


macos_override_keybindings = {
    "tree_up": 337,   # Shift+Up
    "tree_down": 336,   # Shift+Down
    "browser": "ALT+111",   # Alt+O
}
