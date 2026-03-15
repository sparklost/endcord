import http.client
import os.path
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d, %H:%M:%S") + " UTC"
header = f"""# {utc_date}
# Generated from:
# http://www.unicode.org/Public/latest/ucd/extracted/DerivedGeneralCategory.txt
# http://www.unicode.org/Public/latest/ucd/EastAsianWidth.txt
"""


def http_get_with_redirect(host, path):
    """Download data while handling up to 2 redirects"""
    redirects = 0

    while redirects < 2:
        try:
            conn = http.client.HTTPSConnection(host, timeout=30)
            conn.request("GET", path)
            response = conn.getresponse()
        except (socket.gaierror, TimeoutError, ConnectionResetError) as e:
            print(f"Error: {e}")
            return []

        if response.status == 200:
            data = response.read().decode("utf-8", errors="replace")
            conn.close()
            return data.splitlines()

        # redirects
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            if not location:
                print("Error: Redirect without lcation")
                return []
            redirects += 1
            conn.close()
            parsed = urlparse(location)
            if parsed.netloc:
                host = parsed.netloc
            if parsed.path:
                path = parsed.path
            continue
        print(f"HTTP error {response.status} for {host}{path}")
        return []
    return []


def parse_line(line):
    """Parse line according to this conventions: https://www.unicode.org/reports/tr44/#Format_Conventions"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if ";" not in line:
        return None

    range_part, rest = line.split(";", 1)
    range_part = range_part.strip()

    if "#" in rest:
        properties, comment = rest.split("#", 1)
        properties = properties.strip()
        comment = comment.strip()
    else:
        properties = rest.strip()
        comment = ""

    if ".." in range_part:
        lo, hi = range_part.split("..")
        start = int(lo, 16)
        end = int(hi, 16)
    else:
        start = end = int(range_part, 16)

    return [start, end, properties, comment]


def filter_width_general(properties, comment, width):
    """Filter specific width from general category list"""
    if properties == "Sk":
        if "EMOJI MODIFIER" in comment:
            return width == 0
        if "FULLWIDTH" in comment:
            return width == 2
        return width == 1
    if properties in ("Me", "Mn", "Mc", "Cf", "Zl", "Zp"):
        return width == 0
    return width == 1


def filter_width_east(properties):
    """Filter only wide characters from East Asian list"""
    if properties in ("W", "F"):
        return True
    return False


def merge_codepoints_to_ranges(values):
    """Convert sorted codepoints to merged ranges"""
    if not values:
        return []

    sorted_vals = sorted(values)
    ranges = []
    start = prev = sorted_vals[0]

    for cp in sorted_vals[1:]:
        if cp == prev + 1:
            prev = cp
            continue
        ranges.append((start, prev + 1))
        start = prev = cp

    ranges.append((start, prev + 1))
    return ranges


def update_wide_ranged():
    """Download latest unicode data and build list of ranges of wide characters as python file"""
    # download lists
    print("Downloading unicode lists")
    list_general_raw = http_get_with_redirect("www.unicode.org", "/Public/latest/ucd/extracted/DerivedGeneralCategory.txt")
    if not list_general_raw:
        print("Failed downloading unicode lists")
        return
    list_east_raw = http_get_with_redirect("www.unicode.org", "/Public/latest/ucd/EastAsianWidth.txt")
    if not list_east_raw:
        print("Failed downloading unicode lists")
        return

    # read lines into usable data
    list_general = []
    list_east = []
    for line in list_general_raw:
        parsed = parse_line(line)
        if parsed:
            list_general.append(parsed)
    for line in list_east_raw:
        parsed = parse_line(line)
        if parsed:
            list_east.append(parsed)

    # wide from east asian
    wide = set()
    for start, end, properties, comment in list_east:
        if filter_width_east(properties):
            for cp in range(start, end+1):
                wide.add(cp)

    # subtract non-wide from general category
    general_zero = set()
    for start, end, properties, comment in list_general:
        if filter_width_general(properties, comment, width=0):
            for cp in range(start, end+1):
                general_zero.add(cp)
    wide -= general_zero

    # add wide from general category
    general_wide = set()
    for start, end, properties, comment in list_general:
        if filter_width_general(properties, comment, width=2):
            for cp in range(start, end+1):
                general_wide.add(cp)
    wide |= general_wide

    # convert set to merged ranges
    ranges = merge_codepoints_to_ranges(wide)

    # build py file
    path = os.path.expanduser("./endcord/wide_ranges.py")
    with open(path, "w") as f:
        f.write(header)
        f.write("\n")
        f.write("WIDE_RANGES = (\n")
        for line in ranges:
            f.write(f"    {str(line)},\n")
        f.write(")\n")

    print(f"Wide unicode characters ranges saved to {path}")


if __name__ == "__main__":
    update_wide_ranged()
