# ZoomInfo live intent search — version 3.1

## Why the earlier leads looked fictional

The previous dashboard and search API defaulted to sample mode. That mode reads `data/mock/intent_results.json`, containing fictional companies. It never contacts ZoomInfo.

Version 3.0 removes the **Sample demo** selector and rejects sample searches/lookups in the web API. All new web searches use **Live ZoomInfo**. Missing or rejected credentials produce an error; no fictional fallback is used. Existing sample runs are hidden from the dashboard, retained in the database, and cannot send email. Start a new live search after connecting ZoomInfo.

## Connect your account once

1. In ZoomInfo Developer Portal, create a **Standard App** using **Client Credentials**, and select the integration user whose account has the required data entitlement. For GTM.ai accounts, use the Developer area.
2. Enable **Intent** (`api:data:intent`) and **Contact** (`api:data:contact`) for intent search and buyer/contact enrichment. The account must also permit the lookup endpoints. Scopes cannot grant data products that the integration user has not licensed.
3. Copy the app's **Client ID** and **Client Secret** into the root `.env` file. Keep these values on the backend:

```dotenv
ZOOMINFO_CLIENT_ID=your_standard_app_client_id
ZOOMINFO_CLIENT_SECRET=your_standard_app_client_secret
ZOOMINFO_BASE_URL=https://api.zoominfo.com/gtm
ZOOMINFO_TOKEN_URL=https://api.zoominfo.com/gtm/oauth/v1/token
ZOOMINFO_SCOPE=
DEFAULT_COUNTRY=United Kingdom
```

4. Restart FastAPI, reload the dashboard, open **Connections**, and click **Test ZoomInfo connection**. It checks OAuth and performs a fresh intent-topic lookup. A successful check does not prove that every search or enrichment endpoint is included in your subscription.
5. Open **Lead discovery**, use **Live ZoomInfo**, choose your filters, and start a new search. Use **Search options** to turn off contact enrichment when you only want company research.

See ZoomInfo's [Standard App setup](https://docs.zoominfo.com/docs/standard-app) and [scope catalog](https://docs.zoominfo.com/docs/zoominfo-oauth-scopes). `ZOOMINFO_SCOPE` is optional; leave it blank to use the scopes configured on your app, or provide a space-separated subset.

## Do I have to change the secret every 24 hours?

**Access token expiry and client secret expiry are different.** The documented client-credentials flow exchanges the app credentials for a bearer access token. Its `expires_in` value specifies that token's lifetime in seconds; do not assume a fixed 24-hour duration. The backend can request another token with the same app credentials. [ZoomInfo client-credentials documentation](https://docs.zoominfo.com/docs/client-credentials-flow).

The implementation now:

- Reuses a token across concurrent searches and lookups in one backend process.
- Renews it on demand before its reported expiry, with up to 60 seconds of margin.
- Invalidates a rejected token and retries once on HTTP 401, including after a rate-limit retry.
- Keeps tokens in memory and credentials on the backend. Restarting simply obtains another token when needed.
- Shows a useful error if app credentials, scopes or permissions are rejected; it never substitutes fictional leads.

You do not need a daily scheduled job or to paste new access tokens. The backend obtains them as needed, including after being idle for more than a day.

**If the ZoomInfo portal really states that the Client Secret itself expires after 24 hours**, automatic token renewal cannot extend that secret. Confirm with your ZoomInfo admin/account team that you have a Standard App suitable for this server integration and check its credential-expiry policy. The reviewed public documentation does not establish a universal 24-hour client-secret policy or a secret-rotation API. This project does not claim to rotate app secrets automatically. A temporary token generated for Test API Access must not be entered as `ZOOMINFO_CLIENT_SECRET`.

After an actual secret rotation, update the root `.env` and restart the backend. No frontend credential changes are needed.

## Signals filters

The **Intent**, **Company**, and **Location** tabs feed `POST /gtm/data/v1/intent/search`. ZoomInfo requires 1–50 exact topic names; this endpoint finds signals across companies. A targeted single-company intent enrichment is a different endpoint and is not added here. [Search Intent](https://docs.zoominfo.com/reference/searchinterface_searchintent).

| Dashboard / API field | ZoomInfo attribute | Meaning |
| --- | --- | --- |
| Selected topics / `intent_topics` | `topics` | Exact intent names from the live lookup; empty input lets the query agent select matching names |
| `min_signal_score`, `max_signal_score` | `signalScoreMin`, `signalScoreMax` | 60–100, inclusive |
| `signal_start_date`, `signal_end_date` | `signalStartDate`, `signalEndDate` | Optional YYYY-MM-DD date window |
| `audience_strength_min`, `audience_strength_max` | `audienceStrengthMin`, `audienceStrengthMax` | A strongest, E weakest; E through A includes the whole range |
| `industry_codes` | `industryCodes` | Industry code(s), comma-separated |
| `employee_count` | `employeeCount` | Lookup ranges, such as `100to249` |
| `revenue` | `revenue` | Revenue range codes from lookup |
| `tech_products` | `techAttributeTagList` | Technology product IDs from lookup |
| `country` | `country` | Country name(s); empty string means worldwide |
| `state` | `state` | State/province name(s) |
| `metro_region` | `metroRegion` | Metro region name(s) |

These filter names and ranges are also exposed by [ZoomInfo's official CLI](https://github.com/Zoominfo/gtm-ai-cli), `intent search`. The implementation was cross-checked against its published `@zoominfo/gtm-ai-cli` 1.1.0 package. Country/state/metro use lookup names; company code fields use lookup values/codes or resource IDs. Select the canonical values returned by your account.

Focus a lookup input to load suggestions; use its reload button to refresh. Topics, country, industry, employee count, revenue, state, metro and technology values are read from the provider. Live topic names are checked before submitting an intent search. A date/score/audience range in reverse order is rejected. The web application exposes only live provider filters.

The search reads up to two pages of 50 intent signals, merges repeated companies, then applies the existing scoring and maximum-company limit. The maximum is a cap, not a guarantee of that many companies or an exhaustive export. A company can have several signals. Results record the applied filters and provider signal dates for review.

## Useful API requests

All routes below require `X-API-Key` plus an approved Microsoft `X-Session-Token` and are relative to `/api/v1`.

- `POST /zoominfo/test-connection` — check OAuth and fresh topic lookup. Does not return a token or secret.
- `GET /lookups/intent-topics?q=cloud` — live topic suggestions.
- `GET /lookups/industries` — industry lookup values.
- `GET /lookups/countries?refresh=true` — bypass the local cache.
- Other supported lookups: `states`, `metro-regions`, `employee-count`, `revenue-ranges`, `tech-products`, `management-levels`.

Live lookups are cached separately by app ID, API base URL and requested scope for seven days. Reload bypasses the cache. The old shared lookup files are not used.

Example live search; replace topic/location/range values with values actually returned for your account:

```json
{
  "query": "Companies exploring cloud training",
  "mock": false,
  "intent_topics": ["Cloud Applications"],
  "country": "United States",
  "state": "California",
  "employee_count": "100to249,250to499",
  "min_signal_score": 80,
  "max_signal_score": 100,
  "audience_strength_min": "C",
  "audience_strength_max": "A",
  "limit": 10,
  "enrich": false,
  "write_emails": false
}
```

Omit dates for the provider's default window, or supply a suitable recent window. No results can be a legitimate outcome. Broaden the filters or lower the minimum tier; the application will not invent replacement leads.

## Upgrading an existing installation

Extract this version into a new folder. Preserve your existing root `.env`, `frontend/.env.local`, company knowledge, suppression list, historical CLI send log and `data/app.sqlite3` if you want to keep your configuration/history. Stop the old backend before copying the database. Do not replace your existing values with blank example files. Install the Python requirements, run `npm ci` in `frontend`, and restart both servers. Version 3.1 opens the dashboard directly without login. Database migration preserves leads and sender audit history. Run `python setup_local.py`; Microsoft is optional and can be connected in Connections when you want to send. See [MICROSOFT_SETUP.md](MICROSOFT_SETUP.md). Old approvals without a recorded sending account are cleared.

Existing sample history is retained in the database but hidden in the dashboard; it does not become real ZoomInfo data.

## Verification limits

Provider requests were tested with simulated HTTP responses, including filter forwarding, token expiry, concurrency, rejected credentials and pagination. No live ZoomInfo credentials were supplied, so account entitlements and real returned leads still need the connection check and a live search on your machine. No real emails were sent. The existing explicit email approval-and-send workflow is preserved.
