"""WebAuthn ceremonies. Credential bytes stay in the auth store, never the finance database.

Library errors are not returned to the client: origin, rpId and signature failures
are the same result as an unknown credential.
"""

import json
import os
import secrets
import unicodedata
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    options_to_json,
    parse_authentication_credential_json,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.byteslike_to_bytes import byteslike_to_bytes
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import auth_store

RP_NAME = "Finanza"
_MAX_BODY = 65536


def relying_party(request):
    """rpId and the single origin WebAuthn assertions must present.

    Both come from PUBLIC_ORIGIN. WEBAUTHN_RP_ID may narrow rpId to that host
    or one of its parent domains. Nothing here is a fixed deployment name.
    """
    origin = auth_store.configured_origin()
    if origin:
        host = urlsplit(origin).hostname
    else:
        host = request.url.hostname
        origin = f"{request.url.scheme}://{request.url.netloc}"
    rp_id = os.environ.get("WEBAUTHN_RP_ID", "").strip() or host
    if not auth_store.valid_rp_id(rp_id or "") or not auth_store.host_matches_rp(host, rp_id):
        raise auth_store.AuthStateError("WebAuthn relying party is not configured.")
    return origin, rp_id


def _json_options(options):
    return json.loads(options_to_json(options))


def clean_passkey_name(name):
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        return "Passkey"
    if len(cleaned) > 64 or any(unicodedata.category(ch).startswith("C") for ch in cleaned):
        return None
    return cleaned


def _described(payload, parser):
    try:
        if not isinstance(payload, dict) or len(json.dumps(payload)) > _MAX_BODY:
            return None, None, None
        credential = parser(payload)
        client_data = parse_client_data_json(
            byteslike_to_bytes(credential.response.client_data_json)
        )
        return credential, client_data.challenge, credential.raw_id
    except Exception:
        return None, None, None


def authentication_options(ip, rp_id):
    challenge = secrets.token_bytes(32)
    if auth_store.begin_passkey_authentication(ip, challenge) == "limited":
        return "limited"
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        timeout=auth_store.WEBAUTHN_CHALLENGE_TTL * 1000,
        # Empty allowCredentials is omitted below so the browser uses a
        # discoverable credential and the user does not type a username.
        allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    payload = _json_options(options)
    payload.pop("allowCredentials", None)
    # Prefer a passkey available on this device over a QR/security-key prompt.
    # Older browsers ignore hints; existing external passkeys remain usable.
    payload["hints"] = ["client-device"]
    return payload


def registration_options(session_nonce, rp_id, username):
    challenge = secrets.token_bytes(32)
    started = auth_store.begin_passkey_registration(session_nonce, challenge)
    if isinstance(started, str):
        return started
    _, handle, existing = started
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_name=username,
        user_id=handle,
        user_display_name=username,
        challenge=challenge,
        timeout=auth_store.WEBAUTHN_CHALLENGE_TTL * 1000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=item) for item in existing],
    )
    payload = _json_options(options)
    payload["hints"] = ["client-device"]
    if not payload.get("excludeCredentials"):
        payload.pop("excludeCredentials", None)
    return payload


def credential_id_text(raw):
    return bytes_to_base64url(raw)


def credential_id_bytes(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        raw = base64url_to_bytes(value)
    except Exception:
        return None
    if not raw or len(raw) > 1023:
        return None
    return raw


def _verify_authentication(credential, public_key, sign_count, challenge, origin, rp_id):
    verified = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
        require_user_verification=True,
    )
    return verified.new_sign_count


def finish_authentication(payload, ip, origin, rp_id, nonce, expires):
    credential, claimed, raw_id = _described(payload, parse_authentication_credential_json)
    handle = None
    if credential is not None:
        handle = credential.response.user_handle

    def verify(public_key, sign_count, stored_challenge):
        return _verify_authentication(credential, public_key, sign_count, stored_challenge, origin, rp_id)

    return auth_store.complete_passkey_authentication(
        ip, claimed, raw_id, handle, verify, nonce, expires
    )


def finish_registration(payload, name, session_nonce, origin, rp_id):
    cleaned = clean_passkey_name(name)
    if cleaned is None:
        return "invalid"
    credential, claimed, _raw_id = _described(
        payload if isinstance(payload, dict) else None, parse_registration_credential_json
    )

    def verify(stored_challenge):
        if credential is None:
            raise ValueError
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=stored_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_presence=True,
            require_user_verification=True,
        )
        return (
            verified.credential_id,
            verified.credential_public_key,
            verified.sign_count,
            verified.aaguid,
        )

    return auth_store.complete_passkey_registration(session_nonce, claimed, cleaned, verify)
