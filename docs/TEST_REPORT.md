# Verification report — version 3.1

Verified on 21 September 2026 using Python 3.12, a Next.js production build and desktop/mobile Chromium. Python 3.10+ remains the supported project minimum.

| Check | Result |
| --- | --- |
| Backend regression suite | 110 passed |
| Python dependency consistency | Passed |
| Next.js production build and TypeScript | Passed |
| Direct dashboard and optional mailbox browser flow | Passed |
| Desktop and mobile visual checks | Passed |

## Workspace without login

The browser opens the lead dashboard without a Microsoft session. Tests verify research routes, results, exports, history, knowledge and draft editing with only the backend API key. Missing, malformed, disconnected or expired mailbox connections do not block research. Missing Microsoft credentials do not prevent dashboard access. Missing backend API keys still reject API access.

Login, user-access and password controls are removed. The generic Next.js proxy still rejects internal authentication routes and wrong-origin mutations. It obtains mailbox capabilities only from the HttpOnly cookie; a browser-supplied X-Session-Token header cannot impersonate a connected mailbox. The old login cookie is not used.

## Optional Microsoft email

Tests run real MSAL authorization-code, PKCE, nonce, state and token-cache logic against a local fake Entra HTTP service, using local RSA-signed ID tokens. Wrong signatures, issuer, audience, tenant, expiry, nonce, state, expired/replayed flows and mismatched mailbox profiles are rejected. Production identity validation remains enabled.

The browser verifies optional connection from Connections, return to the workspace after failed consent, verified sender display, HttpOnly/Lax connection cookies, draft approval, edit invalidation and a simulated send with sender audit. Disconnect leaves research available. Backend tests verify that final disconnection clears the mailbox cache and ready approvals, and that expiry blocks sending. Access tokens renew using the encrypted MSAL cache.

Only a connected mailbox can approve a draft for sending; approval removal can be performed after disconnection. Another mailbox must approve the draft afresh. Only the connected sending mailbox can resolve an Unknown delivery. Tests retain suppression, daily limits, duplicate prevention, concurrent reservations, exact saved content/version checks and restart recovery without resending. Unapproved drafts never reach the mail transport.

Graph transport tests verify delegated /me/sendMail, exact recipient/subject/body, the delivery reference, Sent Items saving and no sender override. Send rejections, rate limits, timeouts and lost acknowledgements are handled without automatic mail POST retries. CLI/SMTP sending stays disabled.

## ZoomInfo and existing functionality

Existing provider and pipeline tests pass: live defaults, no fictional fallback, OAuth renewal from expires_in, concurrent token requests, one renewal after a 401, credential error handling, Intent/Company/Location filters, topic validation, pagination and lookup-cache separation. New web sample searches/lookups remain rejected and existing sample history stays hidden and unsendable.

## Limits and repeat checks

No real email was sent. Microsoft OAuth and mail delivery used local test fixtures; no actual tenant consent, mailbox delivery, ZoomInfo account or LLM account was exercised. Microsoft consent remains necessary for optional email, including administrator approval when tenant policy requires it. Mailbox connections expire after eight hours; that expiry has no effect on dashboard access.

This is a shared workspace without application login. Anyone who can reach the frontend can view and edit its data; use it locally or behind a separately managed private access layer. Run one backend worker for the current SQLite/in-process job architecture. Existing searches, drafts, knowledge, suppression rules and delivery history are retained on upgrade.

Preview images under docs/previews are clearly labelled test fixtures, not live prospects. The application does not seed these results. The development browser harness is outside the delivered archive; the backend regression tests are included.

```powershell
python -m pytest -q
python -m pip check
cd frontend
npm ci
npm run build
```

The successful test run emits an AnyIO deprecation warning and MSAL's recommendation for a form_post callback. The current authorization-code query callback keeps PKCE, one-use browser binding, fixed redirects and no-store/no-referrer responses. Configure proxy logs to omit callback query strings.
