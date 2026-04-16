"""Tests for T2277 room-specific clean: feature flags, command entry,
encode_room_clean classmethod, and async_start_room_clean entity method."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.robovac.robovac import RoboVac
from custom_components.robovac.vacuums.base import RobovacCommand, RoboVacEntityFeature
from custom_components.robovac.proto_decode import decode_mode_ctrl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_t2277_robovac() -> RoboVac:
    """Create a T2277 RoboVac instance (Tuya transport mocked out)."""
    with patch("custom_components.robovac.robovac.TuyaDevice.__init__", return_value=None):
        robovac = RoboVac(
            model_code="T2277",
            device_id="test_id",
            host="192.168.1.100",
            local_key="test_key",
        )
        robovac.async_set = AsyncMock(return_value=True)
        return robovac


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestT2277FeatureFlags:
    def test_room_feature_is_set(self, mock_t2277_robovac: RoboVac) -> None:
        features = mock_t2277_robovac.getRoboVacFeatures()
        assert features & RoboVacEntityFeature.ROOM


# ---------------------------------------------------------------------------
# ROOM_CLEAN command entry
# ---------------------------------------------------------------------------


class TestT2277RoomCleanCommand:
    def test_room_clean_command_exists(self, mock_t2277_robovac: RoboVac) -> None:
        commands = mock_t2277_robovac.model_details.commands
        assert RobovacCommand.ROOM_CLEAN in commands

    def test_room_clean_uses_dps_152(self, mock_t2277_robovac: RoboVac) -> None:
        commands = mock_t2277_robovac.model_details.commands
        assert commands[RobovacCommand.ROOM_CLEAN]["code"] == 152

    def test_room_clean_has_no_static_values(self, mock_t2277_robovac: RoboVac) -> None:
        """ROOM_CLEAN has no "values" dict — payload is always dynamic."""
        commands = mock_t2277_robovac.model_details.commands
        assert "values" not in commands[RobovacCommand.ROOM_CLEAN]

    def test_room_clean_dps_code_resolves_to_152(self, mock_t2277_robovac: RoboVac) -> None:
        codes = mock_t2277_robovac.getDpsCodes()
        assert codes["ROOM_CLEAN"] == "152"


# ---------------------------------------------------------------------------
# encode_room_clean classmethod
# ---------------------------------------------------------------------------


class TestT2277EncodeRoomClean:
    def _model(self):
        from custom_components.robovac.vacuums.T2277 import T2277
        return T2277

    def test_has_encode_room_clean(self) -> None:
        assert hasattr(self._model(), "encode_room_clean")

    def test_single_room_decodes_to_room(self) -> None:
        payload = self._model().encode_room_clean([1], clean_times=1)
        assert decode_mode_ctrl(payload) == "room"

    def test_multiple_rooms_decode_to_room(self) -> None:
        payload = self._model().encode_room_clean([1, 2, 3])
        assert decode_mode_ctrl(payload) == "room"

    def test_returns_base64_string(self) -> None:
        import base64
        payload = self._model().encode_room_clean([1])
        # Must be valid base64
        decoded = base64.b64decode(payload)
        assert len(decoded) > 0

    def test_different_rooms_produce_different_payloads(self) -> None:
        T2277 = self._model()
        p1 = T2277.encode_room_clean([1])
        p2 = T2277.encode_room_clean([2])
        p12 = T2277.encode_room_clean([1, 2])
        assert p1 != p2
        assert p1 != p12
        assert p2 != p12

    def test_different_from_auto_clean(self) -> None:
        T2277 = self._model()
        room_payload = T2277.encode_room_clean([1])
        auto_value = T2277.commands[RobovacCommand.MODE]["values"]["auto"]
        assert room_payload != auto_value

    def test_map_id_zero_omits_field(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        T2277 = self._model()
        payload = T2277.encode_room_clean([1], map_id=0)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert 3 not in src_fields  # map_id absent

    def test_map_id_nonzero_included(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        T2277 = self._model()
        payload = T2277.encode_room_clean([1], map_id=99)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields.get(3) == 99

    def test_clean_times_propagated(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        T2277 = self._model()
        payload = T2277.encode_room_clean([1], clean_times=2)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields.get(2) == 2  # SelectRoomsClean.clean_times


# ---------------------------------------------------------------------------
# async_start_room_clean entity method
# ---------------------------------------------------------------------------


class TestAsyncStartRoomClean:
    """Unit tests for RoboVacEntity.async_start_room_clean."""

    def _make_entity(self, vacuum_mock=None):
        """Build a minimal RoboVacEntity with a mocked vacuum."""
        from custom_components.robovac.vacuum import RoboVacEntity
        from homeassistant.const import (
            CONF_ACCESS_TOKEN, CONF_DESCRIPTION, CONF_ID,
            CONF_IP_ADDRESS, CONF_MAC, CONF_MODEL, CONF_NAME,
        )

        item = {
            CONF_NAME: "Test T2277",
            CONF_ID: "test_t2277",
            CONF_MODEL: "T2277",
            CONF_IP_ADDRESS: "192.168.1.100",
            CONF_ACCESS_TOKEN: "key",
            CONF_DESCRIPTION: "eufy Clean L60 SES",
            CONF_MAC: "aa:bb:cc:dd:ee:ff",
        }

        with patch("custom_components.robovac.vacuum.RoboVac") as MockRoboVac:
            if vacuum_mock is None:
                mock_vac = MagicMock()
                mock_vac.getHomeAssistantFeatures.return_value = 0
                mock_vac.getRoboVacFeatures.return_value = (
                    RoboVacEntityFeature.ROOM
                    | RoboVacEntityFeature.DO_NOT_DISTURB
                    | RoboVacEntityFeature.BOOST_IQ
                )
                mock_vac.getRoboVacActivityMapping.return_value = None
                mock_vac.getFanSpeeds.return_value = []
                mock_vac.async_set = AsyncMock(return_value=True)
                # Simulate T2277 model_details with encode_room_clean
                from custom_components.robovac.vacuums.T2277 import T2277
                mock_vac.model_details = T2277
                mock_vac.getDpsCodes.return_value = {"ROOM_CLEAN": "152"}
            else:
                mock_vac = vacuum_mock

            MockRoboVac.return_value = mock_vac
            entity = RoboVacEntity(item)
            entity.vacuum = mock_vac
            # Pre-populate DPS cache so get_dps_code("ROOM_CLEAN") works
            entity._dps_codes_memo["ROOM_CLEAN"] = "152"
        return entity, mock_vac

    @pytest.mark.asyncio
    async def test_sends_dps_152_with_encoded_payload(self) -> None:
        entity, mock_vac = self._make_entity()
        await entity.async_start_room_clean([1, 2])
        mock_vac.async_set.assert_called_once()
        call_args = mock_vac.async_set.call_args[0][0]
        assert "152" in call_args
        # Payload must decode to "room"
        assert decode_mode_ctrl(call_args["152"]) == "room"

    @pytest.mark.asyncio
    async def test_uses_clean_times_param(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        entity, mock_vac = self._make_entity()
        await entity.async_start_room_clean([1], clean_times=3)
        payload = mock_vac.async_set.call_args[0][0]["152"]
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields.get(2) == 3

    @pytest.mark.asyncio
    async def test_noop_when_vacuum_is_none(self) -> None:
        entity, mock_vac = self._make_entity()
        entity.vacuum = None
        await entity.async_start_room_clean([1])
        mock_vac.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_warns_and_noops_for_non_protobuf_model(self) -> None:
        entity, mock_vac = self._make_entity()
        # Remove encode_room_clean to simulate an old model
        mock_vac.model_details = MagicMock(spec=[])  # no encode_room_clean attr
        await entity.async_start_room_clean([1])
        mock_vac.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_warns_and_noops_for_empty_room_ids(self) -> None:
        entity, mock_vac = self._make_entity()
        await entity.async_start_room_clean([])
        mock_vac.async_set.assert_not_called()
