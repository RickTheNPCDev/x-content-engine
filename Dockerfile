# Patched for the eMudhra CA incident (3.13.18). Pinned by digest, so a moved tag cannot change the build.
FROM python:3.13.18-slim@sha256:76baad6037690d6e8c4397fbcaef17bca6f19eecef822b708fe8fa3417d30bb5

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

RUN useradd --uid 1000 --no-create-home app && mkdir /data && chown app /data
COPY --chown=app . .

ENV DATA_DIR=/data CONTENT_ENGINE_CONTAINER=1
USER app
CMD ["python", "main.py"]
