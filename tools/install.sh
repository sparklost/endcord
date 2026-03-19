#!/usr/bin/env bash

# this script should be run using curl:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)"
# or using wget:
#   bash -c "$(wget -qO- https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)"
# or download script then run it:
#   wget https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh
#   bash install.sh
# option script arguments:
#   --lite - install lite version instead
#   --uninstall - remove already installed binary
# use them like this:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)" -- --uninstall


# init stuff
set -e
REPO_OWNER="sparklost"
APP_NAME="endcord"
LITE=""
UNINSTALL=false
for arg in "$@"; do
    case "$arg" in
        lite) LITE="-lite";;
        --uninstall) UNINSTALL=true;;
    esac
done
BINARY_NAME="${APP_NAME}${LITE}"


# select install dir
if [[ -w "/usr/local/bin" ]]; then
    INSTALL_DIR="/usr/local/bin"
elif command -v sudo >/dev/null 2>&1; then
    INSTALL_DIR="/usr/local/bin"
else
    INSTALL_DIR="$HOME/.local/bin"
    mkdir -p "$INSTALL_DIR"
fi


# uninstall
if $UNINSTALL; then
    if [[ -f "$INSTALL_DIR/$BINARY_NAME" ]]; then
        if [[ -w "$INSTALL_DIR" ]]; then
            rm -f "$INSTALL_DIR/$BINARY_NAME"
        else
            sudo rm -f "$INSTALL_DIR/$BINARY_NAME"
        fi
        echo "$BINARY_NAME uninstalled successfully"
    else
        echo "$BINARY_NAME is not installed"
    fi
    exit 0
fi


# detect os and architecture
OS="$(uname -s)"
ARCHITECTURE="$(uname -m)"
case "$OS" in
    Linux) PLATFORM="linux"; EXT="tar.gz";;
    Darwin) EXT="zip"
        if [[ "$ARCHITECTURE" == "arm64" ]]; then
            PLATFORM="macos-arm64"
        else
            PLATFORM="macos-x86_64"
        fi
        ;;
    *) echo "Unsupported OS: $OS"; exit 1;;
esac


# get latest version
VERSION=$(curl -s "https://api.github.com/repos/$REPO_OWNER/$APP_NAME/releases/latest" \
    | grep '"tag_name":' \
    | sed -E 's/.*"v?([^"]+)".*/\1/')
if [[ -z "$VERSION" ]]; then
    echo "Failed to fetch latest version"
    exit 1
fi


# check installed version
echo "Checking latest version"
if command -v "$BINARY_NAME" &>/dev/null; then
    INSTALLED_VERSION=$("$BINARY_NAME" -v 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+')
    if [[ "$INSTALLED_VERSION" == "$VERSION" ]]; then
        echo "$BINARY_NAME is up to date: $INSTALLED_VERSION"
        #exit 0
    fi
fi


# build download url
ARCHIVE_NAME="${APP_NAME}${LITE}-${VERSION}-${PLATFORM}.${EXT}"
DOWNLOAD_URL="https://github.com/$REPO_OWNER/$APP_NAME/releases/download/${VERSION}/${ARCHIVE_NAME}"


# download
echo "Downloading $DOWNLOAD_URL"
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"
if command -v curl &>/dev/null; then
  curl -LO "$DOWNLOAD_URL"
elif command -v wget &>/dev/null; then
  wget "$DOWNLOAD_URL"
else
  echo "Need either curl or wget to download binary"
  exit 1
fi


# extract
if [[ "$EXT" == "tar.gz" ]]; then
    tar -xzf "$ARCHIVE_NAME"
else
    unzip -q "$ARCHIVE_NAME"
fi
BIN_PATH=$(find . -type f -name "$BINARY_NAME" | head -n 1)
if [[ -z "$BIN_PATH" ]]; then
    echo "Binary file not found in archive"
    exit 1
fi
chmod +x "$BIN_PATH"


# install
if command -v "$BINARY_NAME" &>/dev/null; then
    echo "Updating $BINARY_NAME: $INSTALLED_VERSION -> $VERSION"
else
    echo "Installing $BINARY_NAME $VERSION"
fi
if [[ -w "$INSTALL_DIR" ]]; then
    mv "$BIN_PATH" "$INSTALL_DIR/$BINARY_NAME"
else
    sudo mv "$BIN_PATH" "$INSTALL_DIR/$BINARY_NAME"
fi
echo "$BINARY_NAME successfully installed to $INSTALL_DIR"


# check PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo "WARNING: $INSTALL_DIR is not in PATH"
fi


exit 0
