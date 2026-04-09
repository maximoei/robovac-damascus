"""Pillow-based vacuum map renderer.

Converts the live occupancy-grid state (received as protobuf stream frames)
into a PNG image suitable for serving from a Home Assistant camera entity.

Colour scheme
-------------
UNKNOWN  (0) – #808080 mid-grey      (unexplored)
OBSTACLE (1) – #1C1C1C near-black    (walls / furniture)
FREE     (2) – #F5F5F5 near-white    (passable floor)
CARPET   (3) – #B3D9FF light-blue    (carpet)

Overlays (drawn in this order, so later layers sit on top)
-----------------------------------------------------------
1. Room tints      – 40 % pastel colour blend over FREE pixels per room ID
2. Dock marker     – gold circle at each dock location from MapInfo
3. Cleaning path   – small blue dots at each recorded PathPoint
4. Robot body      – red circle at current pose
5. Robot heading   – short red line segment indicating theta direction

Coordinate system
-----------------
World coordinates are in m × 100 (1 cm resolution).  The MapInfo origin
defines the world-space position of pixel (0, 0).  The Y-axis in world space
increases upward (north), while image Y increases downward; the renderer
flips the Y-axis when projecting world coords to image pixels.

    pixel_x = (world_x − origin.x) × 10 / resolution_mm × scale
    pixel_y = image_height − (world_y − origin.y) × 10 / resolution_mm × scale
"""
from __future__ import annotations

import io
import logging
import math
from typing import Optional

from .proto_parser import (
    FRAME_FULL,
    FRAME_INCREMENTAL,
    PIXEL_CARPET,
    PIXEL_FREE,
    PIXEL_OBSTACLE,
    PIXEL_UNKNOWN,
    MapFrame,
    MapInfo,
    PathPoint,
    RobotPose,
    RoomOutline,
    decompress_pixels,
    unpack_2bit_pixels,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour constants (R, G, B tuples)
# ---------------------------------------------------------------------------

_PIXEL_COLOURS: dict[int, tuple[int, int, int]] = {
    PIXEL_UNKNOWN:  (128, 128, 128),
    PIXEL_OBSTACLE: (28,  28,  28),
    PIXEL_FREE:     (245, 245, 245),
    PIXEL_CARPET:   (179, 217, 255),
}

# Cyclic room tint palette (0-indexed by room_id − 1)
_ROOM_TINTS: list[tuple[int, int, int]] = [
    (255, 180, 180),  # 1 – rose
    (180, 255, 180),  # 2 – mint
    (180, 180, 255),  # 3 – periwinkle
    (255, 220, 160),  # 4 – peach
    (220, 160, 255),  # 5 – lavender
    (160, 255, 220),  # 6 – aqua
    (255, 255, 160),  # 7 – lemon
    (160, 220, 255),  # 8 – sky
]
_ROOM_TINT_ALPHA = 0.40  # blend fraction for room tint over base pixel colour

_PATH_COLOUR   = (46,  134, 171)  # #2E86AB – steel-blue
_ROBOT_COLOUR  = (230, 57,  70)   # #E63946 – red
_DOCK_COLOUR   = (255, 215, 0)    # #FFD700 – gold
_HEADING_COLOUR = (160, 20,  30)  # darker red for heading line

# Physical sizes used to calculate pixel radii at the map's native resolution
_ROBOT_RADIUS_MM = 175  # ~17.5 cm (typical Eufy body radius)
_DOCK_RADIUS_MM  = 80   # dock marker radius
_PATH_DOT_RADIUS_MM = 15  # path dot radius

# Maximum path history kept in memory
_MAX_PATH_POINTS = 12_000

# Placeholder image dimensions (shown before any map data arrives)
_PLACEHOLDER_W = 320
_PLACEHOLDER_H = 240
_PLACEHOLDER_BG = (80, 80, 80)


class VacuumMapRenderer:
    """Maintains accumulated map state and renders it to PNG bytes on demand.

    Call the ``update_*`` methods as new protobuf frames arrive, then call
    :meth:`render` to obtain the latest PNG image.

    Example::

        renderer = VacuumMapRenderer()
        renderer.update_map(frame)        # from parse_map_frame()
        renderer.update_pose(pose)        # from parse_dynamic_data()
        renderer.update_path([point])     # from parse_path_point()
        png_bytes = renderer.render(scale=2)
    """

    def __init__(self) -> None:
        self._map_info: Optional[MapInfo] = None
        # Flat occupancy grid: length = width × height, values 0–3
        self._grid: Optional[list[int]] = None
        # Room-ID layer: same shape as _grid, 0 = no room
        self._room_grid: Optional[list[int]] = None
        self._pose: Optional[RobotPose] = None
        self._path: list[PathPoint] = []

    # ------------------------------------------------------------------
    # State update API
    # ------------------------------------------------------------------

    def update_map(self, frame: MapFrame) -> None:
        """Apply an I-frame or P-frame to the accumulated occupancy grid.

        For full (I-frame) frames the grid is replaced entirely.
        For incremental (P-frame) frames only non-UNKNOWN pixels are
        written, so UNKNOWN acts as a "no change" sentinel.
        """
        if not frame.pixels:
            return

        raw = frame.pixels
        if frame.compressed:
            raw = decompress_pixels(raw)

        unpacked = unpack_2bit_pixels(raw)

        if frame.map_info is not None:
            self._map_info = frame.map_info

        if self._map_info is None:
            _LOGGER.debug("Map frame received before MapInfo; skipping render")
            return

        w, h = self._map_info.width, self._map_info.height
        total = w * h

        if frame.frame_type == FRAME_FULL or self._grid is None:
            self._grid = unpacked[:total]
            # Pad with UNKNOWN if payload is shorter than the declared grid size
            if len(self._grid) < total:
                self._grid += [PIXEL_UNKNOWN] * (total - len(self._grid))
        else:
            # P-frame: merge only changed pixels
            if len(self._grid) < total:
                self._grid += [PIXEL_UNKNOWN] * (total - len(self._grid))
            for i, val in enumerate(unpacked[:total]):
                if val != PIXEL_UNKNOWN:
                    self._grid[i] = val

    def update_room_outline(self, outline: RoomOutline) -> None:
        """Merge one room's pixel mask into the room-segmentation layer."""
        if self._map_info is None or not outline.outline_pixels:
            return
        w, h = self._map_info.width, self._map_info.height
        total = w * h
        if self._room_grid is None:
            self._room_grid = [0] * total
        elif len(self._room_grid) < total:
            self._room_grid += [0] * (total - len(self._room_grid))

        for i, byte_val in enumerate(outline.outline_pixels[:total]):
            room_id = byte_val & 0xFF
            if room_id != 0:
                self._room_grid[i] = room_id

    def update_pose(self, pose: RobotPose) -> None:
        """Update the robot's current position and heading."""
        self._pose = pose

    def update_path(self, points: list[PathPoint]) -> None:
        """Append new waypoints to the cleaning-path history.

        The history is capped at :data:`_MAX_PATH_POINTS` to bound memory use.
        """
        self._path.extend(points)
        if len(self._path) > _MAX_PATH_POINTS:
            self._path = self._path[-_MAX_PATH_POINTS:]

    def clear_path(self) -> None:
        """Discard the recorded path (call at the start of each new clean)."""
        self._path.clear()

    def has_map_data(self) -> bool:
        """Return True once at least one map frame has been processed."""
        return self._grid is not None and self._map_info is not None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, scale: int = 2) -> bytes:
        """Render the current state to a PNG and return the raw bytes.

        Args:
            scale: Integer upscale factor applied after building the native-
                   resolution occupancy image.  ``scale=2`` doubles both
                   dimensions, giving 4× the pixel area for better visibility.

        Returns:
            PNG bytes.  Returns a grey placeholder before any map data arrives.
        """
        if not self.has_map_data():
            return _render_placeholder()

        try:
            return self._render_map(scale)
        except Exception as exc:
            _LOGGER.warning("Map render failed: %s", exc, exc_info=True)
            return _render_placeholder()

    def _render_map(self, scale: int) -> bytes:
        from PIL import Image, ImageDraw  # type: ignore[import]

        info = self._map_info
        assert info is not None
        grid = self._grid
        assert grid is not None

        w, h = info.width, info.height
        res_mm = max(info.resolution, 1)  # guard against zero

        # ── 1. Build base occupancy image ────────────────────────────────────
        img = Image.new("RGB", (w, h), _PIXEL_COLOURS[PIXEL_UNKNOWN])
        px_access = img.load()
        assert px_access is not None

        for y in range(h):
            for x in range(w):
                idx = y * w + x
                if idx < len(grid):
                    px_access[x, y] = _PIXEL_COLOURS.get(
                        grid[idx], _PIXEL_COLOURS[PIXEL_UNKNOWN]
                    )

        # ── 2. Room tints ────────────────────────────────────────────────────
        if self._room_grid is not None:
            alpha = _ROOM_TINT_ALPHA
            for y in range(h):
                for x in range(w):
                    idx = y * w + x
                    if idx < len(self._room_grid):
                        room_id = self._room_grid[idx]
                        if room_id > 0:
                            tint = _ROOM_TINTS[(room_id - 1) % len(_ROOM_TINTS)]
                            base = px_access[x, y]
                            px_access[x, y] = (
                                int(base[0] * (1 - alpha) + tint[0] * alpha),
                                int(base[1] * (1 - alpha) + tint[1] * alpha),
                                int(base[2] * (1 - alpha) + tint[2] * alpha),
                            )

        # ── 3. Scale up ──────────────────────────────────────────────────────
        if scale > 1:
            img = img.resize((w * scale, h * scale), Image.NEAREST)

        draw = ImageDraw.Draw(img)
        sw, sh = w * scale, h * scale

        def world_to_px(wx: int, wy: int) -> tuple[int, int]:
            """Map world coordinates (m×100) → scaled image pixel position."""
            ox = info.origin.x
            oy = info.origin.y
            # Convert m×100 → mm (*10), then to pixels
            ix = (wx - ox) * 10.0 / res_mm * scale
            iy = (wy - oy) * 10.0 / res_mm * scale
            # Flip Y: world Y increases upward, image Y increases downward
            return int(ix), int(sh - iy)

        def mm_to_px(mm: float) -> int:
            return max(1, int(mm / res_mm * scale))

        # ── 4. Dock markers ──────────────────────────────────────────────────
        dock_r = mm_to_px(_DOCK_RADIUS_MM)
        for dock in info.docks:
            dx, dy = world_to_px(dock.x, dock.y)
            draw.ellipse(
                [dx - dock_r, dy - dock_r, dx + dock_r, dy + dock_r],
                fill=_DOCK_COLOUR,
                outline=(160, 140, 0),
                width=max(1, scale),
            )

        # ── 5. Cleaning path ─────────────────────────────────────────────────
        path_r = max(1, mm_to_px(_PATH_DOT_RADIUS_MM))
        for pt in self._path:
            px, py = world_to_px(pt.x, pt.y)
            draw.ellipse(
                [px - path_r, py - path_r, px + path_r, py + path_r],
                fill=_PATH_COLOUR,
            )

        # ── 6. Robot body + heading ──────────────────────────────────────────
        if self._pose is not None:
            rx, ry = world_to_px(self._pose.x, self._pose.y)
            robot_r = mm_to_px(_ROBOT_RADIUS_MM)
            draw.ellipse(
                [rx - robot_r, ry - robot_r, rx + robot_r, ry + robot_r],
                fill=_ROBOT_COLOUR,
                outline=_HEADING_COLOUR,
                width=max(1, scale),
            )
            # theta is rad × 100; positive = counter-clockwise from +X axis
            theta_rad = self._pose.theta / 100.0
            line_len = int(robot_r * 1.6)
            ex = rx + int(line_len * math.cos(theta_rad))
            ey = ry - int(line_len * math.sin(theta_rad))  # image Y flipped
            draw.line(
                [(rx, ry), (ex, ey)],
                fill=_HEADING_COLOUR,
                width=max(2, scale),
            )

        # ── 7. Serialise ─────────────────────────────────────────────────────
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _render_placeholder() -> bytes:
    """Return a small grey PNG shown before any map data is available."""
    try:
        from PIL import Image  # type: ignore[import]

        img = Image.new("RGB", (_PLACEHOLDER_W, _PLACEHOLDER_H), _PLACEHOLDER_BG)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Absolute fallback: minimal valid 1×1 grey PNG (hard-coded bytes)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\x80\x80\x80\x00\x00\x00\x04\x00\x01"
            b"\xa3\n\x15\xe9\x00\x00\x00\x00IEND\xaeB`\x82"
        )
