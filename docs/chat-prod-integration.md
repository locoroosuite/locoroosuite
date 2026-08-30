# Chat (Matrix) — Production Integration Runbook

Status: **ready to execute** (as inspected on prod, 2026-08-29).
Audience: operator with root on the production host. The app side needs no deploy
beyond the chat release itself — everything below is configuration.

## Production facts (verified)

| Item | Value |
|---|---|
| Synapse | `matrix-synapse` container, `matrixdotorg/synapse:latest`, healthy |
| Auth | `matrix-authentication-service` (MAS) container alongside Synapse |
| Config | `/etc/matrix-synapse/conf.d/` + `/etc/matrix-synapse/homeserver.signing.key` |
| Database | SQLite `/var/lib/matrix-synapse/homeserver.db` (~2 MB — greenfield) |
| Media | `/var/lib/matrix-synapse/media` (~4 KB) |
| Listener | `127.0.0.1:8008` (host-side; a `python` process holds the socket) |
| Updates | `update-matrix.timer` — weekly Monday, pulls `:latest` |

The chat module is verified against **Synapse 1.159** (registration, rooms,
messages, receipts via `POST`, authenticated media via `/_matrix/client/v1/media/*`).

## Steps

### 1. Read (or set) the registration shared secret

```bash
grep -rE 'server_name|registration_shared_secret|enable_registration' \
  /etc/matrix-synapse/conf.d/
```

- If `registration_shared_secret` is set: copy the value for step 3. Also verify
  `user_directory` → `search_all_users: true` is present (see below) — add it if
  missing and restart.
- If missing, add it and restart:

```bash
SECRET="$(openssl rand -hex 32)"
cat > /etc/matrix-synapse/conf.d/registration.yaml <<EOF
registration_shared_secret: "$SECRET"
user_directory:
  search_all_users: true
EOF
docker restart matrix-synapse
curl -s http://localhost:8008/health   # expect: OK
```

`enable_registration` should stay **false** — the app provisions accounts through
the shared-secret admin endpoint, not open signup. `search_all_users: true` is
**required**: without it, user-directory search (used by direct messages and
room invites) only returns users who already share a room, so users can never
find each other on a fresh server.

### 2. Determine the host the app container uses to reach Synapse

```bash
docker inspect matrix-synapse --format '{{json .NetworkSettings.Networks}}'
docker inspect locoroomail-app --format '{{json .NetworkSettings.Networks}}'
```

- Same network → Matrix host is the container name (e.g. `matrix-synapse`).
- Different networks → either attach Synapse to the app's network
  (`docker network connect <app-net> matrix-synapse`) or use the docker bridge
  gateway / host LAN IP. Note: `127.0.0.1:8008` only works from the host, not
  from a bridged container.
- Port is `8008`, TLS **off** (plaintext internal hop, same pattern as the
  mail-api/Collabora services).

Verify from the app container:

```bash
docker exec locoroomail-app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://<MATRIX_HOST>:8008/health').read())"
```

### 3. Configure the domain in the app admin UI

Admin → Domains → *(domain)* → Contacts & calendar settings page (now includes
Matrix):

- **Matrix host**: `<host from step 2>`
- **Matrix port**: `8008`
- **Use TLS**: off
- **Registration shared secret**: `<value from step 1>`

The domain health check should show Chat (Matrix) as *connected*.

### 4. Provision the first account and verify

Log in as a customer account, open **Chat** once. This registers the Matrix
user (`@<localpart>:<server_name>`), stores credentials in the user's encrypted
cache, and does the initial sync. Then verify server-side:

```bash
docker exec matrix-synapse sqlite3 /data/homeserver.db \
  "SELECT name FROM users;"
```

Send a message between two provisioned users; both sides should see it live
(SSE stream) and unread badges should reset on room open.

## Caveats & recommendations

1. **MAS / delegated login**: if Synapse delegates `m.login.password` to MAS,
   the module's *re-login fallback* (used only when a stored access token is
   revoked) will fail with 403. Stored tokens are long-lived, so this is rare;
   recovery is re-provisioning the Matrix account. Long-term option: mint
   tokens through MAS. Verify delegation:
   `curl -s http://localhost:8008/_matrix/client/v3/login | python3 -m json.tool`
   (look for `m.login.password` in `flows`).
2. **Pin the Synapse version**: `update-matrix.timer` pulls `:latest` weekly.
   The Matrix client-server API moves (this integration already had to adopt
   `POST /receipt` and authenticated media). Pin the image to the tested
   version (e.g. `matrixdotorg/synapse:v1.159.0`) and update deliberately.
3. **Media growth**: uploads land in `/var/lib/matrix-synapse/media`
   (50 MB/file cap enforced by the app). Monitor disk usage.
4. **Backups**: the homeserver DB + media dir + signing key
   (`/etc/matrix-synapse/homeserver.signing.key`) are small — include them in
   existing host backups.
