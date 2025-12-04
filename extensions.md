## Installing extensions
Extensions can be installed in `Extensions` directory located in endcord config directory.  
Installation can be done by simply git cloning extension github repo into the extensions directory or by running `endcord -i extension_url`.  
Each extension should be placed there as a folder containing at least `.py` file with same name as folder.  
Extension loading can be toggled in config and is ON by default.  
During loading process some extensions may fail to load or are invalid, check log for more info.  
If extension is built for different version of endcord, there is a chance it may misbehave or even cause a crash. But that should be rare.  
**Disclaimer: You are installing extension at your own risk!** Depending on extension content it may increase risk or even cause your account to be banned. Extensions can be used to steal your token! See [Checking Extensions](#checking-extensions) for some red flags.  


## Misc useful information when installing and writing extensions

### Extension load order and chaining
Extensions are loaded in alphanumeric order, and in some cases it can matter because one extension can modify data before it is accessed by other extension in the chain.  

### Settings
Extensions can access settings loaded from main settings - `config.ini` in config directory.  
Extensions settings must always be in form: `ext_extension_name_setting_name` - starts with `ext_`, followed by lowercase extension name and then custom setting name.  
Settings can be accessed in extension as `app.settings` in extensions `__init__`, it is a dict so do `app.settings.get("ext_extension_name_setting_name", "default_value")`.  

### Forced build-time disable
Extensions are enabled by default, and can be toggled in settings.  
But extension can modify almost everything in endcord, and can even access all the tokens, allowing malicious extensions to steal tokens.  
To prevent extension injection (malware can modify endcord config to enable extensions and inject extension in extensions directory) - which is very unlikely, there is build script option: `--disable-extensions` which disables extension loading in the code itself, overriding config.  

### Extension search and publishing
It is recommended to use `endcord-extension` or `endcord` tags on github and other git hosting services for easier extension search.  

### Logging
Extensions can add log entries at any level and will have their name in the module name section of log entry.  
To add log entry from extension:  
First `imprt logging`. Then add this at global part of the code: `logger = logging.getLogger(__name__)`.  
Now to add an `info` level log entry anywhere in the code: `logger.info("Text to be logged")`. Or use any other of the log levels from `logging` library.  

### Importing modules and accessing files
To import any endcord module simply do `from endcord import endcord_module`.  
To import other modules just do `import module`, extension directory is temporarily added to sys.path.  
To access files from extension directory: `file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file.json")`.  


## Extension structure

### Files structure
Extension should be one directory containing all extension files.  
This directory should contain same named `.py` file, which is main extension file. Extension will not be loaded if this is wrong.  
There can be other `.py` files imported by this main file and any other relevant data, documentation, license or even compiled cython modules.  
If extension is a git repo then make repo name same as main `.py` file, which is stored in the repo root. (this helps in extension installing process).  

### Main extension file structure
Main extension file has some requirements that must be followed otherwise extension will be flagged as invalid and not loaded.  
These requirements are:
- Extension metadata at the global space of the file, in form of constants: `EXT_NAME`, `EXT_VERSION`, `EXT_ENDCORD_VERSION`, `EXT_DESCRIPTION` and `EXT_SOURCE` (url to the source coe). They should all be strings and not empty.
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
- `on_message_event` - in main loop, when message event is received, before event is processed, has event at input and output
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
If endcord is built into binary, only libraries included by endcord can be used by extension.  
This list can be quite long, so check py files for used libraries (entire stdlib should be included by build tool).  
Run `uv tree` to see only libraries included in endcord-lite builds, and `uv tree --group media` to see libraries included in full endcord build.  
It is possible to add entire library to extension directory, which can be imported by extension, but this may be unstable cross-platform.  


## Checking extensions
Extensions can be malicious, trying to steal your token.  
Always check extension contents before running it, few red flags are:
- Any usage of word `token`
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
