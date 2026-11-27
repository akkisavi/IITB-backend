#!/usr/bin/env bash
# Render build command. Requires EVAL_* secrets in the service env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
(cd ../warehouse-ui && npm ci --include=dev --no-audit --no-fund && VITE_API_BASE_URL=/ npm run build)
pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_demo --require-accounts
python manage.py seed_demo_history
