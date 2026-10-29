# Meeting Notes server.   docker compose up -d   (see docs/INSTALL.md)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY dashboard dashboard

# Never run as root; notes live in /data, which is the only writable place.
RUN useradd --system --uid 10001 --no-create-home app \
 && mkdir /data && chown app /data
USER app

# Inside the container it must listen on all interfaces; docker-compose.yml
# publishes it on the host's loopback only.
ENV HOST=0.0.0.0 PORT=8766 DATA_DIR=/data
VOLUME /data
EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8766'), timeout=4).status == 200 else 1)"

CMD ["python", "backend/main.py"]
