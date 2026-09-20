# Chat (Matrix) — Production Integration Runbook

Status: **ready to execute** (facts re-verified on prod, 2026-09-20; the earlier
revision of this doc was written against assumptions from a previous deployment
and has been corrected).
Audience: operator with root on the production host. The app side needs no deploy
beyond the chat release itself — everything below is configuration.

## Production facts (verified)

| Item | Value |
|---|---|
| Synapse | `matrix-synapse` container, `matrixdotorg/synapse` — **pin the tag, do not run `:latest`** (see caveat 3) |
| Verified against | **Synapse 1.159+** (registration, rooms, messages, receipts via `POST`, authenticated media via `/_matrix/client/v1/media/*`) |
| Auth | `matrix-authentication-service` (MAS) container alongside Synapse; Synapse delegates token introspection to it |
| Config | `homeserver.yaml` — the homeserver **container's mounted config file**. The host path `/etc/matrix-synapse/conf.d/` is a stale leftover from a previous deployment; the running container does **not** read it |
| Database | **PostgreSQL** (not SQLite) |
| Reachability | App container (compose network) → host-networked Synapse via the **compose network gateway IP**, dedicated listener bound to that gateway, port `8008`, TLS **off** |
| Firewall | Allow rule for the **compose subnet** on port 8008 |

## Ops prerequisites (required before wiring the domain)

### 1. Dedicated listener on the compose network gateway

The app container runs in a Docker compose network; Synapse is host-networked.
The only address a bridged container can use to reach a host-networked service
is the compose network's **gateway IP** — and it only works if something on the
host listens on it:

- Discover the gateway (and its subnet — the gateway IP **depends on the
  compose network's subnet** and changes if the subnet ever changes):

  ```bash
  docker network inspect <app-network> \
    --format '{{range .IPAM.Config}}{{.Gateway}} (subnet {{.Subnet}}){{end}}'
  ```

- Bind a **dedicated Synapse listener to that gateway IP** on port `8008`,
  TLS off (plaintext internal hop, same pattern as mail-api/Collabora), in the
  container's mounted `homeserver.yaml`.
- Add a **firewall allow rule for the compose subnet**, e.g.:

  ```bash
  ufw allow from <SUBNET> to any port 8008 proto tcp
  ```

`127.0.0.1:8008` only works from the host itself — never from a bridged
container. Use the gateway IP.

### 2. Homeserver config (`homeserver.yaml`, the container's mounted config)

The following must be set in the container's mounted `homeserver.yaml`
(find the mount with `docker inspect matrix-synapse --format '{{json .Mounts}}'`):

- `registration_shared_secret` — **required**. The app provisions Matrix
  accounts through the shared-secret admin endpoint.
- `user_directory` → `search_all_users: true` — **required for DM/invite user
  discovery**. Without it, user-directory search only returns users who already
  share a room, so users can never find each other on a fresh server.
- `enable_registration: false` — keep open signup off; accounts are provisioned
  by the app, not open signup.

If either value is missing, add it:

```yaml
registration_shared_secret: "<openssl rand -hex 32>"
user_directory:
  search_all_users: true
```

Then restart and confirm health:

```bash
docker restart matrix-synapse
curl -s http://localhost:8008/health   # expect: OK
```

Do **not** edit `/etc/matrix-synapse/conf.d/` on the host — the running
container does not read that path.

### 3. Pin the image version

Replace `matrixdotorg/synapse:latest` (and the weekly `update-matrix.timer`
that pulls `:latest`) with a pinned, tested tag ≥ 1.159 (e.g.
`matrixdotorg/synapse:v1.159.0`) and update deliberately. The Matrix
client-server API moves (this integration already had to adopt `POST /receipt`
and authenticated media); unattended `:latest` pulls will eventually break the
chat module.

## Steps

### 1. Read (or set) the registration shared secret

```bash
grep -E 'server_name|registration_shared_secret|search_all_users' \
  <mounted-config-dir>/homeserver.yaml
```

If `registration_shared_secret` or `search_all_users` is missing, add both per
prerequisite 2 and restart.

### 2. Confirm the app container can reach Synapse

```bash
docker exec locoroomail-app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://<GATEWAY_IP>:8008/health').read())"
```

### 3. Configure the domain in the app admin UI

Admin → Domains → *(domain)* → Contacts & calendar settings page (now includes
Matrix):

- **Matrix host**: the compose network gateway IP from prerequisite 1
- **Matrix port**: `8008`
- **Use TLS**: off
- **Registration shared secret**: `<value from step 1>`

The domain health check should show Chat (Matrix) as *connected*. Two distinct
failure states it can show instead:

- *unreachable* — the homeserver itself cannot be reached (wrong gateway IP,
  listener not bound, firewall rule missing).
- *auth backend down* — the homeserver is up but its authentication backend
  (MAS) is not; see caveat 1.

### 4. Provision the first account and verify

Log in as a customer account, open **Chat** once. This registers the Matrix
user (`@<localpart>:<server_name>`), stores credentials in the user's encrypted
cache, and does the initial sync. Then verify server-side against the
homeserver's PostgreSQL database (names depend on the deployment's compose
setup):

```bash
docker exec <postgres-container> psql -U <synapse-db-user> -d <synapse-db-name> \
  -c "SELECT name FROM users;"
```

Send a message between two provisioned users; both sides should see it live
(SSE stream) and unread badges should reset on room open.

## Caveats & recommendations

1. **MAS down ≠ homeserver down.** When MAS is unavailable, Synapse answers
   every *authenticated* request with HTTP 503
   `{"errcode":"M_UNKNOWN","error":"Unable to introspect the access token"}`,
   while unauthenticated endpoints (e.g. `/_matrix/client/versions`, `/health`)
   stay healthy — easy to misread as "chat server broken". The app reports this
   as `MATRIX_AUTH_BACKEND_DOWN` ("the chat server's authentication backend is
   down; contact the homeserver operator") and the admin health check shows
   *auth backend down* rather than *unreachable*. Recovery means restoring the
   `matrix-authentication-service` container; no app-side action is needed.
2. **MAS / delegated login**: if Synapse delegates `m.login.password` to MAS,
   the module's *re-login fallback* (used only when a stored access token is
   revoked) will fail with 403. Stored tokens are long-lived, so this is rare;
   recovery is re-provisioning the Matrix account. Long-term option: mint
   tokens through MAS. Verify delegation:
   `curl -s http://localhost:8008/_matrix/client/v3/login | python3 -m json.tool`
   (look for `m.login.password` in `flows`).
3. **Pin the Synapse version**: see prerequisite 3.
4. **Media growth**: uploads land in the Synapse media store (container's
   mounted volume; 50 MB/file cap enforced by the app). Monitor disk usage.
5. **Backups**: the homeserver config (`homeserver.yaml`), signing key,
   PostgreSQL database, and media directory are small — include them in
   existing host backups.
