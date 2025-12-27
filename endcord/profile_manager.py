import curses
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    import pywintypes
    import win32cred
    BACKSPACE = 8
else:
    BACKSPACE = curses.KEY_BACKSPACE

APP_NAME = "endcord"
MANAGER_TEXT = """ Select or add your profile here. See readme for more info.
 Or just press "Add" button. Use "--manager" flag to show this again."""
NO_KEYRING_TEXT = " Keyring is not installed or is not working properly, see log for more info."
NAME_PROMPT_TEXT = """ Profile name is just an identifier for tokens for different accounts.
 Profiles are useful to quickly switch between multiple accounts.
 If you are going to use one account, type anything here, or leave it blank.
 If this profile name is same as other profile, the old one will be replaced.

 Profile name can be typed/pasted here (with Ctrl+Shift+V on most terminals):



 Enter to confirm, Esc to go back.
 """
TOKEN_PROMPT_TEXT = """ Token is required to access Discord through your account without logging-in.

 Obtaining your token:
 1. Open Discord in browser.
 2. Open developer tools ('F12' or 'Ctrl+Shift+I' on Chrome and Firefox).
 3. Go to the 'Network' tab then refresh the page.
 4. In the 'Filter URLs' text box, search 'discord.com/api'.
 5. Click on any filtered entry. On the right side, switch to 'Header' tab, look for 'Authorization'.
 6. Copy value of 'Authorization: ...' found under 'Request Headers' (right click -> Copy Value)
 7. This is your discord token. DO NOT SHARE IT!

 Token can be typed/pasted here (with Ctrl+Shift+V on most terminals):



 Enter to confirm, Esc to go back.
 """
SOURCE_PROMPT_TEXT = (
    "Select where to save token:",
    "Keyring is secure encrypted storage provided by the OS - recommended,",
    "Plaintext means it will be saved as 'profiles.json' file in endcord config directory",
    "", "", "", "",
    "Enter to confirm, Esc to go back, Up/Down to select",
)

logger = logging.getLogger(__name__)


def setup_secret_service():
    """Check if secret-tool can be run, and if not, setup gnome-keyring daemon running on dbus"""
    try:
        # ensure dbus is running
        if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
            if not shutil.which("dbus-launch"):
                logger.warning("Cant use keyring: 'dbus' package is not installed")
            output = subprocess.check_output(["dbus-launch"]).decode()
            for line in output.strip().splitlines():
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

        # ensure gnome-keyring is running
        # this should start gnome-keyring-daemon
        result = subprocess.run(
            ["secret-tool", "lookup", "service", "keyring-check"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if "not activatable" in result.stderr.decode():
            logger.warning("Cant use keyring: failed to start 'gnome-keyring' daemon, it is probably not installed")

    except subprocess.CalledProcessError:
        logger.warning("Cant use keyring: failed to start gnome-keyring")


def load_secret():
    """Try to load profiles from system keyring"""
    if sys.platform == "linux":
        try:
            result = subprocess.run([
                "secret-tool", "lookup",
                "service", APP_NAME,
                ], capture_output=True, text=True, check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "[]"

    if sys.platform == "win32":
        try:
            cred = win32cred.CredRead(
                f"{APP_NAME} profiles",
                win32cred.CRED_TYPE_GENERIC,
            )
            return str(cred["CredentialBlob"].decode("utf-16le"))
        except pywintypes.error:
            return "[]"

    if sys.platform == "darwin":
        try:
            result = subprocess.run([
                "security", "find-generic-password",
                "-s", APP_NAME,
                "-w",
                ], capture_output=True, text=True, check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "[]"


def save_secret(profiles):
    """Save profiles to system keyring"""
    if sys.platform == "linux":
        try:
            subprocess.run([
                "secret-tool", "store",
                "--label=" + f"{APP_NAME} profiles",
                "service", APP_NAME,
                ], input=profiles.encode(), check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"secret-tool error: {e}")

    elif sys.platform == "win32":
        try:
            win32cred.CredWrite({
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": f"{APP_NAME} profiles",
                "CredentialBlob": profiles,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            }, 0)
        except pywintypes.error as e:
            sys.exit(e)


    elif sys.platform == "darwin":
        subprocess.run([
            "security", "add-generic-password",
            "-s", APP_NAME,
            "-a", "profiles",
            "-w", profiles,
            "-U",
            ], check=True,
        )


def remove_secret():
    """Remove profiles from system keyring"""
    if sys.platform == "linux":
        try:
            subprocess.run([
                "secret-tool", "clear",
                "service", APP_NAME,
                ], check=True,
            )
        except subprocess.CalledProcessError:
            pass

    elif sys.platform == "win32":
        try:
            win32cred.CredDelete(
                f"{APP_NAME} profiles",
                win32cred.CRED_TYPE_GENERIC,
                0,
            )
        except pywintypes.error:
            pass

    elif sys.platform == "darwin":
        subprocess.run([
            "security", "delete-generic-password",
            "-s", APP_NAME,
            ], check=True,
        )


def load_plain(profiles_path):
    """Load profiles from plaintext file"""
    path = os.path.expanduser(profiles_path)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        logger.warning("Invalid profiles.json file")
        return []


def save_plain(profiles, profiles_path):
    """Save profiles to plaintext file"""
    path = os.path.expanduser(profiles_path)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(profiles, f, indent=2)


def remove_plain(profiles_path):
    """Remove profiles from plaintext file"""
    path = os.path.expanduser(profiles_path)
    if os.path.exists(path):
        os.remove(path)


def get_prompt_y(width, text):
    """Get prompt y position from length of text and terminal width"""
    lines = text.split("\n")
    used_lines = len(lines)
    for line in lines:
        used_lines += len(line) // width
    return used_lines - 3


def pad_name(name, date, source, w):
    """Add spaces to name so string always fits max width"""
    text = f" {name} {date:<{20}} {source:<{12}} "
    extra_spaces = w - len(text)
    if extra_spaces > 0:
        name = name + " " * extra_spaces
    return f" {name} {date:<{20}} {source:<{12}} "


def convert_time(unix_time):
    """Convert unix time to current time"""
    if unix_time:
        time_obj = datetime.fromtimestamp(unix_time)
        time_obj = time_obj.astimezone()
        return datetime.strftime(time_obj, "%Y.%m.%d %H:%M")
    return "Unknown"


def draw_buttons(screen, selected, y, w):
    """Draw buttons"""
    # build button strings
    buttons = []
    for button in ("Load", "Add", "Edit", "Delete", "Quit"):
        buttons.append(f"[{button.center(8)}]")

    raw_str = "  ".join(buttons)
    total_len = len(raw_str)
    start_x = max((w - total_len) // 2, 0)

    # draw buttons
    x = start_x
    for num, button in enumerate(buttons):
        if num == selected:
            screen.addstr(y, x, button, curses.color_pair(1) | curses.A_STANDOUT)
        else:
            screen.addstr(y, x, button)
        x += len(button) + 2


def main_tui(screen, profiles_enc, profiles_plain, selected, have_keyring):
    """Main profile manager tui"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.init_pair(1, -1, -1)

    screen.bkgd(" ", curses.color_pair(1))
    screen.addstr(1, 0, MANAGER_TEXT, curses.color_pair(1))
    if not have_keyring:
        screen.addstr(3, 0, NO_KEYRING_TEXT, curses.color_pair(1))

    profiles = [
        {**p, "source": "keyring"} for p in profiles_enc
    ] + [
        {**p, "source": "plaintext"} for p in profiles_plain
    ]
    profiles = sorted(profiles, key=lambda x: x["name"])

    for num, profile in enumerate(profiles):
        if profile["name"] == selected:
            selected_num = num
            break
    else:
        selected_num = 0
    selected_button = 0

    run = True
    proceed = False
    while run:
        regenerate = False
        h, w = screen.getmaxyx()
        title_text = pad_name("Name", "Last used", "Save method", w)
        screen.addstr(4, 0, title_text, curses.color_pair(1) | curses.A_STANDOUT)

        y = 5
        for num, profile in enumerate(profiles):
            date = convert_time(profile["time"])
            text = pad_name(profile["name"], date, profile["source"], w)
            if num == selected_num:
                screen.addstr(y, 0, text, curses.color_pair(1) | curses.A_STANDOUT)
            else:
                screen.addstr(y, 0, text, curses.color_pair(1))
            y += 1
        draw_buttons(screen, selected_button, h-1, w)

        key = screen.getch()
        if key == 27:   # ecape key
            break
        if key == 10:   # ENTER
            if selected_button == 0 and profiles:   # LOAD
                proceed = True
                selected = profiles[selected_num]["name"]
                break
            elif selected_button == 1:   # ADD
                profile, add = manage_profile(screen, have_keyring)
                screen.clear()
                if add:
                    enc_source = profile.pop("source") == "keyring"
                    if enc_source:
                        for num, profile_s in enumerate(profiles_enc):
                            if profile_s["name"] == profile["name"]:
                                profiles_enc[num] = profile
                        else:
                            profiles_enc.append(profile)
                    else:
                        for num, profile_s in enumerate(profiles_plain):
                            if profile_s["name"] == profile["name"]:
                                profiles_plain[num] = profile
                        else:
                            profiles_plain.append(profile)
                regenerate = True
            elif selected_button == 2 and profiles:   # EDIT
                enc_source = profiles[selected_num]["source"] == "keyring"
                for num, profile in enumerate(profiles_enc if enc_source else profiles_plain):
                    if profile["name"] ==  profiles[selected_num]["name"]:
                        break
                profile, edit = manage_profile(screen, have_keyring, editing_profile=profiles[selected_num])
                screen.clear()
                if edit:
                    profile.pop("source")
                    if enc_source:
                        profiles_enc[num] = profile
                    else:
                        profiles_plain[num] = profile
                regenerate = True
            elif selected_button == 3 and profiles:   # DELETE
                profiles_enc, profiles_plain, deleted = delete_profile(screen, profiles_enc, profiles_plain, profiles[selected_num])
                screen.clear()
                if deleted and selected_num > 0:
                    selected_num -=1
                regenerate = True
            elif selected_button == 4:   # QUIT
                break
        elif key == curses.KEY_UP:
            if selected_num > 0:
                selected_num -= 1
        elif key == curses.KEY_DOWN:
            if selected_num < len(profiles) - 1:
                selected_num += 1
        elif key == curses.KEY_LEFT:
            if selected_button > 0:
                selected_button -= 1
        elif key == curses.KEY_RIGHT:
            if selected_button < 4:
                selected_button += 1
        elif key == curses.KEY_RESIZE:
            regenerate = True

        if regenerate:
            screen.bkgd(" ", curses.color_pair(1))
            screen.addstr(1, 0, MANAGER_TEXT, curses.color_pair(1))
            if not have_keyring:
                screen.addstr(3, 0, NO_KEYRING_TEXT, curses.color_pair(1))
            profiles = [
                {**p, "source": "keyring"} for p in profiles_enc
            ] + [
                {**p, "source": "plaintext"} for p in profiles_plain
            ]
            profiles = sorted(profiles, key=lambda x: x["name"])

        screen.refresh()

    screen.clear()
    screen.refresh()

    return profiles_enc, profiles_plain, selected, proceed


def manage_profile(screen, have_keyring, editing_profile=None):
    """Wrapper around 3 steps for adding/editing profile"""
    profile = {
        "name": None,
        "time": None,
        "token": None,
        "source": "plaintext",
    }
    if editing_profile:
        profile = editing_profile

    step = 0
    run = True
    while run:
        if step == 0:   # name
            name, proceed = text_prompt(screen, NAME_PROMPT_TEXT, "PROFILE NAME: ", init=profile["name"])
            if proceed:
                if not name:
                    name = "Default"
                profile["name"] = name
                step += 1
            else:
                return profile, False
        elif step == 1:   # token
            token, proceed = text_prompt(screen, TOKEN_PROMPT_TEXT, "TOKEN: ", mask=True)
            if proceed:
                if token:
                    profile["token"] = token
                    if not have_keyring or editing_profile:   # skip asking for source
                        return profile, True
                    step += 1
            else:
                step -= 1
        elif step == 2:   # source
            source, proceed = source_prompt(screen)
            if source:
                profile["source"] = "plaintext"
            else:
                profile["source"] = "keyring"
            if proceed:
                return profile, True
            step -= 1


def delete_profile(screen, profiles_enc, profiles_plain, selected_profile):
    """Yes/No window asking to delete specified profile"""
    screen.clear()
    selected_name = selected_profile["name"]
    enc_source = selected_profile["source"] == "keyring"
    for num, profile in enumerate(profiles_enc if enc_source else profiles_plain):
        if profile["name"] == selected_name:
            break

    run = True
    while run:
        h, w = screen.getmaxyx()
        text = f"Are you sure you want to delete {profile["name"]} profile? Enter/Y / Esc/N"
        text = text.center(w)
        screen.addstr(int(h/2), 0, text, curses.color_pair(1) | curses.A_STANDOUT)

        key = screen.getch()
        if key == 27 or key == 110:   # ESCAPE / N
            return profiles_enc, profiles_plain, False
        if key == 10 or key == 121:   # ENTER / Y
            if enc_source:
                profiles_enc.pop(num)
            else:
                profiles_plain.pop(num)
            return profiles_enc, profiles_plain, True


def text_prompt(screen, description_text, prompt, init=None, mask=False):
    """Prompt to type/paste text"""
    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    screen.addstr(1, 0, description_text, curses.color_pair(1))
    _, w = screen.getmaxyx()
    prompt_y = get_prompt_y(w, description_text)
    if init:
        text = init
    else:
        text = ""
    prompt_len = len(prompt) + 2
    if mask:
        dots = "•" * len(text[:w-prompt_len])
        screen_text = prompt + dots + " " * (w - len(text)-prompt_len)
    else:
        screen_text = prompt + text[:w-prompt_len] + " " * (w - len(text)-prompt_len)
    screen.addstr(prompt_y, 1, screen_text, curses.color_pair(1) | curses.A_STANDOUT)
    run = True
    proceed = False
    while run:
        key = screen.getch()

        if key == 27:
            screen.nodelay(True)
            key = screen.getch()
            if key == -1:
                # escape key
                screen.nodelay(False)
                break
            sequence = [27, key]
            while key != -1:
                key = screen.getch()
                sequence.append(key)
                if key == 126:
                    break
                if key == 27:   # holding escape key
                    sequence.append(-1)
                    break
            screen.nodelay(False)
            if sequence[-1] == -1 and sequence[-2] == 27:
                break

        if key == 10:   # ENTER
            proceed = True
            break

        if isinstance(key, int) and 32 <= key <= 126:
            text += chr(key)

        if key == BACKSPACE:
            text = text[:-1]

        _, w = screen.getmaxyx()
        if mask:
            dots = "•" * len(text[:w-prompt_len])
            screen_text = prompt + dots + " " * (w - len(text)-prompt_len)
        else:
            screen_text = prompt + text[:w-prompt_len] + " " * (w - len(text)-prompt_len)
        screen.addstr(prompt_y, 1, screen_text, curses.color_pair(1) | curses.A_STANDOUT)
        screen.refresh()

    screen.clear()
    screen.refresh()

    return text.strip(), proceed


def source_prompt(screen):
    """Prompt to select save method"""
    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    h, w = screen.getmaxyx()
    for num, line in enumerate(SOURCE_PROMPT_TEXT):
        screen.addstr(num+1, 0, line.center(w), curses.color_pair(1))
    run = True
    proceed = False
    selected_num = 0
    while run:
        y = len(SOURCE_PROMPT_TEXT) - 3
        h, w = screen.getmaxyx()
        for num, option in enumerate(("Keyring", "Plaintext")):
            text = option.center(11)
            x_gap = (w - 11) // 2
            if num == selected_num:
                screen.addstr(y, x_gap, text, curses.color_pair(1) | curses.A_STANDOUT)
            else:
                screen.addstr(y, x_gap, text, curses.color_pair(1))
            y += 1

        key = screen.getch()

        if key == 27:   # ESCAPE
            break
        elif key == 10:   # ENTER
            proceed = True
            break
        elif key == curses.KEY_UP:
            if selected_num > 0:
                selected_num -= 1
        elif key == curses.KEY_DOWN:
            if selected_num < 1:
                selected_num += 1

        _, w = screen.getmaxyx()
        screen.refresh()

    screen.clear()
    screen.refresh()

    return selected_num, proceed



def update_time(profiles_enc, profiles_plain, profile_name):
    """Update time for selected profile"""
    for profile in profiles_enc:
        if profile["name"] == profile_name:
            profile["time"] = int(time.time())
            return
    for profile in profiles_plain:
        if profile["name"] == profile_name:
            profile["time"] = int(time.time())
            return


def manage(profiles_path, external_selected, force_open=False):
    """Manage and return profiles and selected profile"""
    have_keyring = True
    if sys.platform == "linux" and not shutil.which("secret-tool"):
        have_keyring = False
        logger.warning("Cant use keyring: 'libsecret' package is not installed")

    selected = None
    if have_keyring:
        profiles_enc = load_secret()
        try:
            profiles_enc = json.loads(profiles_enc)
        except json.JSONDecodeError:
            remove_secret()   # failsafe for remnants of old save method
            profiles_enc = None
        if not profiles_enc:
            profiles_enc = []
        else:
            selected = profiles_enc["selected"]
            profiles_enc = profiles_enc["profiles"]
    else:
        profiles_enc = []
    profiles_plain = load_plain(profiles_path)
    if profiles_plain:
        if not selected:
            selected = profiles_plain["selected"]
        profiles_plain = profiles_plain["profiles"]

    if external_selected:
        selected = external_selected

    if (bool(profiles_enc) or bool(profiles_plain)) and selected is not None and not force_open:
        update_time(profiles_enc, profiles_plain, selected)
        if have_keyring:
            save_secret(json.dumps({"selected": selected, "profiles": profiles_enc}))
        save_plain({"selected": selected, "profiles": profiles_plain}, profiles_path)
        profiles = {
            "selected": selected,
            "keyring": profiles_enc,
            "plaintext": profiles_plain,
        }
        return profiles, selected, True


    # if no profiles and have working keyring
    if sys.platform == "linux" and not (bool(profiles_enc) or bool(profiles_plain)) and have_keyring:
        setup_secret_service()

    try:
        data = curses.wrapper(main_tui, profiles_enc, profiles_plain, selected, have_keyring)
        if not data:
            sys.exit()
        else:
            profiles_enc, profiles_plain, selected, proceed = data
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            logger.error(e)
            sys.exit("Curses error, see log for more info")
        proceed = False

    if (bool(profiles_enc) or bool(profiles_plain)):
        if proceed:
            update_time(profiles_enc, profiles_plain, selected)
        if have_keyring:
            save_secret(json.dumps({"selected": selected, "profiles": profiles_enc}))
        save_plain({"selected": selected, "profiles": profiles_plain}, profiles_path)
        profiles = {
            "selected": selected,
            "keyring": profiles_enc,
            "plaintext": profiles_plain,
        }
        return profiles, selected, proceed
    return None, None, False


def refresh_token(new_token, profile_name, profiles_path):
    """Refresh token for specified profile in keyring and plaintext"""
    try:
        profiles_enc = load_secret()
        profiles_enc = json.loads(profiles_enc)
        if profiles_enc:
            profiles_enc = profiles_enc["profiles"]
        else:
            profiles_enc = []
    except Exception:
        profiles_enc = []

    profiles_plain = load_plain(profiles_path)
    if profiles_plain:
        profiles_plain = profiles_plain["profiles"]

    for profile in profiles_enc:
        if profile["name"] == profile_name:
            profile["token"] = new_token
            logger.info(f"Token refreshed for profile {profile_name}")
            break
    else:
        for profile in profiles_plain:
            if profile["name"] == profile_name:
                profile["token"] = new_token
                logger.info(f"Token refreshed for profile {profile_name}")
                break
        else:
            logger.info(f"Failed refreshing token for profile {profile_name}")
            return False

    if profiles_enc:
        save_secret(json.dumps({"selected": profile_name, "profiles": profiles_enc}))
    if profiles_plain:
        save_plain({"selected": profile_name, "profiles": profiles_plain}, profiles_path)

    return True
