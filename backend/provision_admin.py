"""Create the single admin once and save generated credentials privately."""
import argparse
import json
import os
import secrets
from pathlib import Path

import django
from django.contrib.auth import get_user_model

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

parser = argparse.ArgumentParser()
parser.add_argument("--credentials-file", required=True)
args = parser.parse_args()
path = Path(args.credentials_file)
User = get_user_model()
if not User.objects.filter(username="admin").exists():
    password = secrets.token_urlsafe(24)
    User.objects.create_superuser(username="admin", password=password)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"username": "admin", "password": password}, indent=2), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    print("Created admin; credentials saved privately, values not printed.")
else:
    print("Existing admin retained; password unchanged.")
