# WTD API guide

Base URL: `http://127.0.0.1:8000/api/v1`.

The dashboard opens without login. Business endpoints require `X-API-Key: <backend API_KEY>`. Research, lookups, exports, settings, knowledge and draft editing do not require Microsoft. Only `/health`, documentation and the OpenAPI schema are public on the backend. Next.js attaches the backend key on the server; it is never exposed to client JavaScript.

## Optional mailbox connection

Approving an email for delivery, sending and resolving an uncertain outcome also require `X-Session-Token: <opaque mailbox connection>`. This is the app's connection token, not a Microsoft access token. You can remove approval without reconnecting.

The backend `/auth/*` routes retain their existing paths for the mailbox workflow and require the API key. `POST /auth/microsoft/start` returns `authorization_url` and `flow_handle` to Next.js, which stores the handle in a ten-minute HttpOnly callback cookie. `POST /auth/microsoft/callback` accepts `{ "flow_handle": "...", "response": { "code": "...", "state": "..." } }`. After verified Microsoft consent, PKCE, state, nonce and signed-token checks, it returns an eight-hour opaque `session_token` and public `mailbox` profile. There is no application owner allowlist or pending workspace request. Microsoft's tenant consent rules still apply.

`GET /auth/session` returns `{ "connected": false, "configured": false, "mailbox": null }` when disconnected, or a verified mailbox profile when connected. Expired, missing and invalid connections do not block other workspace routes. `DELETE /auth/session` disconnects the provided connection. When no active connection for that mailbox remains, cached tokens and ready approvals are cleared.

The frontend uses `GET/DELETE /api/mailbox`, `POST /api/auth/microsoft/start` and the existing `/api/auth/microsoft/callback`. The generic Next proxy still rejects `/auth/*`, browser mutations still require the configured Origin, and a request header supplied by the browser cannot substitute for the HttpOnly mailbox cookie. Login and `/access/users` routes are removed.

For Swagger, use just the backend key for research. Email actions need a valid mailbox connection from the normal Microsoft consent flow as well. Provider tokens and secrets stay on the server.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` (outside /api/v1) | Public liveness check |
| GET | `/settings` | Configuration presence and sender information; no secrets |
| POST | `/runs` | Start a background lead search; returns 202 and run ID |
| GET | `/runs?limit=50&offset=0` | Saved searches and counts |
| GET | `/runs/{run_id}` | Current progress, errors, leads and drafts |
| GET | `/runs/{run_id}/export?format=csv` | Export current saved results, approval and sent status |
| GET | `/runs/{run_id}/export?format=json` | Full saved search export |
| GET | `/leads?run_id=...&tier=Hot&q=...` | Filter companies; supports limit/offset |
| GET | `/leads/{lead_id}` | Company details, intent evidence and contacts |
| GET | `/emails?run_id=...&status=draft` | Filter email drafts; supports limit/offset |
| GET | `/emails/{email_id}` | Current content, version, approval and delivery status |
| PATCH | `/emails/{email_id}` | Update content; always clears approval |
| POST | `/emails/{email_id}/approval` | Set Ready to send true or false for the current version |
| POST | `/emails/send-ready` | Deliver only the specified, currently approved draft versions |
| GET | `/emails/{email_id}/attempts` | Delivery history, sending account and app delivery references |
| POST | `/emails/{email_id}/resolve` | Connected sender resolves Unknown after checking Sent Items or Exchange trace |
| GET | `/knowledge` | Company profile and programme catalogue |
| PUT | `/knowledge` | Save company profile for future LLM drafts |
| POST | `/zoominfo/test-connection` | Verify OAuth and a fresh intent-topic lookup; no credentials returned |
| GET | `/lookups/intent-topics?q=AI` | Live exact topic names |
| GET | `/lookups/{field}?refresh=true` | Reload industries, employee-count, revenue-ranges, states, metro-regions or tech-products |
| GET | `/lookups/countries` | Live account's accepted country values |
| GET | `/lookups/management-levels` | Live account's management levels |

Paths in the table are relative to `/api/v1` unless explicitly stated. Web searches and lookups are live only; `mock: true` is rejected. Legacy sample runs are excluded from the default history list but retained in the database.

## 1. Start a search

POST `/runs`:

```json
{
  "query": "Companies exploring AI training in the UK",
  "mock": false,
  "limit": 10,
  "country": "United Kingdom",
  "min_tier": "Cool",
  "contacts_per_lead": 1,
  "find_buyers": true,
  "enrich": true,
  "write_emails": true
}
```

Copy `id` from the 202 response. Poll GET `/runs/{id}` every two seconds until `status` is `completed` or `failed`. `result.progress` contains completed graph stages and `result.errors` contains partial-stage warnings. Up to three searches may run concurrently.

Every new draft has `status: "draft"`, `ready_to_send: false`, and `version: 1`. POST `/runs` rejects a `send` field. Generating a draft cannot authorize delivery.

Live mode (`mock: false`) is the default. Missing credentials return 503; rejected provider credentials fail the run without creating sample leads. `mock: true` returns 422; no demo generation endpoint is exposed.

Additional optional fields: `intent_topics` (up to 50 exact names), `min_signal_score` / `max_signal_score` (60–100), `signal_start_date` / `signal_end_date` (YYYY-MM-DD), `audience_strength_min` / `audience_strength_max` (A–E), `industry_codes`, `employee_count`, `revenue`, `tech_products`, `state`, and `metro_region`. The last six are strings supporting canonical comma-separated provider values. These six company/state/metro filters require live mode. Score, date and audience ranges are validated before a run is created.

`country: null` uses the query agent/default country; `country: ""` explicitly means worldwide. The frontend sends the country shown in its Location tab. Explicit topics override automatic topic selection and are checked against ZoomInfo lookup values. Applied provider filters are saved under `result.plan.intent_filters`.

See [ZOOMINFO_SETUP.md](ZOOMINFO_SETUP.md) for field mapping, examples and automatic token renewal. Provider authentication errors are safe messages; access tokens and secrets are not returned.

## 2. Edit a draft

Read the current email with GET `/emails/{email_id}`, then PATCH the content:

```json
{
  "expected_version": 1,
  "to_email": "contact@example.com",
  "to_name": "Alex",
  "subject": "AI skills for your team",
  "body": "Hi Alex,\n\nWould practical AI training be useful for your team?\n\nThanks,\nYour Name"
}
```

All content fields are required for PATCH. The response contains the new version. Editing increments the version, sets status to Draft and removes approval. Invalid addresses, blank messages and header newlines are rejected. A stale version returns 409.

## 3. Approve the exact saved draft

POST `/emails/{email_id}/approval`:

```json
{
  "expected_version": 2,
  "ready_to_send": true
}
```

Use the actual version returned by the last read or edit. Approval increments the version again. The response includes `ready_to_send: true`, `status: "ready"`, `approved_version`, `approved_at` and `approved_by`. Approval is bound to the current Microsoft Object ID; another person must approve the draft again before sending from their mailbox.

To revoke approval without a mailbox connection, send the latest version and `ready_to_send: false`. Use real JSON booleans; strings such as `"true"` are rejected.

Approval alone never sends an email.

## 4. Explicitly send

For a live search only, POST `/emails/send-ready`:

```json
{
  "run_id": "<actual run ID>",
  "emails": [
    {"email_id": "<actual email ID>", "expected_version": 3}
  ]
}
```

Use the version returned by approval. Each ID must belong to the specified run. The request supports up to 50 unique draft IDs. It is synchronous and reports a separate outcome for each item. The frontend submits one approved email at a time to show progress and keep individual requests short.

Example response:

```json
{
  "results": [
    {
      "email_id": "<actual email ID>",
      "status": "sent",
      "detail": "Accepted by Microsoft Graph",
      "message_id": "wtd-<app delivery reference>"
    }
  ],
  "sent_count": 1
}
```

Per-item outcomes are `sent`, `skipped`, `failed` or `unknown`. **HTTP 200 alone does not mean every email was sent. Check `sent_count` and every result.** Legacy sample sends are rejected with 409. A stale/unapproved/duplicate/suppressed draft, or approval by another sender, is skipped. Sending uses delegated `POST /me/sendMail`; `sent_by` and `sender_email` record the submitting identity. Microsoft token errors fail without an HTTP mail submission.

Database reservations are atomic, so concurrent requests cannot claim the same approved draft or recipient twice. A daily cap is checked while reserving each send. There are no automatic retries after an uncertain delivery.

## 5. Resolve an uncertain delivery

Check the sending mailbox's Outlook Sent Items or Exchange trace by sender, recipient and time first. The `message_id` returned by GET `/emails/{id}` or `/emails/{id}/attempts` is the app correlation reference stored in `x-wtd-delivery-id`, not Microsoft's internet Message-ID. Graph sendMail returns 202 without a message ID. Then POST `/emails/{id}/resolve`:

```json
{
  "expected_version": 3,
  "delivered": false
}
```

`delivered: true` records Sent. `delivered: false` records Failed, clears approval and allows a newly reviewed/approved retry. Only Unknown messages can be resolved, and only by the connected sending mailbox. Do not mark an uncertain message as not accepted without verifying the server outcome.

## Read company knowledge

GET `/knowledge` returns your profile and categories. PUT `/knowledge` accepts:

```json
{
  "profile": "Your verified company description, services, programme information and contact details..."
}
```

The profile must contain 50–60,000 characters. It is saved atomically to `knowledge/company_profile.md`; the knowledge cache is cleared. Programme catalogue editing remains in `knowledge/programmes.json`. Existing emails retain their text and approval until you edit them explicitly.

## Evidence and scoring

Each lead returns `signals` (topic, signal score, audience strength and date), `contacts`, `score`, `tier` and `score_reasons`. These are your existing ZoomInfo-derived signals and scoring logic. A high score indicates a prospect worth investigating; it is not a measured probability of buying training. The project has no direct evidence of a confirmed training request or trainee headcount unless you obtain that separately.

## Error handling

| Status | Meaning |
| --- | --- |
| 401 | Missing/incorrect key, or missing/expired mailbox connection for an email action |
| 403 | Another mailbox's delivery resolution, or a rejected browser origin |
| 404 | Run, lead or email does not exist |
| 409 | Version conflict, forbidden state change, sample send or delivery conflict |
| 422 | Invalid request shape/value; extra fields are rejected |
| 429 | Concurrent-search or daily-send capacity reached |
| 502 | Live lookup/connection-check authentication, permission or upstream failure |
| 503 | Required backend API, Microsoft or ZoomInfo configuration is missing |

Search failures occur after the initial 202 and are stored on the run. Batch sending reports routine per-item failures/skips in its result array. If an HTTP send response is lost, refresh email status before retrying; do not infer that the email failed.

## Integration references

This implementation uses FastAPI's [background task mechanism](https://fastapi.tiangolo.com/tutorial/background-tasks/) and Next.js [route handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route). Background jobs live in the API process; they are not an external durable queue. Use one worker with this version.
