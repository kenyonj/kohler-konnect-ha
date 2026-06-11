"""B2C_1A_signin OAuth helper for the Kohler Konnect config flow.

Implements the Authorization Code + PKCE flow Kohler's mobile app uses, so the
integration can seed a B2C refresh token without the standalone CLI. The
sign-in itself happens in the user's browser (Kohler's B2C app registration
locks redirect URIs to the mobile app — no localhost/HA redirect exists), so
the flow is: build an /authorize URL, the user signs in, the browser lands on
an unreachable ``msauth://...?code=...`` URL, the user pastes that URL back,
and this module exchanges the code for tokens server-side.

This is a self-contained reimplementation (no dependency on the library's
private helpers) so a library refactor cannot break the config flow. The
authorize-URL shape is verified accepted by B2C (HTTP 200 + transaction
cookies).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass

import aiohttp

from .const import (
    B2C_AUTHORITY,
    B2C_REDIRECT_URI,
    B2C_SCOPE,
    DEFAULT_CLIENT_ID,
)


class OAuthError(Exception):
    """Raised when the B2C sign-in / token exchange fails."""


@dataclass
class PendingSignIn:
    """State carried between the authorize-URL and code-exchange steps."""

    authorize_url: str
    code_verifier: str
    state: str


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_sign_in() -> PendingSignIn:
    """Build a fresh /authorize URL + the PKCE verifier/state to carry forward."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": DEFAULT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": B2C_REDIRECT_URI,
        "scope": B2C_SCOPE,
        "state": state,
        "nonce": secrets.token_urlsafe(16),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "response_mode": "query",
    }
    # safe="/:" keeps the scope URL readable; redirect_uri stays percent-encoded.
    url = f"{B2C_AUTHORITY}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(
        params, safe="/:"
    )
    return PendingSignIn(authorize_url=url, code_verifier=verifier, state=state)


def parse_redirect(redirect_url: str, expected_state: str) -> str:
    """Extract the auth code from the pasted ``msauth://...`` redirect URL.

    Raises OAuthError on a B2C error, a missing code, or a state mismatch.
    """
    redirect_url = redirect_url.strip()
    if not redirect_url.startswith("msauth://"):
        raise OAuthError(
            "That doesn't look like the redirect URL. It should start with "
            "msauth:// and contain ?code=..."
        )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    if "error" in qs:
        desc = qs.get("error_description", [""])[0] or qs["error"][0]
        raise OAuthError(f"Sign-in failed: {desc}")
    code = qs.get("code", [""])[0]
    if not code:
        raise OAuthError("No authorization code found in the pasted URL.")
    returned_state = qs.get("state", [""])[0]
    if expected_state and returned_state != expected_state:
        raise OAuthError("State mismatch — please restart the sign-in step.")
    return code


async def exchange_code(
    session: aiohttp.ClientSession, code: str, code_verifier: str
) -> str:
    """Exchange the auth code for a B2C refresh token. Returns the refresh token.

    Uses the public B2C token endpoint (same request the standalone CLI makes).
    """
    data = {
        "client_id": DEFAULT_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        # The token endpoint wants the *decoded* redirect URI.
        "redirect_uri": urllib.parse.unquote(B2C_REDIRECT_URI),
        "code_verifier": code_verifier,
        "scope": B2C_SCOPE,
    }
    token_url = f"{B2C_AUTHORITY}/oauth2/v2.0/token"
    try:
        async with session.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            payload = await resp.json()
            if resp.status != 200:
                err = (
                    payload.get("error_description")
                    or payload.get("error")
                    or f"HTTP {resp.status}"
                )
                raise OAuthError(f"Token exchange failed: {err}")
    except aiohttp.ClientError as err:
        raise OAuthError(f"Network error during token exchange: {err}") from err

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise OAuthError("No refresh token returned by Kohler.")
    return refresh_token
