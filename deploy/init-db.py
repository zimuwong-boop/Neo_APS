import os

import psycopg
from psycopg import sql

with psycopg.connect("host=/var/run/postgresql user=postgres dbname=postgres", autocommit=True) as conn:
    user, name = os.environ["DB_USER"], os.environ["DB_NAME"]
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,)).fetchone():
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(user), sql.Literal(os.environ["DB_PASSWORD"])))
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(user)))
