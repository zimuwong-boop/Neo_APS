"""Download project-local Windows Node.js and PostgreSQL distributions."""
import concurrent.futures
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / ".tools"
TOOLS.mkdir(exist_ok=True)
LOCK = json.loads((ROOT / "scripts" / "tool-versions.json").read_text(encoding="utf-8"))


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "NeoAPS-environment-setup"})
    return urllib.request.urlopen(request, timeout=120)


def download(url, destination):
    if destination.is_file():
        return
    print(f"Downloading {destination.name}", flush=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with fetch(url) as source, temporary.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
    temporary.replace(destination)


def extract(archive):
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            resolved = (TOOLS / entry.filename).resolve()
            if not resolved.is_relative_to(TOOLS.resolve()):
                raise ValueError("Unsafe archive entry")
        source.extractall(TOOLS)


def node():
    version = LOCK["node"]["version"]
    filename = f"node-{version}-win-x64.zip"
    archive = TOOLS / filename
    download(f"https://nodejs.org/dist/{version}/{filename}", archive)
    with fetch(f"https://nodejs.org/dist/{version}/SHASUMS256.txt") as response:
        sums = response.read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.endswith("  " + filename))
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected or actual != LOCK["node"]["sha256"]:
        raise ValueError("Node checksum mismatch")
    target = TOOLS / f"node-{version}-win-x64"
    stable = TOOLS / "node"
    if not stable.exists():
        if not (target / "node.exe").exists():
            extract(archive)
        target.rename(stable)
    return {"version": version, "source": f"https://nodejs.org/dist/{version}/", "sha256": expected}


def postgres():
    version, url = LOCK["postgresql"]["version"], LOCK["postgresql"]["source"]
    archive = TOOLS / f"postgresql-{version}-windows-x64-binaries.zip"
    download(url, archive)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != LOCK["postgresql"]["download_sha256"]:
        raise ValueError("PostgreSQL archive differs from the locked download")
    if not (TOOLS / "pgsql" / "bin" / "postgres.exe").exists():
        extract(archive)
    return {"version": version, "source": url, "download_sha256": checksum}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        jobs = {"node": executor.submit(node), "postgresql": executor.submit(postgres)}
        result = {name: task.result() for name, task in jobs.items()}
    (TOOLS / "versions.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({name: value["version"] for name, value in result.items()}), flush=True)
