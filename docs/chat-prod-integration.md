# Chat (Matrix) — Production Integration Runbook

Status: **ready to execute** (facts re-verified on prod, 2026-09-27; this
revision reflects the MAS-based provisioning architecture, HLD U25.4/U25.6).
Audience: operator with root on the production host. The app side needs no
deploy beyond the chat release itself — everything below is configuration.

## Production facts (verified)

| Item | Value |
|---|---|
| Synapse | `matrix-synapse` container, `matrixdotorg/synapse:v1.161.0` — **pin the tag, do not run `:latest`** (see caveat 3) |
| Verified against | **Synapse 1.159+ / MAS 1.24+** (MAS admin API user creation, set-password, compat login, rooms, messages, receipts via `POST`, authenticated media) |
| Auth | `matrix-authentication-service` (MAS) container alongside Synapse; Synapse delegates authentication to it. **Synapse's own shared-secret registration and password-login endpoints are disabled in this mode** (`M_UNRECOGNIZED`) — the app therefore provisions and logs in exclusively through MAS |
| Config | Synapse: the homeserver container's mounted `homeserver.yaml`. MAS: the host-mounted config at `/root/codex-matrix-synapse/mas/config.yaml` (container `/data`). The host path `/etc/matrix-synapse/conf.d/` is a stale leftover and is not read |
| Database | **PostgreSQL** (both Synapse and MAS) |
| Reachability | App container (compose network) → host-networked services via the **compose network gateway IP**: dedicated Synapse listener (port 8008, TLS off) and a dedicated MAS listener (port 8080, TLS off) bound to that gateway |
| Firewall | Allow rule for the **compose subnet** on ports 8008 and 8080 |

## Ops prerequisites (required before wiring the domain)

### 1. Dedicated listeners on the compose network gateway

The app container runs in a Docker compose network; Synapse and MAS are
host-networked. The only address a bridged container can use to reach them is
the compose network's **gateway IP** — and only if something on the host
listens on it:

- Discover the gateway (it changes if the compose subnet changes):

  ```bash
  docker network inspect <app-network> \
    --format '{{range .IPAM.Config}}{{.Gateway}} (subnet {{.Subnet}}){{end}}'
  ```

- Synapse already has a listener bound to the gateway IP on port `8008`
  (`client` resource, TLS off) in its mounted `homeserver.yaml`.

- **MAS needs an equivalent listener** with the `compat` and `adminapi`
  resources on the gateway IP (its current listener binds `127.0.0.1` only).
  In the MAS config's `http.listeners`, add:

  ```yaml
  http:
    listeners:
      # ... existing web listener stays ...
      - name: app-gateway
        resources:
          - name: compat      # /_matrix/client/v3/login for the app
          - name: adminapi    # /api/admin/v1 for provisioning
        binds:
          - host: <GATEWAY_IP>
            port: 8080
        proxy_protocol: false
  ```

- Firewall allow rules for the compose subnet:

  ```bash
  ufw allow from <SUBNET> to any port 8008 proto tcp
  ufw allow from <SUBNET> to any port 8080 proto tcp
  ```

### 2. MAS admin OAuth client for the app

The app authenticates to the MAS Admin API with a **client-credentials** OAuth
client holding the `urn:mas:admin` scope. In the MAS config:

```yaml
clients:
  - client_id: <ULID>            # e.g. generate with: mas-cli config generate
    client_auth_method: client_secret_basic
    client_secret: <strong secret>

policy:
  data:
    admin_clients:
      - <ULID>                   # same client_id as above
```

Note: the client **must not** have a `redirect_uris` key (MAS 1.24 rejects
machine clients declared with an empty redirect list), and the MAS config file
must live in a directory containing **only** that config file — extra files
next to it break the config loader.

Then restart and confirm health:

```bash
docker restart matrix-authentication-service
curl -s http://localhost:8081/health   # expect: OK
```

### 3. Synapse homeserver config

Already in place (verified): `matrix_authentication_service.enabled: true`
pointing at MAS, and a listener for the compose gateway. No shared-secret
registration is used by the app anymore; `registration_shared_secret` in the
config is inert for chat. `user_directory` settings are no longer used for
discovery either — the app's chat discovery is database-backed (HLD U25.8).

### 4. Pin the image versions

Keep Synapse pinned (e.g. `v1.161.0`) and MAS pinned (e.g. `1.24.0`); replace
the weekly `update-matrix.timer` that pulls `:latest` with deliberate updates.
The Matrix client-server API moves; unattended pulls will eventually break the
chat module.

## Steps

### 1. Create the admin OAuth client (prerequisite 2)

Keep the client id/secret ready.

### 2. Confirm the app container reaches both services

```bash
docker exec locoroomail-app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://<GATEWAY_IP>:8008/health').read())"
docker exec locoroomail-app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://<GATEWAY_IP>:8080/health').read())"
```

### 3. Configure the domain in the app admin UI

Admin → Domains → *(domain)* → Contacts, calendar & chat settings:

- **Matrix host**: the compose network gateway IP
- **Matrix port**: `8008`
- **Use TLS**: off
- **MAS URL**: `http://<GATEWAY_IP>:8080`
- **MAS OAuth client id / secret**: from step 1
- **Chat visibility**: select which other domains (on the same chat server)
  this domain's users may discover and message. Default: own domain only.

The domain health check should show Chat (Matrix) as *connected*. Distinct
failure states it can show instead:

- *unreachable* — the homeserver cannot be reached (wrong gateway IP, listener
  not bound, firewall rule missing).
- *MAS not set* — homeserver reachable but the MAS config fields are missing.
- *MAS unreachable* — MAS config present but the MAS endpoint is not reachable
  (listener not bound to the gateway, MAS down).
- *auth backend down* — the homeserver is up but its delegated authentication
  backend (MAS) is not; see caveat 1.

### 4. Provision the first account and verify

Log in as a customer account, open **Chat** once. This provisions the user
through the MAS admin API, stores credentials in the user's encrypted cache,
and does the initial sync. Then verify server-side:

```bash
docker exec <postgres-container> psql -U <synapse-db-user> -d <synapse-db-name> \
  -c "SELECT name FROM users;"
```

Pre-existing MAS identities with the same localpart (e.g. `@ruben`) are
**taken over**: the app resets their password to an app-generated one stored
encrypted on the account row. Old sessions of that identity may be revoked
and its password becomes app-managed (HLD U25.6). Homeserver-only identities
without a matching suite account (bots) are untouched and invisible to the
suite's chat discovery.

Send a message between two provisioned users; both sides should see it live
(SSE stream) and unread badges should reset on room open.

## Caveats & recommendations

1. **MAS down ≠ homeserver down.** When MAS is unavailable, Synapse answers
   every *authenticated* request with HTTP 503
   `{"errcode":"M_UNKNOWN","error":"Unable to introspect the access token"}`,
   while unauthenticated endpoints (e.g. `/_matrix/client/v3/versions`,
   `/health`) stay healthy — easy to misread as "chat server broken". The app
   reports this as `MATRIX_AUTH_BACKEND_DOWN` ("the chat server's
   authentication backend is down; contact the homeserver operator") and the
   admin health check shows *auth backend down* rather than *unreachable*.
   Recovery means restoring the `matrix-authentication-service` container; no
   app-side action is needed.
2. **Password re-login self-heals.** Access tokens from the MAS compat login
   are long-lived; if one is revoked, the app re-logs in from its encrypted
   password backups (chat cache + account row). If a user's backup was
   encrypted with an older domain secret (pre-MAS deployments), the first
   provisioning after the migration re-establishes a fresh backup.
3. **Pin Synapse + MAS versions**: see prerequisite 4.
4. **Media growth**: uploads land in the Synapse media store (container's
   mounted volume; 50 MB/file cap enforced by the app). Monitor disk usage.
5. **Backups**: the Synapse config, MAS config (including its OAuth clients
   and signing keys), signing key, PostgreSQL databases, and media directory
   are small — include them in existing host backups. Losing the MAS config
   secret (`secrets.encryption`) loses MAS-stored session data.
