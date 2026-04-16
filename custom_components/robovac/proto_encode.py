"""Zero-dependency protobuf encoder for Eufy RoboVac DPS 152 (ModeCtrlRequest).

Wire format (symmetric inverse of proto_decode.py):
  Each encoded DPS value is a base64 string.  Raw bytes are:
    byte 0      — length prefix (number of protobuf bytes that follow)
    bytes 1+    — standard protobuf binary encoding

Protobuf binary format:
  Field tag = (field_number << 3) | wire_type
    wire_type 0 : varint
    wire_type 2 : length-delimited (varint length prefix + bytes)
  Varint: little-endian base-128 (MSB=1 → more bytes follow)

Proto source: control.proto (cloud/control.proto from eufy firmware)

Verified encodings (all match existing T2277 hardcoded values):
  encode_mode_ctrl_simple(0)                              → "AA=="
  encode_mode_ctrl_simple(6)                              → "AggG"
  encode_mode_ctrl_simple(13)                             → "AggN"
  encode_mode_ctrl_simple(14)                             → "AggO"
  encode_mode_ctrl_auto(clean_times=1)                    → "BBoCCAE="
"""

import base64


# ---------------------------------------------------------------------------
# Low-level wire-format primitives
# ---------------------------------------------------------------------------

def _encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a protobuf varint (little-endian base-128)."""
    if value == 0:
        return b"\x00"
    result = []
    while value > 0:
        b = value & 0x7F
        value >>= 7
        if value:
            b |= 0x80
        result.append(b)
    return bytes(result)


def _field_varint(field_num: int, value: int) -> bytes:
    """Encode a varint field (wire type 0): tag + varint value."""
    tag = _encode_varint((field_num << 3) | 0)
    return tag + _encode_varint(value)


def _field_bytes(field_num: int, data: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2): tag + varint length + bytes."""
    tag = _encode_varint((field_num << 3) | 2)
    return tag + _encode_varint(len(data)) + data


def _with_length_prefix(data: bytes) -> str:
    """Prepend a 1-byte length prefix and return as a base64 string."""
    return base64.b64encode(bytes([len(data)]) + data).decode("ascii")


# ---------------------------------------------------------------------------
# ModeCtrlRequest encoders  (control.proto → ModeCtrlRequest)
# ---------------------------------------------------------------------------

def encode_mode_ctrl_simple(method: int) -> str:
    """Encode a ModeCtrlRequest with no param field.

    Used for: pause, stop, go-home, resume, and standby.

    method=0 with no param encodes as an *empty* message (the standby sentinel
    "AA=="), matching the firmware convention in T2277 — the decoder recognises
    an empty message with no seq as standby.  All other method values encode
    field_1 = method.

    ModeCtrlRequest.Method enum values (control.proto):
      0  START_AUTO_CLEAN       6  START_GOHOME     12  STOP_TASK
      1  START_SELECT_ROOMS_CLEAN   9  START_FAST_MAPPING  13  PAUSE_TASK
      2  START_SELECT_ZONES_CLEAN  10  START_GOWASH   14  RESUME_TASK
      3  START_SPOT_CLEAN          ...
    """
    if method == 0:
        # standby sentinel: empty protobuf message
        return _with_length_prefix(b"")
    return _with_length_prefix(_field_varint(1, method))


def encode_mode_ctrl_auto(clean_times: int = 1) -> str:
    """Encode ModeCtrlRequest for START_AUTO_CLEAN (method=0, AutoClean param).

    Per firmware convention, when AutoClean param is present the method=0 field
    is omitted — the decoder infers AUTO_CLEAN from the presence of the param
    in oneof field 3.

    ModeCtrlRequest oneof Param layout (control.proto):
      auto_clean = 3   ← AutoClean { uint32 clean_times = 1; bool force_mapping = 2; }

    Verified: clean_times=1 → "BBoCCAE="
    """
    auto_clean = _field_varint(1, clean_times)          # AutoClean.clean_times
    return _with_length_prefix(_field_bytes(3, auto_clean))  # oneof auto_clean = 3


def encode_mode_ctrl_rooms(
    rooms: list[dict[str, int]],
    clean_times: int = 1,
    map_id: int = 0,
    releases: int = 0,
) -> str:
    """Encode ModeCtrlRequest for START_SELECT_ROOMS_CLEAN (method=1).

    ModeCtrlRequest oneof Param layout (control.proto):
      select_rooms_clean = 4   ← SelectRoomsClean

    SelectRoomsClean schema (control.proto):
      message Room { uint32 id = 1; uint32 order = 2; }
      repeated Room rooms      = 1;
      uint32       clean_times = 2;
      uint32       map_id      = 3;   // 0 → field omitted
      uint32       releases    = 4;   // 0 → field omitted
      Mode         mode        = 5;   // 0=GENERAL (default, omitted)

    Args:
        rooms: List of dicts with required key "id" and optional "order".
               "order" defaults to 1-based index position.
        clean_times: Number of cleaning passes per room (≥ 1).
        map_id: Map identifier from device storage (0 → omit field, device
                uses current map).
        releases: Map version correction number (0 → omit).
    """
    src = b""
    for i, room in enumerate(rooms):
        room_msg = _field_varint(1, room["id"])               # Room.id
        room_msg += _field_varint(2, room.get("order", i + 1))  # Room.order
        src += _field_bytes(1, room_msg)                      # repeated rooms = 1
    src += _field_varint(2, clean_times)                      # SelectRoomsClean.clean_times
    if map_id:
        src += _field_varint(3, map_id)
    if releases:
        src += _field_varint(4, releases)

    msg = _field_varint(1, 1)        # method = START_SELECT_ROOMS_CLEAN
    msg += _field_bytes(4, src)      # oneof select_rooms_clean = 4
    return _with_length_prefix(msg)
