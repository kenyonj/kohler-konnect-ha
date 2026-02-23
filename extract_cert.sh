#!/usr/bin/env bash
# extract_cert.sh — Extract the Kohler Konnect mTLS client cert from the official APK.
#
# Usage:
#   ./extract_cert.sh [path-to-kohler.xapk-or-apk]
#
# If no path is given, downloads the XAPK from APKPure automatically.
# Requires: curl, unzip, openssl, python3 (standard macOS/Linux tools).

set -euo pipefail

DEST_DIR="${DEST_DIR:-$HOME/.kohler_konnect}"
CERT_OUT="$DEST_DIR/client.crt"
KEY_OUT="$DEST_DIR/client.key"
P12_PASSWORD="d6jaqQ1nJxFAuXs"
XAPK_URL="https://d.apkpure.com/b/XAPK/com.kohler.hermoth?version=latest"

echo "Kohler Konnect cert extractor"
echo "================================"

mkdir -p "$DEST_DIR"

# ---- Step 1: Get the APK ----
if [ -n "${1:-}" ]; then
  SRC="$1"
  echo "Using provided file: $SRC"
else
  echo "Downloading Kohler Konnect XAPK..."
  SRC="$DEST_DIR/kohler.xapk"
  curl -L -o "$SRC" "$XAPK_URL"
fi

# ---- Step 2: Extract the base APK from XAPK (XAPK = zip of APKs) ----
WORK="$DEST_DIR/work"
rm -rf "$WORK" && mkdir -p "$WORK"

echo "Extracting XAPK..."
unzip -q "$SRC" -d "$WORK" || true  # XAPK may not be a valid zip on some builds

# Find the base APK (largest .apk file in the extracted dir, or named com.kohler.hermoth.apk)
BASE_APK=$(find "$WORK" -name "*.apk" | sort -k1 -rn | head -1 || true)
if [ -z "$BASE_APK" ]; then
  # Maybe the input was already a plain APK
  BASE_APK="$SRC"
fi
echo "Using APK: $BASE_APK"

# ---- Step 3: Extract app_certificate.p12 from the APK ----
P12_OUT="$DEST_DIR/app_certificate.p12"
unzip -p "$BASE_APK" res/raw/app_certificate.p12 > "$P12_OUT" 2>/dev/null || \
  unzip -p "$BASE_APK" assets/app_certificate.p12 > "$P12_OUT" 2>/dev/null || \
  { echo "ERROR: Could not find app_certificate.p12 in APK"; exit 1; }

echo "Extracted p12 cert: $P12_OUT"

# ---- Step 4: Convert P12 → PEM cert + key ----
echo "Converting P12 to PEM..."
openssl pkcs12 -in "$P12_OUT" -clcerts -nokeys -out "$CERT_OUT" \
  -passin "pass:$P12_PASSWORD" -legacy 2>/dev/null || \
openssl pkcs12 -in "$P12_OUT" -clcerts -nokeys -out "$CERT_OUT" \
  -passin "pass:$P12_PASSWORD"

openssl pkcs12 -in "$P12_OUT" -nocerts -nodes -out "$KEY_OUT" \
  -passin "pass:$P12_PASSWORD" -legacy 2>/dev/null || \
openssl pkcs12 -in "$P12_OUT" -nocerts -nodes -out "$KEY_OUT" \
  -passin "pass:$P12_PASSWORD"

# Strip bag attributes (HA just needs clean PEM)
python3 - <<'EOF'
import sys, re

for path in ["'"$CERT_OUT"'", "'"$KEY_OUT"'"]:
    with open(path) as f:
        content = f.read()
    # Keep only PEM blocks
    blocks = re.findall(r'-----BEGIN[^-]+-----.*?-----END[^-]+-----', content, re.DOTALL)
    with open(path, 'w') as f:
        f.write('\n'.join(blocks) + '\n')
    print(f"Cleaned: {path}")
EOF

chmod 600 "$KEY_OUT" "$CERT_OUT"

echo ""
echo "Done!"
echo "  Cert: $CERT_OUT"
echo "  Key:  $KEY_OUT"
echo ""
echo "Add these paths to your Home Assistant configuration:"
echo "  kohler_konnect:"
echo "    cert_path: $CERT_OUT"
echo "    key_path:  $KEY_OUT"
echo ""
echo "Or copy them to your HA config directory and reference them there."
