"""Constants for the Kohler Konnect integration."""

from __future__ import annotations

DOMAIN = "kohler"

# Polling interval for the DataUpdateCoordinator (seconds).
SCAN_INTERVAL = 10

# ---------------------------------------------------------------------------
# Config-entry keys
# ---------------------------------------------------------------------------
# username / password come from homeassistant.const (CONF_USERNAME/CONF_PASSWORD).
CONF_APIM_KEY = "apim_subscription_key"
CONF_CLIENT_ID = "client_id"
CONF_API_RESOURCE = "api_resource"
CONF_TENANT_ID = "tenant_id"
# Refresh token issued by the B2C_1A_signin policy. Required for /commands/*
# writes (warmup, presets, valve control) — Kohler's backend rejects the
# ROPC-policy token on those endpoints with HTTP 403. Seed once per account:
#
#   python -m kohler_anthem.b2c_signin url        # prints an /authorize URL
#   # open it, sign in, copy the msauth:// redirect URL from the address bar
#   python -m kohler_anthem.b2c_signin exchange '<msauth://...>'
#
# The exchange step prints the refresh_token to paste into the config flow.
CONF_B2C_REFRESH_TOKEN = "b2c_refresh_token"

# ---------------------------------------------------------------------------
# App-global defaults (baked into the Kohler Konnect mobile app; not secret).
# These match the values the official client uses, so the user does not have
# to supply them. Exposed as advanced fields in the config flow in case Kohler
# rotates them.
# ---------------------------------------------------------------------------
DEFAULT_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
# API resource for the OAuth scope. The library normalizes this into
# "https://konnectkohler.onmicrosoft.com/<guid>/apiaccess". The GUID form is
# the only one Kohler's tenant currently honors (the older "api-mob/access"
# path form fails with AADB2C90205).
DEFAULT_API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"

# Device SKU for the Anthem shower (Graphic Control System).
SKU_GCS = "GCS"
