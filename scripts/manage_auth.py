#!/usr/bin/env python3
"""Enroll/rotate TOTP, revoke sessions, or revoke passkeys locally; never touches financial data."""

import argparse
import getpass
import os
import sys
from pathlib import Path

# Support running directly from the repository, without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyotp
from app.auth_store import AuthStateError, configure, revoke_all, revoke_passkeys, state_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "rotate", "revoke-sessions", "revoke-passkeys"])
    parser.add_argument("--state", default=None, help="Private auth DB path; defaults to AUTH_DB_PATH or private/auth/auth.db.")
    parser.add_argument("--account", default=os.environ.get("APP_USER", "admin"), help="Authenticator display label.")
    args = parser.parse_args()
    try:
        path = state_path(args.state)
        if args.action == "revoke-sessions":
            revoke_all(path)
            print("All sessions revoked.")
            return
        if args.action == "revoke-passkeys":
            count = revoke_passkeys(path)
            label = "passkey" if count == 1 else "passkeys"
            print(f"Revoked {count} {label}. Password and authenticator codes are unchanged.")
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise AuthStateError("Enrollment requires a private interactive terminal; do not redirect secrets to logs.")
        if args.action == "setup" and path.exists():
            raise AuthStateError("Authentication store exists. Use rotate to replace the factor and revoke sessions.")
        secret = pyotp.random_base32()
        print("Add a time-based (TOTP), 6-digit, 30-second entry in your authenticator.")
        print("Keep this key private. Enter it manually in your authenticator:")
        print(secret)
        print("Local provisioning URI (do not open an external QR generator):")
        print(pyotp.TOTP(secret).provisioning_uri(name=args.account, issuer_name="Finanza"))
        code = getpass.getpass("Current authenticator code (input hidden): ").strip()
        codes = configure(path, secret, code, rotate=args.action == "rotate")
        print("Two-factor authentication configured. Store these one-use recovery codes offline:")
        for recovery in codes:
            print(recovery)
        print("These codes will not be shown again. The password is still required.")
        print("Wait for the next authenticator code before logging in. Enable MFA_REQUIRED=true and restart the app.")
    except (AuthStateError, OSError) as exc:
        parser.exit(1, f"Authentication setup failed: {exc}\n")


if __name__ == "__main__":
    main()
