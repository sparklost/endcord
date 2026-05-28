#!/usr/bin/env bash

# This script is intended to be run by endcord.
#
# It can be configured in endcord config:
# custom_media_player = "path/to/this/file.sh"
# custom_media_terminal = True
# custom_media_blacklist = ["URL", "YT"]
# custom_media_hint = True
#
# This script can also be installed to PATH to simplify things
#
# Script accepts 2 arguments:
# - first is file path
# - second is file type, it can be any of: img, gif, video, audio, URL, YT (for youtube)
# Currently script can only handle img, gif, video and audio.
# This script depends on other programs to display media so they should be installed. Recommended: timg.

set -e

FILE="${1:-}"
TYPE="${2:-}"
RET=0

if [[ -z "$FILE" ]]; then
    echo "Usage: $(basename "$0") <file_path> [file_type]"
    exit 1
fi

printf "\e[?1049h"

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE"
    read -n1 -rsp $"Press any key to continue..."
    printf "\e[?1049l"
    exit 1
fi


cleanup() {
    printf "\e[?1049l"
    stty sane
}

trap cleanup EXIT INT TERM


case "$TYPE" in
    img)
        if command -v timg &>/dev/null; then
            timg -C "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        elif command -v kitten &>/dev/null; then
            kitten icat --hold "$FILE"
        elif command -v viu &>/dev/null; then
            viu "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        elif command -v chafa &>/dev/null; then
            chafa "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        elif command -v img2sixel &>/dev/null; then
            img2sixel "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        else
            echo "Error: no image viewer found. Install one of: kitten, timg, viu, chafa"
            read -n1 -rsp $"Press any key to continue..."
            RET=1
        fi
        ;;
    gif)
        if command -v timg &>/dev/null; then
            timg -C "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        elif command -v viu &>/dev/null; then
            viu "$FILE"
            read -n1 -rsp $"Press any key to continue..."
        else
            echo "Error: no image viewer found. Install one of: kitten, timg, viu, chafa"
            read -n1 -rsp $"Press any key to continue..."
            RET=1
        fi
        ;;
    video)
        if command -v timg &>/dev/null; then
            timg "$FILE"
        elif command -v mpv &>/dev/null; then
            mpv --vo=kitty "$FILE"
        else
            echo "Error: no video player found. Install one of: timg, mpv"
            read -n1 -rsp $"Press any key to continue..."
            RET=1
        fi
        ;;
    audio)
        if command -v mpv &>/dev/null; then
            mpv --no-video "$f"
        elif command -v ffplay &>/dev/null; then
            ffplay -nodisp -autoexit "$f"
        elif command -v aplay &>/dev/null && [[ "${f,,}" == *.wav ]]; then
            aplay "$f"
        else
            echo "Error: no audio player found. Install one of: mpv, ffplay"
            read -n1 -rsp $"Press any key to continue..."
            RET=1
        fi
        ;;
    *)
        echo "Error: unknown file type "$TYPE" (valid: img, gif, video, audio)"
        read -n1 -rsp $"Press any key to continue..."
        RET=1
        ;;
esac


printf "\e[?1049l"
exit $RET
