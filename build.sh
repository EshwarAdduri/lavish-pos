#!/usr/bin/env bash
# Runs on Render every time you push to GitHub. Stops at the first error.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py seed_shop        # only adds the starter menu if the menu is empty
python manage.py ensure_owner     # only creates the owner login if it doesn't exist
