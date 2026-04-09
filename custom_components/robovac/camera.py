"""Home Assistant Camera entity for Eufy vacuum live map visualization.

One :class:`VacuumMapCamera` entity is created per map-capable vacuum that
has a non-``None`` ``map_dps_codes`` attribute on its model class.

Data flow
---------
1. The vacuum's Tuya TCP connection pushes a DPS update.
2. :class:`~.vacuum.RoboVacEntity` calls ``update_entity_values()``, which
   dispatches :data:`~.const.SIGNAL_MAP_UPDATE` via the HA dispatcher with
   the full ``tuyastatus`` dict as the payload.
3. :class:`VacuumMapCamera` (subscribed to that signal in
   ``async_added_to_hass``) receives the dict, extracts the DPS codes
   listed in :attr:`~.vacuums.base.MapDpsCodes`, base64-decodes the values,
   parses them with :mod:`.proto_parser`, and feeds the results to
   :class:`~.map_renderer.VacuumMapRenderer`.
4. When the HA frontend polls ``/api/camera_proxy/<entity_id>`` (typically
   every few seconds), :meth:`VacuumMapCamera.async_camera_image` returns
   the latest PNG bytes produced by the renderer.

Platform setup
--------------
The camera platform is listed in :data:`~.__init__.PLATFORMS` alongside
``vacuum`` and ``sensor``.  Because :func:`async_setup_entry` only reads from
the config entry (not from ``hass.data``), it is safe to set up the camera
platform concurrently with the vacuum platform.  The dispatcher subscription
established in ``async_added_to_hass`` naturally handles the ordering.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_MODEL, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VACS, DOMAIN, SIGNAL_MAP_UPDATE, SIGNAL_VACUUM_DOCKED
from .eufy_cloud_map import EufyCloudMapFetcher
from .map_renderer import VacuumMapRenderer
from .proto_parser import (
    parse_dynamic_data,
    parse_map_frame,
    parse_path_point,
    parse_room_outline,
)
from .vacuums import ROBOVAC_MODELS
from .vacuums.base import MapDpsCodes

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a VacuumMapCamera for each map-capable vacuum in the entry.

    A camera is only created when the vacuum model defines ``map_dps_codes``
    (i.e. the model class has a non-``None`` ``map_dps_codes`` attribute).
    """
    vacuums = config_entry.data[CONF_VACS]
    entities: list[VacuumMapCamera] = []

    for item_id, item in vacuums.items():
        model_code = item.get(CONF_MODEL, "")[:5]
        model_class = ROBOVAC_MODELS.get(model_code)
        if model_class is None:
            _LOGGER.debug(
                "camera: model %s not found in ROBOVAC_MODELS; skipping", model_code
            )
            continue

        map_dps: MapDpsCodes | None = getattr(model_class, "map_dps_codes", None)
        if map_dps is None:
            _LOGGER.debug(
                "camera: model %s has no map_dps_codes; skipping camera setup",
                model_code,
            )
            continue

        _LOGGER.debug(
            "camera: creating VacuumMapCamera for %s (model=%s)",
            item.get(CONF_NAME, item_id),
            model_code,
        )
        entities.append(
            VacuumMapCamera(
                item,
                map_dps,
                username=config_entry.data.get(CONF_USERNAME, ""),
                password=config_entry.data.get(CONF_PASSWORD, ""),
            )
        )

    async_add_entities(entities)


class VacuumMapCamera(Camera):
    """Camera entity that shows the live vacuum floor map as a PNG image.

    The image is built from occupancy-grid data (``stream.Map`` protobuf
    frames), overlaid with the cleaning path, robot position, dock location,
    and room segmentation.  All data arrives via DPS push updates from the
    vacuum device.

    The entity is linked to its vacuum by unique ID only (no direct object
    reference), which keeps the camera and vacuum platforms fully decoupled.
    """

    _attr_has_entity_name = True
    _attr_name = "Map"
    _attr_content_type = "image/png"
    _attr_supported_features = CameraEntityFeature(0)
    _attr_should_poll = False

    def __init__(
        self,
        item: dict[str, Any],
        map_dps: MapDpsCodes,
        username: str = "",
        password: str = "",
    ) -> None:
        super().__init__()
        self._vacuum_id: str = item[CONF_ID]
        self._map_dps = map_dps
        self._renderer = VacuumMapRenderer()
        self._cloud_fetcher = EufyCloudMapFetcher(username, password) if username else None

        self._attr_unique_id = f"{item[CONF_ID]}_map"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, item[CONF_ID])},
            name=item[CONF_NAME],
        )

    # ------------------------------------------------------------------
    # HA entity lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to DPS dispatcher updates once the entity is registered."""
        signal = f"{SIGNAL_MAP_UPDATE}_{self._vacuum_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._on_dps_update)
        )

        # Subscribe to docked-state transitions to trigger cloud map fetch.
        if self._cloud_fetcher is not None:
            docked_signal = f"{SIGNAL_VACUUM_DOCKED}_{self._vacuum_id}"
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, docked_signal, self._on_vacuum_docked
                )
            )

        # Seed initial state from any DPS data already held by the vacuum entity
        vacuum_entity = self.hass.data.get(DOMAIN, {}).get(CONF_VACS, {}).get(
            self._vacuum_id
        )
        if vacuum_entity is not None and vacuum_entity.tuyastatus:
            self._on_dps_update(vacuum_entity.tuyastatus)

    # ------------------------------------------------------------------
    # Camera interface
    # ------------------------------------------------------------------

    def _on_vacuum_docked(self) -> None:
        """Triggered when the vacuum transitions to DOCKED state.

        Schedules an async cloud map fetch so the camera shows the floor
        map of the completed cleaning session.
        """
        _LOGGER.debug(
            "Vacuum %s docked – scheduling cloud map fetch", self._vacuum_id
        )
        self.hass.async_create_task(self._fetch_cloud_map())

    async def _fetch_cloud_map(self) -> None:
        """Fetch the latest clean map from Eufy cloud and feed it to the renderer."""
        if self._cloud_fetcher is None:
            return
        try:
            raw = await self._cloud_fetcher.get_latest_map(self._vacuum_id)
        except Exception as exc:
            _LOGGER.warning(
                "Cloud map fetch failed for %s: %s", self._vacuum_id, exc
            )
            return

        if raw is None:
            _LOGGER.debug("Cloud map fetch returned no data for %s", self._vacuum_id)
            return

        # Try to parse the binary blob as a stream.Map protobuf frame.
        try:
            frame = parse_map_frame(raw)
            if frame is not None:
                self._renderer.update_map(frame)
                self.async_write_ha_state()
                _LOGGER.debug(
                    "Cloud map applied for %s (%d bytes)", self._vacuum_id, len(raw)
                )
            else:
                _LOGGER.debug(
                    "Cloud map bytes for %s could not be parsed as stream.Map",
                    self._vacuum_id,
                )
        except Exception as exc:
            _LOGGER.warning(
                "Failed to parse cloud map for %s: %s", self._vacuum_id, exc
            )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return the latest rendered map PNG.

        The renderer returns a grey placeholder until the first map frame
        is received, so this method always returns *something*.
        """
        try:
            return self._renderer.render()
        except Exception as exc:
            _LOGGER.error("Failed to render vacuum map for %s: %s", self._vacuum_id, exc)
            return None

    # ------------------------------------------------------------------
    # DPS update handler
    # ------------------------------------------------------------------

    def _on_dps_update(self, dps: dict[str, Any]) -> None:
        """Decode incoming DPS values and feed them to the renderer.

        Called by the HA dispatcher whenever the vacuum entity receives a
        state push or completes a poll cycle.  Only the DPS codes listed in
        ``map_dps_codes`` are inspected; all others are silently skipped.
        """
        updated = False

        # -- Occupancy grid (stream.Map – I-frames and P-frames) --
        if self._map_dps.map_frame is not None:
            raw = _extract_bytes(dps, self._map_dps.map_frame)
            if raw is not None:
                frame = parse_map_frame(raw)
                if frame is not None:
                    self._renderer.update_map(frame)
                    updated = True
                    _LOGGER.debug(
                        "Map frame received for %s: id=%d type=%d w=%d h=%d compressed=%s",
                        self._vacuum_id,
                        frame.frame_id,
                        frame.frame_type,
                        frame.map_info.width if frame.map_info else 0,
                        frame.map_info.height if frame.map_info else 0,
                        frame.compressed,
                    )

        # -- Robot pose (stream.DynamicData) --
        if self._map_dps.robot_pose is not None:
            raw = _extract_bytes(dps, self._map_dps.robot_pose)
            if raw is not None:
                pose = parse_dynamic_data(raw)
                if pose is not None:
                    self._renderer.update_pose(pose)
                    updated = True

        # -- Cleaning path (stream.PathPoint) --
        if self._map_dps.path_data is not None:
            raw = _extract_bytes(dps, self._map_dps.path_data)
            if raw is not None:
                point = parse_path_point(raw)
                if point is not None:
                    self._renderer.update_path([point])
                    updated = True

        # -- Room outlines (stream.RoomOutline) --
        if self._map_dps.room_outline is not None:
            raw = _extract_bytes(dps, self._map_dps.room_outline)
            if raw is not None:
                outline = parse_room_outline(raw)
                if outline is not None:
                    self._renderer.update_room_outline(outline)
                    updated = True

        if updated:
            self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_bytes(dps: dict[str, Any], code: str) -> bytes | None:
    """Extract the binary payload for *code* from a DPS dict.

    Tuya / Eufy devices deliver binary DPS values as base64-encoded strings.
    This helper handles both the string (base64) and raw-bytes cases.

    Returns ``None`` when *code* is absent from *dps*.
    """
    value = dps.get(code)
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return base64.b64decode(value)
        except Exception:
            # Not valid base64 – treat as raw ASCII payload
            return value.encode()
    return None
