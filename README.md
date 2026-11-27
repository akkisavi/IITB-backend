# warehouse-api

Django REST Framework API for the Warehouse Management System. Main docs (overview, design, AI usage) live in [`../warehouse-ui/`](../warehouse-ui/README.md) in this same repository.

## Runtime
- Python **3.12** (see `.python-version`)
- Django 5.1, DRF (token auth), django-cors-headers, gunicorn, whitenoise

## Database
Local development uses SQLite at `SQLITE_PATH` (default `./db.sqlite3`). Set `DATABASE_URL` to a PostgreSQL connection URL for the hosted service. The Render Blueprint creates a Postgres database and supplies its internal URL. For an existing manually configured Render service, create a Render Postgres database and set the service's `DATABASE_URL` to its internal URL before deploying this version.

## Local setup
```powershell
python -m venv .venv
.venv\Scripts\activate                      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Settings read the process environment only (no .env loader). Use your own disposable values:
$env:EVAL_ADMIN_PASSWORD="..."; $env:EVAL_AUDITOR_PASSWORD="..."; $env:EVAL_TECHNICIAN_PASSWORD="..."
python manage.py migrate --noinput
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000 --noreload
```
Readiness: `GET /health/` returns `{"status":"ok"}` without auth. `.env.example` lists every variable with placeholders. While `DJANGO_ALLOWED_HOSTS` holds only `127.0.0.1`/`localhost` the API runs in local mode: a dev secret key is used if `DJANGO_SECRET_KEY` is unset, and any localhost UI origin is allowed by CORS.

### Demo accounts
| Role | Hosted username | Local setup reads |
|---|---|---|
| Admin | `demo_admin` | `EVAL_ADMIN_USERNAME`, `EVAL_ADMIN_PASSWORD` |
| Auditor | `demo_auditor` | `EVAL_AUDITOR_USERNAME`, `EVAL_AUDITOR_PASSWORD` |
| Technician | `demo_technician` | `EVAL_TECHNICIAN_USERNAME`, `EVAL_TECHNICIAN_PASSWORD` |

`seed_demo` creates or updates each account from these variables (a role whose password is unset is skipped). Usernames default to the hosted ones above. Never commit the passwords.

### Uniqueness and concurrency
"Pallet number / barcode unique among *active* pallets" is a real database constraint: `active_pallet_number` and `active_barcode` carry a unique index and are cleared (NULL) on unload. **One pallet per location** is a one-to-one column. SQLite allows one writer at a time (20 s busy timeout), so two simultaneous loads into the same slot end in one success and one `409`; a Playwright test checks this.

## Commands
| Command | Effect |
|---|---|
| `python manage.py seed_demo` | Idempotent. Creates/updates demo users from `EVAL_*` (skips a role whose password is unset), ensures 25 locations and shuttle `SH-01`, and adds five sample pallets only to an empty DB. Never deletes data. Add `--require-accounts` to fail if a password is missing. |
| `python manage.py seed_demo_history` | Adds six days of sample load/unload activity to an untouched demo warehouse. Safe to run again; it skips changed or already seeded stock. |
| `python manage.py reset_demo --yes-i-am-sure` | Destructive: deletes pallets, operation logs and snapshots (not users), then reseeds. Refuses without the flag, and refuses on a hosted deploy (`DJANGO_ALLOWED_HOSTS` not local) unless `--allow-remote` is also passed. |
| `python manage.py test` | API tests. |

`evaluation.json` tells the evaluator to run `seed_demo` and probe `/health/`.

## Environment variables
| Var | Default | Notes |
|---|---|---|
| `SQLITE_PATH` | `./db.sqlite3` | Local SQLite file when `DATABASE_URL` is unset |
| `DATABASE_URL` | unset | PostgreSQL connection URL; use Render's internal URL in production |
| `DJANGO_SECRET_KEY` | dev value in local mode or with `DJANGO_DEBUG=1` | Required when hosted |
| `DJANGO_DEBUG` | `0` | |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | comma list; any other host means hosted mode |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | comma list |
| `CSRF_TRUSTED_ORIGINS` | unset | comma list |
| `EVAL_{ADMIN,AUDITOR,TECHNICIAN}_USERNAME` | `demo_admin` / `demo_auditor` / `demo_technician` | |
| `EVAL_{ADMIN,AUDITOR,TECHNICIAN}_PASSWORD` | unset | 12-128 chars, no whitespace; never commit |
| `RESEND_API_KEY` | unset | Resend HTTPS email API; set on Render free to send alerts and password resets |
| `EMAIL_FROM_ADDRESS` | `MAILER_MAIL` | Sender on a verified Resend domain when using the API |
| `MAILER_ADDRESS`, `MAILER_PORT`, `MAILER_MAIL`, `MAILER_PASS`, `MAILER_FROM_NAME` | unset | SMTP for local or paid hosting; without an API key or SMTP user, emails print to the console |
| `LOW_BATTERY_ALERT_TO` | `EMAIL_FROM_ADDRESS` | comma list of automatic alert recipients |

## Local vs hosted
| | Local / evaluation | Hosted |
|---|---|---|
| DB | SQLite file (`SQLITE_PATH`) | PostgreSQL via `DATABASE_URL` |
| Concurrency | SQLite single-writer lock + unique constraints | PostgreSQL transactions + unique constraints |
| Server | `runserver` | gunicorn on Render free web service |
| Availability | always on | Sleeps after 15 min idle; first request takes about a minute. |

## Deployment (free, no payment card)
Stack: **Render web service and Postgres** (API and database) + **Vercel Hobby** (UI). Render's free Postgres database expires 30 days after creation; upgrade it before expiry if the data must remain available.

Render free web services block outbound SMTP ports 25, 465 and 587. To send email there, add a verified sending domain in Resend, then set `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS` and `LOW_BATTERY_ALERT_TO` in the Render service environment and redeploy. `EMAIL_FROM_ADDRESS` must use the verified domain. The Shuttle test alert reports an error if the provider rejects the send; a success means the provider accepted it, not that the recipient inbox has delivered it.

**Data persistence:** The build and `start.sh` run migrations and the demo seed. On an empty Postgres database, this creates three accounts from the `EVAL_*` environment variables, 25 locations, one shuttle, five live pallets, and six days of sample movements. On an existing database, it preserves warehouse changes and updates the demo account passwords from the environment. Switching from the old SQLite deployment does not copy existing live data. Export that data first if it must be kept.

Deployment checklist:
1. Push this repo to a public GitHub repository.
2. **Render** (render.com, sign in with GitHub): New > Blueprint and pick the repo (uses `render.yaml`). For an existing Web Service, create a Postgres database in the same region, set `DATABASE_URL` to its internal URL, use build `bash build.sh` and start `bash start.sh`, and keep health check `/health/`.
3. Set env vars: `DJANGO_SECRET_KEY` (random; the Blueprint generates it), `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS=<service>.onrender.com`, and three different disposable passwords `EVAL_ADMIN_PASSWORD`, `EVAL_AUDITOR_PASSWORD`, `EVAL_TECHNICIAN_PASSWORD` (the same values go in the submission form).
4. Deploy. The build and startup run migrations and demo seeding. The deployment fails with a named missing variable if any `EVAL_*_PASSWORD` is unset. Check `https://<service>.onrender.com/health/`.
5. Deploy the UI (see UI README), then set `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` to the Vercel URL (`https://<project>.vercel.app`, no trailing slash) and run Manual Deploy on Render.
6. Optional: a free uptime pinger reduces cold starts. Not required; the brief allows sleep.
