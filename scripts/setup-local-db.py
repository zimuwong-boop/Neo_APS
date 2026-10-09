"""Initialize/start the project's dedicated local PostgreSQL cluster."""
import os
import subprocess
import tempfile
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / ".tools" / "pgsql" / "bin"
DATA = Path(os.environ["LOCALAPPDATA"]) / "NeoAPS" / "postgres-dev"
CONFIG = dotenv_values(ROOT / ".env.dev")
PORT = CONFIG["DB_PORT"]
USER = CONFIG["DB_USER"]
NAME = CONFIG["DB_NAME"]


def run(executable, *args, env=None, check=True):
    return subprocess.run([str(BIN / executable), *map(str, args)], env=env, check=check,
                          creationflags=subprocess.CREATE_NO_WINDOW)


if not (DATA / "PG_VERSION").exists():
    if DATA.exists() and any(DATA.iterdir()):
        raise RuntimeError("Nonempty database directory without PG_VERSION; refusing initialization")
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as password_file:
        password_file.write(CONFIG["DB_PASSWORD"])
        password_path = Path(password_file.name)
    try:
        run("initdb.exe", "-D", DATA, "-U", USER, "--encoding=UTF8", "--locale=C",
            "--auth=scram-sha-256", "--pwfile", password_path)
    finally:
        password_path.unlink()
if (DATA / "PG_VERSION").read_text().strip() != "17":
    raise RuntimeError("PostgreSQL major version mismatch")
if run("pg_ctl.exe", "-D", DATA, "status", check=False).returncode:
    run("pg_ctl.exe", "-D", DATA, "-l", DATA / "server.log", "-o",
        f"-p {PORT} -h 127.0.0.1", "-w", "-t", "60", "start")
with psycopg.connect(host="127.0.0.1", port=PORT, user=USER,
                    password=CONFIG["DB_PASSWORD"], dbname="postgres", autocommit=True) as conn:
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (NAME,)).fetchone():
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(NAME), sql.Identifier(USER)))
    version = conn.execute("SHOW server_version").fetchone()[0]
print(f"Local PostgreSQL {version} ready on 127.0.0.1:{PORT}; database {NAME}")
print(f"Data directory: {DATA}")
