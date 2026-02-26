import glob
import logging
import os
import sys
import threading
import time
import traceback

try:
    import orjson as json
except ImportError:
    try:
        import ujson as json
    except ImportError:
        import json

from endcord import peripherals

if sys.platform != "linux":
    import psutil

GAME_DETECTION_DELAY = 5
MAX_CACHE_AGE = 604800   # 7 days
logger = logging.getLogger(__name__)

proc_cache = {}   # pid = [path, alive]


def get_user_processes_diff_linux():
    """
    Get newly added and removed user processes on linux, deduplicated and cached.
    Not using psutil because this is much more efficient.
    """
    added = []
    removed = []

    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue

        # check cache
        if pid in proc_cache:
           proc_cache[pid][1] = True
           continue
        proc_cache[pid] = [None, True]

        # read and check uid
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                uid = None
                for line in f:
                    if line.startswith("Uid:"):
                        uid = int(line.split()[1])
                        break
        except Exception:
            continue
        if uid is None or uid < 1000:
            continue

        # read cmdline
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                # decode only what is needed, not entire file
                cmdline = f.read().partition(b" -")[0].partition(b"\x00-")[0]
                prefix, exe, _ = cmdline.partition(b".exe")
                cmdline = prefix + exe
                if not cmdline:
                    continue
                cmdline = cmdline.decode("utf-8")
        except Exception:
            continue

        # skip libraries and bash
        if cmdline.startswith("/usr/lib") or cmdline.startswith("bash"):
            continue

        # if path doesnt have / or \ its definitely not a game
        path = cmdline.replace("\\", "/").replace("\x00", "")
        if "/" not in path:
            continue

        # cleanup path
        path = path.lower().replace(":", "")

        # add to cache and newly added processes
        proc_cache[pid] = [path, True]
        if path not in added:
            added.append(path)

    # remove all not alive processes, and flip alive status
    for key in list(proc_cache.keys()):
        if not proc_cache[key][1]:
            path = proc_cache[key][0]
            if path and path not in removed:
                removed.append((path))
            del proc_cache[key]
        else:
            proc_cache[key][1] = False

    return added, removed


def get_user_processes_diff_windows():
    """Get newly added and removed user processes on windows, deduplicated and cached"""
    added = []
    removed = []

    # not doing process_iter([...]) because there is caching
    current_username = psutil.Process().username().split("\\")[-1]
    for p in psutil.process_iter():
        pid = p.pid

        # check cache
        if pid in proc_cache:
           proc_cache[pid][1] = True
           continue
        proc_cache[pid] = [None, True]

        # skip system processes
        username = p.username()
        if username is None:
            continue

        # keep only current user
        if username.split("\\")[-1] != current_username:
            continue

        # get cmdline
        cmdline = p.cmdline()
        if not cmdline:
            continue
        cmdline = cmdline[0]

        # skip processes from windows dirs
        if ":\\Windows\\" in cmdline or ":\\Program Files\\WindowsApps\\" in cmdline:
            continue

        # if path doesnt have / or \ its definitely not a game
        path = cmdline.replace("\\", "/").replace("\x00", "")
        if "/" not in path:
            continue

        # cleanup path
        path = path.lower().replace(":", "")

        # add to cache and newly added processes
        proc_cache[pid] = [path, True]
        if path not in added:
            added.append(path)

    # remove all not alive processes, and flip alive status
    for key in list(proc_cache.keys()):
        if not proc_cache[key][1]:
            path = proc_cache[key][0]
            if path and path not in removed:
                removed.append((path))
            del proc_cache[key]
        else:
            proc_cache[key][1] = False

    return added, removed


def get_user_processes_diff_darwin():
    """
    Get newly added and removed user processes on macos, deduplicated and cached.
    Probably wont work but here it is anyways.
    """
    added = []
    removed = []
    user_uid = psutil.Process().uids().real
    for p in psutil.process_iter():
        pid = p.pid

        # check cache
        if pid in proc_cache:
           proc_cache[pid][1] = True
           continue
        proc_cache[pid] = [None, True]

        # check uid
        if user_uid:
            try:
                uid = p.uids().real
            except Exception:
                continue
            if uid != user_uid:
                continue

        # get cmdline
        try:
            cmdline = p.cmdline()
        except Exception:
            continue
        if not cmdline:
            continue
        cmdline = cmdline[0]

        # cleanup path
        path = cmdline.replace("\\", "/").replace("\x00", "")
        path = path.lower().replace(":", "")

        # add to cache and newly added processes
        proc_cache[pid] = [path, True]
        if path not in added:
            added.append(path)


    # remove all not alive processes, and flip alive status
    for key in list(proc_cache.keys()):
        if not proc_cache[key][1]:
            path = proc_cache[key][0]
            if path and path not in removed:
                removed.append((path))
            del proc_cache[key]
        else:
            proc_cache[key][1] = False

    return added, removed


if sys.platform == "linux":
    get_user_processes_diff = get_user_processes_diff_linux
elif sys.platform == "darwin":
    get_user_processes_diff = get_user_processes_diff_darwin
else:
    get_user_processes_diff = get_user_processes_diff_windows


def find_detectable_apps_file(directory):
    """Find detectable_apps_[etag].ndjson path and extract etag"""
    pattern = os.path.expanduser(os.path.join(directory, "detectable_apps_*.ndjson"))
    matches = glob.glob(pattern)
    if not matches:
        return None, None, 0
    path = matches[0]
    filename_parts = os.path.basename(path)[:-7].split("_")
    etag = filename_parts[2]
    save_time = int(filename_parts[3]) * 1000 if len(filename_parts) >= 4 else 0
    return path, etag, save_time


def find_app(proc_path, list_path, my_platform):
    """Search the detectable applications list and find the app"""
    proc_path = proc_path.lower()

    try:
        f = open(list_path, "r", encoding="utf-8")
    except Exception:
        return None, None, None
    try:
        for line in f:
            try:
                app = json.loads(line)   # [id, name, [os, app_path]]
            except Exception:
                continue
            for platform_val, app_path in app[2]:
                if not app_path:
                    continue
                if my_platform == 0:   # linux matches linux(0) and windows(1)
                    if platform_val not in (0, 1):
                        continue
                elif my_platform == 1:   # windows
                    if platform_val != 1:
                        continue
                elif platform_val != 2:   # macos
                    continue
                if app_path in proc_path:
                    return app[0], app[1], app_path[1:]
    finally:
        f.close()
    return None, None, None


class GameDetection:
    """Main game detection class"""

    def __init__(self, app, discord, blacklist, download_delay=7):
        self.app = app
        self.discord = discord
        self.run = True
        self.changed = False
        self.cache = []
        self.activities = []
        self.blacklist = blacklist
        self.download_delay = download_delay * 86400
        threading.Thread(target=self.main, daemon=True, args=()).start()


    def main(self):
        """
        Main thread that:
        - checks and downloads detetcable applications list on startup
        - checks for added/removed application processes
        - detects games
        - builds and stores/removes activity for each game
        - updates activity session
        """
        # get platform
        if sys.platform == "linux":
            platform = 0
        elif sys.platform == "win32":
            platform = 1
        elif sys.platform == "darwin":
            platform = 2
        else:
            logger.warning(f"Game detection service cannot be started on this platform: {sys.platform}")
            return

        # download new detectable apps list if N days passed and resource changed on the server
        old_path, old_etag, save_time = find_detectable_apps_file(peripherals.config_path)
        if self.download_delay == 0 or time.time() - save_time > self.download_delay:
            path, etag = self.discord.get_detectable_apps(peripherals.config_path, old_etag)
            if not path:
                logger.info("Could not start game detection service: failed to download detectable applications list")
                return
            if old_etag != etag:
                logger.info(f'Downloaded new detectable applications list with ETag: W/"{etag}"')
                if old_path:
                    os.remove(old_path)
            del (old_path, old_etag)
        else:
            path = old_path

        # load cached processes and remove outdated
        self.cache = peripherals.load_json("detected_apps_cache.json", {})   # {proc_path: [app_id, app_name, app_path, last_seen]...}
        now = int(time.time())
        outdated = [key for key, val in self.cache.items() if now - val[3] > MAX_CACHE_AGE]
        for key in outdated:
            del self.cache[key]
        del outdated

        # update last seen times in cache
        try:
            added, _ = get_user_processes_diff()
        except BaseException as e:
            logger.error(f"Game detection service stopped because of error:/n{"".join(traceback.format_exception(e))}")
            self.run = False
            return
        global proc_cache
        proc_cache = {}
        for proc_path in added:
            proc = self.cache.get(proc_path)
            if proc:
                self.cache[proc_path][3] = now

        # main loop
        logger.info("Game detection service started")
        cache_changed = True   # to save updated times
        _get_user_processes_diff = get_user_processes_diff
        while self.run:
            try:
                added, removed = _get_user_processes_diff()
            except BaseException as e:
                logger.error(f"Game detection service stopped because of error:/n{"".join(traceback.format_exception(e))}")
                self.run = False
                return

            for proc_path in added:
                proc = self.cache.get(proc_path)
                if proc:
                    app_id, app_name, app_path = proc[0], proc[1], proc[2]
                else:
                    app_id, app_name, app_path = find_app(proc_path, path, platform)
                    self.cache[proc_path] = [app_id, app_name, app_path, int(time.time())]
                    cache_changed = True

                # skip unindentified
                if not app_id:
                    continue
                if app_id in self.blacklist:
                    continue

                # when identified app appears
                # update activity session
                self.discord.update_activity_session(
                    app_id,
                    exe_path=app_path,
                    closed=False,
                    session_id=self.app.session_id,
                    media_session_id=self.app.get_media_session_id(),
                    voice_channel_id=self.app.get_voice_channel_id(),
                )
                # add activity
                self.activities.append({
                    "type": 0,
                    "application_id": app_id,
                    "name": app_name,
                    "timestamps": {"start": int(time.time()*1000)},
                })
                self.changed = True
                logger.info(f"Game added to activities: {app_name}")

            # when identified app disappears
            for proc_path in removed:
                # find app_name and app_path
                data = self.cache.get(proc_path)
                if not data:
                    continue
                app_id, app_name, app_path, _ = data
                if not app_id:
                    continue
                if app_id in self.blacklist:
                    continue

                # update activity session
                self.discord.update_activity_session(
                    app_id,
                    exe_path=app_path,
                    closed=True,
                    session_id=self.app.session_id,
                    media_session_id=self.app.get_media_session_id(),
                    voice_channel_id=self.app.get_voice_channel_id(),
                )
                # remove activity
                for num, activity in enumerate(self.activities):
                    if activity["application_id"] == app_id:
                        del self.activities[num]
                        break
                self.changed = True
                logger.info(f"Game removed from activities: {app_name}")

            if cache_changed:
                cache_changed = False
                peripherals.save_json(self.cache, "detected_apps_cache.json")

            time.sleep(GAME_DETECTION_DELAY)


    def get_activities(self):
        """Get activities for all detected games, and if they changed"""
        cache = self.changed
        self.changed = False
        return self.activities, cache


    def get_detected(self):
        """Get all detected games from cache"""
        detected = []
        for app in self.cache.values():
            if app[0]:
                detected.append((app[0], app[1]))
        return detected


    def set_blacklist(self, blacklist):
        """Set blacklisted games"""
        self.blacklist = blacklist
        global proc_cache
        proc_cache = {}

        for app_id in blacklist:
            if not app_id:
                return
            # find app_name and app_path
            for app in self.cache.values():
                if app[0] == app_id:
                    app_name = app[1]
                    app_path = app[2]
                    break
            else:
                continue

            # update activity session
            self.discord.update_activity_session(
                app_id,
                exe_path=app_path,
                closed=True,
                session_id=self.app.session_id,
                media_session_id=self.app.get_media_session_id(),
                voice_channel_id=self.app.get_voice_channel_id(),
            )
            # remove activity
            for num, activity in enumerate(self.activities):
                if activity["application_id"] == app_id:
                    del self.activities[num]
                    break
            self.changed = True
            logger.info(f"Game removed from activities: {app_name}")
