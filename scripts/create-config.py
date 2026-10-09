import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for example, target in [(".env.dev.example", ".env.dev"), ("deploy/.env.example", "deploy/.env")]:
    path = ROOT / target
    if not path.exists():
        text = (ROOT / example).read_text(encoding="utf-8")
        text = text.replace("replace-with-generated-secret", secrets.token_urlsafe(48))
        text = text.replace("replace-with-generated-password", secrets.token_urlsafe(32))
        path.write_text(text, encoding="utf-8")
        print(f"Created {target} with generated credentials (values not printed).")
    else:
        print(f"Keeping existing {target}.")
