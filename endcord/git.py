import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
from urllib.parse import urlencode, urlparse

from endcord import peripherals

logger = logging.getLogger(__name__)
HEADER = {
    "User-Agent": peripherals.APP_NAME + "/" + peripherals.VERSION,   # required by github
    "Accept": "application/vnd.github+json",
}

def install_extension(url, cli=False, prefer_tag=None, update=False):
    """Install extension from specified git repo url"""
    # init stuff
    if ":" not in url and "@" not in url and len(url.split("/")) == 2:
        url = "https://github.com/" + url
    ext_dir = os.path.expanduser(os.path.join(peripherals.config_path, "Extensions"))
    ext_name = url.strip("/").split("/")[-1]
    ext_owner = url.strip("/").split("/")[-2]
    ext_path = os.path.join(ext_dir, ext_name)
    if not os.path.exists(ext_dir):
        os.makedirs(os.path.expanduser(ext_dir), exist_ok=True)
    if not update and os.path.exists(ext_path):
        return 2, "Extension with this name is already installed"
    text = f"Installing extension to: {ext_dir}"
    if cli:
        print(text)
    else:
        logger.info(text)

    try:
        # use github api
        if "github.com" in url:
            if not prefer_tag:
                prefer_tag = check_for_update("0.0.0", ext_owner, ext_name)
            status = download_gh_repo(ext_owner, ext_name, ext_path, prefer_tag)
            if status == 1:
                setup_extension(ext_path)
                return 1, "Extension installed successfully. Restart endcord to load it"
            if status == 2:
                return 4, "Error occured. See log for more info"
            if status == 3:
                return 3, "Could not find this extension"
            return None, ""

        # pull/clone with git command
        if not shutil.which("git"):
            text = "Git is required to install extension"
            if cli:
                print(text)
            else:
                logger.info(text)
            return 0, text
        if update:
            result = subprocess.run(["git", "pull"], cwd=ext_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            if not cli:
                os.environ["GIT_TERMINAL_PROMPT"] = "0"
            result = subprocess.run(["git", "clone", url], cwd=ext_dir, capture_output=True, text=True, check=False)

        # return stuff
        if cli:
            print(result.stdout + result.stderr)
            return None, ""
        if result.returncode == 0:
            setup_extension(ext_path)
            return 1, "Extension installed successfully. Restart endcord to load it"
        if "not read Username" in result.stderr:
            return 3, "Could not find this extension"

    except subprocess.CalledProcessError as e:
        logger.error(e.stderr.decode() if e.stderr else str(e))
    except RuntimeError as e:
        logger.error(f"Install extension error: {e}")
    return 4, "Error occured. See log for more info"


def setup_extension(ext_path):
    """Setup extension dependencies"""
    if not os.path.exists(os.path.join(ext_path, "setup.py")):
        return
    if not shutil.which("virtualenv"):
        logger.warn("Could not setup extension: virtualenv is not found on system")
        return
    subprocess.run(["python", "setup.py"], cwd=ext_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ver_to_tuple(v):
    """Convert semver version to tuple"""
    v = v.split("v")[-1].split("-")[0].split("+")[0]
    return tuple(int(p) for p in v.split("."))


def check_for_update(current_version, owner, repo):
    """Chek specified repo for update"""
    try:
        connection = http.client.HTTPSConnection("api.github.com", timeout=10)
        connection.request("GET", f"/repos/{owner}/{repo}/releases/latest", headers=HEADER)
        response = connection.getresponse()
    except (socket.gaierror, TimeoutError):
        connection.close()
        return None
    if response.status == 200:
        data = json.loads(response.read())
        connection.close()
        latest = data["tag_name"].lstrip("v")
        if ver_to_tuple(current_version) < ver_to_tuple(latest):
            return latest
        return None
    connection.close()
    logger.error(f"Failed checking for updates for gh repo {owner}/{repo}, http error code: {response.status}")
    return None


def search_gh_repos(topic, num=30, page=1):
    """Search github repositories"""
    query = urlencode({
        "q": f"topic:{topic}",
        "per_page": num,
        "page": page,
    })
    try:
        connection = http.client.HTTPSConnection("api.github.com", timeout=10)
        connection.request("GET", f"/search/repositories?{query}", headers=HEADER)
        response = connection.getresponse()
    except (socket.gaierror, TimeoutError):
        connection.close()
        return None
    if response.status == 200:
        data = json.loads(response.read())
        connection.close()
        repos = []
        for item in data["items"]:
            repo_name = item["name"]
            description = item["description"] or "No description"
            owner = item["owner"]["login"]
            repos.append((owner, repo_name, description))
        return repos
    connection.close()
    logger.error(f"Failed searching for repos: {topic}, page: {page}, http error code: {response.status}")
    return None


def download_gh_repo(owner, repo, save_path, tag=None):
    """Download zipball of repo tag or main and extract it to save_path"""
    host = "api.github.com"
    url = f"/repos/{owner}/{repo}/zipball"
    if tag:
        url += "/" + tag
    redirects = 0

    while redirects < 2:
        try:
            connection = http.client.HTTPSConnection(host, timeout=10)
            connection.request("GET", url, headers=HEADER)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 2

        if response.status == 200:
            data = response.read()
            connection.close()

            # unzip
            import io
            import zipfile
            temp_dir = os.path.join(save_path, "temp")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(path=temp_dir)

            # move files to save_path
            extracted_dir = os.path.join(temp_dir, os.listdir(temp_dir)[0])
            if os.path.isdir(extracted_dir):
                for item in os.listdir(extracted_dir):
                    shutil.move(os.path.join(extracted_dir, item), os.path.join(save_path, item))
                shutil.rmtree(temp_dir)

            return 1

        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            if not location:
                print("Error: Redirect without lcation")
                return []
            redirects += 1
            connection.close()
            parsed = urlparse(location)
            if parsed.netloc:
                host = parsed.netloc
            if parsed.path:
                url = parsed.path
            continue

        connection.close()
        logger.error(f"Failed downloading repo {owner}/{repo}:{tag}, http error code: {response.status}")
        return 3 if response.status == 404 else 2
