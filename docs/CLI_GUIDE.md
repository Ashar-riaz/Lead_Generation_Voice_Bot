# Research CLI

The command line still runs the existing LangGraph research and drafting pipeline. Email sending now requires a Microsoft mailbox connection and explicit approval in the web workspace. `--send` exits with an explanatory error; SMTP settings no longer enable delivery. Use `app.main:app` to start FastAPI, not `main:app`.

## Live research

```powershell
python main.py "companies exploring AI training" --limit 10
python main.py --topics cloud
python main.py --lookup countries
python main.py "companies exploring cloud training" --no-enrich --no-emails
```

Live mode requires ZoomInfo app credentials in the root `.env`. Access tokens refresh automatically. CLI exports are written under `outputs/`; standalone CLI searches are not automatically imported into the web database. Start a web search to review/send its drafts through Microsoft.

## Options

| Option | Purpose |
| --- | --- |
| `--limit` | Maximum companies, default 10 |
| `--country` | Country filter using provider lookup values |
| `--min-tier` | Cool, Warm or Hot |
| `--contacts-per-lead` | Enrichment count per company; consumes account credits |
| `--no-buyers` | Skip additional decision-maker search |
| `--no-enrich` | Skip contact enrichment |
| `--no-emails` | Research without drafting |
| `--topics [keyword]` | List/filter exact provider intent topic names |
| `--lookup FIELD` | List provider values, such as countries |
| `--draw-graph` | Write the pipeline's Mermaid graph |
| `--verbose` | Diagnostic logging |
| `--mock` | Developer-only offline fixture for tests; fictional data |
| `--send` | Removed: use a Microsoft mailbox connection and the web email review screen |

The offline `data/mock/` fixture is retained only for development and regression tests. It is unavailable in the web source selector or search API, is never a fallback for failed live requests, and cannot send mail.

Company profile and programme facts remain in `knowledge/`. `LLM_PROVIDER=none` uses template drafting. Provider credentials, real account entitlements and contact availability govern live results; an intent signal is research interest, not proof of a confirmed training requirement.
