#!/usr/bin/env bash
# Render build command. Requires EVAL_* secrets in the service env.
set -o errexit
pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_demo
