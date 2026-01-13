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
AUTH_METHOD_PROMPT_TEXT = (
    "Select authentication method:",
    "",
    "",
    "",
    "",
    "",
    "",
    "Enter to confirm, Esc to go back, Up/Down to select",
)
QR_AUTH_TEXT = """ Scan this QR code with the Discord mobile app to log in.

 1. Open Discord on your phone
 2. Go to Settings > Scan QR Code
 3. Point your camera at the code below
 4. Tap 'Yes, log me in' when prompted

 The QR code expires in 2 minutes. Press Esc to cancel.
"""
QR_WAITING_TEXT = " Waiting for confirmation on your phone..."
QR_SCANNED_TEXT = " QR code scanned by: {username}"
SOURCE_PROMPT_TEXT = (
    "Select where to save token:",
    "Keyring is secure encrypted storage provided by the OS - recommended,",
    "Plaintext means it will be saved as 'profiles.json' file in endcord config directory",
    "",
    "",
    "",
    "",
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
                return False
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
            return False

    except subprocess.CalledProcessError:
        logger.warning("Cant use keyring: failed to start gnome-keyring")
        return False

    return True


def load_secret():
    """Try to load profiles from system keyring"""
    if sys.platform == "linux":
        try:
            result = subprocess.run(
                [
                    "secret-tool",
                    "lookup",
                    "service",
                    APP_NAME,
                ],
                capture_output=True,
                text=True,
                check=True,
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
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    APP_NAME,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "[]"


def save_secret(profiles):
    """Save profiles to system keyring"""
    if sys.platform == "linux":
        try:
            subprocess.run(
                [
                    "secret-tool",
                    "store",
                    "--label=" + f"{APP_NAME} profiles",
                    "service",
                    APP_NAME,
                ],
                input=profiles.encode(),
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"secret-tool error: {e}")

    elif sys.platform == "win32":
        try:
            win32cred.CredWrite(
                {
                    "Type": win32cred.CRED_TYPE_GENERIC,
                    "TargetName": f"{APP_NAME} profiles",
                    "CredentialBlob": profiles,
                    "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                },
                0,
            )
        except pywintypes.error as e:
            sys.exit(e)

    elif sys.platform == "darwin":
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s",
                APP_NAME,
                "-a",
                "profiles",
                "-w",
                profiles,
                "-U",
            ],
            check=True,
        )


def remove_secret():
    """Remove profiles from system keyring"""
    if sys.platform == "linux":
        try:
            subprocess.run(
                [
                    "secret-tool",
                    "clear",
                    "service",
                    APP_NAME,
                ],
                check=True,
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
        subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s",
                APP_NAME,
            ],
            check=True,
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

    profiles = [{**p, "source": "keyring"} for p in profiles_enc] + [
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
        draw_buttons(screen, selected_button, h - 1, w)

        key = screen.getch()
        if key == 27:  # ecape key
            break
        if key == 10:  # ENTER
            if selected_button == 0 and profiles:  # LOAD
                proceed = True
                selected = profiles[selected_num]["name"]
                break
            elif selected_button == 1:  # ADD
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
            elif selected_button == 2 and profiles:  # EDIT
                enc_source = profiles[selected_num]["source"] == "keyring"
                profile_index = None
                for num, profile_data in enumerate(profiles_enc if enc_source else profiles_plain):
                    if profile_data.get("name") == profiles[selected_num]["name"]:
                        profile_index = num
                        break
                if profile_index is None:
                    logger.warning("Profile not found during edit")
                    regenerate = True
                    continue
                profile, edit = manage_profile(screen, have_keyring, editing_profile=profiles[selected_num])
                screen.clear()
                if edit:
                    profile.pop("source", None)
                    if enc_source:
                        profiles_enc[profile_index] = profile
                    else:
                        profiles_plain[profile_index] = profile
                regenerate = True
            elif selected_button == 3 and profiles:  # DELETE
                profiles_enc, profiles_plain, deleted = delete_profile(
                    screen, profiles_enc, profiles_plain, profiles[selected_num]
                )
                screen.clear()
                if deleted and selected_num > 0:
                    selected_num -= 1
                regenerate = True
            elif selected_button == 4:  # QUIT
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
            profiles = [{**p, "source": "keyring"} for p in profiles_enc] + [
                {**p, "source": "plaintext"} for p in profiles_plain
            ]
            profiles = sorted(profiles, key=lambda x: x["name"])

        screen.refresh()

    screen.clear()
    screen.refresh()

    return profiles_enc, profiles_plain, selected, proceed


def manage_profile(screen, have_keyring, editing_profile=None):
    """Wrapper around steps for adding/editing profile with QR code or manual token"""
    profile = {
        "name": None,
        "time": None,
        "token": None,
        "source": "plaintext",
    }
    if editing_profile:
        profile = editing_profile

    step = 0
    use_qr_auth = False
    run = True
    while run:
        if step == 0:  # name
            name, proceed = text_prompt(screen, NAME_PROMPT_TEXT, "PROFILE NAME: ", init=profile["name"])
            if proceed:
                if not name:
                    name = "Default"
                profile["name"] = name
                step += 1
            else:
                return profile, False
        elif step == 1:  # auth method selection (skip if editing)
            if editing_profile:
                # When editing, go straight to manual token entry
                step = 2
                use_qr_auth = False
                continue
            auth_method, proceed = auth_method_prompt(screen)
            if proceed:
                use_qr_auth = auth_method == 0
                step += 1
            else:
                step -= 1
        elif step == 2:  # token (QR or manual)
            if use_qr_auth:
                token, proceed = qr_auth_prompt(screen)
                if proceed and token:
                    profile["token"] = token
                    if not have_keyring:  # skip asking for source
                        return profile, True
                    step += 1
                elif not proceed:
                    step -= 1
                else:
                    # QR auth failed but not cancelled, stay on same step to retry or go back
                    step -= 1
            else:
                token, proceed = text_prompt(screen, TOKEN_PROMPT_TEXT, "TOKEN: ", mask=True)
                if proceed:
                    if token:
                        profile["token"] = token
                        if not have_keyring or editing_profile:  # skip asking for source
                            return profile, True
                        step += 1
                else:
                    step -= 1
        elif step == 3:  # source
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
    selected_name = selected_profile.get("name", "Unknown")
    enc_source = selected_profile.get("source") == "keyring"

    profile_index = None
    for num, profile_data in enumerate(profiles_enc if enc_source else profiles_plain):
        if profile_data.get("name") == selected_name:
            profile_index = num
            break

    if profile_index is None:
        logger.warning(f"Profile '{selected_name}' not found for deletion")
        return profiles_enc, profiles_plain, False

    run = True
    while run:
        h, w = screen.getmaxyx()
        text = f"Are you sure you want to delete {selected_name} profile? Enter/Y / Esc/N"
        text = text.center(w)
        screen.addstr(int(h / 2), 0, text, curses.color_pair(1) | curses.A_STANDOUT)

        key = screen.getch()
        if key == 27 or key == 110:  # ESCAPE / N
            return profiles_enc, profiles_plain, False
        if key == 10 or key == 121:  # ENTER / Y
            if enc_source:
                profiles_enc.pop(profile_index)
            else:
                profiles_plain.pop(profile_index)
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
        dots = "•" * len(text[: w - prompt_len])
        screen_text = prompt + dots + " " * (w - len(text) - prompt_len)
    else:
        screen_text = prompt + text[: w - prompt_len] + " " * (w - len(text) - prompt_len)
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
                if key == 27:  # holding escape key
                    sequence.append(-1)
                    break
            screen.nodelay(False)
            if sequence[-1] == -1 and sequence[-2] == 27:
                break

        if key == 10:  # ENTER
            proceed = True
            break

        if isinstance(key, int) and 32 <= key <= 126:
            text += chr(key)

        if key == BACKSPACE:
            text = text[:-1]

        _, w = screen.getmaxyx()
        if mask:
            dots = "•" * len(text[: w - prompt_len])
            screen_text = prompt + dots + " " * (w - len(text) - prompt_len)
        else:
            screen_text = prompt + text[: w - prompt_len] + " " * (w - len(text) - prompt_len)
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
        screen.addstr(num + 1, 0, line.center(w), curses.color_pair(1))
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

        if key == 27:  # ESCAPE
            break
        elif key == 10:  # ENTER
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


def auth_method_prompt(screen):
    """Prompt to select authentication method (QR code or manual token)"""
    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    h, w = screen.getmaxyx()
    for num, line in enumerate(AUTH_METHOD_PROMPT_TEXT):
        screen.addstr(num + 1, 0, line.center(w), curses.color_pair(1))
    run = True
    proceed = False
    selected_num = 0  # 0 = QR Code (recommended), 1 = Manual Token
    options = (
        "QR Code - Scan with Discord app (Recommended)",
        "Manual Token - Copy from browser DevTools",
    )
    opt_width = max(len(o) for o in options) + 4
    while run:
        y = len(AUTH_METHOD_PROMPT_TEXT) - 5
        h, w = screen.getmaxyx()
        for num, option in enumerate(options):
            text = option.center(opt_width)
            x_gap = max(0, (w - opt_width) // 2)
            if num == selected_num:
                screen.addstr(y, x_gap, text, curses.color_pair(1) | curses.A_STANDOUT)
            else:
                screen.addstr(y, x_gap, text, curses.color_pair(1))
            y += 1

        key = screen.getch()

        if key == 27:  # ESCAPE
            break
        elif key == 10:  # ENTER
            proceed = True
            break
        elif key == curses.KEY_UP:
            if selected_num > 0:
                selected_num -= 1
        elif key == curses.KEY_DOWN:
            if selected_num < 1:
                selected_num += 1

        screen.refresh()

    screen.clear()
    screen.refresh()

    return selected_num, proceed  # 0 = QR Code, 1 = Manual


def qr_auth_prompt(screen):
    """Display QR code for authentication and wait for user to scan"""
    try:
        from . import qr_auth
    except ImportError:
        logger.warning("QR auth module not available")
        return None, False

    if not qr_auth.HAVE_CRYPTO:
        screen.clear()
        screen.bkgd(" ", curses.color_pair(1))
        h, w = screen.getmaxyx()
        error_msg = "QR Code login requires 'cryptography' package."
        install_msg = "Install with: pip install cryptography"
        screen.addstr(h // 2 - 1, max(0, (w - len(error_msg)) // 2), error_msg, curses.color_pair(1))
        screen.addstr(h // 2 + 1, max(0, (w - len(install_msg)) // 2), install_msg, curses.color_pair(1))
        screen.addstr(h // 2 + 3, max(0, (w - 20) // 2), "Press any key...", curses.color_pair(1))
        screen.refresh()
        screen.getch()
        return None, False

    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    screen.nodelay(False)

    token = None
    cancelled = False
    qr_displayed = False
    user_scanned = None
    error_msg = None

    # State variables for the auth thread
    state = {"qr_ascii": None, "qr_url": None, "username": None, "token": None, "error": None, "done": False}

    def on_qr_ready(url, ascii_art):
        state["qr_url"] = url
        state["qr_ascii"] = ascii_art

    def on_user_scanned(user_data):
        state["username"] = user_data.get_display_name()

    def on_token_received(tok):
        state["token"] = tok
        state["done"] = True

    def on_error(e):
        state["error"] = str(e)
        state["done"] = True

    # Start auth in a thread
    import threading

    auth_thread = None
    client = qr_auth.RemoteAuthClient()
    client.on_qr_code = on_qr_ready
    client.on_user_data = on_user_scanned
    client.on_token = on_token_received
    client.on_error = on_error

    def run_auth():
        try:
            client.connect_and_wait(timeout=120)
        except qr_auth.QRAuthCancelled:
            pass
        except qr_auth.QRAuthTimeout:
            state["error"] = "QR code expired. Please try again."
        except Exception as e:
            state["error"] = str(e)
        finally:
            state["done"] = True

    auth_thread = threading.Thread(target=run_auth, daemon=True)
    auth_thread.start()

    # Display loop
    screen.nodelay(True)
    run = True
    last_draw = 0

    while run:
        h, w = screen.getmaxyx()

        # Check for escape key
        try:
            key = screen.getch()
            if key == 27:
                cancelled = True
                client.close()
                break
        except Exception:
            pass

        # Redraw periodically
        current_time = time.time()
        if current_time - last_draw > 0.1:  # 10 FPS
            last_draw = current_time
            screen.clear()

            # Draw header text
            lines = QR_AUTH_TEXT.strip().split("\n")
            for num, line in enumerate(lines):
                if num < h - 2:
                    screen.addstr(num + 1, 0, line[: w - 1], curses.color_pair(1))

            qr_start_y = len(lines) + 2

            # Draw QR code if available
            if state["qr_ascii"]:
                qr_lines = state["qr_ascii"].split("\n")
                for num, line in enumerate(qr_lines):
                    y = qr_start_y + num
                    if y < h - 3:
                        x = max(0, (w - len(line)) // 2)
                        try:
                            screen.addstr(y, x, line[: w - 1], curses.color_pair(1))
                        except curses.error:
                            pass

                # Show URL below QR
                url_y = qr_start_y + len(qr_lines) + 1
                if url_y < h - 2 and state["qr_url"]:
                    url_text = f"URL: {state['qr_url']}"
                    x = max(0, (w - len(url_text)) // 2)
                    try:
                        screen.addstr(url_y, x, url_text[: w - 1], curses.color_pair(1))
                    except curses.error:
                        pass

            elif not state["done"]:
                # Loading message
                loading_msg = "Generating QR code..."
                screen.addstr(qr_start_y, max(0, (w - len(loading_msg)) // 2), loading_msg, curses.color_pair(1))

            # Show scanned user
            if state["username"]:
                scanned_msg = QR_SCANNED_TEXT.format(username=state["username"])
                try:
                    screen.addstr(h - 3, 0, scanned_msg[: w - 1], curses.color_pair(1) | curses.A_BOLD)
                    screen.addstr(h - 2, 0, QR_WAITING_TEXT[: w - 1], curses.color_pair(1))
                except curses.error:
                    pass

            # Show error
            if state["error"]:
                try:
                    screen.addstr(h - 2, 0, f" Error: {state['error']}"[: w - 1], curses.color_pair(1))
                except curses.error:
                    # Ignore drawing errors (e.g., when the terminal is too small); not critical to flow.
                    pass

            screen.refresh()

        # Check if auth completed
        if state["done"]:
            if state["token"]:
                token = state["token"]
            break

        time.sleep(0.05)

    # Cleanup
    screen.nodelay(False)

    # Wait for auth thread to complete with timeout
    if auth_thread and auth_thread.is_alive():
        auth_thread.join(timeout=2.0)
        if auth_thread.is_alive():
            logger.warning("Auth thread did not complete in time")

    screen.clear()
    screen.refresh()

    if cancelled:
        return None, False

    return token, token is not None


def update_time(profiles_enc, profiles_plain, profile_name):
    """Update time for selected profile"""
    for profile in profiles_enc:
        if profile.get("name") == profile_name:
            profile["time"] = int(time.time())
            return
    for profile in profiles_plain:
        if profile.get("name") == profile_name:
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
            remove_secret()  # failsafe for remnants of old save method
            profiles_enc = None
        if not profiles_enc:
            profiles_enc = []
        else:
            # Safely extract with validation
            if isinstance(profiles_enc, dict):
                selected = profiles_enc.get("selected")
                profiles_enc = profiles_enc.get("profiles", [])
            else:
                profiles_enc = []
    else:
        profiles_enc = []
    profiles_plain = load_plain(profiles_path)
    if profiles_plain:
        # Safely extract with validation
        if isinstance(profiles_plain, dict):
            if not selected:
                selected = profiles_plain.get("selected")
            profiles_plain = profiles_plain.get("profiles", [])
        else:
            profiles_plain = []

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
        have_keyring = setup_secret_service()

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

    if bool(profiles_enc) or bool(profiles_plain):
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
