# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

from itertools import product

ERROR_CORRECTION = ("L", "M", "Q", "H")
ERROR_CORRECTION_BITS = (1, 0, 3, 2)
QR_PAD_BYTES = (236, 17)   # 0xEC and 0x11
ALPHA_NUM = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
PATTERN_POSITION_TABLE = (
    (), (6, 18), (6, 22), (6, 26), (6, 30), (6, 34),
    (6, 22, 38), (6, 24, 42), (6, 26, 46), (6, 28, 50),
)
REED_SOLO_BLOCK_TABLE = (   # L, M, Q, H
    (1, 26, 19), (1, 26, 16), (1, 26, 13), (1, 26, 9),
    (1, 44, 34), (1, 44, 28), (1, 44, 22), (1, 44, 16),
    (1, 70, 55), (1, 70, 44), (2, 35, 17), (2, 35, 13),
    (1, 100, 80), (2, 50, 32), (2, 50, 24), (4, 25, 9),
    (1, 134, 108), (2, 67, 43), (2, 33, 15, 2, 34, 16), (2, 33, 11, 2, 34, 12),
    (2, 86, 68), (4, 43, 27), (4, 43, 19), (4, 43, 15),
    (2, 98, 78), (4, 49, 31), (2, 32, 14, 4, 33, 15), (4, 39, 13, 1, 40, 14),
    (2, 121, 97), (2, 60, 38, 2, 61, 39), (4, 40, 18, 2, 41, 19), (4, 40, 14, 2, 41, 15),
    (2, 146, 116), (3, 58, 36, 2, 59, 37), (4, 36, 16, 4, 37, 17), (4, 36, 12, 4, 37, 13),
    (2, 86, 68, 2, 87, 69), (4, 69, 43, 1, 70, 44), (6, 43, 19, 2, 44, 20), (6, 43, 15, 2, 44, 16),
)
EXP_TABLE = []
LOG_TABLE = []


def init_gf_tables():
    """Initialize Galios Field exp and log tables"""
    global EXP_TABLE, LOG_TABLE
    EXP_TABLE = [0] * 256
    LOG_TABLE = [0] * 256
    val = 1
    for i in range(255):
        EXP_TABLE[i] = val
        LOG_TABLE[val] = i
        # multiply by 2 in GF(2^8)
        val <<= 1
        if val & 0x100:
            val ^= 0x11D

    EXP_TABLE[255] = EXP_TABLE[0]


def get_reed_solo_blocks(version, error_correction):
    """Get Reed-Solomon blocks for specific version and error correction"""
    block = REED_SOLO_BLOCK_TABLE[(version - 1) * 4 + error_correction]
    blocks = []
    for i in range(0, len(block), 3):
        count, total_count, data_count = block[i : i + 3]
        for _ in range(count):
            blocks.append((total_count, data_count))
    return blocks


def gf_log(n):
    """Return the Galois Field logarithm of n"""
    return LOG_TABLE[n]


def gf_gexp(n):
    """Return the Galois Field exponent of n"""
    return EXP_TABLE[n % 255]


def gf_poly_mul(p1, p2):
    """Galios Field polynomial multiplication"""
    num = [0] * (len(p1) + len(p2) - 1)
    for i, item in enumerate(p1):
        for j, other_item in enumerate(p2):
            num[i + j] ^= gf_gexp(gf_log(item) + gf_log(other_item))
    return num


def gf_poly_mod(p1, p2):
    """Galios Field polynomial modulo"""
    difference = len(p1) - len(p2)
    if difference < 0:
        return p1
    ratio = gf_log(p1[0]) - gf_log(p2[0])
    num = [item ^ gf_gexp(gf_log(other_item) + ratio) for item, other_item in zip(p1, p2)]
    if difference:
        num.extend(p1[-difference:])
    offset = 0
    for offset in range(len(num)):
        if num[offset] != 0:
            break
    num = num[offset:]
    return gf_poly_mod(num, p2)


def bit_str(val, width):
    """Convert integer to bit string"""
    return f"{val:0{width}b}"


def bch_encode(data, shift_bits, generator, gen_len, mask=0):
    """Encode the data using BCH algorithm"""
    d = data << shift_bits
    while d.bit_length() >= gen_len:
        shift = d.bit_length() - gen_len
        d ^= generator << shift
    return ((data << shift_bits) | d) ^ mask


def generate_qr(data, error_correction="M", border=0):
    """Automatically select config for QR and make it"""
    error_correction = ERROR_CORRECTION.index(error_correction)
    if not isinstance(data, bytes):
        data = str(data).encode("utf-8")
    binary_mode = not all(char in ALPHA_NUM for char in data)
    # skipping data optimization by splitting into chunks

    # find best version
    needed_bits = 4 + (8 if binary_mode else 9)   # 4 bits for mode and 8/9 for count indicator
    if binary_mode:
        needed_bits += len(data) * 8
    else:   # alphanumeric: 11 bits per pair, 6 bits for a leftover odd character
        pairs = len(data) // 2
        has_leftover = len(data) % 2
        needed_bits += (pairs * 11) + (has_leftover * 6)
    for version in range(1, 11):   # doing only up to version 10
        blocks = get_reed_solo_blocks(version, error_correction)
        bit_capacity = 8 * sum(block[1] for block in blocks)
        if bit_capacity >= needed_bits:
            break
    else:
        return None

    # find best mask pattern
    min_penalty = 0
    mask_pattern = 0
    for i in range(8):
        modules = make_qr(data, version, i, error_correction, binary_mode)
        penalty = calculate_total_penalty(modules)
        if i == 0 or min_penalty > penalty:
            min_penalty = penalty
            mask_pattern = i

    # make final qrcode
    modules = make_qr(data, version, mask_pattern, error_correction, binary_mode)

    # add border if needed
    if not border:
        return modules
    new_width = len(modules) + border * 2
    code = [[False] * new_width] * border
    for module in modules:
        code.append([False] * border + module + [False] * border)
    code += [[False] * new_width] * border
    return code


def make_qr(data, version, mask_pattern, error_correction, binary_mode):
    """Make QR code from given configuration"""
    modules_count = version * 4 + 17
    modules = [[None] * modules_count for i in range(modules_count)]

    # place finder pattern and separators
    for row, col in ((0, 0), (modules_count - 7, 0), (0, modules_count - 7)):
        for r in range(-1, 8):   # 8x8 area to have space for separators
            for c in range(-1, 8):
                target_row = row + r
                target_col = col + c
                if target_row < 0 or target_row >= modules_count or target_col < 0 or target_col >= modules_count:
                    continue   # bounds check
                if 0 <= r < 7 and 0 <= c < 7:   # finder pattern
                    is_ring = (r in (0, 6) or c in (0, 6))
                    is_center = (2 <= r <= 4 and 2 <= c <= 4)
                    modules[target_row][target_col] = is_ring or is_center
                else:   # separator zone
                    modules[target_row][target_col] = False

    # place position adjustment patterns
    pos = PATTERN_POSITION_TABLE[version - 1]
    for row, col in product(pos, repeat=2):
        if modules[row][col] is not None:
            continue
        for r in range(5):
            for c in range(5):
                is_ring = (r in (0, 4) or c in (0, 4))
                is_center = (r == 2 and c == 2)
                modules[row + r - 2][col + c - 2] = is_ring or is_center

    # Place timing patterns
    for i in range(8, modules_count - 8):
        if modules[i][6] is None:   # vertical
            modules[i][6] = (i % 2 == 0)
        if modules[6][i] is None:   # horizontal
            modules[6][i] = (i % 2 == 0)

    # place type info
    raw_bits = (ERROR_CORRECTION_BITS[error_correction] << 3) | mask_pattern
    bits = bch_encode(raw_bits, shift_bits=10, generator=0x0537, gen_len=11, mask=0x5412)
    for i in range(15):
        value = ((bits >> i) & 1) == 1
        # vertical
        if i < 6:
            modules[i][8] = value
        elif i < 8:
            modules[i + 1][8] = value
        else:
            modules[modules_count - 15 + i][8] = value
        # horizontal
        if i < 8:
            modules[8][modules_count - i - 1] = value
        elif i < 9:
            modules[8][15 - i - 1 + 1] = value
        else:
            modules[8][15 - i - 1] = value
    modules[modules_count - 8][8] = True   # fixed module

    # place version number as BCH error correction code
    if version >= 7:
        bits = bch_encode(version, shift_bits=12, generator=0x1F25, gen_len=13)
        for i in range(18):
            modules[i // 3][i % 3 + modules_count - 8 - 3] = ((bits >> i) & 1) == 1
        for i in range(18):
            modules[i % 3 + modules_count - 8 - 3][i // 3] = ((bits >> i) & 1) == 1

    # encode data and do reed-solomon
    data = generate_qr_data(version, error_correction, data, binary_mode)

    # place data in zigzag traversal loop and apply mask
    def get_bits():   # iterator that yields bool for every bit in data
        for byte in data:
            for bit_index in range(7, -1, -1):
                yield ((byte >> bit_index) & 1) == 1
        while True:  # out of data
            yield False
    bit_stream = get_bits()
    row = modules_count - 1
    col = modules_count - 1
    direction = -1
    while col > 0:
        if col == 6:   # skip column 6 - reserved for the vertical timing pattern
            col -= 1
        # traverse the current column pair vertically up and down
        for col_i in (col, col - 1):
            if modules[row][col_i] is None:
                dark = next(bit_stream)
                if apply_mask(mask_pattern, row, col_i):
                    dark = not dark
                modules[row][col_i] = dark
        row += direction
        # if hit the boundary - shift left and go back
        if row < 0 or row >= modules_count:
            row -= direction   # undo step outside boundaries
            direction = -direction   # reverse vertical direction
            col -= 2   # next 2 column strip

    return modules


def generate_qr_data(version, error_correction, data, binary_mode):
    """Encode data and generates an interleaved Reed-Solomon error-corrected stream"""
    init_gf_tables()
    bits = []

    # encode data into bitstream
    bits.append(bit_str(4 if binary_mode else 2, 4))   # mode indicator
    bits.append(bit_str(len(data), 8 if binary_mode else 9))   # character count indicator
    if binary_mode:   # payload
        bits.extend(bit_str(c, 8) for c in data)
    else:
        data = data.upper()
        for i in range(0, len(data), 2):
            chars = data[i : i + 2]
            if len(chars) > 1:
                val = ALPHA_NUM.find(chars[0]) * 45 + ALPHA_NUM.find(chars[1])
                bits.append(bit_str(val, 11))
            else:
                bits.append(bit_str(ALPHA_NUM.find(chars), 6))
    bit_string = "".join(bits)
    reedsolo_blocks = get_reed_solo_blocks(version, error_correction)
    bit_limit = sum(block[1] * 8 for block in reedsolo_blocks)
    if len(bit_string) > bit_limit:
        return None

    # terminate with zeros and pad to byte alignment
    bit_string += "0" * min(bit_limit - len(bit_string), 4)
    if len(bit_string) % 8:
        bit_string += "0" * (8 - (len(bit_string) % 8))

    # fill remaining space with alternating qr padding bytes
    pad_index = 0
    while len(bit_string) < bit_limit:
        bit_string += bit_str(QR_PAD_BYTES[pad_index % 2], 8)
        pad_index += 1

    # convert to list of byte integers
    data_bytes = [int(bit_string[i:i+8], 2) for i in range(0, len(bit_string), 8)]

    # generate reed-solomon error correction
    offset = 0
    data_chunk_data, error_chunk_data = [], []
    max_data_chunk, max_error_chunk = 0, 0
    for block in reedsolo_blocks:
        total_count, data_chunk_count = block[0], block[1]
        error_chunk_count = total_count - data_chunk_count
        max_data_chunk = max(max_data_chunk, data_chunk_count)
        max_error_chunk = max(max_error_chunk, error_chunk_count)
        # slice data blocks
        current_data_chunk = data_bytes[offset : offset + data_chunk_count]
        offset += data_chunk_count
        # calculate reed-solomon polynomial (could use precomputed tables but no need)
        rs_poly = [1]
        for i in range(error_chunk_count):
            rs_poly = gf_poly_mul(rs_poly, [1, gf_gexp(i)])
        # generate error correction remainder
        raw_poly = current_data_chunk + [0] * (len(rs_poly) - 1)
        mod_poly = gf_poly_mod(raw_poly, rs_poly)
        # pad with zeros
        needed_padding = error_chunk_count - len(mod_poly)
        current_error_chunk = [0] * max(0, needed_padding) + mod_poly
        data_chunk_data.append(current_data_chunk)
        error_chunk_data.append(current_error_chunk)

    # interleave data and error code
    final_stream = []
    for i in range(max_data_chunk):
        for data_chunk in data_chunk_data:
            if i < len(data_chunk):
                final_stream.append(data_chunk[i])
    for i in range(max_error_chunk):
        for error_chunk in error_chunk_data:
            if i < len(error_chunk):
                final_stream.append(error_chunk[i])
    return final_stream


def apply_mask(mask_pattern, row, col):
    """Return True if the pixel at (row, col) should be inverted for given mask pattern"""
    match mask_pattern:
        case 0: return (row + col) % 2 == 0   # noqa
        case 1: return row % 2 == 0   # noqa
        case 2: return col % 3 == 0   # noqa
        case 3: return (row + col) % 3 == 0   # noqa
        case 4: return (row // 2 + col // 3) % 2 == 0   # noqa
        case 5: return (row * col) % 2 + (row * col) % 3 == 0   # noqa
        case 6: return ((row * col) % 2 + (row * col) % 3) % 2 == 0   # noqa
        case _: return ((row + col) % 2 + (row * col) % 3) % 2 == 0   # noqa


def calculate_total_penalty(modules):
    """Calculate total penalty value from all 4 QR penalty rules"""
    modules_count = len(modules)
    penalty = 0

    # rule 1 - check horizontal and vertical lines for consecutive modules
    for row in modules:
        penalty += calculate_line_penalty(row)
    for col_idx in range(modules_count):
        column = [modules[row_idx][col_idx] for row_idx in range(modules_count)]
        penalty += calculate_line_penalty(column)

    # rule 2 - 2x2 blocks of same value
    for row in range(modules_count - 1):
        this_row = modules[row]
        next_row = modules[row + 1]
        for col in range(modules_count - 1):
            if this_row[col] == this_row[col + 1] == next_row[col] == next_row[col + 1]:
                penalty += 3

    # rule 3 - sequences that look like finder pattern
    pattern_1 = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_2 = [False, False, False, False, True, False, True, True, True, False, True]
    for row in modules:
        for c in range(modules_count - 10):
            check = row[c : c + 11]
            if check == pattern_1 or check == pattern_2:
                penalty += 40
    for c in range(modules_count):
        for r in range(modules_count - 10):
            check = [modules[r + i][c] for i in range(11)]
            if check == pattern_1 or check == pattern_2:
                penalty += 40

    # rule 4 - balance of dark vs white
    dark_count = sum(sum(row) for row in modules)
    dark_percentage = (dark_count * 100) / modules_count ** 2
    deviation = abs(dark_percentage - 50)
    penalty += int(deviation // 5) * 10   # 10 penalty points for every 5%

    return penalty


def calculate_line_penalty(line):
    """Calculate rule 1 penalty points for a single row or column"""
    penalty = 0
    run_length = 0
    previous = None
    for value in line:
        if value == previous:
            run_length += 1
        else:
            if run_length >= 5:
                penalty += (run_length - 2)
            run_length = 1
            previous = value
    if run_length >= 5:
        penalty += (run_length - 2)
    return penalty


def gen_qr_terminal_string(text, text_above="", text_below=""):
    """Convert string to QR code string ready to be printed to terminal and check for terminal size"""
    import shutil
    fg_white = "\x1b[38;5;15m"
    bg_black = "\x1b[48;5;16m"
    reset = "\x1b[0m"
    matrix = generate_qr(text)

    height = len(matrix)
    width = len(matrix[0])
    term = shutil.get_terminal_size()
    screen_height = term.lines
    screen_width = term.columns

    text_above = [x.center(screen_width) for x in text_above.split("\n")]
    text_below = [x.center(screen_width) for x in text_below.split("\n")]
    screen_height -= len(text_above) + len(text_below)

    # check terminal size
    height_real = (height + 1) // 2
    if screen_width < width or screen_height < height_real:
        pad_top = [bg_black + (" " * screen_width) + reset] * ((screen_height - 3 // 2) // 2)
        pad_bot = [bg_black + (" " * screen_width) + reset] * (height_real - 3 - ((screen_height - 3 // 2) // 2))
        text = ["Terminal too small:", f"Need: {width}x{height_real}.", f"Have: {screen_width}x{screen_height}."]
        return "\n".join(pad_top) + "\n" + "\n".join([x.center(screen_width) for x in text]) + "\n" + "\n".join(pad_bot)

    # build string
    out_lines = []
    out_lines.append(bg_black + fg_white + "\n".join(text_above) + reset)

    # calculate padding
    padding_h = (screen_height - height // 2) // 2
    padding_w = (screen_width - width) // 2

    # top padding
    for _ in range(padding_h):
        out_lines.append(bg_black + (" " * screen_width) + reset)

    for y in range(0, height, 2):
        top_line = matrix[y]
        bottom_line = matrix[y + 1] if y + 1 < height else [False] * width
        line_parts = []

        # left padding
        if padding_w > 0:
            line_parts.append(bg_black + (" " * padding_w))

        # qr code
        for x in range(width):
            top = top_line[x]
            bottom = bottom_line[x]
            if top and bottom:
                line_parts.append("█")
            elif top and not bottom:
                line_parts.append("▀")
            elif not top and bottom:
                line_parts.append("▄")
            else:
                line_parts.append(" ")

        # right padding
        visible_len = padding_w + width
        if visible_len < screen_width:
            line_parts.append(bg_black + (" " * (screen_width - visible_len)))

        line_parts.append(reset)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height + 1:
        out_lines.append(bg_black + (" " * screen_width) + reset)

    out_lines.append(bg_black + fg_white + "\n".join(text_below) + reset)

    return "\n".join(out_lines)


def draw_qr_curses(screen, text, text_above="", text_below="", color_id=0, margin=False):
    """Render a QR matrix with text above and below on curses screen"""
    import curses
    screen_height, screen_width = screen.getmaxyx()
    screen.bkgd(" ", color_id)
    text_above = [x.center(screen_width) for x in text_above.split("\n")]
    text_below = [x.center(screen_width) for x in text_below.split("\n")]
    matrix = generate_qr(text)
    height = len(matrix)
    width = len(matrix[0])
    available_height = screen_height - len(text_above) - len(text_below)

    height_real = (height + 1) // 2
    if screen_width < (width + margin*2) or available_height < (height_real + margin*2):
        screen.clear()
        text = ["Terminal too small:", f"Need: {width}x{height_real}", f"Have: {screen_width}x{available_height}"]
        start_y = max(0, (screen_height - len(text)) // 2)
        for idx, line in enumerate(text):
            if start_y + idx < screen_height:
                screen.addstr(start_y + idx, max(0, (screen_width - len(line)) // 2), line, color_id)
        screen.refresh()
        return

    current_y = 0
    for line in text_above:
        if current_y < screen_height:
            screen.insstr(current_y, 0, line[:screen_width], color_id)
            current_y += 1

    padding_h = (available_height - height_real) // 2
    padding_w = (screen_width - width) // 2

    for y in range(0, height, 2):
        top_line = matrix[y]
        bottom_line = matrix[y + 1] if y + 1 < height else [False] * width
        draw_y = current_y + padding_h + (y // 2)
        if draw_y >= screen_height:
            break

        qr_chars = []
        for x in range(width):
            top = top_line[x]
            bottom = bottom_line[x]
            if top and bottom:
                char = "█"
            elif top and not bottom:
                char = "▀"
            elif not top and bottom:
                char = "▄"
            else:
                char = " "
            qr_chars.append(char)
        left_padding = " " * max(0, padding_w)
        right_padding_len = max(0, screen_width - (padding_w + width))
        right_padding = " " * right_padding_len
        full_line = left_padding + "".join(qr_chars) + right_padding
        full_line = full_line[:screen_width]
        try:
            screen.insstr(draw_y, 0, full_line, color_id)
        except curses.error:
            pass

    start_below_y = screen_height - len(text_below)
    for idx, line in enumerate(text_below):
        draw_y = start_below_y + idx
        if 0 <= draw_y < screen_height:
            screen.insstr(draw_y, 0, line[:screen_width], color_id)

    screen.refresh()
