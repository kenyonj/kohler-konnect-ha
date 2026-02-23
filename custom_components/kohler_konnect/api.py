"""Kohler Konnect API client."""
from __future__ import annotations

import logging
import os
import ssl
import tempfile
import base64
from typing import Any

import requests

from .const import (
    API_BASE,
    B2C_CLIENT_ID,
    B2C_SCOPE,
    B2C_TOKEN_URL,
    SERVICE_TOKEN_APIM_KEY,
    SERVICE_TOKEN_URL,
    SKU_GCS,
)

_LOGGER = logging.getLogger(__name__)

# The mTLS client certificate (embedded from app_certificate.p12)
# CN=apim-prod-us, valid through Aug 2026
_CERT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIFRzCCAy+gAwIBAgIRAMbPaL5y2K2rP3jfYOGFrJcwDQYJKoZIhvcNAQELBQAw
REPLACE_WITH_REAL_CERT
-----END CERTIFICATE-----
"""
_KEY_PEM = """\
-----BEGIN PRIVATE KEY-----
REPLACE_WITH_REAL_KEY
-----END PRIVATE KEY-----
"""

# Paths on disk (written at runtime)
_CERT_PATH: str | None = None
_KEY_PATH: str | None = None


def _ensure_cert_files() -> tuple[str, str]:
    """Write embedded cert/key to temp files if not already done."""
    global _CERT_PATH, _KEY_PATH
    if _CERT_PATH and _KEY_PATH and os.path.exists(_CERT_PATH):
        return _CERT_PATH, _KEY_PATH

    # Use existing extracted files if present (dev mode)
    if os.path.exists("/tmp/kohler_client.crt"):
        _CERT_PATH = "/tmp/kohler_client.crt"
        _KEY_PATH = "/tmp/kohler_client.key"
        return _CERT_PATH, _KEY_PATH

    # Write embedded cert/key to temp files
    cert_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=".crt", mode="w"
    )
    cert_file.write(_CERT_PEM)
    cert_file.close()

    key_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=".key", mode="w"
    )
    key_file.write(_KEY_PEM)
    key_file.close()

    _CERT_PATH = cert_file.name
    _KEY_PATH = key_file.name
    return _CERT_PATH, _KEY_PATH


class KohlerKonnectAPI:
    """Client for the Kohler Konnect API."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._user_token: str | None = None
        self._apim_key: str | None = None
        self._tenant_id: str | None = None
        self._devices: list[dict] = []

    def _session(self) -> requests.Session:
        """Build a requests session with mTLS client cert."""
        cert, key = _ensure_cert_files()
        s = requests.Session()
        s.cert = (cert, key)
        s.verify = True  # verify server cert normally
        return s

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._user_token}",
            "Ocp-Apim-Subscription-Key": self._apim_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    # Auth                                                                 #
    # ------------------------------------------------------------------ #

    def _fetch_service_token(self) -> None:
        """Fetch the service token + real APIM key (no user needed)."""
        s = self._session()
        resp = s.get(
            SERVICE_TOKEN_URL,
            headers={"Ocp-Apim-Subscription-Key": SERVICE_TOKEN_APIM_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._apim_key = data["apim_key"]
        _LOGGER.debug("Service token fetched, APIM key acquired")

    def _fetch_user_token(self) -> None:
        """Fetch user access token via ROPC flow."""
        resp = requests.post(
            B2C_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": B2C_CLIENT_ID,
                "username": self._username,
                "password": self._password,
                "scope": B2C_SCOPE,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._user_token = data["access_token"]
        # Extract tenant (OID) from token payload
        import json as _json
        payload = self._user_token.split(".")[1]
        # Pad base64
        payload += "=" * (-len(payload) % 4)
        claims = _json.loads(base64.b64decode(payload))
        self._tenant_id = claims.get("oid") or claims.get("sub")
        _LOGGER.debug("User token fetched, tenant_id=%s", self._tenant_id)

    def authenticate(self) -> None:
        """Full auth: service token + user token."""
        self._fetch_service_token()
        self._fetch_user_token()

    # ------------------------------------------------------------------ #
    # Device discovery                                                     #
    # ------------------------------------------------------------------ #

    def get_devices(self) -> list[dict]:
        """Return the list of registered devices."""
        s = self._session()
        resp = s.get(
            f"{API_BASE}/devices/api/v1/device-management/customer-device/{self._tenant_id}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._devices = []
        for home in data.get("customerHome", []):
            for device in home.get("devices", []):
                device["homeId"] = home["homeId"]
                device["homeName"] = home["homeName"]
                self._devices.append(device)
        return self._devices

    # ------------------------------------------------------------------ #
    # State                                                                #
    # ------------------------------------------------------------------ #

    def get_gcs_advanced_state(self, device_id: str) -> dict[str, Any]:
        """Get the full advanced state of a GCS (Anthem) shower."""
        s = self._session()
        resp = s.get(
            f"{API_BASE}/devices/api/v1/device-management/gcs-state/gcsadvancestate/{device_id}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_evo_state(self, device_id: str) -> dict[str, Any]:
        """Get the EVO state (connection state, error state)."""
        s = self._session()
        resp = s.get(
            f"{API_BASE}/devices/api/v1/device-management/evo-state/{device_id}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_presets(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Get all presets and experiences for the customer."""
        tid = tenant_id or self._tenant_id
        s = self._session()
        resp = s.get(
            f"{API_BASE}/devices/api/v1/device-management/customer-experience/{tid}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_all_state(self) -> dict[str, Any]:
        """Fetch all state needed for HA entities."""
        if not self._devices:
            self.get_devices()

        result = {}
        for device in self._devices:
            did = device["deviceId"]
            sku = device.get("sku", "")
            if sku == SKU_GCS:
                result[did] = {
                    "device": device,
                    "advanced_state": self.get_gcs_advanced_state(did),
                    "evo_state": self.get_evo_state(did),
                }
        return result

    # ------------------------------------------------------------------ #
    # Commands                                                             #
    # ------------------------------------------------------------------ #

    def start_warmup(self, device_id: str) -> dict[str, Any]:
        """Start shower warmup (pre-heat, no water running)."""
        s = self._session()
        resp = s.post(
            f"{API_BASE}/platform/api/v1/commands/gcs/warmup",
            headers=self._headers(),
            json={
                "deviceId": device_id,
                "tenantId": self._tenant_id,
                "sku": SKU_GCS,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def start_preset(self, device_id: str, preset_id: str) -> dict[str, Any]:
        """Start a saved preset."""
        s = self._session()
        resp = s.post(
            f"{API_BASE}/platform/api/v1/commands/gcs/startpreset",
            headers=self._headers(),
            json={
                "deviceId": device_id,
                "tenantId": self._tenant_id,
                "sku": SKU_GCS,
                "presetOrExperienceId": preset_id,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def stop_shower(self, device_id: str) -> dict[str, Any]:
        """Stop the shower (solo write system = all valves off)."""
        s = self._session()
        resp = s.post(
            f"{API_BASE}/platform/api/v1/commands/gcs/solowritesystem",
            headers=self._headers(),
            json={
                "deviceId": device_id,
                "tenantId": self._tenant_id,
                "sku": SKU_GCS,
                "anthemValveControlModel": {
                    "valveIndex": "Valve1",
                    "out1": "0",
                    "out2": "0",
                    "out3": "0",
                    "temperatureSetpoint": "0",
                    "flowSetpoint": "0",
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def write_outlet_config(
        self,
        device_id: str,
        valve_index: str = "Valve1",
        outlet: str = "2",
        temperature: float = 39.4,
        flow: int = 19,
    ) -> dict[str, Any]:
        """Set a specific outlet temperature and flow."""
        s = self._session()
        resp = s.post(
            f"{API_BASE}/platform/api/v1/commands/gcs/writeoutletconfig",
            headers=self._headers(),
            json={
                "deviceId": device_id,
                "tenantId": self._tenant_id,
                "sku": SKU_GCS,
                "valveIndex": valve_index,
                "outletIndex": outlet,
                "temperatureSetpoint": str(temperature),
                "flowSetpoint": str(flow),
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
