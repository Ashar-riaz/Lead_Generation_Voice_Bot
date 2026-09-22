# WTD Lead Workspace — version 3.1

A Next.js frontend and FastAPI backend for live ZoomInfo lead research, reviewed email drafts and Microsoft 365 outreach.

**The dashboard opens directly without login. Microsoft is an optional mailbox connection for sending reviewed emails. Searches, drafts, filters, exports, company knowledge and ZoomInfo token renewal work without Microsoft.**

The **Sample demo** source has been removed. Web searches and lookups use live ZoomInfo; failures never substitute fictional leads. Existing sample history is hidden from the dashboard and remains unsendable.

## Install on Windows / VS Code

Extract the ZIP, open the `wtd-lead-gen` folder, and run:

```powershell
python -m venv VE
.\VE\Scripts\python.exe -m pip install -r requirements.txt
.\VE\Scripts\python.exe setup_local.py
```

The setup script creates the two environment files, a matching backend API key, and an encryption key for Microsoft token caches. It preserves existing non-empty values. Python 3.10+ and Node.js 20.9+ are required; verification used Python 3.12.

Microsoft credentials and `MICROSOFT_ADMIN_OBJECT_IDS` are not required to open the dashboard. Add your ZoomInfo credentials for live research. Configure Microsoft later, only if you want to send emails; see [docs/MICROSOFT_SETUP.md](docs/MICROSOFT_SETUP.md).

Start the backend in one terminal:

```powershell
.\VE\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start Next.js in another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://localhost:3000**. The lead dashboard opens immediately without a login screen. Use this exact hostname because it matches the configured callback and `DASHBOARD_ORIGIN`. Keep both terminals running.

On macOS/Linux, create the environment with `python3 -m venv VE` and use `VE/bin/python` in place of `.\VE\Scripts\python.exe`.

## Workspace access

The Microsoft login screen, owner/user approval screens and workspace allowlist have been removed. Anyone who can reach the frontend can use the shared workspace and edit its data. Keep this version on your local machine or a trusted private network; protect the frontend with a separate access layer before exposing it publicly. The backend still requires its server API key, and browser mutations still require the configured origin.

## Optional Microsoft mailbox

Open **Connections → Connect Microsoft mailbox** when you want to send. Use your organisation's Microsoft 365 mailbox and complete Microsoft's consent process. Microsoft consent is still required for delegated email access and may require your tenant administrator. It no longer blocks lead discovery, drafting or exports. `MICROSOFT_ADMIN_OBJECT_IDS` is unused and can be removed from your existing `.env`.

There is no shared default sending account: each browser connects its own mailbox. Review the displayed From account, turn on **Ready to send**, then explicitly click **Send email** or **Send ready emails**. Connecting alone sends nothing. **Disconnect mailbox** removes this browser's connection while keeping the workspace open. After the final active connection for that mailbox disconnects, its cached tokens and unsent approvals are cleared. In-flight sends cannot be recalled.

## Connect live ZoomInfo

Add your Standard App credentials to the root `.env`, restart FastAPI, then use **Connections → Test ZoomInfo connection**:

```dotenv
ZOOMINFO_CLIENT_ID=your_actual_client_id
ZOOMINFO_CLIENT_SECRET=your_actual_client_secret
```

The search includes **Intent**, **Company** and **Location** filters with live provider lookups. Access tokens renew automatically using `expires_in`; no daily token replacement is needed. An expired or revoked **app secret** still needs administrator replacement. See [docs/ZOOMINFO_SETUP.md](docs/ZOOMINFO_SETUP.md).

Use `LLM_PROVIDER=none` for programme-based email templates, or configure your Gemini/Anthropic key. Contact enrichment can consume ZoomInfo credits. You can disable enrichment or drafting in Search options. A company may have no draft when no contact email is available.

## Review and send through Microsoft

| Action | Result |
| --- | --- |
| Run a search | Save drafts; send nothing |
| Save an edit | Clear approval; require a fresh review |
| Turn on Ready to send | Bind the saved recipient and content to your Microsoft account |
| Click Send email | Send that approved draft from your mailbox |
| Click Send ready emails | Send only drafts you approved in that search |
| Open a draft approved by a colleague | Review and approve it yourself before sending from your mailbox |
| Definitive send failure | Clear approval; require review and reapproval |
| Uncertain send outcome | Block retries until the sending mailbox verifies and resolves the outcome |

Microsoft Graph's `202 Accepted` response records **Sent** in the app. It does not establish inbox delivery. The request saves a copy in Outlook Sent Items. There is no automatic follow-up or blind retry. See [Microsoft's sendMail contract](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

For an Unknown outcome, check the sending mailbox's Sent Items or ask your Exchange administrator to trace the sender, recipient and time. The displayed `message_id` is an **app delivery reference** placed in `x-wtd-delivery-id`; it is not Microsoft's internet Message-ID.

`SENDER_NAME`, `SENDER_TITLE`, `SENDER_EMAIL` and `SENDER_PHONE` customize draft signatures. They do not change the Graph sender or Reply-To. Review the signature when different team members send. Shared-mailbox sending is not included.

The workspace-wide daily cap uses UTC and counts accepted, reserved and uncertain attempts. Duplicate-recipient protection spans all users and searches. Add opt-outs to `data/suppression.txt`, one address per line. Historical `data/send_log.json` entries are respected. Replies/unsubscribes are not read automatically. CLI sending and SMTP delivery are disabled.

## Upgrade from the previous ZIP

Stop both servers. Back up and retain your root `.env`, `frontend/.env.local`, `data/app.sqlite3`, company knowledge, suppression list and any historical `data/send_log.json`. Extract this version into a new folder and copy those retained files into it. Copy the database only after the old backend has stopped.

Reinstall Python requirements, run `python setup_local.py`, run `npm ci` in `frontend`, and restart. The dashboard is available immediately. Your saved leads, drafts and delivery history are retained. For email, reconnect in Connections; the existing Web callback URL and Microsoft credentials still apply. This version uses a new mailbox cookie, so old login cookies are ignored. `MICROSOFT_ADMIN_OBJECT_IDS`, `DASHBOARD_PASSWORD` and SMTP settings are unused and can be removed. Keep `API_KEY` matching in both environment files. Keep `TOKEN_ENCRYPTION_KEY` stable. Older approvals without a recorded sending account are cleared on database migration.

## API and project files

- Microsoft setup: [docs/MICROSOFT_SETUP.md](docs/MICROSOFT_SETUP.md)
- API routes and authentication: [docs/API_GUIDE.md](docs/API_GUIDE.md)
- Swagger: **http://127.0.0.1:8000/docs**; schema: [docs/openapi.json](docs/openapi.json)
- ZoomInfo filters and token renewal: [docs/ZOOMINFO_SETUP.md](docs/ZOOMINFO_SETUP.md)
- Verification and limitations: [docs/TEST_REPORT.md](docs/TEST_REPORT.md)
- Research-only CLI: [docs/CLI_GUIDE.md](docs/CLI_GUIDE.md)

Business API routes require `X-API-Key`. Approving for delivery, sending and resolving an uncertain send also require a valid Microsoft mailbox `X-Session-Token`. Removing approval does not require a mailbox. Next.js attaches the API key on the server and, when connected, the HttpOnly mailbox cookie. Provider access/refresh tokens and secrets never enter browser JavaScript.

| Path | Purpose |
| --- | --- |
| `app/main.py` | API routes and authorization |
| `app/microsoft.py`, `app/auth_store.py` | Optional mailbox connections and encrypted token caches |
| `app/graph_mail.py` | Delegated Graph mail transport |
| `app/store.py`, `app/services.py` | Versioned drafts, delivery reservations and lead searches |
| `frontend/components/microsoft-connection.tsx` | Optional mailbox connect/disconnect controls |
| `frontend/components/email-editor.tsx` | Review, sender-specific approval and delivery history |
| `frontend/app/api/auth/microsoft/` | Server-side mailbox connection and existing callback |
| `src/zoominfo/`, `src/graph/`, `src/agents/` | Existing research and drafting pipeline |
| `knowledge/` | Your company profile and programme catalogue |
| `tests/` | Local authentication, provider, API and delivery tests |

## Running and testing

```powershell
python -m pytest -q
python -m pip check
cd frontend
npm run build
npm run start
```

Run **one FastAPI worker** with the current SQLite/in-process job architecture. Restart recovery marks unfinished searches Failed and in-flight sends Unknown, without resending. Use a stable encryption key and back up it, the database, knowledge and suppression list separately from the source ZIP. The archive contains no real credentials, sessions, databases or generated leads.

For private hosting behind your chosen access layer, use HTTPS, set `DASHBOARD_ORIGIN` to the public origin, `COOKIE_SECURE=true`, and register the matching HTTPS callback. Keep FastAPI private behind Next.js. Mailbox connections last eight hours; Microsoft may require reconnection or consent again. Configure proxy access logs to omit OAuth callback query strings.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| Microsoft email not configured | Research works; follow `docs/MICROSOFT_SETUP.md` if you want to send |
| Microsoft redirect mismatch | Use the exact Web callback and open `http://localhost:3000` |
| Need admin approval / consent | Ask your tenant administrator to grant delegated User.Read and Mail.Send |
| Backend unavailable | Start `app.main:app` on port 8000 and check `BACKEND_URL` |
| Invalid API key | Match `API_KEY` in both environment files and restart both servers |
| Send disabled | Connect a mailbox in Connections, save and approve the draft, and use a live search |
| Microsoft rejects mail | Check Mail.Send consent, Exchange Online mailbox and organisational policy |
| Unknown delivery | Verify Sent Items or Exchange trace before resolving; absence alone may be inconclusive |
| ZoomInfo unavailable | Test Connections and check Standard App credentials and account entitlements |
