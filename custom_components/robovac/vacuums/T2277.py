"""eufy Clean L60 SES (T2277)"""
import logging
from homeassistant.components.vacuum import VacuumEntityFeature
from .base import RoboVacEntityFeature, RobovacCommand, RobovacModelDetails

_LOGGER = logging.getLogger(__name__)


class T2277(RobovacModelDetails):
    homeassistant_features = (
        VacuumEntityFeature.CLEAN_SPOT
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.STOP
    )
    robovac_features = (
        RoboVacEntityFeature.DO_NOT_DISTURB
        | RoboVacEntityFeature.BOOST_IQ
        | RoboVacEntityFeature.ROOM
    )
    commands = {
        RobovacCommand.MODE: {
            # DPS 152 — ModeCtrlRequest (control.proto)
            # field_1 [varint]  method: 6=START_GOHOME, 12=STOP_TASK, 13=PAUSE_TASK,
            #                          14=RESUME_TASK, 0=START_AUTO_CLEAN (needs field_3)
            # field_2 [varint]  seq:    request sequence number (ignored in decoding)
            # field_3 [message] param:  AutoClean { clean_times=1 } for START_AUTO_CLEAN
            # Decoded by decode_dps() via proto_decode.decode_mode_ctrl().
            "code": 152,
            "values": {
                "standby": "AA==",      # empty payload
                "pause": "AggN",      # method=PAUSE_TASK (13)
                "stop": "AggG",      # method=START_GOHOME (6)
                "return": "AggG",      # method=START_GOHOME (6)
                "auto": "BBoCCAE=",  # param.auto_clean={clean_times=1}
                "nosweep": "AggO",      # method=RESUME_TASK (14)
            },
        },
        RobovacCommand.START_PAUSE: {
            "code": 152,
            "values": {
                "start": "AggO",         # method=RESUME_TASK (14)
                "pause": "AggN",         # method=PAUSE_TASK (13)
            },
            # Note: DPS 156 is an alternative boolean start/pause toggle present on
            # T2267 (base L60), but on T2277 (SES firmware) the primary control path
            # is DPS 152 (ModeCtrlRequest).  DPS 156 is not mapped here to avoid
            # ambiguity; use DPS 152 values above for all start/pause operations.
        },
        RobovacCommand.RETURN_HOME: {
            "code": 152,
            "values": {
                "return": "AggG",        # method=START_GOHOME (6)
            },
        },
        RobovacCommand.STATUS: {
            # DPS 153 — WorkStatus (work_status.proto)
            # field_1  [message] Mode      { value: 0=AUTO, 1=SELECT_ROOM, 2=SELECT_ZONE, 3=SPOT }
            # field_2  [varint]  State:    0=STANDBY, 1=SLEEP, 2=FAULT, 3=CHARGING,
            #                             4=FAST_MAPPING, 5=CLEANING, 6=REMOTE_CTRL,
            #                             7=GO_HOME, 8=CRUISING
            # field_3  [message] Charging  { 0=DOING, 1=DONE, 2=ABNORMAL }
            # field_6  [message] Cleaning  { RunState: 0=DOING, 1=PAUSED }
            # field_8  [message] GoHome    { RunState: 0=DOING, 1=PAUSED }
            # field_10 [message] Relocating — robot relocalizing on map
            # field_11 [message] Breakpoint — will resume after charge
            # Decoded by decode_dps() via proto_decode.decode_work_status().
            "code": 153,
        },
        RobovacCommand.CLEAN_PARAM: {
            # DPS 154 — CleanParamResponse (clean_param.proto)
            # field_1 CleanParam  clean_param         — global default params
            # field_3 CleanParam  area_clean_param    — area-specific params
            # field_4 CleanParam  running_clean_param — currently active params
            # CleanParam: fan (0=QUIET…4=MAX_PLUS), clean_type (0=SWEEP_ONLY…),
            #             clean_extent (0=NORMAL, 1=NARROW), mop_level (0=LOW…2=HIGH)
            # Decoded by decode_dps() via proto_decode.decode_clean_param_response().
            "code": 154,
        },
        RobovacCommand.DIRECTION: {
            # DPS 155 — manual drive direction (write-only trigger, String W)
            # Mirrors T2267 mapping. Values: brake, forward, back, left, right.
            # Used for remote-control / manual drive mode.
            "code": 155,
            "values": {
                "brake": "brake",
                "forward": "forward",
                "back": "back",
                "left": "left",
                "right": "right",
            },
        },
        RobovacCommand.DO_NOT_DISTURB: {
            # DPS 157 — do-not-disturb on/off toggle (Bool RW)
            # Confirmed in T2267 (same 150+ DPS family).
            # T2277 already declares RoboVacEntityFeature.DO_NOT_DISTURB; this
            # code was missing from the mapping, causing fallback to TuyaCodes
            # default "107" which does not exist on this device.
            "code": 157,
        },
        RobovacCommand.FAN_SPEED: {
            "code": 158,
            "values": {
                "quiet": "Quiet",
                "standard": "Standard",
                "turbo": "Turbo",
                "max": "Max",
            },
        },
        RobovacCommand.BOOST_IQ: {
            # DPS 159 — BoostIQ on/off toggle (Bool RW)
            # Auto-increases suction power on carpet. Confirmed in T2267.
            # T2277 already declares RoboVacEntityFeature.BOOST_IQ; this code
            # was missing, causing fallback to TuyaCodes default "118" which
            # does not exist on this device.
            "code": 159,
        },
        RobovacCommand.LOCATE: {
            "code": 160,
            "values": {
                "locate": "true",
            },
        },
        RobovacCommand.BATTERY: {
            "code": 163,
        },
        RobovacCommand.CLEAN_RECORDS: {
            # DPS 164 — CleanRecordList (repeated outer field_4 entries)
            # Each entry: timestamp (Unix seconds) + status code.
            # Decoded by decode_dps() via proto_decode.decode_clean_record_list().
            "code": 164,
        },
        RobovacCommand.ANALYSIS_STATS: {
            # DPS 167 — AnalysisStatistics subset (analysis.proto)
            # Raw integer counters per field for clean / gohome / relocate sub-sessions.
            # Decoded by decode_dps() via proto_decode.decode_analysis_stats().
            "code": 167,
        },
        RobovacCommand.CONSUMABLES: {
            # DPS 168 — ConsumableResponse (consumable.proto)
            # ConsumableRuntime hours: side_brush, rolling_brush, filter_mesh,
            #   scrape, sensor, mop, dustbag, dirty_watertank, dirty_waterfilter.
            # Decoded by decode_dps() via proto_decode.decode_consumable_response().
            "code": 168,
        },
        RobovacCommand.DEVICE_INFO: {
            # DPS 169 — DeviceInfo (app_device_info.proto)
            # Fields: product_name, device_mac, software (firmware), hardware, wifi_name.
            # Decoded by decode_dps() via proto_decode.decode_device_info().
            "code": 169,
        },
        RobovacCommand.WORK_STATUS_V2: {
            # DPS 173 — outer-wrapped WorkStatus used by station-aware firmware variants
            # Outer field_1 = WorkStatus; may omit explicit State/Mode when sub-state
            # messages (Cleaning, GoHome, GoWash) can be used to infer overall state.
            # Decoded by decode_dps() via proto_decode.decode_work_status_v2().
            "code": 173,
        },
        RobovacCommand.UNISETTING: {
            # DPS 176 — UnisettingResponse (unisetting.proto)
            # Fields: wifi_ssid, wifi_signal_pct (0–100), multi_map, custom_clean_mode,
            #         map_valid, children_lock.
            # Decoded by decode_dps() via proto_decode.decode_unisetting_response().
            "code": 176,
        },
        RobovacCommand.ERROR: {
            # DPS 177 — ErrorCode (error_code.proto)
            # field_3  [repeated uint32] warn    — active warning codes
            # field_10 [message]         new_code — newly triggered error/warn codes
            # Codes mapped via T2277_ERROR_CODES in proto_decode.py.
            # Decoded by decode_dps() via proto_decode.decode_error_code().
            "code": 177,
        },
        RobovacCommand.ACTIVE_ERRORS: {
            # DPS 178 — ErrorCode (error_code.proto) with packed field_2 error codes
            # field_2  [packed uint32]   error   — active system error codes
            # Codes mapped via T2277_ERROR_CODES in proto_decode.py.
            # Decoded by decode_dps() via proto_decode.decode_error_code().
            "code": 178,
        },
        RobovacCommand.LAST_CLEAN: {
            # DPS 179 — AnalysisResponse (analysis.proto)
            # Statistics for the most recently completed clean session:
            #   clean_id, success, mode, clean_time_s, clean_area_m2, room_count.
            # Decoded by decode_dps() via proto_decode.decode_analysis_response().
            "code": 179,
        },
        RobovacCommand.ROOM_CLEAN: {
            # DPS 152 — ModeCtrlRequest with method=START_SELECT_ROOMS_CLEAN (1)
            # Payload is built dynamically by encode_room_clean(); no static
            # "values" dict because the payload depends on the requested room IDs.
            # See SelectRoomsClean in control.proto:
            #   repeated Room rooms = 1;  { uint32 id=1; uint32 order=2; }
            #   uint32 clean_times = 2;
            #   uint32 map_id      = 3;   (0 → omit, device uses current map)
            #   uint32 releases    = 4;   (0 → omit)
            "code": 152,
        },
    }

    @classmethod
    def decode_dps(cls, dps_code: int, raw_b64: str) -> str | None:
        """Decode a T2277 DPS value using protobuf. Returns None to fall back to lookup table."""
        from custom_components.robovac.proto_decode import (
            decode_mode_ctrl,
            decode_work_status,
            decode_work_status_v2,
            decode_error_code,
            decode_clean_param_response,
            decode_consumable_response,
            decode_device_info,
            decode_unisetting_response,
            decode_analysis_response,
            decode_clean_record_list,
            decode_analysis_stats,
        )

        try:
            if dps_code == 152:
                return decode_mode_ctrl(raw_b64)
            if dps_code == 153:
                return decode_work_status(raw_b64)
            if dps_code == 154:
                d = decode_clean_param_response(raw_b64)
                rcp = d.get("running_clean_param") or d.get("clean_param") or {}
                return rcp.get("fan") or str(d) if d else None
            if dps_code == 164:
                records = decode_clean_record_list(raw_b64)
                return f"{len(records)} record(s)" if records else None
            if dps_code == 167:
                d = decode_analysis_stats(raw_b64)
                return str(d) if d else None
            if dps_code == 168:
                hours = decode_consumable_response(raw_b64)
                if not hours:
                    return None
                return " | ".join(f"{k}: {v}h" for k, v in hours.items())
            if dps_code == 169:
                d = decode_device_info(raw_b64)
                return str(d) if d else None
            if dps_code == 173:
                return decode_work_status_v2(raw_b64)
            if dps_code == 176:
                d = decode_unisetting_response(raw_b64)
                return str(d) if d else None
            if dps_code == 177:
                return decode_error_code(raw_b64)
            if dps_code == 178:
                return decode_error_code(raw_b64)
            if dps_code == 179:
                d = decode_analysis_response(raw_b64)
                return str(d) if d else None
        except Exception as exc:
            _LOGGER.warning("proto_decode failed for DPS %d value %r: %s", dps_code, raw_b64, exc)
        return None

    @classmethod
    def encode_room_clean(
        cls,
        room_ids: list[int],
        clean_times: int = 1,
        map_id: int = 0,
        releases: int = 0,
        customize: bool = False,
    ) -> str:
        """Encode a DPS 152 payload for START_SELECT_ROOMS_CLEAN (method=1).

        Builds a ModeCtrlRequest with the SelectRoomsClean param (oneof field 4).
        Each room ID is wrapped in a Room sub-message {id, order} as required by
        control.proto.  order defaults to 1-based index position.

        Args:
            room_ids: Device-assigned room IDs from the stored map.
            clean_times: Number of cleaning passes.  Ignored by the device
                         when customize=True (per-room count comes from stored
                         custom params set via the eufy app).
            map_id: Map identifier (0 → device uses the currently loaded map).
            releases: Map version correction number (0 → omit).
            customize: When True, sends SelectRoomsClean.mode = CUSTOMIZE so
                       the device applies the per-room fan speed, clean type,
                       and sweep count already stored on the map (configured
                       via the eufy app).  When False, uses the global
                       area_clean_param from DPS 154 with clean_times passes.

        Returns:
            Base64-encoded protobuf string ready to send on DPS 152.
        """
        from custom_components.robovac.proto_encode import encode_mode_ctrl_rooms
        rooms = [{"id": rid, "order": i + 1} for i, rid in enumerate(room_ids)]
        return encode_mode_ctrl_rooms(rooms, clean_times, map_id, releases, customize)
