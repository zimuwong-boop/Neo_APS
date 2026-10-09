"""Check local Markdown document links without accessing credentials or the network."""
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
documents = [ROOT / "README.md", *(ROOT / "doc").glob("*.md")]
errors = []
for document in documents:
    for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
        target = target.strip("<>").split("#", 1)[0]
        if not target or "://" in target:
            continue
        if not (document.parent / unquote(target)).resolve().exists():
            errors.append(f"{document.name}: {target}")
if errors:
    raise SystemExit("Missing document targets: " + ", ".join(errors))
print(f"Checked local links in {len(documents)} Markdown documents.")
