# Kimi direct transport (experimental)

The direct transport uses Kimi's existing account session, without browser tabs
while answering. It is opt-in and exposes only `kimi-instant` (`k2d6-chat`). The
separate `kimi-web` adapter continues to use the model selected in a browser tab.
K3, K2.8 Preview, agent mode and paid capabilities are not qualified here.

On 2026-10-10 one direct Instant completion returned the exact synthetic marker
with the owned Safari tab closed. A subsequent explicit refresh returned HTTP
200, rotated both credentials and granted 900 seconds of access-token lifetime.
The response uses lowerCamelCase Protobuf JSON fields. This one live exchange
qualifies the refresh protocol; the automatic 60-second threshold, concurrency
and failures are covered by offline tests. Native Harness 0.2.0-rc.2 then
completed a controlled `read` and exact answer in two model steps, 16 seconds,
using that refreshed session with the Kimi tab closed. Resuming a direct
conversation id has offline coverage but has not been qualified live.

## Manual initial capture

Install Safari Userscripts from its official distribution and enable it for
`https://www.kimi.ai` (or `https://www.kimi.com`) and local loopback, as described
in [the browser setup](../browser/README.md#safari). Sign in on the normal site.
Read the actual **Save Location** in the Userscripts application; do not infer
the directory from another bridge's generated files.

```sh
python -m integrations.kimi_session --script-directory '/actual/Userscripts/save/folder'
```

Reload the signed-in Kimi tab, then click **Save Kimi session locally**. This
explicit action copies that account's access and refresh tokens to
`session/kimi/session.json` on this computer. The helper has a one-time nonce,
expires after ten minutes and deletes its private userscript on exit. It sends
no model request and never clears an existing provider pause. Close the Kimi
tab after capture to avoid competing browser and bridge refreshes.

The credential file has mode `0600` in a `0700` directory on POSIX. Never commit,
paste or upload it. Exported scripts contain temporary pairing credentials and
must likewise remain private.

## Request-time renewal

To opt in, enable `KIMI_ENABLED=1`, `KIMI_TRANSPORT=direct` and,
separately, `KIMI_RENEW_SESSION=1`. Restart the bridge and reinstall the Harness
catalog with `python -m integrations.harness --install desktop --without-browser`.
The filter includes the explicitly advertised `kimi-instant` model only.

Before a new request, a token with less than 60 seconds remaining is renewed
once through Kimi's normal `AuthService/RefreshToken` endpoint. A local file lock
serializes captures and exchanges. The response must contain a fresh token for
the same account, and the new pair must be atomically persisted before sending
the prompt. This is local account/expiry checking, not offline JWT signature
verification. The server never runs a browser, periodic keepalive or silent
login to maintain access.

An HTTP refusal, uncertain timeout, invalid response or failed write pauses
Kimi. There is no retry, automatic pause reset or replay of the model request.
If the session is revoked, sign in and capture again manually. Inspect the
normal site before explicitly resuming with `python -m providers.access resume kimi`.
Free account quotas and service limits still apply.

The endpoint and protocol come from Kimi's published frontend; this is not an
official or guaranteed third-party API. Frontend changes can stop the adapter.
