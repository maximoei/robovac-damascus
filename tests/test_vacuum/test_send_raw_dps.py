"""Tests for the vacuum.send_raw_dps service and async_send_raw_dps entity method."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.robovac.vacuums.base import RoboVacEntityFeature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entity():
    """Build a minimal RoboVacEntity with a mocked vacuum."""
    from custom_components.robovac.vacuum import RoboVacEntity
    from homeassistant.const import (
        CONF_ACCESS_TOKEN,
        CONF_DESCRIPTION,
        CONF_ID,
        CONF_IP_ADDRESS,
        CONF_MAC,
        CONF_MODEL,
        CONF_NAME,
    )

    item = {
        CONF_NAME: "Test Vacuum",
        CONF_ID: "test_vac",
        CONF_MODEL: "T2277",
        CONF_IP_ADDRESS: "192.168.1.100",
        CONF_ACCESS_TOKEN: "key",
        CONF_DESCRIPTION: "eufy Clean L60 SES",
        CONF_MAC: "aa:bb:cc:dd:ee:ff",
    }

    with patch("custom_components.robovac.vacuum.RoboVac") as MockRoboVac:
        mock_vac = MagicMock()
        mock_vac.getHomeAssistantFeatures.return_value = 0
        mock_vac.getRoboVacFeatures.return_value = RoboVacEntityFeature.ROOM
        mock_vac.getRoboVacActivityMapping.return_value = None
        mock_vac.getFanSpeeds.return_value = []
        mock_vac.async_set = AsyncMock(return_value=True)
        MockRoboVac.return_value = mock_vac
        entity = RoboVacEntity(item)
        entity.vacuum = mock_vac
    return entity, mock_vac


# ---------------------------------------------------------------------------
# async_send_raw_dps entity method
# ---------------------------------------------------------------------------


class TestAsyncSendRawDps:
    @pytest.mark.asyncio
    async def test_forwards_string_value(self) -> None:
        entity, mock_vac = _make_entity()
        await entity.async_send_raw_dps(152, "AggG")
        mock_vac.async_set.assert_called_once_with({"152": "AggG"})

    @pytest.mark.asyncio
    async def test_forwards_integer_value(self) -> None:
        entity, mock_vac = _make_entity()
        await entity.async_send_raw_dps(163, 95)
        mock_vac.async_set.assert_called_once_with({"163": 95})

    @pytest.mark.asyncio
    async def test_dps_code_is_string_key(self) -> None:
        """DPS must be converted to a string for the Tuya API dict key."""
        entity, mock_vac = _make_entity()
        await entity.async_send_raw_dps(158, "Standard")
        call_args = mock_vac.async_set.call_args[0][0]
        assert "158" in call_args
        assert isinstance(list(call_args.keys())[0], str)

    @pytest.mark.asyncio
    async def test_noop_when_vacuum_is_none(self) -> None:
        entity, mock_vac = _make_entity()
        entity.vacuum = None
        await entity.async_send_raw_dps(152, "AggG")
        mock_vac.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_arbitrary_dps_codes_accepted(self) -> None:
        """Any positive integer DPS code must be accepted without error."""
        entity, mock_vac = _make_entity()
        for dps in (1, 100, 152, 176, 255):
            mock_vac.async_set.reset_mock()
            await entity.async_send_raw_dps(dps, "test")
            mock_vac.async_set.assert_called_once_with({str(dps): "test"})
