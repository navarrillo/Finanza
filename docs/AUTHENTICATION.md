# Two-factor authentication and sessions

Production login requires the configured username, password, and either a six-digit TOTP authenticator code or one unused recovery code. TOTP uses PyOTP (RFC 6238), 30-second steps and at most one adjacent step of clock tolerance. The server must keep its clock synchronized. No email, SMS, remote QR service or external identity provider is involved.

Enrollment and recovery administration run in a private terminal on your computer/server, never through an unauthenticated web page. The website only accepts login credentials; it cannot reveal, enroll, disable or replace a factor. This installation still has one configured user (`APP_USER`), not a multi-user account system.

## Existing installations: required upgrade step

**Password-only installations must enroll before restarting the upgraded app.** Installations already using MFA keep their authenticator and recovery codes; the separate auth store upgrades automatically as described below. MFA is enabled by default. A missing, invalid or unwritable authentication store prevents startup; losing it during operation returns an authentication error, never password-only access. Existing session cookies are invalidated by this upgrade. No finance database migration is needed.

The authentication database contains the TOTP secret, hashes of recovery codes, hashed session identifiers, activity/expiry timestamps and hashed-address login failures. It is **separate from `finanzas.db`**. Only the authentication directory is writable by the dashboard; the financial data directory remains mounted read-only. The TOTP secret must be recoverable for verification and is stored in the private file, not hashed or encrypted at rest. Protect the directory, backups and server access accordingly; recovery codes and session identifiers are stored only as hashes.

### Docker

Keep your current password, session secret and finance database settings. Add/update these values in your private `.env`:

```dotenv
DEMO_MODE=false
MFA_REQUIRED=true
AUTH_DIRECTORY=./private/auth
AUTH_DB_PATH=./private/auth/auth.db
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://finanza.example.com
```

Replace the public origin with your own HTTPS domain. Compose uses `AUTH_DIRECTORY` as the host bind mount and sets the container's `AUTH_DB_PATH=/auth/auth.db`; the `.env` path above is for local tools. If you choose an absolute authentication directory, adjust both host values and commands. Do not put it in the finance data directory or a public web directory.

From the repository root on the Linux server:

```bash
mkdir -p private/auth
sudo chown 10001:10001 private/auth
sudo chmod 700 private/auth
docker compose build
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py setup --state /auth/auth.db
```

The last command is interactive. Add the displayed secret manually to a TOTP authenticator (six digits, 30 seconds), then enter a current code to prove setup worked. It also displays an `otpauth://` URI for compatible local tools; do not paste it into an online QR generator. Store the ten recovery codes offline, separately from your password. Each code replaces the TOTP code for **one login** and still requires your password. They cannot be displayed again.

Only after enrollment succeeds:

```bash
docker compose up -d --force-recreate
```

Wait for the authenticator's next code: the setup confirmation code is already consumed. Open your HTTPS domain and sign in with both factors. Keep the app behind the reverse proxy and preserve its private port binding. A missing authentication directory makes Compose refuse the mount rather than create a directory owned by the wrong user.

### Without Docker

Install `requirements.txt` into your virtual environment. For a new real installation run `python3 scripts/setup.py`; for an existing installation preserve your `.env` and add `MFA_REQUIRED=true`, `AUTH_DB_PATH=./private/auth/auth.db` and `DEMO_MODE=false`.

```bash
source .venv/bin/activate
python scripts/manage_auth.py setup --state ./private/auth/auth.db
```

Enrollment creates the file with owner-only read/write permissions. Run the app and enrollment tool under the same OS account, or arrange equivalent directory access. Load your `.env` and start Uvicorn as described in the README. The CLI does not load `.env` itself; pass `--state` explicitly when its path differs from the default. `--account` changes the authenticator's display label only; it does not configure `APP_USER`.

## Monthly use

The password step does not create an authenticated session or grant access to data. It issues a signed HttpOnly cookie and a pending record in the auth store, valid for five minutes and at most five wrong code attempts in addition to per-address/global limits. At most thirty pending challenges can exist at once. The code step atomically consumes both proof and factor before creating a session. Refreshing the page returns to the password form.

Upgrading the original MFA implementation automatically upgrades only the separate auth store to version 2 to hold pending challenges. Existing enrollment and recovery codes remain valid; old session cookies require signing in again. No financial schema or data migration occurs.

Enter your username and password, then select **Continue**. A separate form asks only for the authenticator code. **Use a recovery code** switches that second form to recovery mode (hyphens are optional); **Back to sign in** cancels the pending proof. The password is cleared from the form after step one and is never sent again with the code. Codes are never returned by an API or stored in browser storage.

- Each TOTP time step and recovery code can be used once. Consumption is atomic across simultaneous requests and persists across app restarts.
- Failed logins are limited to eight per observed address and thirty globally per five minutes. Both password and factor failures count; the MFA limits persist across restarts. The global limit can temporarily block legitimate login attempts during an attack. Wait for the window to clear; restarting is not a bypass.
- MFA sessions expire after one hour, or fifteen minutes without an authenticated API request. Activity cannot extend the absolute lifetime. The browser also clears the dashboard after fifteen minutes without a successful authenticated API response. Reloading an expired session requires both factors again.
- Logout deletes the registered session, so a copied cookie stays invalid after restart. Changing `APP_PASSWORD`, `APP_USER`, `SESSION_SECRET` or MFA mode invalidates existing signed cookies. Rotating the factor deletes all registered sessions.
- Session cookies remain HttpOnly, SameSite Strict and Secure when `COOKIE_SECURE=true`. A valid session is still a bearer credential: MFA does not protect a cookie that has already been stolen from an active session.

The local HTTP demo is the only password-only mode: `MFA_REQUIRED=false`, `DEMO_MODE=true`, `COOKIE_SECURE=false`, with no `PUBLIC_ORIGIN`. Setup with `--demo` writes those settings; upgrading an existing demo requires adding them and creating `private/auth/` if absent (running setup again creates that directory without overwriting `.env`). Demo logout revocation and login limits remain in memory; production MFA uses persistent state. Never expose this demo mode on a public domain.

## Lost phone, rotation and session revocation

Use an unused recovery code **together with your password** to log in. Rotate enrollment through your private server terminal, confirm the new authenticator, and save the new recovery codes:

```bash
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py rotate --state /auth/auth.db
```

Rotation invalidates the old authenticator, all old recovery codes and all registered sessions immediately; no app restart is required. It refuses to modify a file that is not a Finanza authentication store. If you lose both phone and recovery codes, server administrator access is required for this operation; there is no email reset or password-only web bypass. Protect SSH access as carefully as the app login.

To revoke every session without replacing the authenticator:

```bash
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py revoke-sessions --state /auth/auth.db
```

Without Docker, use the same actions with `python scripts/manage_auth.py` and the local `--state` path. An interrupted/failed setup leaves MFA unavailable until an administrator resolves it; never turn off MFA to work around a production configuration error.

Keep an access-controlled, consistent backup of authentication state. Restoring an old backup rolls back consumed codes, failures and revocations: **stop the app, restore, rotate enrollment and recovery codes, change `SESSION_SECRET`, then restart**. Do not deploy cloned copies of the same authentication store as independent live servers. All processes of one installation must share this local filesystem store; network filesystems and distributed replicas are outside this design.

## Scope and references

TOTP adds a second factor when a password is stolen. It is susceptible to real-time phishing; passkeys/WebAuthn would provide stronger origin-bound authentication in a future change. This PR does not implement passkeys, multi-user enrollment, remembered devices, or remote password/MFA resets.

Implementation guidance: [PyOTP security checklist](https://pyauth.github.io/pyotp/), [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238), and [OWASP MFA guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).
