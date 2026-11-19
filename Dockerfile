# ⚠️ STAGE ORDER IS LOAD-BEARING. `prod` must stay LAST.
# An untargeted build (CI, the release workflow, a bare `docker build .`) builds
# the FINAL stage. If `dev` ever ends up last, production ships pytest.
#
# Base image pinned by digest: a tag is movable, a digest is not. The tag names
# the Python PATCH so Dependabot's docker updater sees a name change.
FROM python:3.14.10-slim@sha256:3cabfd7d7aaf394e6040e79fa1eec6ca1c52b74ed9188650472695d9a9326999 AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# No home and no login shell: nothing reads $HOME.
RUN useradd --no-create-home --shell /usr/sbin/nologin --uid 10001 appuser
# ⚠️ Root owns the code: the running app must not be able to rewrite itself.
# CI's image job fails if appuser can write anything under /app.
COPY . .

# The version this image WAS BUILT AS, so a deploy can be verified.
ARG APP_VERSION=dev
ARG APP_COMMIT=dev
ENV APP_VERSION=${APP_VERSION}
ENV APP_COMMIT=${APP_COMMIT}

USER appuser
EXPOSE 8000
# ONE process on purpose: the droplet is shared and has 2 GB. Rate limits live
# in Postgres (#2), so a second process would be correct too, just not needed.
# --proxy-headers trusts host Nginx's X-Forwarded-*; "*" is safe because compose
# publishes the port on 127.0.0.1 only, so Nginx is the only client.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]

# Local development only — selected by docker-compose.override.yml.
FROM base AS dev
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER appuser

# The shipped image. Deliberately empty: it exists so that this, not `dev`, is
# the final stage.
FROM base AS prod
