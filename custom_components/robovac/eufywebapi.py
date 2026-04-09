"""Eufy Web API integration for RoboVac.

Original Work from: Andre Borie https://gitlab.com/Rjevski/eufy-device-id-and-local-key-grabber
"""

from typing import Optional
import requests

eufyheaders = {
    "User-Agent": "EufyHome-Android-2.4.0",
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


class EufyLogon:
    """Class to handle Eufy API authentication and requests."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the EufyLogon class.

        Args:
            username: The user's email address
            password: The user's password
        """
        self.username = username
        self.password = password

    def get_user_info(self) -> Optional[requests.Response]:
        """Get user information from Eufy API.

        Returns:
            Response object or None if connection error occurs.
        """
        login_url = "https://home-api.eufylife.com/v1/user/email/login"
        login_auth = {
            "client_Secret": "GQCpr9dSp3uQpsOMgJ4xQ",
            "client_id": "eufyhome-app",
            "email": self.username,
            "password": self.password,
        }

        try:
            return requests.post(login_url, json=login_auth, headers=eufyheaders)
        except requests.exceptions.ConnectionError:
            return None

    def get_user_settings(
        self, url: str, userid: str, token: str
    ) -> Optional[requests.Response]:
        """Get user settings from Eufy API.

        Args:
            url: Base URL for the API
            userid: User ID
            token: Authentication token

        Returns:
            Response object or None if connection error occurs.
        """
        setting_url = url + "/v1/user/setting"
        eufyheaders["token"] = token
        eufyheaders["id"] = userid
        try:
            return requests.request(
                "GET", setting_url, headers=eufyheaders, timeout=1.5
            )
        except requests.exceptions.ConnectionError:
            return None

    def get_user_center_info(self, token: str) -> Optional[requests.Response]:
        """Get user-center credentials needed for aiot-clean-api authentication.

        The returned response contains ``user_center_id`` and
        ``user_center_token``, which are used as ``gtoken`` (MD5 of
        user_center_id) and ``x-auth-token`` when calling
        ``aiot-clean-api-pr.eufylife.com``.

        Args:
            token: The access token from :meth:`get_user_info`.

        Returns:
            Response object or None if connection error occurs.
        """
        url = "https://api.eufylife.com/v1/user/user_center_info"
        headers = {**eufyheaders, "token": token}
        try:
            return requests.get(url, headers=headers)
        except requests.exceptions.ConnectionError:
            return None

    def get_device_info(
        self, url: str, userid: str, token: str
    ) -> Optional[requests.Response]:
        """Get device information from Eufy API.

        Args:
            url: Base URL for the API
            userid: User ID
            token: Authentication token

        Returns:
            Response object or None if connection error occurs.
        """
        device_url = url + "/v1/device/v2"
        eufyheaders["token"] = token
        eufyheaders["id"] = userid
        try:
            return requests.request("GET", device_url, headers=eufyheaders)
        except requests.exceptions.ConnectionError:
            return None
