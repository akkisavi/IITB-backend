#!/usr/bin/env bash
# Recreate the demo baseline when Render starts with a fresh ephemeral SQLite file.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
python manage.py migrate --noinput
python manage.py seed_demo --require-accounts
python manage.py seed_demo_history
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 60
