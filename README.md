# warehouse-api

Django REST Framework API for the Warehouse Management System. Main docs (overview, design, AI usage) live in [`../warehouse-ui/`](../warehouse-ui/README.md) in this same repository.

## Runtime
- Python **3.12** (see `.python-version`)
- Django 5.1, DRF (token auth), django-cors-headers, gunicorn, whitenoise

## Database: SQLite
The API uses one SQLite file, at `SQLITE_PATH` (default `./db.sqlite3`), both locally and when hosted. `migrate` creates it if it does not exist.

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

### Uniqueness and concurrency
"Pallet number / barcode unique among *active* pallets" is a real database constraint: `active_pallet_number` and `active_barcode` carry a unique index and are cleared (NULL) on unload. **One pallet per location** is a one-to-one column. SQLite allows one writer at a time (20 s busy timeout), so two simultaneous loads into the same slot end in one success and one `409`; a Playwright test checks this.

## Commands
| Command | Effect |
|---|---|
| `python manage.py seed_demo` | Idempotent. Creates/updates demo users from `EVAL_*` (skips a role whose password is unset), ensures 25 locations and shuttle `SH-01`, adds sample pallets only to an empty DB, fills missing sample history for the previous 6 days, upserts today's live snapshot. Never deletes data. |
| `python manage.py reset_demo --yes-i-am-sure` | Destructive: deletes pallets, operation logs and snapshots (not users), then reseeds. Refuses without the flag, and refuses on a hosted deploy (`DJANGO_ALLOWED_HOSTS` not local) unless `--allow-remote` is also passed. |
| `python manage.py test` | API tests. |

`evaluation.json` tells the evaluator to run `seed_demo` and probe `/health/`.

## Environment variables
| Var | Default | Notes |
|---|---|---|
| `SQLITE_PATH` | `./db.sqlite3` | Database file |
| `DJANGO_SECRET_KEY` | dev value in local mode or with `DJANGO_DEBUG=1` | Required when hosted |
| `DJANGO_DEBUG` | `0` | |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | comma list; any other host means hosted mode |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | comma list |
| `CSRF_TRUSTED_ORIGINS` | unset | comma list |
| `EVAL_{ADMIN,AUDITOR,TECHNICIAN}_USERNAME` | `demo_admin` / `demo_auditor` / `demo_technician` | |
| `EVAL_{ADMIN,AUDITOR,TECHNICIAN}_PASSWORD` | unset | 12-128 chars, no whitespace; never commit |
| `MAILER_ADDRESS`, `MAILER_PORT`, `MAILER_MAIL`, `MAILER_PASS`, `MAILER_FROM_NAME` | unset | SMTP for low-battery alerts; without `MAILER_MAIL` alerts print to the console |
| `LOW_BATTERY_ALERT_TO` | `MAILER_MAIL` | comma list of alert recipients |

## Local vs hosted
| | Local / evaluation | Hosted |
|---|---|---|
| DB | SQLite file (`SQLITE_PATH`) | SQLite file on the Render instance |
| Concurrency | SQLite single-writer lock + unique constraints | Same |
| Server | `runserver` | gunicorn on Render free web service |
| Availability | always on | Sleeps after 15 min idle; first request takes about a minute. |

## Deployment (free, no payment card)
Stack: **Render free web service** (API, SQLite) + **Vercel Hobby** (UI).

**Data persistence:** Render's free filesystem is ephemeral. The build runs `migrate` and `seed_demo`, so every deploy, restart or wake from sleep starts again from the seeded demo data, and changes made through the app (loads, unloads, new users) are lost. For data that must last, attach a persistent disk (paid) and point `SQLITE_PATH` at it.

Checklist (the owner must sign in; nothing is automated):
1. Push this repo to a public GitHub repository.
2. **Render** (render.com, sign in with GitHub): New > Blueprint and pick the repo (uses `render.yaml`). Or New > Web Service: runtime Python, plan Free, build `bash build.sh`, start `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`, health check path `/health/`.
3. Set env vars: `DJANGO_SECRET_KEY` (random; the Blueprint generates it), `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS=<service>.onrender.com`, and three different disposable passwords `EVAL_ADMIN_PASSWORD`, `EVAL_AUDITOR_PASSWORD`, `EVAL_TECHNICIAN_PASSWORD` (the same values go in the submission form).
4. Deploy. The build runs `collectstatic`, `migrate` and `seed_demo` (Render's pre-deploy command is paid-only, so these run in the build). Check `https://<service>.onrender.com/health/`.
5. Deploy the UI (see UI README), then set `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` to the Vercel URL (`https://<project>.vercel.app`, no trailing slash) and run Manual Deploy on Render.
6. Optional: a free uptime pinger reduces cold starts. Not required; the brief allows sleep.
