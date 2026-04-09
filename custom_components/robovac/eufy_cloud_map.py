"""Eufy cloud map fetcher.

Retrieves the stored floor map for the most recent cleaning session from the
Eufy cloud API (``aiot-clean-api-pr.eufylife.com``).

Authentication chain
--------------------
1. POST ``https://home-api.eufylife.com/v1/user/email/login``
   → ``access_token``
2. GET  ``https://api.eufylife.com/v1/user/user_center_info``
   with ``token: access_token`` header
   → ``user_center_token``, ``user_center_id``
   ``gtoken = MD5(user_center_id)`` (computed locally)
3. Subsequent calls to ``aiot-clean-api-pr.eufylife.com`` use:
   ``x-auth-token: user_center_token``, ``gtoken: MD5(user_center_id)``,
   ``app-name: eufy_home``, ``model-type: PHONE``, ``os-version: Android``

Map retrieval strategy
----------------------
The module tries a short list of candidate endpoints in order and uses the
first one that returns a map URL or binary blob.  Device identifiers are
passed as ``device_sn`` in the JSON body of POST requests.

The raw bytes returned by :meth:`EufyCloudMapFetcher.get_latest_map` are
intended to be passed directly to
:func:`~.proto_parser.parse_map_frame` (stream.Map protobuf format), but
the module does **not** import ``proto_parser`` – parsing is the caller's
responsibility.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

_LOGIN_URL = "https://home-api.eufylife.com/v1/user/email/login"
_USER_CENTER_URL = "https://api.eufylife.com/v1/user/user_center_info"
_AIOT_BASE = "https://aiot-clean-api-pr.eufylife.com"

_LOGIN_HEADERS = {
    "User-Agent": "EufyHome-Android-3.1.3-753",
    "timezone": "Europe/London",
    "category": "Home",
    "token": "",
    "uid": "",
    "openudid": "sdk_gphone64_arm64",
    "clientType": "2",
    "language": "en",
    "country": "US",
    "Accept-Encoding": "gzip",
}

# Candidate POST endpoints to try in order when fetching the latest clean map.
# Payload for all: {"device_sn": <device_id>}
_MAP_ENDPOINTS = [
    "/app/devicemanage/get_map_data",
    "/app/devicemanage/get_clean_record",
    "/app/devicemanage/get_clean_history",
    "/app/map/get_clean_map",
    "/app/clean/get_clean_record_list",
    "/app/map/get_map_list",
]

# Keys (nested or flat) that may hold a downloadable map URL in the response.
_MAP_URL_KEYS = ("map_url", "mapUrl", "url", "file_url", "fileUrl")


class EufyCloudMapFetcher:
    """Async helper that authenticates with Eufy cloud and fetches stored maps.

    Tokens are cached and reused across calls; re-authentication is attempted
    automatically if a request returns an auth-failure response code.

    Args:
        username: Eufy account e-mail address.
        password: Eufy account password.
        session:  Optional ``aiohttp.ClientSession`` to reuse.  If *None*,
                  a new session is created lazily and closed by :meth:`close`.
    """

    def __init__(
        self,
        username: str,
        password: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._owns_session = session is None

        self._user_center_token: str | None = None
        self._gtoken: str | None = None
        # Random 32-hex openudid generated once per instance (like a device ID)
        self._openudid: str = os.urandom(16).hex()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session (only if we created it)."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Perform the two-step Eufy authentication flow.

        Returns:
            ``True`` on success, ``False`` if any step fails.
        """
        session = await self._get_session()

        # Step 1 – login
        login_body = {
            "client_Secret": "GQCpr9dSp3uQpsOMgJ4xQ",
            "client_id": "eufyhome-app",
            "email": self._username,
            "password": self._password,
        }
        try:
            async with session.post(
                _LOGIN_URL, json=login_body, headers=_LOGIN_HEADERS
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Eufy login failed: HTTP %d", resp.status
                    )
                    return False
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            _LOGGER.warning("Eufy login request failed: %s", exc)
            return False

        if data.get("res_code") != 1:
            _LOGGER.warning(
                "Eufy login rejected (res_code=%s)", data.get("res_code")
            )
            return False

        access_token: str = data.get("access_token", "")
        if not access_token:
            _LOGGER.warning("Eufy login: no access_token in response")
            return False

        # Step 2 – user_center_info
        headers = {**_LOGIN_HEADERS, "token": access_token}
        try:
            async with session.get(
                _USER_CENTER_URL, headers=headers
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "user_center_info failed: HTTP %d", resp.status
                    )
                    return False
                uc_data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            _LOGGER.warning("user_center_info request failed: %s", exc)
            return False

        # The response may nest data under different keys depending on region.
        payload: dict[str, Any] = (
            uc_data.get("user_info")
            or uc_data.get("data")
            or uc_data
        )

        self._user_center_token = (
            payload.get("user_center_token")
            or payload.get("userCenterToken")
            or ""
        )

        # gtoken = MD5(user_center_id) – computed locally, not fetched.
        user_center_id: str = (
            payload.get("user_center_id")
            or payload.get("userCenterId")
            or ""
        )
        if user_center_id:
            self._gtoken = hashlib.md5(user_center_id.encode()).hexdigest()
        else:
            self._gtoken = ""

        if not self._user_center_token:
            _LOGGER.warning(
                "user_center_info: could not extract user_center_token. "
                "Keys present: %s",
                list(payload.keys()),
            )
            return False

        _LOGGER.debug(
            "Eufy cloud authentication successful (gtoken computed from user_center_id=%s)",
            bool(user_center_id),
        )
        return True

    # ------------------------------------------------------------------
    # Map fetching
    # ------------------------------------------------------------------

    def _aiot_headers(self) -> dict[str, str]:
        return {
            "user-agent": "EufyHome-Android-3.1.3-753",
            "timezone": "Europe/London",
            "openudid": self._openudid,
            "language": "en",
            "country": "US",
            "os-version": "Android",
            "model-type": "PHONE",
            "app-name": "eufy_home",
            "x-auth-token": self._user_center_token or "",
            "gtoken": self._gtoken or "",
            "content-type": "application/json; charset=UTF-8",
        }

    async def get_latest_map(self, device_id: str) -> bytes | None:
        """Return raw map bytes for the most recent cleaning session.

        Tries several candidate API endpoints in order and returns the first
        binary payload found.  Returns ``None`` if every endpoint fails or
        no map data is available.

        Args:
            device_id: The Tuya/Eufy device ID (``CONF_ID`` from config).

        Returns:
            Raw bytes of the latest map (stream.Map protobuf format), or
            ``None`` if unavailable.
        """
        if not self._user_center_token:
            ok = await self.authenticate()
            if not ok:
                return None

        session = await self._get_session()
        headers = self._aiot_headers()
        payload = {"device_sn": device_id}

        for path in _MAP_ENDPOINTS:
            url = _AIOT_BASE + path
            try:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status in (401, 403):
                        _LOGGER.debug(
                            "Auth error on %s; re-authenticating", path
                        )
                        self._user_center_token = None
                        ok = await self.authenticate()
                        if not ok:
                            return None
                        headers = self._aiot_headers()
                        continue
                    if resp.status != 200:
                        _LOGGER.debug(
                            "POST %s → HTTP %d; trying next endpoint",
                            path, resp.status,
                        )
                        continue

                    content_type = resp.headers.get("Content-Type", "")
                    if "application/json" in content_type or "text/" in content_type:
                        body = await resp.json(content_type=None)
                        map_bytes = await _extract_map_from_json(body, session)
                        if map_bytes is not None:
                            _LOGGER.debug(
                                "Cloud map obtained via %s (%d bytes)",
                                path, len(map_bytes),
                            )
                            return map_bytes
                        _LOGGER.debug(
                            "POST %s returned JSON but no map data; trying next",
                            path,
                        )
                    else:
                        # Direct binary response
                        raw = await resp.read()
                        if raw:
                            _LOGGER.debug(
                                "Cloud map binary from %s (%d bytes)",
                                path, len(raw),
                            )
                            return raw

            except aiohttp.ClientError as exc:
                _LOGGER.debug("Request to %s failed: %s", path, exc)
                continue

        _LOGGER.warning(
            "No cloud map found for device %s after trying all endpoints",
            device_id,
        )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _extract_map_from_json(
    body: Any,
    session: aiohttp.ClientSession,
) -> bytes | None:
    """Walk a JSON response looking for a downloadable map URL or inline data.

    If a URL is found, downloads it and returns the raw bytes.
    If base64-encoded inline map data is found, decodes and returns it.
    Returns ``None`` if nothing actionable is present.
    """
    import base64

    if not isinstance(body, dict):
        return None

    # Unwrap common envelope wrappers
    data: Any = body.get("data") or body.get("result") or body
    if isinstance(data, list) and data:
        data = data[0]  # take the most recent record

    if not isinstance(data, dict):
        return None

    # Check for a direct map URL field
    for key in _MAP_URL_KEYS:
        url = data.get(key)
        if isinstance(url, str) and url.startswith("http"):
            _LOGGER.debug("Found map URL in response key=%s: %s…", key, url[:80])
            return await _download_url(url, session)

    # Check for base64-encoded inline map data
    for key in ("map_data", "mapData", "map_content", "content"):
        val = data.get(key)
        if isinstance(val, str) and len(val) > 100:
            try:
                return base64.b64decode(val)
            except Exception:
                pass

    return None


async def _download_url(
    url: str, session: aiohttp.ClientSession
) -> bytes | None:
    """Download binary data from *url* and return the raw bytes."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                _LOGGER.debug("Map URL download failed: HTTP %d", resp.status)
                return None
            return await resp.read()
    except aiohttp.ClientError as exc:
        _LOGGER.debug("Map URL download error: %s", exc)
        return None
