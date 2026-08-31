# The dashboard, the six-hourly scheduler and the summaries, in one container.
# The charts are drawn in the browser, so nothing here needs a display.
#
# Pinned rather than floating: an unpinned tag means the runtime can change
# under a test suite that never ran on it.
FROM python:3.12.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WOM_DATA_DIR=/data

WORKDIR /app

# Spelled out rather than -r requirements.txt so a rebuild cannot pick up
# something the tests never saw.
RUN pip install --no-cache-dir \
        "flask>=3.0" "waitress>=3.0" "requests>=2.31" "anthropic>=1.0" "tzdata>=2024.1"

COPY wom/ ./wom/
COPY assets/ ./assets/
COPY web_app.py wom_tracker.py ./

# The volume mounts here; the app writes its database, config, prompts and
# logs into it, so everything survives a redeploy.
VOLUME /data

EXPOSE 8000

# --with-scheduler is what replaces the desktop app: this process does the
# six-hourly updates and the summaries as well as serving the pages.
CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8000", "--with-scheduler"]
