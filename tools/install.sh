#!/usr/bin/env bash

# run this script using cur or wgetl:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)"
#   bash -c "$(wget -qO- https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)"
# or download script then run it:
#   wget https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh
#   bash install.sh

set -e

REPO_OWNER="sparklost"
APP_NAME="endcord"
VERSION="1.5.4"
VALID_NAMES=("endcord" "endcord-micro" "endcord-mini" "endcord-lite" "endcord-medium" "endcord-full" "endcord-gui")
APP_ID="com.${REPO_OWNER}.${APP_NAME}"

RED=$'\033[1;31m'
GREEN=$'\033[1;32m'
YELLOW=$'\033[1;33m'
YELLOW_L=$'\033[0;33m'
BLUE=$'\033[1;34m'
PURPLE=$'\033[1;35m'
CYAN=$'\033[1;36m'
NC=$'\033[0m'

ACTION=""
METHOD=""
MODE=""
TARGET_VERSION="latest"
GIT_REF=""
INSTALL_UV=false
SKIP_DESKTOP=false
SKIP_DOCS=false
KEEP_TEMP=false
FORCE=false
AUTO_YES=false
EXTRA_BUILD_ARGS=()
CUSTOM_PREFIX=""
LOCATION=""
ADD_SUFFIX=""

b_nuitka=" "; b_noclang=" "; b_custompy=" "
b_nocython=" "; b_nodeps=" "; b_rnnoise=" "; b_noext=" "


usage() {
    cat << EOF
Usage: ${PURPLE}${APP_NAME}-installer${NC} [options]

Without options an interactive menu is shown. Every option given here
will skip that matching step, so the script can be used non-interactively.

${BLUE}Action:${NC}
  --install              install endcord
  --update               update the existing install
  --uninstall            remove endcord

${BLUE}What to install:${NC}
  --binary               download official prebuilt binary (default)
  --source               build from source (slow)
  --mode, --level LEVEL  full | medium | lite | mini | micro | gui
  --version VERSION      release to download (default: latest)
  --ref REF              git branch or tag to build (default: latest main)

${BLUE}Build options:${NC}
  --nuitka               use nuitka to build (slower build but better executable)
  --noclang              do not prefer clang even if installed
  --custom-python        build custom python (smaller binary, Linux only)
  --nocython             skip compiling cython code
  --nocompile-deps       do not compile dependencies with custom flags
  --bundle-rnnoise       bundle RNNoise library (only for full level)
  --disable-extensions   disable extensions support in the code
  --build-arg ARG        pass any other argument to build.py (repeatable)
  --install-uv           install uv automatically if it is missing

${BLUE}Where to install:${NC}
  --system               system-wide (/usr/local), asks for sudo if needed
  --user                 current user only (~/.local)
  --installed-path FILE  path to existing installation (for update/uninstall)
  --prefix DIR           custom prefix (binary goes to DIR/bin)
  --suffix               keep the "-level" suffix on installed binary

${BLUE}Other:${NC}
  --no-desktop           do not install .desktop file and icon (Linux)
  --no-docs              do not install docs, readme and license
  --keep-temp            do not delete the temporary directory after finished
  --force                reinstall same version, skip safety questions
  -y, --yes              never ask, use defaults for anything not given
  --no-color             disable colors
  -h, --help             show this help message and exit
  -v, --version          display the ${APP_NAME}-installer version and exit
EOF
}


# argparser
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) ACTION=2 ;;
        --update) ACTION=1 ;;
        --uninstall) ACTION=3 ;;
        --binary) METHOD=1 ;;
        --source) METHOD=2 ;;
        --installed-path) INSTALLED_PATH="$2"; shift ;;
        --mode|--level) MODE="${2^^}"; shift ;;
        --version) TARGET_VERSION="$2"; shift ;;
        --ref) GIT_REF="$2"; shift ;;
        --nuitka) b_nuitka="x" ;;
        --noclang) b_noclang="x" ;;
        --custom-python) b_custompy="x" ;;
        --nocython) b_nocython="x" ;;
        --nocompile-deps) b_nodeps="x" ;;
        --bundle-rnnoise) b_rnnoise="x" ;;
        --disable-extensions) b_noext="x" ;;
        --build-arg) EXTRA_BUILD_ARGS+=("$2"); shift ;;
        --install-uv) INSTALL_UV=true ;;
        --system) LOCATION=1 ;;
        --user) LOCATION=2 ;;
        --prefix) CUSTOM_PREFIX="$2"; shift ;;
        --suffix) ADD_SUFFIX=2 ;;
        --no-desktop) SKIP_DESKTOP=true ;;
        --no-docs) SKIP_DOCS=true ;;
        --keep-temp) KEEP_TEMP=true ;;
        --force) FORCE=true ;;
        -y|--yes) AUTO_YES=true ;;
        --no-color) RED=""; GREEN=""; YELLOW=""; YELLOW_L=""; BLUE=""; PURPLE=""; CYAN=""; NC="" ;;
        -h|--help) usage; exit 0 ;;
        -v|--version) echo -e "endcord-installer $VERSION"; exit 0 ;;
        *) echo -e "${RED}Unknown option:${NC} $1"; usage; exit 1 ;;
    esac
    shift
done


# system checks
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Linux) PLATFORM="linux"; EXT="tar.gz" ;;
    Darwin) EXT="zip"; [[ "$ARCH" == "arm64" ]] && PLATFORM="macos-arm64" || PLATFORM="macos-x86_64" ;;
    *) echo -e "${RED}Unsupported OS:${NC} $OS"; exit 1 ;;
esac
[[ -f /etc/NIXOS ]] && IS_NIXOS=true || IS_NIXOS=false
echo ""
echo "${GREEN}$APP_NAME installer${NC} by ${PURPLE}sparklost${NC}"
echo "Platform: $OS $ARCH"


# detect already installed binaries
FOUND_BINS=()
IFS=':' read -ra PATH_DIRS <<< "$PATH"
for p in "${PATH_DIRS[@]}"; do
    if [[ -d "$p" ]]; then
        for name in "${VALID_NAMES[@]}"; do
            bin_path="$p/$name"
            if [[ -x "$bin_path" ]]; then
                duplicate=false
                for b in "${FOUND_BINS[@]}"; do
                    [[ "$b" == "$bin_path" ]] && duplicate=true
                done
                [[ "$duplicate" == false ]] && FOUND_BINS+=("$bin_path")
            fi
        done
    fi
done
INSTALLED_PATH=""

# auto yes
if [[ "$AUTO_YES" == true ]]; then
    ACTION=${ACTION:-1}
    METHOD=${METHOD:-1}
    MODE=${MODE:-FULL}
    LOCATION=${LOCATION:-2}
    ADD_SUFFIX=${ADD_SUFFIX:-1}
    [[ -z "$INSTALLED_PATH" && ${#FOUND_BINS[@]} -gt 0 ]] && INSTALLED_PATH="${FOUND_BINS[0]}"
    [[ -z "$INSTALLED_PATH" && -z "$ACTION" ]] && ACTION=2
fi

# find latest version
LATEST_VERSION=""
if [[ ${#FOUND_BINS[@]} -gt 0 ]]; then
    if [[ "$TARGET_VERSION" == "latest" ]]; then
        LATEST_VERSION=$(curl -s "https://api.github.com/repos/$REPO_OWNER/$APP_NAME/releases/latest" | grep '"tag_name":' | sed -E 's/.*"v?([^"]+)".*/\1/')
    else
        LATEST_VERSION="$TARGET_VERSION"
    fi
fi

# prompts

# binary selection prompt
if [[ -z "$ACTION" ]]; then
    if [[ -z "$INSTALLED_PATH" ]]; then
        echo ""
        [[ ${#FOUND_BINS[@]} -ne 1 ]] && multiple="s" || multiple=""
        echo -e "${CYAN}==>${NC} Select what to do. Found ${#FOUND_BINS[@]} existing installation${multiple}:"

        for i in "${!FOUND_BINS[@]}"; do
            OUTPUT=$("${FOUND_BINS[$i]}" -v 2>/dev/null || true)
            BIN_VERSION=$(echo "$OUTPUT" | awk '{print $NF}')
            BIN_VERSION=${BIN_VERSION:-unknown}
            if [[ "$BIN_VERSION" != "$LATEST_VERSION" && "$BIN_VERSION" != "unknown" && -n "$LATEST_VERSION" ]]; then
                OUTDATED="${YELLOW}[OUTDATED: $BIN_VERSION -> $LATEST_VERSION]${NC}"
            else
                OUTDATED="${GREEN}[$BIN_VERSION]${NC}"
            fi
            echo -e "  ${CYAN}$((i+1)))${NC} Manage ${YELLOW_L}${FOUND_BINS[$i]}${NC} $OUTDATED"
        done

        echo -e "  ${CYAN}$(( ${#FOUND_BINS[@]} + 1 )))${NC} Install new binary"
        echo -e "  ${CYAN}$(( ${#FOUND_BINS[@]} + 2 )))${NC} Open $APP_NAME repository"
        echo -e "  ${CYAN}$(( ${#FOUND_BINS[@]} + 3 )))${NC} Report a bug"
        echo -e "  ${CYAN}$(( ${#FOUND_BINS[@]} + 4 )))${NC} Join $APP_NAME discord server"
        echo -en "Choice [${CYAN}1${NC}] (q to quit): "
        read BINARY
        BINARY=${BINARY:-1}
        [[ "${BINARY,,}" == "q" ]] && exit 0

        [[ "$BINARY" == "$((${#FOUND_BINS[@]} + 2))" ]] && { xdg-open "https://github.com/sparklost/endcord"; exit 0; }
        [[ "$BINARY" == "$((${#FOUND_BINS[@]} + 3))" ]] && { xdg-open "https://github.com/sparklost/endcord/issues"; exit 0; }
        [[ "$BINARY" == "$((${#FOUND_BINS[@]} + 4))" ]] && { xdg-open "https://discord.gg/judQSxw5K2"; exit 0; }

        if [[ "$BINARY" -le "${#FOUND_BINS[@]}" ]]; then
            INSTALLED_PATH="${FOUND_BINS[$((BINARY-1))]}"
        else
            ACTION=2
        fi
    fi

    if [[ -z "$ACTION" ]]; then
        echo ""
        echo "What to do with ${YELLOW_L}$INSTALLED_PATH${NC}?"
        echo -e "  ${CYAN}1)${NC} Update"
        echo -e "  ${CYAN}2)${NC} Reinstall / Modify"
        echo -e "  ${CYAN}3)${NC} Uninstall"
        echo -en "Choice [${CYAN}1${NC}] (q to quit): "
        read ACTION
        ACTION=${ACTION:-1}
        [[ "${ACTION,,}" == "q" ]] && exit 0
    fi
else
    # Only fallback to first found if no path was passed via args
    [[ -z "$INSTALLED_PATH" && ${#FOUND_BINS[@]} -gt 0 ]] && INSTALLED_PATH="${FOUND_BINS[0]}"
fi


# get target binary
if [[ -n "$INSTALLED_PATH" && "$ACTION" != "2" ]]; then
    OUTPUT=$("$INSTALLED_PATH" -v 2>/dev/null || true)
    INST_VER=$(echo "$OUTPUT" | awk '{print $NF}')
    INST_MODE=$(echo "$OUTPUT" | awk '{$NF=""; print $0}' | xargs)
    if [[ -n "$LATEST_VERSION" && "$INST_VER" != "$LATEST_VERSION" && -n "$INST_VER" ]]; then
        OUTDATED="${YELLOW}[OUTDATED: $INST_VER -> $LATEST_VERSION]${NC}"
    else
        OUTDATED="${GREEN}[${INST_VER:-unknown}]${NC}"
    fi
fi


# handle uninstall
if [[ "$ACTION" == 3 ]]; then
    echo "Uninstalling $APP_NAME from ${YELLOW_L}$INSTALLED_PATH${NC}..."
    PREFIX="$(dirname "$(dirname "$INSTALLED_PATH")")"
    rm -f "$INSTALLED_PATH"

    OTHER_IN_PREFIX=false
    for bin in "${FOUND_BINS[@]}"; do
        bin_prefix="$(dirname "$(dirname "$bin")")"
        if [[ "$bin" != "$INSTALLED_PATH" && "$bin_prefix" == "$PREFIX" ]]; then
            OTHER_IN_PREFIX=true
            break
        fi
    done
    if [[ "$OTHER_IN_PREFIX" == false ]]; then
        rm -f "$PREFIX/share/applications/${APP_ID}.svg" 2>/dev/null || true
        rm -f "$PREFIX/share/icons/hicolor/scalable/apps/${APP_ID}.svg" 2>/dev/null || true
        rm -rf "$PREFIX/share/doc/endcord" 2>/dev/null || true
        rm -rf "$PREFIX/share/licenses/endcord" 2>/dev/null || true
    else
        echo "Other installation(s) found under ${YELLOW_L}$PREFIX${NC}; keeping desktop, icon, and doc files"
    fi

    echo ""
    echo -e "${GREEN}Uninstalled successfully.${NC}"
    exit 0
fi


# how-to-get prompt
if [[ -z "$METHOD" ]]; then
    echo ""
    echo "${CYAN}==>${NC} How do you want to get $APP_NAME?"
    echo -e "  ${CYAN}1)${NC} Download a prebuilt binary (fast, less customizable/flexible)"
    echo -e "  ${CYAN}2)${NC} Build latest from source (slow, latest updates, needs build tools)"
    echo -en "Choice [${CYAN}1${NC}] (q to quit): "
    read METHOD
    METHOD=${METHOD:-1}
    [[ "${METHOD,,}" == "q" ]] && exit 0
fi


# mode prompt
if [[ "$ACTION" == "1" && -n "$INST_MODE" && -z "$MODE" ]]; then
    MODE=$(echo "$INST_MODE" | sed -n 's/.*(\(.*\)).*/\1/p')
    MODE="${MODE^^}"
fi

if [[ -z "$MODE" ]]; then
    [[ "$METHOD" == "1" ]] && MUST_BUILD="${YELLOW}(NO DOWNLOAD)${NC} " || MUST_BUILD=""
    echo ""
    echo "${CYAN}==>${NC} Which 'level' of $APP_NAME to install?"
    echo -e "  ${CYAN}1)${NC} FULL     media and voice call support"
    echo -e "  ${CYAN}2)${NC} MEDIUM   ${MUST_BUILD}no media and voice calls, but can display images"
    echo -e "  ${CYAN}3)${NC} LITE     no image, media or voice call support"
    echo -e "  ${CYAN}4)${NC} MINI     ${MUST_BUILD}like LITE, no sound unless paplay/pw-cat exist, no voice recording"
    echo -e "  ${CYAN}5)${NC} MICRO    ${MUST_BUILD}max compatibility on legacy/weird systems, no QR code and email login"
    [[ "$OS" != "Darwin" ]] && echo -e "  ${CYAN}6)${NC} ${PURPLE}GUI${NC}      windowed mode (needs GTK3), builds FULL level"
    echo -en "Choice [${CYAN}1${NC}] (q to quit): "
    read MODE_CHOICE
    MODE_CHOICE=${MODE_CHOICE:-1}
    [[ "${MODE_CHOICE,,}" == "q" ]] && exit 0
    case "$MODE_CHOICE" in
        2) MODE="MEDIUM" ;;
        3) MODE="LITE" ;;
        4) MODE="MINI" ;;
        5) MODE="MICRO" ;;
        6) MODE="GUI" ;;
        *) MODE="FULL" ;;
    esac
fi
if [[ "$METHOD" == "1" && "$MODE_CHOICE" =~ ^[245]$ ]]; then
    echo -e "${RED}Error:${NC} There is currently no available endcord binary for ${MODE} level"
    exit 1
fi
if [[ "$OS" == "Darwin" && "$MODE" == "GUI" ]]; then
    echo -e "${RED}Error:${NC} GUI mode is not supported on macOS"
    exit 1
fi
MODE_LOWER="${MODE,,}"


# name prompt
if [[ "$ACTION" == "1" && -n "$INSTALLED_PATH" ]]; then
    # inherit existing name on update
    FINAL_BIN_NAME=$(basename "$INSTALLED_PATH")
else
    if [[ -z "$ADD_SUFFIX" && "$MODE" != "FULL" && "$ACTION" != "3" ]]; then
        echo ""
        echo "${CYAN}==>${NC} Install the binary as:"
        echo -e "  ${CYAN}1)${NC} endcord"
        echo -e "  ${CYAN}2)${NC} endcord-${MODE_LOWER}"
        echo -en "Choice [${CYAN}1${NC}]: "
        read ADD_SUFFIX
        ADD_SUFFIX=${ADD_SUFFIX:-1}
    fi
    ADD_SUFFIX=${ADD_SUFFIX:-1}
fi


# build options prompt
if [[ "$METHOD" == "2" && $# -eq 0 && "$AUTO_YES" == false ]]; then
    echo ""
    draw_menu() {
        echo "${CYAN}==>${NC} Build options (numbers to toggle, e.g. \"1 3\", Enter to continue)"
        echo -e "  [${b_nuitka/x/${GREEN}x${NC}}] ${CYAN}1)${NC} --nuitka              slow build, but better executable (recommended for GUI)"
        echo -e "  [${b_noclang/x/${GREEN}x${NC}}] ${CYAN}2)${NC} --noclang             do not prefer clang if installed"
        echo -e "  [${b_custompy/x/${GREEN}x${NC}}] ${CYAN}3)${NC} --custom-python       build custom python, smaller binary (linux only)"
        echo -e "  [${b_nocython/x/${GREEN}x${NC}}] ${CYAN}4)${NC} --nocython            skip compiling cython code (slower executable)"
        echo -e "  [${b_nodeps/x/${GREEN}x${NC}}] ${CYAN}5)${NC} --nocompile-deps      do not compile dependencies with custom flags"
        echo -e "  [${b_rnnoise/x/${GREEN}x${NC}}] ${CYAN}6)${NC} --bundle-rnnoise      build RNNoise and bundle it in the binary"
        echo -e "  [${b_noext/x/${GREEN}x${NC}}] ${CYAN}7)${NC} --disable-extensions  disable extensions support in the code"
    }
    draw_menu
    while true; do
        echo -en "> "
        read opts
        [[ -z "$opts" ]] && break
        for opt in $opts; do
            case $opt in
                1) [[ "$b_nuitka" == " " ]] && b_nuitka="x" || b_nuitka=" " ;;
                2) [[ "$b_noclang" == " " ]] && b_noclang="x" || b_noclang=" " ;;
                3) [[ "$b_custompy" == " " ]] && b_custompy="x" || b_custompy=" " ;;
                4) [[ "$b_nocython" == " " ]] && b_nocython="x" || b_nocython=" " ;;
                5) [[ "$b_nodeps" == " " ]] && b_nodeps="x" || b_nodeps=" " ;;
                6) [[ "$b_rnnoise" == " " ]] && b_rnnoise="x" || b_rnnoise=" " ;;
                7) [[ "$b_noext" == " " ]] && b_noext="x" || b_noext=" " ;;
            esac
        done
        echo -en "\033[9A\033[J"
        draw_menu
    done
fi


# location prompt
if [[ "$ACTION" == "1" && -n "$INSTALLED_PATH" ]]; then
    INSTALL_DIR=$(dirname "$INSTALLED_PATH")
    SHARE_DIR="${INSTALL_DIR%/bin}/share"
    [[ ! -w "$INSTALL_DIR" ]] && SUDO="sudo" || SUDO=""
else
    if [[ -n "$CUSTOM_PREFIX" ]]; then
        INSTALL_DIR="$CUSTOM_PREFIX/bin"
        SHARE_DIR="$CUSTOM_PREFIX/share"
        SUDO=""
    elif [[ -z "$LOCATION" ]]; then
        if [[ "$EUID" -ne 0 ]]; then
            echo ""
            echo "${CYAN}==>${NC} Where to install?"
            echo -e "  ${CYAN}1)${NC} System (${YELLOW_L}/usr/local/bin${NC}) - will ask for sudo"
            echo -e "  ${CYAN}2)${NC} User (${YELLOW_L}$HOME/.local/bin${NC})"
            echo -en "Choice [${CYAN}1${NC}]: "
            read LOCATION
            LOCATION=${LOCATION:-1}
        else
            LOCATION=1
        fi
    fi
    if [[ -z "$CUSTOM_PREFIX" ]]; then
        [[ "$LOCATION" == "2" && "$IS_NIXOS" == true ]] && { echo -e "${RED}Error:${NC} Cant install to system on NixOS"; exit 1; }
        if [[ "$LOCATION" == "2" ]]; then
            INSTALL_DIR="$HOME/.local/bin"
            SHARE_DIR="$HOME/.local/share"
            SUDO=""
        else
            INSTALL_DIR="/usr/local/bin"
            SHARE_DIR="/usr/local/share"
            [[ "$EUID" -ne 0 ]] && SUDO="sudo" || SUDO=""
        fi
    fi
fi


# execution

# prepare
TEMP_DIR=$(mktemp -d)
[[ "$KEEP_TEMP" != true ]] && trap 'rm -rf "$TEMP_DIR"' EXIT
cd "$TEMP_DIR"
[[ "$MODE_LOWER" == "full" ]] && BIN_SUFFIX="" || BIN_SUFFIX="-$MODE_LOWER"
TARGET_BIN_NAME="${APP_NAME}${BIN_SUFFIX}"


# build
if [[ "$METHOD" == "2" ]]; then
    echo -e "\n${CYAN}==>${NC} Building from source..."
    if ! command -v uv &>/dev/null; then
        if [[ "$INSTALL_UV" == true || "$AUTO_YES" == true ]]; then
            echo -e "${CYAN}==>${NC} Installing uv..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.cargo/bin:$PATH"
        else
            echo -e "${RED}Error:${NC} 'uv' is required. Install it or run with --install-uv"
            exit 1
        fi
    fi

    git clone "https://github.com/$REPO_OWNER/$APP_NAME.git" $APP_NAME
    cd $APP_NAME
    if [[ -n "$GIT_REF" ]]; then
        git checkout "$GIT_REF"
    elif [[ "$TARGET_VERSION" != "latest" ]]; then
        git checkout "$TARGET_VERSION"
    fi

    BUILD_ARGS=()
    [[ "$b_nuitka" == "x" ]] && BUILD_ARGS+=("--nuitka")
    [[ "$b_noclang" == "x" ]] && BUILD_ARGS+=("--noclang")
    [[ "$b_custompy" == "x" && "$OS" == "Linux" ]] && BUILD_ARGS+=("--custom-python")
    [[ "$b_nocython" == "x" ]] && BUILD_ARGS+=("--nocython")
    [[ "$b_nodeps" == "x" ]] && BUILD_ARGS+=("--nocompile-deps")
    [[ "$b_rnnoise" == "x" ]] && BUILD_ARGS+=("--bundle-rnnoise")
    [[ "$b_noext" == "x" ]] && BUILD_ARGS+=("--disable-extensions")
    [[ ${#EXTRA_BUILD_ARGS[@]} -gt 0 ]] && BUILD_ARGS+=("${EXTRA_BUILD_ARGS[@]}")

    export UV_NO_CACHE=1
    if [[ "$MODE" == "GUI" ]]; then
        python build.py --clean-uv --toggle-windowed
        python build.py --clean-uv --level FULL "${BUILD_ARGS[@]}"
        BIN_PATH="./dist/endcord-gui"
    else
        BUILD_ARGS+=("--level" "$MODE")
        python build.py --clean-uv "${BUILD_ARGS[@]}"
        BIN_PATH="./dist/$TARGET_BIN_NAME"
    fi
    SRC_DIR="."
fi


# download
if [[ "$METHOD" == "1" ]]; then
    echo -e "\n${CYAN}==>${NC} Downloading prebuilt binary..."

    if [[ "$OS" == "Linux" && "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
        echo -e "${RED}Error:${NC} Prebuilt Linux binaries are currently only available for x64 architecture"
        echo -e "Your architecture is ${CYAN}$ARCH${NC}, please build from source instead"
        exit 1
    fi

    if [[ "$TARGET_VERSION" == "latest" ]]; then
        [[ -z "$LATEST_VERSION" ]] && LATEST_VERSION=$(curl -s "https://api.github.com/repos/$REPO_OWNER/$APP_NAME/releases/latest" | grep '"tag_name":' | sed -E 's/.*"v?([^"]+)".*/\1/')
        VERSION="$LATEST_VERSION"
    else
        VERSION="$TARGET_VERSION"
    fi
    [[ -z "$VERSION" ]] && { echo -e "${RED}Error:${NC} Failed to fetch version info"; exit 1; }
    if [[ "$ACTION" == "1" && "$INST_VER" == "$VERSION" && "$FORCE" != true ]]; then
        echo -e "${GREEN}$APP_NAME is already up to date ($INST_VER)${NC}"
        exit 0
    fi

    ARCHIVE_NAME="${TARGET_BIN_NAME}-${VERSION}-${PLATFORM}.${EXT}"
    DOWNLOAD_URL="https://github.com/$REPO_OWNER/$APP_NAME/releases/download/${VERSION}/${ARCHIVE_NAME}"
    echo "Downloading $DOWNLOAD_URL..."
    if command -v curl &>/dev/null; then
        curl -LO "$DOWNLOAD_URL"
    else
        wget "$DOWNLOAD_URL"
    fi
    if [[ "$EXT" == "tar.gz" ]]; then
        tar -xzf "$ARCHIVE_NAME"
    else
        unzip -q "$ARCHIVE_NAME"
    fi
    BIN_PATH=$(find . -maxdepth 2 -type f -name "$TARGET_BIN_NAME" | head -n 1)
    SRC_DIR="."
fi


# install binary
if [[ -z "$BIN_PATH" || ! -f "$BIN_PATH" ]]; then
    echo -e "${RED}Error:${NC} Binary not found after generation/download"
    exit 1
fi
if [[ -z "$FINAL_BIN_NAME" ]]; then
    if [[ "$MODE" == "FULL" || "$ADD_SUFFIX" == "1" ]]; then
        FINAL_BIN_NAME="endcord"
    else
        FINAL_BIN_NAME="endcord-${MODE_LOWER}"
    fi
fi
echo -e "\n${CYAN}==>${NC} Installing to ${YELLOW_L}$INSTALL_DIR/$FINAL_BIN_NAME${NC}..."
$SUDO mkdir -p "$INSTALL_DIR"
$SUDO cp "$BIN_PATH" "$INSTALL_DIR/$FINAL_BIN_NAME"
$SUDO chmod +x "$INSTALL_DIR/$FINAL_BIN_NAME"


# install .desktop and icon
if [[ "$OS" == "Linux" && "$SKIP_DESKTOP" == false ]]; then
    DESKTOP_PATH=$(find "$SRC_DIR" -name "${APP_ID}.desktop" | head -n 1)
    echo $DESKTOP_PATH
    ICON_PATH=$(find "$SRC_DIR" -name "endcord.svg" | head -n 1)
    echo $ICON_PATH
    if [[ -n "$DESKTOP_PATH" && -n "$ICON_PATH" ]]; then
        echo "Installing .desktop file and icon..."
        $SUDO mkdir -p "$SHARE_DIR/applications" "$SHARE_DIR/icons/hicolor/scalable/apps"
        $SUDO cp "$DESKTOP_PATH" "$SHARE_DIR/applications/${APP_ID}.desktop"
        $SUDO cp "$ICON_PATH" "$SHARE_DIR/icons/hicolor/scalable/apps/${APP_ID}.svg"
        if command -v update-desktop-database &>/dev/null; then
            $SUDO update-desktop-database "$SHARE_DIR/applications" || true
        fi
    fi
fi


# install documentation
if [[ "$SKIP_DOCS" == false ]]; then
    DOC_DIR="$SHARE_DIR/doc/$APP_NAME"
    LICENSE_DIR="$SHARE_DIR/licenses/$APP_NAME/"
    $SUDO mkdir -p "$DOC_DIR"
    $SUDO mkdir -p "$LICENSE_DIR"
    [[ -d "$SRC_DIR/docs" ]] && $SUDO cp -r "$SRC_DIR/docs/"* "$DOC_DIR/" 2>/dev/null || true
    [[ -f "$SRC_DIR/README.md" ]] && $SUDO cp "$SRC_DIR/README.md" "$DOC_DIR/" 2>/dev/null || true
    [[ -f "$SRC_DIR/LICENSE" ]] && $SUDO cp "$SRC_DIR/LICENSE" "$DOC_DIR/" 2>/dev/null || true
    [[ -f "$SRC_DIR/LICENSE" ]] && $SUDO cp "$SRC_DIR/LICENSE" "$LICENSE_DIR" 2>/dev/null || true
fi


# warnings
echo ""
[[ ":$PATH:" != *":$INSTALL_DIR:"* ]] && cat << EOF
${YELLOW}WARNING:${NC} ${YELLOW_L}$INSTALL_DIR${NC} is not in your PATH!
 You wont be able to run ${APP_NAME} and this script will not be able to detect this install
Add this to your bashrc/zshrc: export PATH="$HOME/.local/bin:$PATH"
EOF
[[ "$IS_NIXOS" == true && -z "$NIX_LD" && ! -d /run/current-system/sw/share/nix-ld ]] && cat << EOF
${YELLOW}WARNING, NixOS Detected: 'nix-ld' is required to run ${APP_NAME}${NC}
Installation will complete, but launching the binary requires dynamic linking support.
To enable 'nix-ld', add this to your /etc/nixos/configuration.nix:
   programs.nix-ld.enable = true;
Then rebuild your system:
   $ sudo nixos-rebuild switch
After this ${APP_NAME} will run without re-installing.
EOF
echo -e "${CYAN}==>${NC} ${GREEN}$APP_NAME-${MODE_LOWER} installed successfully${NC}"
