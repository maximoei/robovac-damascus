"""Unit tests for proto_encode module.

Verifies that every encoder produces the exact same base64 output as the
hardcoded values used in T2277.py, and that encoded payloads round-trip
correctly through proto_decode.decode_mode_ctrl().
"""

import base64
import pytest

from custom_components.robovac.proto_encode import (
    _encode_varint,
    _field_varint,
    _field_bytes,
    _with_length_prefix,
    encode_mode_ctrl_simple,
    encode_mode_ctrl_auto,
    encode_mode_ctrl_rooms,
)
from custom_components.robovac.proto_decode import decode_mode_ctrl


# ============================================================================
# Low-level primitive tests
# ============================================================================


class TestEncodeVarint:
    def test_zero(self) -> None:
        assert _encode_varint(0) == b"\x00"

    def test_one(self) -> None:
        assert _encode_varint(1) == b"\x01"

    def test_127_single_byte(self) -> None:
        assert _encode_varint(127) == b"\x7f"

    def test_128_two_bytes(self) -> None:
        # 128 = 0x80 → varint [0x80, 0x01]
        assert _encode_varint(128) == b"\x80\x01"

    def test_300(self) -> None:
        # 300 = varint [0xAC, 0x02]
        assert _encode_varint(300) == b"\xac\x02"

    def test_roundtrip_with_decoder(self) -> None:
        from custom_components.robovac.proto_decode import _parse_varint
        for v in (0, 1, 13, 14, 127, 128, 300, 16383, 16384):
            encoded = _encode_varint(v)
            decoded, pos = _parse_varint(encoded, 0)
            assert decoded == v, f"Round-trip failed for {v}"
            assert pos == len(encoded)


class TestFieldVarint:
    def test_field1_value6(self) -> None:
        # field_num=1, wire_type=0 → tag=0x08; value=6 → 0x06
        assert _field_varint(1, 6) == b"\x08\x06"

    def test_field1_value13(self) -> None:
        assert _field_varint(1, 13) == b"\x08\x0d"

    def test_field1_value14(self) -> None:
        assert _field_varint(1, 14) == b"\x08\x0e"

    def test_field2_value1(self) -> None:
        # field_num=2, wire_type=0 → tag=0x10; value=1 → 0x01
        assert _field_varint(2, 1) == b"\x10\x01"


class TestFieldBytes:
    def test_field3_two_bytes(self) -> None:
        # field_num=3, wire_type=2 → tag=0x1A; length=2; data=0x08 0x01
        data = b"\x08\x01"
        result = _field_bytes(3, data)
        assert result == b"\x1a\x02\x08\x01"

    def test_field4_empty(self) -> None:
        # field_num=4, wire_type=2 → tag=0x22; length=0
        assert _field_bytes(4, b"") == b"\x22\x00"


class TestWithLengthPrefix:
    def test_empty_data(self) -> None:
        # 0 bytes → prefix=0x00 → base64 of b'\x00' = "AA=="
        assert _with_length_prefix(b"") == "AA=="

    def test_two_bytes(self) -> None:
        # b'\x08\x06' → prefix=0x02 → b'\x02\x08\x06' → "AggG"
        assert _with_length_prefix(b"\x08\x06") == "AggG"


# ============================================================================
# ModeCtrlRequest encoder vs known hardcoded T2277 values
# ============================================================================


class TestEncodeModeCtrlSimple:
    """Verify against the exact base64 constants from T2277 commands dict."""

    def test_standby_empty_message(self) -> None:
        assert encode_mode_ctrl_simple(0) == "AA=="

    def test_gohome_method6(self) -> None:
        assert encode_mode_ctrl_simple(6) == "AggG"

    def test_pause_method13(self) -> None:
        assert encode_mode_ctrl_simple(13) == "AggN"

    def test_resume_method14(self) -> None:
        assert encode_mode_ctrl_simple(14) == "AggO"

    def test_stop_task_method12(self) -> None:
        # method=12 (STOP_TASK) — not currently in T2277 values but must encode
        result = encode_mode_ctrl_simple(12)
        raw = base64.b64decode(result)[1:]  # strip length prefix
        assert raw == b"\x08\x0c"

    def test_fast_mapping_method9(self) -> None:
        result = encode_mode_ctrl_simple(9)
        raw = base64.b64decode(result)[1:]
        assert raw == b"\x08\x09"


class TestEncodeModeCtrlAuto:
    def test_default_clean_times_1(self) -> None:
        assert encode_mode_ctrl_auto(1) == "BBoCCAE="

    def test_clean_times_2(self) -> None:
        result = encode_mode_ctrl_auto(2)
        # Should differ from clean_times=1
        assert result != encode_mode_ctrl_auto(1)
        # Decode must still give "auto"
        assert decode_mode_ctrl(result) == "auto"

    def test_round_trip_decode(self) -> None:
        assert decode_mode_ctrl(encode_mode_ctrl_auto(1)) == "auto"
        assert decode_mode_ctrl(encode_mode_ctrl_auto(2)) == "auto"


# ============================================================================
# encode_mode_ctrl_rooms — round-trip and structural tests
# ============================================================================


class TestEncodeModeCtrlRooms:
    def test_single_room_decodes_to_room(self) -> None:
        """Encoded room-clean payload must decode to 'room' (method=1)."""
        payload = encode_mode_ctrl_rooms([{"id": 1}], clean_times=1)
        assert decode_mode_ctrl(payload) == "room"

    def test_multiple_rooms_decode_to_room(self) -> None:
        payload = encode_mode_ctrl_rooms([{"id": 1}, {"id": 2}, {"id": 3}])
        assert decode_mode_ctrl(payload) == "room"

    def test_method_field_is_1(self) -> None:
        """Verify raw bytes contain method=1 as the first varint field."""
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 1}])
        fields = _parse_proto(_strip_length_prefix(payload))
        assert fields.get(1) == 1  # method = START_SELECT_ROOMS_CLEAN

    def test_select_rooms_clean_in_field4(self) -> None:
        """Verify the SelectRoomsClean param is encoded in oneof field 4."""
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 2}])
        fields = _parse_proto(_strip_length_prefix(payload))
        # field 3 (auto_clean) must be absent; field 4 (select_rooms_clean) present
        assert 3 not in fields
        assert 4 in fields

    def test_room_id_and_order_encoded(self) -> None:
        """Verify Room sub-message contains id and order."""
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 5, "order": 2}])
        outer = _parse_proto(_strip_length_prefix(payload))
        src_bytes = outer[4]  # SelectRoomsClean bytes
        src_fields = _parse_proto(src_bytes)
        room_bytes = src_fields[1]  # repeated Room rooms = 1 (first entry)
        room_fields = _parse_proto(room_bytes)
        assert room_fields[1] == 5  # Room.id
        assert room_fields[2] == 2  # Room.order

    def test_default_order_is_1based_index(self) -> None:
        """Without explicit order, rooms get order=1,2,3,..."""
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 10}, {"id": 20}])
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        rooms = src_fields[1]  # list when repeated
        if not isinstance(rooms, list):
            rooms = [rooms]
        orders = []
        for rb in rooms:
            rf = _parse_proto(rb)
            orders.append(rf.get(2))
        assert orders == [1, 2]

    def test_clean_times_encoded(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 1}], clean_times=3)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields[2] == 3  # SelectRoomsClean.clean_times

    def test_map_id_omitted_when_zero(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 1}], map_id=0)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert 3 not in src_fields  # map_id field absent

    def test_map_id_included_when_nonzero(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 1}], map_id=42)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields[3] == 42  # SelectRoomsClean.map_id

    def test_releases_included_when_nonzero(self) -> None:
        from custom_components.robovac.proto_decode import _parse_proto, _strip_length_prefix
        payload = encode_mode_ctrl_rooms([{"id": 1}], releases=7)
        outer = _parse_proto(_strip_length_prefix(payload))
        src_fields = _parse_proto(outer[4])
        assert src_fields[4] == 7  # SelectRoomsClean.releases

    def test_different_from_auto_clean(self) -> None:
        """Room clean and auto clean payloads must be distinct."""
        assert encode_mode_ctrl_rooms([{"id": 1}]) != encode_mode_ctrl_auto(1)

    def test_different_from_standby(self) -> None:
        assert encode_mode_ctrl_rooms([{"id": 1}]) != encode_mode_ctrl_simple(0)
