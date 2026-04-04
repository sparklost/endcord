import curses
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

if sys.platform == "win32":
    import pywintypes
    import win32cred
    BACKSPACE = 8
else:
    BACKSPACE = curses.KEY_BACKSPACE
if sys.platform.startswith("android"):
    sys.platform = "linux"
if "bsd" in sys.platform:
    sys.platform = "linux"

try:
    import __main__
    APP_NAME = __main__.APP_NAME   # set in main.py
except (AttributeError, NameError):
    APP_NAME = "endcord"

PART_HINT = "Enter to confirm, Esc to go back."
FULL_HINT = PART_HINT[:-1] + ", Up/Down to select."
CHECK_LOG = " Check log for more info."
MANAGER_TEXT = """ Select or add your profile here. See readme for more info.
 Or just press "Add" button. Use "--manager" flag to show this again."""
NO_KEYRING_TEXT = " Keyring is not installed or is not working properly, see log for more info."
NAME_PROMPT_TEXT = f""" Profile name is just an identifier for tokens for different accounts.
 Profiles are useful to quickly switch between multiple accounts.
 If you are going to use one account, type anything here, or leave it blank.
 If this profile name is same as other profile, the old one will be replaced.

 Profile name can be typed/pasted here (with Ctrl+Shift+V on most terminals):



 {PART_HINT}
"""
TOKEN_PROMPT_TEXT = f""" Token is required to access Discord through your account without logging-in.

 Obtaining your token:
 1. Open Discord in browser.
 2. Open developer tools ('F12' or 'Ctrl+Shift+I' on Chrome and Firefox).
 3. Go to the 'Network' tab then refresh the page.
 4. In the 'Filter URLs' text box, search 'discord.com/api'.
 5. Click on any filtered entry. On the right side, switch to 'Header' tab, look for 'Authorization'.
 6. Copy value of 'Authorization: ...' found under 'Request Headers' (right click -> Copy Value)
 7. This is your discord token. DO NOT SHARE IT!

 Token can be typed/pasted here (with Ctrl+Shift+V on most terminals):



 {PART_HINT}
"""
SOURCE_PROMPT_TEXT = (
    "Select where to save token:",
    "Keyring is secure encrypted storage provided by the OS - recommended,",
    "Plaintext means it will be saved as 'profiles.json' file in endcord config directory",
    "", "", "", "",
    FULL_HINT,
)
SOURCE_PROMPT_OPTIONS = ("Keyring", "Plaintext")
SELECT_LOGIN_TEXT = (
    "Select login method:",
    "", "", "", "", "",
    FULL_HINT,
)
LOGIN_OPTIONS = (
    "Paste token (recommended)",
    "Email/phone number and password",
    "Scan QR code",
)
EMAIL_LOGIN_TEXT = f""" Login with your email/phone number and password.

 Credentials will never be saved to disk.
 They are only used to obtain token which will be stored for later use.
 WARNING: It is recomended to use "Paste token" method.
 Verify on github issues and with other custom clients that there are no recent
 reports of discord banning accouts for using login with email.
 If using phone number, it must be E.164-formatted (the one with + at the start).





 {FULL_HINT}
"""
UNABLE_LOGIN_TEXT = "Unable to login:"
ANY_KEY_TEXT = "Press any key to go back."
SMS_PROMPT_TEXT = f"""SMS with verification code has been sent to:

Enter the code here to verify your login.



 {PART_HINT}
"""
EMAIL_PROMPT_TEXT = f"""Email with verification code has been sent to:

Enter the code here to verify your login.



 {PART_HINT}
"""
FAILED_AUTH_IP_TEXT = ("Failed to authorize this IP address." + CHECK_LOG, "", ANY_KEY_TEXT)
NETWORK_ERROR_TEXT = ("Network error occured.", "", ANY_KEY_TEXT)
SELECT_MFA_TEXT = (
    "Select multi-factor authentication method:",
    "", "", "", "",
    FULL_HINT,
)
TOTP_PROMPT_TEXT = f""" Enter the code fron your TOTP authentucator app here.



 {PART_HINT}
"""
CAPTCHA_REQUIRED_TEXT = (UNABLE_LOGIN_TEXT, "Captcha is required.", "Login with official client first over this IP, then try again.", "", ANY_KEY_TEXT)
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
    if sys.platform == "linux":
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(profiles, f, indent=2)
    else:
        with open(path, "w") as f:
            json.dump(profiles, f, indent=2)


def remove_plain(profiles_path):
    """Remove profiles from plaintext file"""
    path = os.path.expanduser(profiles_path)
    if os.path.exists(path):
        os.remove(path)


def get_prompt_y(width, text, index_back):
    """Get prompt y position from length of text and terminal width"""
    lines = text.split("\n")
    used_lines = len(lines)
    for line in lines:
        used_lines += len(line) // width
    return max(used_lines - index_back, 0)


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


def init_auth(config):
    """Initialize discord authentication API class"""
    from endcord import auth, client_properties
    if config["client_properties"].lower() == "anonymous":
        client_prop = client_properties.get_anonymous_properties()
    else:
        client_prop = client_properties.get_default_properties()
    if config["custom_user_agent"]:
        client_prop = client_properties.add_user_agent(client_prop, config["custom_user_agent"])
    user_agent = client_prop["browser_user_agent"]
    client_prop = client_properties.encode_properties(client_prop)
    logger.debug(f"User-Agent: {user_agent}")
    return auth.Discord(
        config["custom_host"],
        client_prop,
        user_agent,
        proxy=config["proxy"],
    )


def main_tui(screen, profiles_enc, profiles_plain, selected, have_keyring, config):
    """Main profile manager tui"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.init_pair(1, -1, -1)
    curses.init_pair(2, -1, 241)

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
                profile, add = manage_profile(screen, have_keyring, config)
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
                profile, edit = manage_profile(screen, have_keyring, config, editing_profile=profiles[selected_num])
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


def manage_profile(screen, have_keyring, config, editing_profile=None):
    """Wrapper around many steps for adding/editing profile"""
    profile = {
        "name": "",
        "time": None,
        "token": "",
        "source": "plaintext",
    }
    email = ""
    password = ""
    if editing_profile:
        profile = editing_profile

    step = 0
    step_history = []

    def set_step(new_step, mem=True):
        nonlocal step
        if mem and (not step_history or new_step != step_history[-1]):
            step_history.append(step)
        if not mem and step_history:
            step_history.pop(-1)
        step = new_step

    def prev_step():
        nonlocal step
        step = step_history.pop(-1)

    set_step(1)
    discord_auth = None
    run = True
    skip_login_prompt = False
    data = {}
    while run:
        if step == 1:   # profile name
            name, proceed = text_prompt(screen, NAME_PROMPT_TEXT, ("PROFILE NAME: ", ), init=(profile["name"], ), prompt_idx_back=3)
            if proceed:
                if not name:
                    name = "Default"
                profile["name"] = name
                set_step(2)
            else:
                return profile, False

        elif step == 2:   # select login method
            # 100 - paste token, 200 - email and, 300 - qr code
            selection, proceed = select_prompt(screen, SELECT_LOGIN_TEXT, LOGIN_OPTIONS, -4)
            if proceed:
                set_step((selection + 1) * 100)
            else:
                prev_step()

        elif step == 100:   # token login
            token, proceed = text_prompt(screen, TOKEN_PROMPT_TEXT, ("TOKEN: ", ), mask=(True, ), init=(profile["token"], ), prompt_idx_back=3)
            if proceed:
                if token:
                    profile["token"] = token
                    set_step(900)
            else:
                prev_step()

        elif step == 200:   # email login
            proceed = True
            if not skip_login_prompt:
                texts, proceed = text_prompt(
                    screen,
                    EMAIL_LOGIN_TEXT,
                    ("EMAIL: ", "PASSWORD: "),
                    init=(email, password),
                    mask=(False, True),
                    prompt_idx_back=5,
                )
                email, password = texts
            if proceed:
                if email and password:
                    if not discord_auth:
                        discord_auth = init_auth(config)
                    status, data = discord_auth.login(email, password)
                    if status == 0 and data:   # success
                        profile["token"] = data
                        return profile, True
                    if status == 1:   # mfa required
                        set_step(210)
                    elif status == 2:   # wrong password
                        key_prompt(screen, (UNABLE_LOGIN_TEXT, "Login or password is invalid", "", ANY_KEY_TEXT))
                    elif status == 3:   # verify phone number
                        trying = True
                        while trying:
                            sms_code = ""
                            sms_prompt = SMS_PROMPT_TEXT[:45] + email + SMS_PROMPT_TEXT[45:]
                            sms_code, proceed = text_prompt(screen, sms_prompt, ("SMS CODE: ", ), init=(sms_code, ), prompt_idx_back=3)
                            if not proceed:
                                break
                            if not sms_code:
                                continue
                            phone_status, phone_token = discord_auth.phone_verify(email, sms_code)
                            if phone_status == 1:
                                key_prompt(screen, ("Phone number is invalid or wrong." + CHECK_LOG, "", ANY_KEY_TEXT))
                                continue
                            elif phone_status == 2:
                                key_prompt(screen, NETWORK_ERROR_TEXT)
                                continue
                            success = discord_auth.authorize_ip(phone_token)
                            if success:
                                break
                            else:
                                key_prompt(screen, FAILED_AUTH_IP_TEXT)
                        skip_login_prompt = True
                    elif status == 4:   # verify email
                        trying = True
                        while trying:
                            email_url = ""
                            email_prompt = EMAIL_PROMPT_TEXT[:47] + email + EMAIL_PROMPT_TEXT[47:]
                            email_url, proceed = text_prompt(screen, email_prompt, ("URL: ", ), init=(email_url, ), prompt_idx_back=3)
                            if not proceed:
                                break
                            if not sms_code:
                                continue
                            match = re.search(r"token=([^&#]+)", email_url.strip())
                            if match:
                                email_token = match.group(1)
                            else:
                                key_prompt(screen, ("Provided url is invalid", "", ANY_KEY_TEXT))
                                continue
                            status = discord_auth.authorize_ip(email_token)
                            if status == 1:
                                key_prompt(screen, FAILED_AUTH_IP_TEXT)
                            elif status == 2:
                                key_prompt(screen, NETWORK_ERROR_TEXT)
                            else:
                                break
                        skip_login_prompt = True
                    elif status == 5:   # account suspended
                        key_prompt(screen, (UNABLE_LOGIN_TEXT, "Account is suspended.", "", ANY_KEY_TEXT))
                    elif status == 6:   # account disabled/marked for deletion
                        key_prompt(screen, (UNABLE_LOGIN_TEXT, "Account is disabled or marked for deletion.", "", ANY_KEY_TEXT))
                    elif status == 7:   # captha required
                        key_prompt(screen, CAPTCHA_REQUIRED_TEXT)
                    elif status == 8:   # network error
                        key_prompt(screen, (UNABLE_LOGIN_TEXT, "Network error occured, check logfor more info.", "", ANY_KEY_TEXT))
            else:
                prev_step()

        elif step == 210:   # select mfa
            mfa_options = data.get("mfa_options", [])
            selection, proceed = select_prompt(screen, SELECT_MFA_TEXT, mfa_options, -3)
            if proceed:
                if mfa_options[selection] == "SMS":
                    set_step(211)
                elif mfa_options[selection] == "TOTP":
                    set_step(212)
            else:
                prev_step()

        elif step == 211:   # sms mfa
            proceed = key_prompt(screen, "Enter to send SMS with verification code, Esc to go back.", enter=True)
            if not proceed:
                set_step(210)
                continue
            status, phone_number = discord_auth.send_mfa_sms(data.get("ticket"))
            if status == 1:
                key_prompt(screen, ("Failed to authorize this ip address." + CHECK_LOG, "", ANY_KEY_TEXT))
                set_step(210)
                continue
            elif status == 2:
                key_prompt(screen, NETWORK_ERROR_TEXT)
                set_step(210)
                continue
            sms_prompt = SMS_PROMPT_TEXT[:45] + phone_number + SMS_PROMPT_TEXT[45:]
            sms_code = ""
            trying = True
            while trying:
                sms_code, proceed = text_prompt(screen, sms_prompt, ("SMS CODE: ", ), init=(sms_code, ), prompt_idx_back=3)
                if not proceed:
                    set_step(210)
                    break
                status, token = discord_auth.verify_mfa("sms", sms_code, data.get("login_instance_id"), data.get("ticket"))
                if status == 1:
                    key_prompt(screen, ("Failed to authorize this ip address." + CHECK_LOG, "", ANY_KEY_TEXT))
                    set_step(210)
                elif status == 2:
                    key_prompt(screen, NETWORK_ERROR_TEXT)
                    set_step(210)
                if token:
                    profile["token"] = token
                    set_step(900)
                    break

        elif step == 212:   # totp mfa
            totp_code = ""
            trying = True
            while trying:
                totp_code, proceed = text_prompt(screen, TOTP_PROMPT_TEXT, ("TOTP CODE: ", ), init=(totp_code, ), prompt_idx_back=3)
                if not proceed:
                    set_step(210)
                    break
                status, token = discord_auth.verify_mfa("totp", totp_code, data.get("login_instance_id"), data.get("ticket"))
                if token:
                    profile["token"] = token
                    set_step(900)
                    break

        elif step == 300:   # qr code login
            from endcord import auth, client_properties, terminal_qr, terminal_utils
            discord_auth = init_auth(config)
            if config["custom_user_agent"]:
                user_agent =  config["custom_user_agent"]
            else:
                user_agent = client_properties.get_user_agent(anonymous=(config["client_properties"].lower() == "anonymous"))
            gateway_auth = auth.Gateway(user_agent, proxy=config["proxy"])
            success = gateway_auth.connect()
            if not success:
                key_prompt(screen, NETWORK_ERROR_TEXT)
                gateway_auth.disconnect_ws()
                set_step(2, mem=False)
                continue
            run = True
            drawing = False
            esc_detector = None
            while run:
                if drawing and not esc_detector.is_alive():
                    set_step(2, mem=False)
                    break
                state = gateway_auth.get_state()
                timeout = gateway_auth.get_remaining_time()
                timeout_text = f"Session will timeout in: {f"{(timeout) // 60}m {timeout % 60} s"}"
                if state != 1 and drawing:
                    terminal_utils.stop_esc_detector()
                    terminal_utils.leave_tui()
                    resume_curses(screen)
                    drawing = False
                if state == 0:   # wait
                    pass
                elif state == 1:   # have fingerprint
                    fingerprint = gateway_auth.get_fingerprint()
                    url = f"https://{discord_auth.host}/ra/{fingerprint}"
                    if not drawing:
                        pause_curses()
                        terminal_utils.enter_tui()
                        if not esc_detector or not esc_detector.is_alive():
                            esc_detector = threading.Thread(target=terminal_utils.esc_detector, daemon=True)
                            esc_detector.start()
                        drawing = True
                elif state == 2:   # pending ticket
                    user_id, username = gateway_auth.get_user()
                    text = f"Waiting for verification. \nUsername:{username}\nUser ID: {user_id}\n " + timeout_text
                    draw_text(screen, text, center=True)
                elif state == 3:   # success
                    ticket = gateway_auth.get_ticket()
                    status, encrypted_token = discord_auth.exchange_ticket(ticket)
                    if status == 1:
                        key_prompt(screen, ("Failed to exchange ticket." + CHECK_LOG, "", ANY_KEY_TEXT))
                        set_step(2, mem=False)
                        break
                    elif status == 2:
                        key_prompt(screen, CAPTCHA_REQUIRED_TEXT)
                        set_step(2, mem=False)
                        break
                    elif status == 3:
                        key_prompt(screen, NETWORK_ERROR_TEXT)
                        set_step(2, mem=False)
                        break
                    token = gateway_auth.decrypt_token(encrypted_token)
                    profile["token"] = token
                    gateway_auth.disconnect_ws()
                    return profile, True
                elif state in (4, 5, 6):   # network error, timeout, cancel
                    if state == 4:
                        text = NETWORK_ERROR_TEXT
                    elif state == 5:
                        text = ("Authentication session timed out. Try again.", "", ANY_KEY_TEXT)
                    elif state == 6:
                        text = ("Authentication session has been canceled. Try again.", "", ANY_KEY_TEXT)
                    else:
                        text = "unknown"
                    key_prompt(screen, text)
                    gateway_auth.disconnect_ws()
                    set_step(2, mem=False)
                    break
                if drawing:
                    text_above = "Scan this QR code with your phone to login:"
                    text_bellow = url + "\n\n" + timeout_text + "\nEsc to go back."
                    _, string = terminal_qr.gen_qr_terminal_string(url, text_above, text_bellow)
                    terminal_utils.draw(string)
                time.sleep(0.1)
            terminal_utils.stop_esc_detector()

        elif step == 900:   # save method
            if not have_keyring or editing_profile:   # skip asking for source
                return profile, True
            source, proceed = select_prompt(screen, SOURCE_PROMPT_TEXT, SOURCE_PROMPT_OPTIONS, -3)
            if source:
                profile["source"] = "plaintext"
            else:
                profile["source"] = "keyring"
            if proceed:
                return profile, True
            prev_step()


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


def text_prompt(screen, description_text, prompts, init=None, mask=None, prompt_idx_back=0, spacing=1):
    """Prompt to type/paste multiple text lines"""

    if init is None:
        init = [""] * len(prompts)
    if mask is None:
        mask = [False] * len(prompts)

    texts = list(init)
    selected = 0

    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    screen.addstr(1, 0, description_text, curses.color_pair(1))

    _, w = screen.getmaxyx()
    base_y = get_prompt_y(w, description_text, prompt_idx_back)
    input_index = len(texts[selected])

    def draw():
        for i, prompt in enumerate(prompts):
            y = base_y + i * (1 + spacing)
            prompt_len = len(prompt) + 2
            text = texts[i]
            if mask[i]:
                dots = "•" * len(text[:w - prompt_len])
                line = prompt + dots
            else:
                line = prompt + text[:w - prompt_len]
            line += " " * (w - len(line) - 1)
            if i == selected:
                screen.addstr(y, 1, line, curses.color_pair(1) | curses.A_STANDOUT)
                screen.addch(y, input_index + prompt_len - 1, line[input_index + prompt_len - 2], curses.color_pair(2))
            else:
                screen.addstr(y, 1, line, curses.color_pair(2))    # gray
        screen.refresh()

    draw()

    run = True
    proceed = False
    while run:
        key = screen.getch()

        if key == 27:  # ESC
            screen.nodelay(True)
            key = screen.getch()
            if key == -1:
                screen.nodelay(False)
                break
            sequence = [27, key]
            while key != -1:
                key = screen.getch()
                sequence.append(key)
                if key == 126:
                    break
                if key == 27:
                    sequence.append(-1)
                    break
            screen.nodelay(False)
            if sequence[-1] == -1 and sequence[-2] == 27:
                break

        if key == 10:  # ENTER
            try:
                selected = next(i for i, t in enumerate(texts) if not t.strip())
            except StopIteration:
                proceed = True
                break

        elif key == BACKSPACE or key == 127:
            texts[selected] = texts[selected][:input_index-1] + texts[selected][input_index:]
            input_index -= 1

        if key == curses.KEY_UP:
            selected = max(0, selected - 1)
            input_index = len(texts[selected])

        elif key == curses.KEY_DOWN:
            selected = min(len(prompts) - 1, selected + 1)
            input_index = len(texts[selected])

        elif key == curses.KEY_LEFT and input_index > 0:
            input_index -= 1

        elif key == curses.KEY_RIGHT and input_index < len(texts[selected]):
            input_index += 1

        elif isinstance(key, int) and 32 <= key <= 126:
            texts[selected] = texts[selected][:input_index] + chr(key) + texts[selected][input_index:]
            input_index += 1

        elif key == 9:  # TAB
            selected = (selected + 1) % len(prompts)
            input_index = len(texts[selected])

        draw()

    screen.clear()
    screen.refresh()

    texts = [t.strip() for t in texts]
    if len(texts) == 1:
        texts = texts[0]
    return texts, proceed


def select_prompt(screen, description_text, options, y_offset):
    """Prompt to select from given options"""
    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    h, w = screen.getmaxyx()
    for num, line in enumerate(description_text):
        screen.addstr(num+1, 0, line.center(w), curses.color_pair(1))
    run = True
    proceed = False
    selected_num = 0
    longest = len(max(options, key=len)) + 2
    while run:
        y = len(description_text) + y_offset
        h, w = screen.getmaxyx()
        for num, option in enumerate(options):
            text = option.center(longest)
            x_gap = (w - longest) // 2
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
            if selected_num < len(options) - 1:
                selected_num += 1

        _, w = screen.getmaxyx()
        screen.refresh()

    screen.clear()
    screen.refresh()

    return selected_num, proceed


def key_prompt(screen, description_text, enter=False):
    """Prompt to press enter/esc or any key"""
    screen.clear()
    screen.bkgd(" ", curses.color_pair(1))
    _, w = screen.getmaxyx()
    for num, line in enumerate(description_text):
        screen.addstr(num+1, 0, line.center(w), curses.color_pair(1))
    run = True
    proceed = False
    while run:
        key = screen.getch()
        if key and not enter:
            proceed = True
            break
        if key == 27:   # ESCAPE
            break
        elif key == 10:   # ENTER
            proceed = True
            break
    screen.clear()
    screen.refresh()
    return proceed


def draw_text(screen, text, center=False):
    """Draw text on screen, optionally centered"""
    _, w = screen.getmaxyx()
    for num, line in enumerate(text.split("\n")):
        if center:
            screen.addstr(num+1, 0, line.center(w), curses.color_pair(1))
        else:
            screen.addstr(num+1, 0, line, curses.color_pair(1))
    screen.refresh()


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


def pause_curses():
    """Pause curses, releasing terminal"""
    curses.def_prog_mode()
    curses.endwin()


def resume_curses(screen):
    """Resume curses, capturing terminal"""
    curses.reset_prog_mode()
    curses.curs_set(0)
    curses.flushinp()
    screen.refresh()


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


def manage(profiles_path, external_selected, config, force_open=False):
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
        have_keyring = setup_secret_service()

    try:
        data = curses.wrapper(main_tui, profiles_enc, profiles_plain, selected, have_keyring, config)
        if not data:
            sys.exit(0)
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
    if have_keyring:
        save_secret(json.dumps({"selected": selected, "profiles": profiles_enc}))
    save_plain({"selected": selected, "profiles": profiles_plain}, profiles_path)
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
