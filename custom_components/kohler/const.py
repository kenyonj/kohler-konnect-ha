"""Constants for Kohler Konnect integration."""

DOMAIN = "kohler"
SCAN_INTERVAL = 10  # seconds

# Auth
B2C_TENANT = "konnectkohler.onmicrosoft.com"
B2C_POLICY = "B2C_1_ROPC_Auth"
B2C_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
B2C_SCOPE = (
    "openid "
    "https://konnectkohler.onmicrosoft.com/"
    "f5d87f3d-bdeb-4933-ab70-ef56cc343744/apiaccess "
    "offline_access"
)
B2C_TOKEN_URL = (
    f"https://konnectkohler.b2clogin.com/{B2C_TENANT}"
    f"/{B2C_POLICY}/oauth2/v2.0/token"
)

# Service token (bootstrap — no user needed)
SERVICE_TOKEN_URL = (
    "https://az-amer-prod-kohlerkonnect-apim.azure-api.net/token/api/v1/token/"
)
SERVICE_TOKEN_APIM_KEY = "ca2f50cbc01845e9af356f866b16c9f1"

# API
API_BASE = "https://api-kohler-us.kohler.io"

# Device SKU
SKU_GCS = "GCS"  # Anthem shower (Grad Control System)
