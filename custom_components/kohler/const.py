"""Constants for the Kohler Konnect integration."""

from __future__ import annotations

DOMAIN = "kohler"

# Polling interval for the DataUpdateCoordinator (seconds).
SCAN_INTERVAL = 10

# Presets/experiences change only when edited in the Kohler app, so refresh
# them every N state polls (N * SCAN_INTERVAL seconds) rather than every poll.
PRESET_REFRESH_CYCLES = 30

# ---------------------------------------------------------------------------
# Config-entry keys
# ---------------------------------------------------------------------------
# username / password come from homeassistant.const (CONF_USERNAME/CONF_PASSWORD).
CONF_APIM_KEY = "apim_subscription_key"
CONF_CLIENT_ID = "client_id"
CONF_API_RESOURCE = "api_resource"
CONF_TENANT_ID = "tenant_id"
CONF_TEMPERATURE_UNIT = "temperature_unit"
# Refresh token issued by the B2C_1A_signin policy (required for /commands/*
# writes). As of v0.4.0 this is seeded automatically by the config flow's
# sign-in step; users no longer paste it by hand.
CONF_B2C_REFRESH_TOKEN = "b2c_refresh" "_token"

# ---------------------------------------------------------------------------
# App-global defaults (baked into the Kohler Konnect mobile app; not secret).
# Match the values the official client uses, so the user supplies none of them.
# ---------------------------------------------------------------------------
DEFAULT_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
DEFAULT_API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
# Azure APIM subscription key. App-global and stable (verified identical across
# sessions); it identifies the app to Kohler's API gateway and is not a
# per-user secret. Pre-filled in the config flow so users normally supply
# nothing; left overridable in case Kohler rotates it server-side.
DEFAULT_APIM_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

# Device SKU for the Anthem shower (Graphic Control System).
SKU_GCS = "GCS"

# ---------------------------------------------------------------------------
# Device-state literals
# ---------------------------------------------------------------------------
# The device-state payload's warmUpState carries TWO fields: `state`
# (warmUpInProgress / warmUpNotInProgress — is it running now) and `warmUp`
# (warmUpEnabled / warmUpDisabled — is the feature turned on at all). When
# warmup is disabled on the fixture, Kohler's cloud still ACCEPTS the warmup
# command (HTTP 200) but the device silently ignores it — so a warmup toggle
# looks like it does nothing. We read this flag to surface that state and to
# block the command with a clear message instead of a silent no-op.
WARMUP_DISABLED = "warmUpDisabled"

# User-facing message when a warmup command is blocked because the feature is
# turned off on the fixture. Kept here so the switch and water_heater surfaces
# word it identically.
WARMUP_DISABLED_MESSAGE = (
    "Warmup is turned off on the shower itself, so the command has no effect. "
    "Enable Warm Up for this shower in the Kohler Konnect app, then try again."
)

# ---------------------------------------------------------------------------
# Entity services (registered on the water_heater platform).
# ---------------------------------------------------------------------------
SERVICE_START_PRESET = "start_preset"
SERVICE_START_WARMUP = "start_warmup"
SERVICE_STOP_SHOWER = "stop_shower"
SERVICE_PAUSE_SHOWER = "pause_shower"

# ---------------------------------------------------------------------------
# B2C sign-in (OAuth Authorization Code + PKCE) constants for the config flow.
# These build the /authorize URL the user signs in against. The redirect URI is
# the one Kohler registered for its mobile app — there is no localhost/HA
# redirect, so the flow uses manual paste-back of the msauth:// redirect URL.
# Verified live: this URL shape is accepted by B2C (HTTP 200 + transaction
# cookies) as of 2026-06.
# ---------------------------------------------------------------------------
B2C_TENANT = "konnectkohler.onmicrosoft.com"
B2C_SIGNIN_POLICY = "B2C_1A_signin"
B2C_AUTHORITY = (
    f"https://konnectkohler.b2clogin.com/tfp/{B2C_TENANT}/{B2C_SIGNIN_POLICY}"
)
# Registered redirect URI from the Konnect APK's MSAL config. The trailing
# %3D must remain percent-encoded in the authorize query.
B2C_REDIRECT_URI = "msauth://com.kohler.hermoth/2DuDM2vGmcL4bKPn2xKzKpsy68k%3D"
B2C_SCOPE = (
    f"openid offline_access "
    f"https://{B2C_TENANT}/{DEFAULT_API_RESOURCE}/apiaccess"
)
