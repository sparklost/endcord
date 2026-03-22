## Installing extensions
Extensions can be installed in `Extensions` directory located in endcord config directory.  
Installation can be done by simply git cloning extension repo into the extensions directory or by running `endcord -i [url]`.  
There is also client command available: `install_extension [url]`. Running it without url will update all installed extensions.  
Instead `url` can also be used `repo_owner/repo_name` which assumes github.  
Extension loading can be toggled in config and is ON by default.  
During loading process some extensions may fail to load or are invalid, check log for more info.  
If extension is built for different version of endcord, there is a chance it may misbehave or even cause a crash. But that should be rare.  
Extensions can be used to steal your token! See [Checking Extensions](#checking-extensions) for some red flags.  


## Disclaimer
> [!WARNING]
> Using third-party client is against Discord's Terms of Service and may cause your account to be banned!  
> **Use endcord and/or this extension at your own risk!**  
> Depending on extension content it may increase risk or even cause your account to be banned.  
> Extensions can be used to steal your token! See [Checking Extensions](#checking-extensions) for some red flags.  
> Extensions may be used for harmful or unintended purposes.  
> **Endcord developer is not responsible for any misuse or for actions taken by users.**  


## Misc useful information when installing and writing extensions

### Extension load order and chaining
Extensions are loaded in alphanumeric order, and in some cases it can matter because one extension can modify data before it is accessed by other extension in the chain.  

### Settings
Extensions can access settings loaded from main settings - `config.ini` in config directory.  
Extensions settings must always be in form: `ext_extension_name_setting_name` - starts with `ext_`, followed by lowercase extension name and then custom setting name. Extension name should be same as repo name, use underscore instead dash, and remove prepended "endcord".
Settings can be accessed in extension as `app.settings` in extensions `__init__`, it is a dict so do `app.settings.get("ext_extension_name_setting_name", "default_value")`.  

### Forced build-time disable
Extensions are enabled by default, and can be toggled in settings.  
But extension can modify almost everything in endcord, and can even access all the tokens, allowing malicious extensions to steal tokens.  
To prevent extension injection (malware can modify endcord config to enable extensions and inject extension in extensions directory) - which is very unlikely, there is build script option: `--disable-extensions` which disables extension loading in the code itself, overriding config.  

### Extension search and publishing
It is recommended to use `endcord-extension` tag on github and other git hosting services for easier extension search. Repo name should be prepended with `endcord` eg. `endcord-your-extension-name`.  

### Logging
Extensions can add log entries at any level and will have their name in the module name section of log entry.  
To add log entry from extension:  
First `imprt logging`. Then add this at global part of the code: `logger = logging.getLogger(__name__)`.  
Now to add an `info` level log entry anywhere in the code: `logger.info("Text to be logged")`. Or use any other of the log levels from `logging` library.  

### Importing modules and accessing files
To import any endcord module simply do `from endcord import endcord_module`.  
To import other modules just do `import module`, extension directory is temporarily added to sys.path.  
To access files from extension directory: `file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file.json")`.  

### Extension updates
Only extensions published on github can be updated with endcord built-in system.  
To publish new update, simply create new release named with version number.  


## Extension structure

### Files structure
Extension should be one directory containing all extension files.  
This directory should contain `.py` file with same name, which is main extension file. Extension will not be loaded if this is wrong.  
There can be other `.py` files imported by this main file, and files for any other relevant data, documentation, license or even compiled cython modules.  
If extension is a git repo, then main `.py` file will have same name as repo, and should be placed in repo root. (this helps in extension installing process).  

### Main extension file structure
Main extension file has some requirements that must be followed otherwise extension will be flagged as invalid and not loaded.  
These requirements are:
- Extension metadata at the global space of the file, in form of constants: `EXT_NAME`, `EXT_VERSION`, `EXT_ENDCORD_VERSION`, `EXT_DESCRIPTION` and `EXT_SOURCE` (url to the source code). They should all be strings and not empty.
- `Extension` class.
- `Extension` class must contain `__init__` method that takes one argument - `app` which is entire endcord app class. it is recommended to keep `app` as `self.app` so other methods can access anything from app class later.

### Extension access points and methods
Extension can contain specifically named methods. These methods are then detected by endcord app class and executed at specific points in the endcord own code.  
Methods take and return no arguments unless specified.  
Some methods will accept one or more arguments, and same number of variables must be returned by that method (or None to do it automatically), which allows for modification of these variables.  
Arguments are chained between extensions having same named method, extensions are executed in alphabetical order.  
Method names can be searched in `./endcord/app.py` code to see where they are executed.  

### List of extension access points names and their locations in endcord code:
- `__init__` - on end of app class init
- `on_main_start` - just before main loop starts
- `on_main_loop` - first in main loop
- `on_message_event` - in main loop, when message event is received, before event is processed; has event at input and output; only "relevant" messages are passed here
- `on_switch_channel_start` - near start of switch_channel, after self.active_channel is updated
- `on_switch_channel_end` - near end of switch_channel, before UI is updated
- `on_reconnect` - near end of reconnect, before UI is updated
- `on_resize` - in main loop, when screen geometry change is detected, before all other resize actions and after self.chat_dim is updated
- `on_escape_key` - near end escape key handling in wait_input
- `on_start_call` - on end of start_call
- `on_leave_call` - on end of leave_call
- `on_call_gateway_event` - in process_call_voice_gateway_events, before event is processed, has event at input and output
- `on_call_voice_gateway_event` - in process_call_voice_gateway_events, before event is processed, has event at input and output
- `on_execute_command` - at the start of execute_command, ran only of there are no matched builtin commands
- `init_bindings` - in load_extensions in tui.py, executed right after initializing all extensions in app.py
- `on_binding` - at the end of common_keybindings in tui.py, executed only if there are no default bindings matched
- `on_wait_input` - at the end of wait_input in app.py, executed only if there are no default action codes matched
- `on_gateway_event` - at the start of loop in receiver in gateway.py, event data is passed as argument
- `on_message_event_is_irrelevant` - in gateway.py near `elif optext == "MESSAGE_CREATE"` decides if these events are relevant and should be further processed. Has **raw** message event and event optext at input and is expected to return `True` if message is relevant (doesn't override already relevant messages).

## Adding a command
1. Add method named `on_execute_command` to extension class, it takes 3 arguments: `command_text` (str), `chat_sel` (int) - line selected in the chat, `tree_sel` (int) -  line selected in the channel tree.
    - Match keyword usually with `cmd_text.startswith("some_text")`, and if needed, use regex to match arguments like channel id, numbers etc.
    - If nothing is matched, return `False`.
    - If command is matched, execute your code, then return True.
    - Some commands shouldn't be executed when viewing forum, to check if forum is opened use `if self.app.forum:`
    - To get message/forum thread object use: `self.messages[self.lines_to_msg(chat_sel)]`
    - To get metadata for selected object in tree use: `self.tree_metadata[tree_sel]`
2. Optionally add global constant `EXT_COMMAND_ASSIST` with format: `(("command - descriotion", "command"), (...)...)`. It will be appended to builtin commands.


## Adding a binding
1. Add method `init_bindings` to extension class, it takes 1 argumet: `keybindings` - a dict: {keybinding_name: value}
    - First try to load binding from `keybindings` which follows format given in #settings, it should have default value.
    - Store those values in the class, they will be accessed by `on_binding`
    - It must return a dict containing only this extensions bindings, with format: {keybinding_name: value}
2. Add method named `on_binding` to extension class, it takes 3 arguments: `key`, `is_command` (bool), `is_forum` (bool)
    - `key` will be same thing as printed in keybinding resolver. `is_forum` means that currently command is being typed. `is_forum` means that forum is currently opened.
    - Test if `key` is same as specific keybinding that was defined in `init_bindings`.
    - `on_binding` must return value that represents the action code. This action code is matched in `on_wait_input`. It is recommended to use action codes above 1000 to avoid any collisions with default ones, and other extensions.
    - If no key is matched, return `None`
    - If needed execute tui related code with `self.app.tui.some_function()`
3. Add method named `on_wait_input` to extension class, it takes 3 arguments: `action_code` (int), `input_text` (str), `chat_sel` (int), `tree_sel` (int)
    - test for action code
    - If matched:
    - `self.restore_input_text = (input_text, "standard")` - this will set mode to "standard" for input line and keep same `input_text` in input line when binding is pressed.
    - Alternatively modify `input_text` or change mode: "standard", "standard extra", "standard insert", "prompt", "after prompt", "autocomplete", "search", "command", "react", "edit".
    - If there is going to be a prompt and input text should be cached and later restored: set `self.restore_input_text = (None, "prompt")`, and `self.add_to_store(self.active_channel["channel_id"], input_text)`
    - Return `True` only if binding is matched


## Executing existing command
Extensions can execute existing client-side commands with this code:
```py
command_text = "some_command argument_1 some text"
command_type, command_args = self.app.parser.command_string(command_text)
chat_sel = self.app.tui.get_chat_selected()[0]
tree_sel = self.app.tui.get_tree_selected()
self.app.execute_command(command_type, command_args, command_text, chat_sel, tree_sel)
```


## Modifying existing code
Existing code in endcord `app` class can be modified, by replacing `app` class methods with custom methods.  
But be warned: replacing method like this will also replace any updates made to it in new endcord version, so extension muss be updated accordingly.  
To do this:  
1. Define a custom named method in extension class.
2. Copy code from that method in endcord app class to this extension method.
3. Replace all `self`s with `self.app` (or whatever you named it in `__init__`)
4. Do your codifications to the code.
5. In extension `__init__`, do: `self.app.method_name_in_app = method_name_in_extension`
6. same thing can done to modify `app` class attributes, or even other subclass attributes and methods, like `self.app.gateway.update_presence`

### What cant be modified
This will modify `app` class methods ran at the end of app `__init__`, which means anything ran in `app` before that, will run original code.  
Look for `__init__` in `app` class in `./endcord/app.py` to see what is ran before extensions are loaded.  


## Available libraries
Run `uv tree` to see only libraries included in endcord-lite builds, and `uv tree --group media` to see libraries included in full endcord build (this list doesnt show stdlib libraries, but they should all be available).  
It is possible to add entire library to extension directory, which can be imported by extension, but this may be unstable cross-platform.  


## Creating bots
1. First of all, bot has to have `Bot ` prepended to its token.  
2. It is recommended to set bot intents value in the config `capabilities` option. Default is `50364033` which allows basic chat features.  
Refer to [this](https://docs.discord.com/developers/events/gateway#gateway-intents) for more info on intents.  
4. Next step is to register application commands.  
To register a command, use `app.discord.bot_register_command(command_obj, guild_id=None, json=False)` in the extension.  
If `guild_id` is ommited then this will be global command.  
`command_obj` is python object, but json string can be passed too, just set `json=True`.  
`command_obj` is send as-is without any checks, refer to [this](https://docs.discord.com/developers/interactions/application-commands#application-command-object) for more info on how to write commands.  
Command is registered only once, registering command with same name will overwrite old one.  
To update command use `app.discord.bot_update_command(command_obj, command_id, guild_id=None)`.  
To delete command use `app.discord.bot_delete_command(command_id, guild_id=None)`.  
To obtain role ids for specific guild (needed for creating command permissions), run `dump_roles` endcord command while inside desired guild. Json file will be saved in "Debug" directory in endcord config location.  
5. Handle received interactions  
Interactions are received by gateway, and buffered. To get one by one interaction from the buffer run: `app.gateway.bot_get_interactions()`.  
Interaction object structure can be found [here](https://docs.discord.com/developers/interactions/receiving-and-responding#interaction-object).  
It will return either raw interaction object or `None` when buffer is empty.  
It is recommended to poll `bot_get_interactions`.  
If interaction will take a long time to complete (eg. doing some CPU-heavy task) then offload it to a thread, so polling loop kepps running with low latency.
Store `id` and `token` values somewhere, because they are needed to send the response (`interaction_id` and `interaction_token` args).  
Note that bot must respond within 3 seconds.  
6. Respond to interaction  
To respond, simply call `app.discord.bot_respond_interaction(response_type, response_obj, interaction_id, interaction_token)`
Responding to interactions is documented in detail [here](https://docs.discord.food/interactions/receiving-and-responding#responding-to-an-interaction). Response object structure can be found [here](https://docs.discord.food/interactions/receiving-and-responding#interaction-callback-message-data-structure).  
Be sure to always implement `PONG` response.  
7. Long response
If the response is going to take a while, then first send deferred response (`response_type=5`).  
And then when final response is ready, edit the original interaction with `app.discord.bot_edit_interaction(response_obj, interaction_token)`.  
Note that `interaction_token` will expire in 15 minutes.  
Or delete it with `app.discord.bot_delete_interaction(interaction_token)`.  


## Checking extensions
Extensions can be malicious, trying to steal your token.  
Always check extension contents before running it, few red flags are:
- Any usage of word `token`, unless its only checking for `"Bot"`
- Attempting to access: `app.discord.headers`, `app.profiles`
- Modifying app.discord methods to change their host
- Having discord snowflakes hardcoded or in some of the files
- Loading base64 encoded strings, or any other obfuscated content
- Enabling `http.client` logging


## Example extension
```py
import logging

from endcord import peripherals

EXT_NAME = "Notify Test"
EXT_VERSION = "0.1.0"
EXT_ENDCORD_VERSION = "0.9.0"
EXT_DESCRIPTION = "An extension that sends desktop notification every time user sends a message containing word 'test'"
EXT_SOURCE = "https://github.com/sparklost/endcord"
logger = logging.getLogger(__name__)


class Extension:
    """Main extension class"""
    def __init__(self, app):
        self.app = app
        self.enabled = app.config.get("ext_notify_test_enable", True)

    def on_message_event(self, new_message):
        """Ran when message event is received"""
        data = new_message["d"]
        if new_message["op"] == "MESSAGE_CREATE" and data["user_id"] == self.app.my_id:
            peripherals.notify_send(
                "Extension Test",
                "You sent a Test",
                sound=self.app.notification_sound,
                custom_sound=self.app.notification_path,
            )
            logger.info("You sent a Test")
```
