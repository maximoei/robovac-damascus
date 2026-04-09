"""Minimal binary protobuf parser for Eufy vacuum stream messages.

Parses the subset of proto messages used for live map visualization without
requiring the full ``protobuf`` or ``betterproto`` packages.  All decoding is
done with Python's built-in ``struct`` module.

Proto schemas come from the eufy-mqtt-vacuum project:
  https://github.com/terabyte128/eufy-mqtt-vacuum/tree/main/proto/cloud

Relevant files
--------------
  stream.proto   – Map, DynamicData, PathPoint, RoomOutline, Metadata
  common.proto   – Point (sint32 x/y, m×100), Pose (x/y/theta)
  map_manage.proto – MapEntity (stored maps), MapExtras (room params)

Coordinate units
----------------
  x, y   : sint32, unit = m × 100  (1 cm resolution)
  theta  : sint32, unit = rad × 100
  resolution: uint32, unit = mm per pixel (typical default: 50 mm = 5 cm)

Pixel encoding (stream.Map.pixel)
----------------------------------
  2 bits per pixel, 4 pixels packed per byte, LSB-first within each byte:
    bits [1:0]  → pixel 0
    bits [3:2]  → pixel 1
    bits [5:4]  → pixel 2
    bits [7:6]  → pixel 3
  Values: UNKNOWN=0, OBSTACLE=1, FREE=2, CARPET=3

DPS payload framing
-------------------
  Most binary DPS payloads on Eufy / Tuya devices carry one leading byte
  whose value equals the byte-length of the following protobuf blob.  This
  matches the ``has_length=True`` path in eufy-mqtt-vacuum's decoder.py.
  ``strip_prefix=True`` (the default) removes that byte before parsing.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pixel-type constants  (stream.Map 2-bit encoding)
# ---------------------------------------------------------------------------

PIXEL_UNKNOWN = 0    # unexplored / unknown
PIXEL_OBSTACLE = 1   # wall or furniture
PIXEL_FREE = 2       # passable floor
PIXEL_CARPET = 3     # carpet

# ---------------------------------------------------------------------------
# Frame-type constants  (stream.Map.frame_type)
# ---------------------------------------------------------------------------

FRAME_FULL = 0         # I-frame: replace entire grid
FRAME_INCREMENTAL = 1  # P-frame: update changed pixels (UNKNOWN means "no change")

# ---------------------------------------------------------------------------
# Raw protobuf varint / field helpers
# ---------------------------------------------------------------------------


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf base-128 varint starting at *pos*.

    Returns ``(value, new_pos)``.  Raises ``ValueError`` on truncated input.
    """
    result, shift = 0, 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated varint in protobuf data")


def _zigzag(n: int) -> int:
    """Decode a zigzag-encoded varint (used for sint32 / sint64 fields)."""
    return (n >> 1) ^ -(n & 1)


def _parse_fields(data: bytes) -> dict[int, list]:
    """Parse all top-level protobuf fields from *data*.

    Returns a mapping of ``field_number → [raw_value, …]``.  Field numbers
    may appear more than once (repeated fields accumulate into the list).

    Wire-type semantics:
      0 → varint (returned as int)
      1 → 64-bit fixed (returned as int, little-endian unsigned)
      2 → length-delimited (returned as bytes)
      5 → 32-bit fixed (returned as int, little-endian unsigned)
    """
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _read_varint(data, pos)
        except ValueError:
            break
        field_num = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:  # varint
            val, pos = _read_varint(data, pos)
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 1:  # 64-bit fixed
            if pos + 8 > len(data):
                break
            val = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 2:  # length-delimited
            try:
                length, pos = _read_varint(data, pos)
            except ValueError:
                break
            if pos + length > len(data):
                break
            val = data[pos : pos + length]
            pos += length
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 5:  # 32-bit fixed
            if pos + 4 > len(data):
                break
            val = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            fields.setdefault(field_num, []).append(val)
        else:
            _LOGGER.debug(
                "Unknown protobuf wire type %d at offset %d; stopping parse",
                wire_type,
                pos,
            )
            break

    return fields


# ---------------------------------------------------------------------------
# Parsed data-structure types
# ---------------------------------------------------------------------------


@dataclass
class MapPoint:
    """2-D coordinate in vacuum map space (common.proto Point).

    Units: m × 100 (so value 150 means 1.5 m = 150 cm).
    """

    x: int = 0
    y: int = 0


@dataclass
class MapPose(MapPoint):
    """Position + heading (common.proto Pose).

    ``theta`` is in rad × 100 (value 314 ≈ π rad = 180°).
    """

    theta: int = 0


@dataclass
class MapInfo:
    """Grid dimensions and spatial metadata (stream.proto MapInfo)."""

    width: int = 0       # pixels
    height: int = 0      # pixels
    resolution: int = 50  # mm per pixel (default 5 cm)
    origin: MapPoint = field(default_factory=MapPoint)
    docks: list[MapPose] = field(default_factory=list)


@dataclass
class MapFrame:
    """One frame of the occupancy grid (stream.proto Map).

    ``pixels`` is raw (possibly LZ4-compressed) 2-bit-per-pixel data.
    Call :func:`decompress_pixels` when ``compressed`` is True, then
    :func:`unpack_2bit_pixels` to get a flat list of pixel values.
    """

    map_info: Optional[MapInfo] = None
    frame_id: int = 0
    frame_type: int = FRAME_FULL
    pixels: bytes = b""
    compressed: bool = False  # True → LZ4-compressed


@dataclass
class RobotPose:
    """Current robot position and heading (stream.proto DynamicData.pose)."""

    x: int = 0      # m × 100
    y: int = 0      # m × 100
    theta: int = 0  # rad × 100


@dataclass
class PathPoint:
    """One waypoint in the cleaning trajectory (stream.proto PathPoint)."""

    x: int = 0  # m × 100
    y: int = 0  # m × 100


@dataclass
class RoomOutline:
    """Room-segmentation pixel layer (stream.proto RoomOutline).

    ``outline_pixels`` is a byte sequence with the same width × height as
    the base map.  Each byte is the room ID (0 = no room) for that cell.
    """

    room_id: int = 0
    outline_pixels: bytes = b""


# ---------------------------------------------------------------------------
# Pixel helpers
# ---------------------------------------------------------------------------


def unpack_2bit_pixels(data: bytes) -> list[int]:
    """Unpack 2-bit per pixel data (4 pixels per byte, LSB-first).

    Returns a flat list of pixel values (0–3).  Callers should truncate to
    ``width × height`` elements using the corresponding :class:`MapInfo`.
    """
    result: list[int] = []
    for byte in data:
        result.append(byte & 0x3)
        result.append((byte >> 2) & 0x3)
        result.append((byte >> 4) & 0x3)
        result.append((byte >> 6) & 0x3)
    return result


def decompress_pixels(data: bytes) -> bytes:
    """LZ4-decompress map pixel data.

    Returns the decompressed bytes, or *data* unchanged when lz4 is not
    installed (logs a one-time warning in that case).
    """
    try:
        import lz4.frame as _lz4  # type: ignore[import]

        return _lz4.decompress(data)
    except ImportError:
        _LOGGER.warning(
            "lz4 package is not installed; LZ4-compressed map frames cannot "
            "be decoded.  Install it with: pip install lz4"
        )
        return data
    except Exception as exc:
        _LOGGER.debug("LZ4 decompression failed: %s", exc)
        return data


# ---------------------------------------------------------------------------
# Submessage parsers (internal)
# ---------------------------------------------------------------------------


def _parse_map_point(raw: bytes) -> MapPoint:
    f = _parse_fields(raw)
    return MapPoint(
        x=_zigzag(f.get(1, [0])[0]),
        y=_zigzag(f.get(2, [0])[0]),
    )


def _parse_map_pose(raw: bytes) -> MapPose:
    f = _parse_fields(raw)
    return MapPose(
        x=_zigzag(f.get(1, [0])[0]),
        y=_zigzag(f.get(2, [0])[0]),
        theta=_zigzag(f.get(3, [0])[0]),
    )


def _parse_map_info(raw: bytes) -> MapInfo:
    f = _parse_fields(raw)
    info = MapInfo(
        width=f.get(1, [0])[0],
        height=f.get(2, [0])[0],
        resolution=f.get(3, [50])[0],
    )
    if 4 in f:
        info.origin = _parse_map_point(f[4][0])
    for dock_raw in f.get(5, []):
        info.docks.append(_parse_map_pose(dock_raw))
    return info


def _strip_length_prefix(data: bytes) -> bytes:
    """Remove the leading length-prefix byte used by most Eufy DPS payloads."""
    return data[1:] if data else data


# ---------------------------------------------------------------------------
# Public parse functions
# ---------------------------------------------------------------------------


def parse_map_frame(data: bytes, strip_prefix: bool = True) -> Optional[MapFrame]:
    """Parse a ``stream.Map`` protobuf payload.

    Args:
        data: Raw bytes from the DPS value (already base64-decoded).
        strip_prefix: Strip the leading length-prefix byte (default True).

    Returns:
        A :class:`MapFrame`, or ``None`` if parsing fails.
    """
    try:
        if strip_prefix:
            data = _strip_length_prefix(data)
        f = _parse_fields(data)
        frame = MapFrame()
        if 1 in f:
            frame.map_info = _parse_map_info(f[1][0])
        frame.frame_id = f.get(2, [0])[0]
        frame.frame_type = f.get(3, [FRAME_FULL])[0]
        frame.pixels = f.get(4, [b""])[0]
        # compress_type: 0 = none, 1 = lz4_fast
        frame.compressed = f.get(5, [0])[0] != 0
        return frame
    except Exception as exc:
        _LOGGER.debug("parse_map_frame failed: %s", exc)
        return None


def parse_dynamic_data(data: bytes, strip_prefix: bool = True) -> Optional[RobotPose]:
    """Parse a ``stream.DynamicData`` payload to extract the robot pose.

    Args:
        data: Raw bytes from the DPS value.
        strip_prefix: Strip the leading length-prefix byte (default True).

    Returns:
        A :class:`RobotPose`, or ``None`` if parsing fails.
    """
    try:
        if strip_prefix:
            data = _strip_length_prefix(data)
        f = _parse_fields(data)
        pose = RobotPose()
        # field 1 = pose (Pose submessage)
        if 1 in f:
            p = _parse_map_pose(f[1][0])
            pose.x, pose.y, pose.theta = p.x, p.y, p.theta
        return pose
    except Exception as exc:
        _LOGGER.debug("parse_dynamic_data failed: %s", exc)
        return None


def parse_path_point(data: bytes, strip_prefix: bool = True) -> Optional[PathPoint]:
    """Parse a single ``stream.PathPoint`` payload.

    Args:
        data: Raw bytes from the DPS value.
        strip_prefix: Strip the leading length-prefix byte (default True).

    Returns:
        A :class:`PathPoint`, or ``None`` if parsing fails.
    """
    try:
        if strip_prefix:
            data = _strip_length_prefix(data)
        f = _parse_fields(data)
        # field 1 = x (sint32), field 2 = y (sint32)
        return PathPoint(
            x=_zigzag(f.get(1, [0])[0]),
            y=_zigzag(f.get(2, [0])[0]),
        )
    except Exception as exc:
        _LOGGER.debug("parse_path_point failed: %s", exc)
        return None


def parse_room_outline(data: bytes, strip_prefix: bool = True) -> Optional[RoomOutline]:
    """Parse a ``stream.RoomOutline`` payload.

    Args:
        data: Raw bytes from the DPS value.
        strip_prefix: Strip the leading length-prefix byte (default True).

    Returns:
        A :class:`RoomOutline`, or ``None`` if parsing fails.
    """
    try:
        if strip_prefix:
            data = _strip_length_prefix(data)
        f = _parse_fields(data)
        # field 1 = room_id (uint32), field 2 = pixel data (bytes)
        return RoomOutline(
            room_id=f.get(1, [0])[0],
            outline_pixels=f.get(2, [b""])[0],
        )
    except Exception as exc:
        _LOGGER.debug("parse_room_outline failed: %s", exc)
        return None
