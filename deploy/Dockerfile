FROM public.ecr.aws/docker/library/node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM public.ecr.aws/docker/library/python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PGDATA=/var/lib/neo-aps/postgres \
    PATH=/usr/lib/postgresql/17/bin:$PATH \
    DB_HOST=127.0.0.1 DB_PORT=5432 DJANGO_DEBUG=false
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gosu postgresql-common \
    && mkdir -p /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && echo 'create_main_cluster = false' > /etc/postgresql-common/createcluster.conf \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-17 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 neoapp
WORKDIR /app
COPY backend/requirements-dev.txt /app/backend/requirements-dev.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-dev.txt
COPY backend/ /app/backend/
COPY deploy/ /app/deploy/
COPY --from=frontend /build/dist/ /app/backend/webdist/
RUN chmod +x /app/deploy/*.sh \
    && DJANGO_SECRET_KEY=build-only-placeholder DB_NAME=build DB_USER=build DB_PASSWORD=build python backend/manage.py collectstatic --noinput
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD ["python", "/app/deploy/healthcheck.py"]
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
