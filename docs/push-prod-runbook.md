# Web Push production fix runbook (U4.4 / U24.19 / U24.29)

Goal: restore mobile Web Push notifications on `suite.locoroo.net` and verify
the new error monitoring catches regressions. Expected end state: a test email
delivers a push notification ~2-10 seconds after arrival.

## Background (what was broken)

1. `PUSH_VAPID_SUBJECT=ruben@locoroo.net` (no `mailto:`) made py_vapid raise
   `VapidException: Missing 'sub'` on every single send.
2. One transient `idle not supported` at container start permanently downgraded
   the account to 60s polling (fixed: 3-consecutive-failure rule, U4.4).
3. No `push_key_store` row → headless IDLE watchers didn't survive restarts
   (needs one webmail login after deploy to store the wrapped DEK).
4. Dovecot `* OK Still here` keepalives triggered no-op syncs (fixed: filtered).

## Steps (user-run; the app container is NOT deployed by agents)

1. **Ship the app changes.** The fixes are in the app repo on `master`
   (commit 708a350 + the follow-up working-tree changes from 2026-09-20).
   Build/deploy the new image as usual for `/opt/locoroomail/docker-compose.prod.yml`.

2. **Fix the VAPID subject** in `/opt/locoroomail/docker-compose.prod.yml`:
   ```yaml
   PUSH_VAPID_SUBJECT: mailto:ruben@locoroo.net
   ```
   (The app now also auto-normalizes a bare email, but set the correct
   `mailto:` form anyway so config is explicit.)

3. **Raise log level to INFO** (same file) so arming/IDLE transitions are
   visible while verifying:
   ```yaml
   LOG_LEVEL: INFO
   ```
   Can be lowered back to WARNING afterwards. Note: at WARNING the
   `locoroomail_errors` monit check still sees all failure lines (they are
   WARNING/ERROR level); INFO only adds the healthy-state lines.

4. **Restart the app container**:
   ```bash
   cd /opt/locoroomail && docker compose -f docker-compose.prod.yml up -d locoroomail
   ```

5. **Log into webmail once** at https://suite.locoroo.net (any account that
   should get push). This stores the wrapped DEK row and arms headless
   watchers. Watch for in logs:
   ```bash
   docker logs locoroomail --since 2m | grep -E "push key store saved|push armed"
   ```
   Expected: `push key store saved customer_id=1` and `push armed user_id=1`.

6. **Send a test email** to that account from any external address.

7. **Expected**: push notification on the phone ~2-10s after delivery.
   Verify the pipeline in logs if it doesn't show:
   ```bash
   docker logs locoroomail --since 2m | grep -E "push|idle"
   ```

## Monitoring (already installed 2026-09-20, commit 1d6596b in /root/agent)

- Monit check `locoroomail_errors` greps the last 10 minutes of container logs
  for: `push send error`, `push vapid config invalid`, `idle handshake failed`,
  `idle unsupported`, `push arm skipped`, `idle worker error`,
  `worker manager * error`. Alerts go to Matrix (fallback Telegram) after 2
  consecutive failing checks (~2 min at every-2-cycles), recovery alert when
  the window is clean.
- Manual run: `/usr/local/bin/check_locoroomail_errors.sh` (add
  `LOCOROOMAIL_LOG_WINDOW=24h` to widen the window).

## Rollback

- App: redeploy the previous image tag; remove the `LOG_LEVEL` override.
- Monitoring: `rm /etc/monit/conf.d/locoroomail-errors.conf
  /usr/local/bin/check_locoroomail_errors.sh && monit -t && systemctl reload monit`
  and revert commit `1d6596b` in `/root/agent`.
