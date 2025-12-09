## **DO NOT CONTRIBUTE**
You'll save your mental health, and I will save time trying to understand your code. Open an issue, and I will get to it.  
The code is total mess that only I can efficiently navigate through, and if someone else adds any-quality code to it, it will only get harder for me.  
Any PR will be rejected, sorry.  


## **LLM generated code is strongly prohibited**
If PR is suspected to have more than 5% of AI generated code, it will be closed ASAP, and the features will be "really" implemented by someone else, without any association with that PR. No exceptions.  
So, before even thinking about putting it through a LLM, open an issue and save yourself the trouble, because others can do it for free and better.  


## Contributing rules
- Don't use inheritance. It makes code even more unreadable.
- Don't use dataclasses, it's too late now, use nested lists and dicts.
- Don't write tests, this cant be tested.
- NO typing!
- Don't refactor, format (other than the existing ruff config), clean or unnecessarily optimize. I like the code the way it is.
- Don't use `requests`, it uses 3MB more RAM than `http.client`.
- Use `os.path` instead `pathlib`, its making things weird.
- NO `asyncio`, this is pure `threading` project.
- If you know how to do it, and it's not really hard, do it yourself, don't import large libraries for that.


## Running from source
1. setup uv environment if not already: `uv sync --all-groups`
2. Run main.py: `uv run main.py`


## Useful debugging things

### Debug points in the code
- `debug_events` - save all received events from gateway
- `debug_guilds_tree` - print all tree data in jsons
- `255_curses_bug` - this part of the code should be changed after [ncurses bug](https://github.com/python/cpython/issues/119138) is fixed. If there is no note, just remove the code

### Network tab filter
Filter for network tab in dev tools:  
`-.js -css -woff -svg -webp -png -ico -webm -science -txt -mp3`

### Monitor IPC on linux socket
```bash
sudo mv /run/user/1000/discord-ipc-0 /run/user/1000/discord-ipc-0.original
sudo socat -t100 -x -v UNIX-LISTEN:/run/user/1000/discord-ipc-0,mode=777,reuseaddr,fork UNIX-CONNECT:/run/user/1000/discord-ipc-0.original
```

### Log discord events to console
Open discord web or install discord-development  
Or regular discord: in `.config/discord/config.json` put: `"DANGEROUS_ENABLE_DEVTOOLS_ONLY_ENABLE_IF_YOU_KNOW_WHAT_YOURE_DOING": true`  
Open dev tools: `Ctrl+Shift+I`  
Type: `allow pasting`  
Paste code from [here](https://gist.github.com/MPThLee/3ccb554b9d882abc6313330e38e5dfaa?permalink_comment_id=5583182#gistcomment-5583182)  
Go to discord settings, in developer options, logging tab, enable "Logging Gateway Events to Console"  
In dev tools console select "Verbose" level (chrome and desktop client only)  

### Full API documentation
https://github.com/discord-userdoccers/discord-userdoccers

### App command permissions chart
https://discord.com/developers/docs/change-log#upcoming-application-command-permission-changes

### Layout
Standard:
```
┌────────────────────────────────────────────────┐
│┌─TITLE─┐┌─────────────── TITLE ─────┐┌────────┐│
││       ││                           ││        ││
││       ││                           ││ MEMBER ││
││       ││            CHAT           ││  LIST  ││
││       ││                           ││        ││
││       ││                           ││        ││
││ TREE  │└───────────────────────────┘└────────┘│
││       │┌────────────── EXTRA2 ───────────────┐│
││       ││             EXTRA BODY              ││
││       │┌────────────── EXTRA1 ───────────────┐│
││       │├────────────── STATUS ───────────────┤│
││       ││[PROMPT]>                            ││
│└───────┘└─────────────────────────────────────┘│
└────────────────────────────────────────────────┘
```
Compact:
```
┌────────────────────────────────────────────────┐
│W TITLE W│WWWWWWWWWWWWWWW TITLE WWWWWWWWWWWWWWWW│
│         │                             │        │
│         │                             │        │
|         │                             │ MEMBER │
│         │            CHAT             │  LIST  │
│         │                             │        │
│         │                             │        │
│  TREE   │                             │        │
│         │MMMMMMMMMMMMMMM EXTRA2 MMMMMMMMMMMMMMM│
│         │              EXTRA BODY              │
│         │UUUUUUUUUUUUUUU EXTRA1 UUUUUUUUUUUUUUU│
│         │WWWWWWWWWWWWWWW STATUS WWWWWWWWWWWWWWW│
│         │[PROMPT]>                             │
└────────────────────────────────────────────────┘
```

### Tree layout and formatting
```
> FOLDER
> GUILD
|--> CATEGORY
|  |-- CHANNEL
|  |--> CHANNEL
|  |  |-< THREAD
|  |  \-< THREAD
|  |  end_channel 1300
|  \--> CHANNEL
|  end_category 1200
|--> CATEGORY
|  end_category 1200
end_guild 1100
end_folder 1000
```

### Support for zstd stream compression:
```py
inflator = zstandard.ZstdDecompressor().decompressobj()
def zstd_decompress(data):
    """Decompress zstd stream"""
    buffer = bytearray(data)
    try:
        return inflator.decompress(buffer)
    except zstandard.ZstdError as e:
        logger.error(f"zstd error: {e}")
        return None
```
Use `&compress=zstd-stream` in gateway url.

### Decode X-Super-Properties
```py
import base64
import json
encoded = "STRING_HERE"
decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
print(json.dumps(decoded, indent=2))
```

### Spacebar differences
In the code, all spacebar fixes can be found as `# spacebar_fix - [note]`.  
`[note]` contains some information on what is changed.  

- `/api/v9/users/@me/mentions`:
/global_name - missing

- `/api/v9/users/{my_id}/channels`:
/recipients/global_name - missing

- `/api/v9/users/{user_id}/profile`:  
/user/avatar_decoration_data - missing  
user/flags - missing  
/user/global_name - missing  
/user/primary_guild - missing  

- `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `/api/v9/channels/{channel_id}/messages`, `/api/v9/guilds/{guild_id}/messages/search` and `/api/v9/channels/{channel_id}/pins`:
/author/global_name - missing  
/referenced_message - should be null only if its pointing to delteted message, otherwise remove this field  
/referenced_message/author/global_name - missing  
/reactions/emoji/id - missing  
/poll - should be removed if its `null`  
/interaction - should be removed if its `null`  
/mentions/username - missing  
/embeds/type - missing  

- `READY`:  
/merged_members/id - should be /merged_members/user_id  
/read_state/entries/mention_count - should be `0`, not `null`  
/private_channels/recipient_ids - missing, have recipients list instead
/users/global_name - missing  
/user_guild_settings/entries/channel_overrides/collapsed - missing  

- `TYPING_START`:
/member/user/global_name - missing  

- `GUILD_MEMBER_LIST_UPDATE`:
/items/member/user/global_name - missing  
/item/member/user/global_name - missing  

- `MESSAGE_REACTION_ADD`:
/member/user - missing  
/emoji/id - missing when its standard emoji  

- `MESSAGE_REACTION_REMOVE`:
/emoji/id - missing when its standard emoji  

- `GUILD_MEMBERS_CHUNK`:
/members/user/global_name - missing  

- `PRESENCE_UPDATE`:
/status - missing

- Misc:
Spacebar is still using old `user_settings` instead new protobuf settings.  
