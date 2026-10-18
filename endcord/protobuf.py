# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import struct


def read_varint(data, position):
    """Parse base128 varint from byte array at the given position"""
    result = 0
    shift = 0
    while True:
        b = data[position]
        position += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, position


def make_varint(value):
    """Encode integer into a base128 varint byte string"""
    if value < 0:
        value = (1 << 64) + value   # per protobuf spec
    result = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            result.append(b | 0x80)
        else:
            result.append(b)
            break
    return bytes(result)


def make_tag(field_num, wire_type):
    """Calculate key tag varint byte signature"""
    return make_varint((field_num << 3) | wire_type)


def parse_message(data, schema, pos=0, end_pos=None):
    """Parse binary protobuf message into dict, using provided schema"""
    if end_pos is None:
        end_pos = len(data)
    result = {}

    while pos < end_pos:
        tag, pos = read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        # skip field
        if field_num not in schema:
            if wire_type == 0:
                _, pos = read_varint(data, pos)
            elif wire_type == 1:
                pos += 8
            elif wire_type == 2:
                length, pos = read_varint(data, pos)
                pos += length
            elif wire_type == 5:
                pos += 4
            continue

        field_name, field_definition = schema[field_num]

        # nested submessage
        if isinstance(field_definition, dict):
            length, pos = read_varint(data, pos)
            result[field_name] = parse_message(data, field_definition, pos, pos + length)
            pos += length

        # plain types
        elif isinstance(field_definition, str):
            if field_definition == "string":
                length, pos = read_varint(data, pos)
                result[field_name] = data[pos:pos+length].decode("utf-8")
                pos += length
            elif field_definition == "fixed64":
                result[field_name] = str(struct.unpack("<Q", data[pos:pos+8])[0])   # imitating json str data
                pos += 8
            elif field_definition == "uint32":
                value, pos = read_varint(data, pos)
                result[field_name] = value

        # structured fields
        elif isinstance(field_definition, tuple):
            definition_kind, definition_type = field_definition

            if definition_kind == "wrapper":
                length, pos = read_varint(data, pos)
                if pos >= pos + length:
                    value = "" if definition_type == "string" else (False if definition_type == "bool" else 0)
                tag, pos_i = read_varint(data, pos)
                if tag >> 3 == 1:
                    if definition_type == "string":
                        length_i, pos_i = read_varint(data, pos_i)
                        value = data[pos_i:pos_i+length_i].decode("utf-8")
                    elif definition_type in ("bool", "uint64", "int64"):
                        value, _ = read_varint(data, pos_i)
                        value = bool(value) if definition_type == "bool" else str(value)   # imitating json str data
                else:
                    value = None
                result[field_name] = value
                pos += length

            elif definition_kind == "packed":
                length, pos = read_varint(data, pos)
                value = []
                pos_i = pos
                while pos_i < pos + length:
                    value.append(str(struct.unpack("<Q", data[pos_i:pos_i+8])[0]))
                    pos_i += 8
                if field_name not in result:
                    result[field_name] = []
                result[field_name].extend(value)
                pos += length

            elif definition_kind == "map":
                length, pos = read_varint(data, pos)
                sub_data = data[pos:pos+length]
                key_type, val_type = definition_type
                entry_schema = {1: ("key", key_type), 2: ("value", val_type)}
                parsed_entry = parse_message(sub_data, entry_schema)
                if field_name not in result:
                    result[field_name] = {}
                key = parsed_entry.get("key", "")   # fallback
                value = parsed_entry.get("value", {} if isinstance(val_type, dict) else 0)
                result[field_name][key] = value
                pos += length

            elif definition_kind == "repeated":
                length, pos = read_varint(data, pos)
                if field_name not in result:
                    result[field_name] = []
                result[field_name].append(parse_message(data, definition_type, pos, pos + length))
                pos += length

            elif definition_kind == "repeated_primitive":
                if definition_type == "string":
                    length, pos = read_varint(data, pos)
                    if field_name not in result:
                        result[field_name] = []
                    result[field_name].append(data[pos:pos+length].decode("utf-8"))
                    pos += length

    return result


def serialize_message(data_dict, schema):
    """Serialize binary protobuf message from dict, using provided schema"""
    result = bytearray()

    for field_num, (field_name, field_def) in schema.items():
        if field_name not in data_dict or data_dict[field_name] is None:
            continue
        value = data_dict[field_name]

        # nested submessages
        if isinstance(field_def, dict):
            submessage_bytes = serialize_message(value, field_def)
            result.extend(make_tag(field_num, 2) + make_varint(len(submessage_bytes)) + submessage_bytes)

        # plain types
        elif isinstance(field_def, str):
            if field_def == "string":
                str_bytes = value.encode("utf-8")
                result.extend(make_tag(field_num, 2) + make_varint(len(str_bytes)) + str_bytes)
            elif field_def == "fixed64":
                result.extend(make_tag(field_num, 1) + struct.pack("<Q", int(value)))
            elif field_def == "uint32":
                result.extend(make_tag(field_num, 0) + make_varint(int(value)))

        # structured fields
        elif isinstance(field_def, tuple):
            definition_kind, definition_type = field_def
            if definition_kind == "wrapper":
                if definition_type == "string":
                    str_bytes = value.encode("utf-8")
                    inner_bytes = make_tag(1, 2) + make_varint(len(str_bytes)) + str_bytes
                elif definition_type == "bool":
                    inner_bytes = make_tag(1, 0) + make_varint(1 if value else 0)
                elif definition_type in ("uint64", "int64"):
                    inner_bytes = make_tag(1, 0) + make_varint(int(value))
                else:
                    inner_bytes = b""
                result.extend(make_tag(field_num, 2) + make_varint(len(inner_bytes)) + inner_bytes)
            elif definition_kind == "packed":
                if definition_type == "fixed64":
                    packed_bytes = b"".join(struct.pack("<Q", int(v)) for v in value)
                    if packed_bytes:
                        result.extend(make_tag(field_num, 2) + make_varint(len(packed_bytes)) + packed_bytes)
            elif definition_kind == "map":
                key_type, val_type = definition_type
                entry_schema = {1: ("key", key_type), 2: ("value", val_type)}
                for k, v in value.items():
                    entry_data = {"key": k, "value": v}
                    entry_bytes = serialize_message(entry_data, entry_schema)
                    result.extend(make_tag(field_num, 2) + make_varint(len(entry_bytes)) + entry_bytes)
            elif definition_kind == "repeated":
                for item in value:
                    submessage_bytes = serialize_message(item, definition_type)
                    result.extend(make_tag(field_num, 2) + make_varint(len(submessage_bytes)) + submessage_bytes)
            elif definition_kind == "repeated_primitive":
                if definition_type == "string":
                    for item in value:
                        str_bytes = item.encode("utf-8")
                        result.extend(make_tag(field_num, 2) + make_varint(len(str_bytes)) + str_bytes)


    return bytes(result)
