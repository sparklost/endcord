import curses
import importlib.util
import logging
import os
import signal
import sys
import time
import traceback

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"   # fix for https://github.com/Nuitka/Nuitka/issues/3442

from endcord import arg, defaults, peripherals

APP_NAME = "endcord"
VERSION = "1.3.0"
default_config_path = peripherals.config_path
log_path = peripherals.log_path
uses_pgcurses = hasattr(curses, "PGCURSES")

logger = logging
logging.basicConfig(
    level="INFO",
    filename=os.path.expanduser(os.path.join(log_path, APP_NAME + ".log")),
    encoding="utf-8",
    filemode="w",
    format="{asctime} - {levelname}\n  [{module}]: {message}\n",
    style="{",
    datefmt="%Y-%m-%d-%H:%M:%S",
)


def sigint_handler(_signum, _frame):
    """Handling Ctrl-C event"""
    try:
        # in case curses.wrapper doesnt restore terminal
        curses.nocbreak()
        curses.echo()
        curses.endwin()
    except curses.error:
        pass
    sys.exit(0)


def main(args):
    """Main function"""
    if not uses_pgcurses:
        peripherals.ensure_terminal()
    config_path = args.config
    theme_path = args.theme
    if config_path:
        config_path = os.path.expanduser(config_path)
    config, gen_config, error = peripherals.merge_configs(config_path, theme_path)
    if error:
        sys.exit(error)
    if sys.platform == "win32":
        defaults.keybindings.update(defaults.windows_override_keybindings)
    elif sys.platform == "darwin":
        defaults.keybindings.update(defaults.macos_override_keybindings)
    keybindings = peripherals.load_config(
        config_path,
        defaults.keybindings,
        section="keybindings",
        gen_config=gen_config,
    )
    command_bindings = peripherals.load_config(
        config_path,
        defaults.command_bindings,
        section="command_bindings",
        gen_config=gen_config,
        merge=True,
    )
    if config["vim_mode"]:
        vim_keybindings = peripherals.load_config(
            config_path,
            defaults.vim_mode_bindings,
            section="vim_mode_bindings",
            gen_config=gen_config,
            merge=True,
        )
        keybindings = peripherals.merge_keybindings(keybindings, vim_keybindings, command_bindings)
    if not uses_pgcurses:
        keybindings = peripherals.convert_keybindings(keybindings)
        command_bindings = peripherals.convert_keybindings_cmd(command_bindings)

    keybindings = peripherals.normalize_keybindings(keybindings)

    os.environ["ESCDELAY"] = "25"   # 25ms
    if os.environ.get("TERM", "") == "linux" or os.environ.get("TERM", "").startswith("xterm"):   # for xterm-ghostty
        os.environ["REALTERM"] = os.environ["TERM"]
        os.environ["TERM"] = "xterm-256color"   # try to force 256-color mode
    peripherals.ensure_ssl_certificates()

    if args.colors:
        # import here for faster startup
        from endcord import color
        if uses_pgcurses:
            curses.enable_tray = False
        color.color_palette()
        sys.exit(0)
    elif args.keybinding:
        from endcord import keybinding
        if uses_pgcurses:
            curses.enable_tray = False
        keybinding.picker(keybindings, command_bindings)
        sys.exit(0)
    elif args.media:
        if not (
            importlib.util.find_spec("PIL") is not None and
            importlib.util.find_spec("av") is not None and
            importlib.util.find_spec("nacl") is not None
        ):
            print("Terminal media player is not supported", file=sys.stderr)
            sys.exit(1)
        from endcord import media
        if uses_pgcurses:
            curses.enable_tray = False
        try:
            media.runner(args.media, config, keybindings)
        except curses.error as e:
            if str(e) != "endwin() returned ERR":
                logger.error(traceback.format_exc())
                sys.exit("Curses error, see log for more info")
        sys.exit(0)
    elif args.install_extension:
        peripherals.install_extension(args.install_extension)
        sys.exit(0)

    if args.proxy:
        config["proxy"] = args.proxy
    if args.host:
        config["custom_host"] = args.host
    if args.debug or config["debug"]:
        logging.getLogger().setLevel(logging.DEBUG)

    from endcord import profile_manager
    logger.info(f"Started endcord {VERSION}")
    if args.token:
        profiles = {"selected": "default", "plaintext": [{"name": "default", "token": args.token, "time": int(time.time())}], "keyring": []}
        proceed = True
    else:
        profiles_path = os.path.join(peripherals.config_path, "profiles.json")
        if args.profile:
            selected = args.profile
        else:
            selected = None
        profiles, selected, proceed = profile_manager.manage(profiles_path, selected, config, force_open=args.manager)
        if not profiles:
            print("Token not provided in profile manager nor as argument")
            sys.exit(0)
    if not proceed:
        sys.exit(0)

    try:
        from endcord import app
        curses.wrapper(app.Endcord, config, keybindings, command_bindings, profiles, VERSION)
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            logger.error(traceback.format_exc())
            sys.exit("Curses error, see log for more info")
    sys.exit(0)


if __name__ == "__main__":
    args = arg.parser(APP_NAME, VERSION, default_config_path, log_path)
    signal.signal(signal.SIGINT, sigint_handler)
    main(args)
