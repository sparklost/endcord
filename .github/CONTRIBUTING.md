## **NOT OPEN SOURCE**
Endcord is licensed under source-available license. This means it is **NOT OPEN SOURCE**. How this affects you:
If you are a user, this **doesn't affect** the slightest how you are using endcord.  
If you are a developer, you are **NOT ALLOWED TO PUBLICLY MODIFY THE CODE**.  
If you are a package maintainer, license specifically allows it to distribute **binaries built from verbatim unmodified source code**.  
Slightly longer and more detailed explanation is in the [license file](LICENSE).
Why? Because this is one-person project, and this person is greedily taking all the fun of programming for themselves. And as bonus they ensures that this project stays 100% human made, forever.

### The only ways you can contribute
- Open an issue containing bug report or feature request
- Create and maintain packages for other OSs, distros and install systems
- Spread the word


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


## Architecture
Endcord is developed in "Modular composition and component-based architecture using singleton services with a central orchestrator".
Explanation:
- Modular component-based - program is split into independent modules (classes in separate files), each responsible for a specific thing.
- Composition - main app class contains and uses instances of other classes instead of inheriting from them.
- Singleton services - for each class only one instance is used.
- Central orchestrator - Main app class is controller that: creates all instances, wires them together, does core logic.


## Build protobuf
DEPRECATED - Custom protobuf implementation is used
```bash
sudo pacman -Syu protobuf
protoc -I tools --python_out=endcord user_settings.proto
sed -i '/wrappers_pb2/s/$/   # noqa/' endcord/user_settings_pb2.py
ruff check --fix endcord/user_settings_pb2.py
protoc -I tools --python_out=endcord user_frecency.proto
ruff check --fix endcord/user_frecency_pb2.py
```


## Useful debugging things

### Debug points in the code
- `debug_events` - save all received events from gateway.
- `debug_guilds_tree` - print all tree data in jsons.
- `255_curses_bug` - this part of the code should be changed after [ncurses bug](https://github.com/python/cpython/issues/119138) is fixed. If there is no note, just remove the code.
- `fix_member_list_selection` - properly select member list when figured out how to use `id` in `GUILD_MEMBER_LIST_UPDATE`.
- `fix_davey` - commented pieces of code in voice.py that use davey instead dave.py, should be re-visited after [this bug](https://github.com/Snazzah/davey/issues/15) gets fixed.

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
Or regular discord: in `~/.config/discord/settings.json` put: `"DANGEROUS_ENABLE_DEVTOOLS_ONLY_ENABLE_IF_YOU_KNOW_WHAT_YOURE_DOING": true`  
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
││       ││                           I│        ││
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
/edited_timestamp - missing for some messages

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
Gateway returns error code 4000 if event "update presence" (opcode 3) is sent.  


## Build steps for package maintainers
1. Download and build custom python with recent ncurses (optional)
- Clang is optional everywhere, but it's recommended as it provides better binary.  
- The entire process is done by `tools/build_python.sh`. to run it: `bash tools/build_python.sh 3.14.5 clang curses v6_6_20251230`.
- Python will be installed in `./.cpython/bin/python3.14`.
- Once finished just configure venv to use `./.cpython/bin/python3.14`

2. Setup dependencies  
- Linux system build dependencies when using nuitka (recommended): `clang` (or gcc), `patchelf` (DO NOT use v0.18.x).
- Any python virtual environment manager can be used that can read dependencies from `pyproject.toml`, here `uv` is used by default.  
- Only python versions 3.12-3.14 can currently build binaries.  
- `uv sync --all-groups` - full endcord  
    Will install all dependencies from pyproject.toml under `dependencies`, `build`, and `media`.  
- `uv sync --group build` - endcord-lite  
    Will install all dependencies from pyproject.toml under `dependencies` and `build`.  

3. Check if numpy without openblas is already installed, if returns 1 then its not, so download and build numpy  
- Numpy build can be skipped if its impossible, but binary will be few MB larger.  
- If building with pyinstaller skip numpy build.  
- This command will print `1` if openblas is found in numpy, and then it has to be built locally.  
```bash
uv run python -c "import numpy; print(int(numpy.__config__.show_config('dicts')['Build Dependencies']['blas'].get('found', False)))"
```

4. Download and build numpy (optional)
- Set `CFLAGS` and `LDFLAGS` obtained from step 7.
```bash
export CC=clang
export CXX=clang++
uv pip install pip
.venv/bin/python -m pip uninstall --yes numpy
.venv/bin/python -m pip install --no-cache-dir --no-binary=:all: numpy --config-settings=setup-args=-Dblas=None --config-settings=setup-args=-Dlapack=None
uv pip uninstall pip
```

5. Build cython extensions  
- Skip this step only if final binary gives error `Dynamic module does not define module export function`.
- Compiler args are set in the setup.py, so no need to set them as env vars.
```bash
export CC=clang
export CXX=clang++
uv run python setup.py build_ext --inplace
```

6. Run patches and cleanups
- run `patch_soundcard()` from build.py - **required!**
- run `compress_emoji()` from build.py - will make `emoji.json` smaller.

7. Get and run build command
- Build commands can be obtained by running `python build.py --print-cmd`, adding other arguments will change the printed command.  
- The script will also append to `CFLAGS` and `LDFLAGS` env variables, which depend on compielr used. Same compiler args are used for all compilations except custom python build (the bash script will set them).
- Recommended: `python build.py --print-cmd --nuitka`.  
