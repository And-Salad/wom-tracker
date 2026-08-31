# The dashboard, the six-hourly scheduler and the summaries, in one container.
#
# Only the web half of the app is installed: no tkinter, no matplotlib, no
# pystray. The charts are drawn in the browser with D3, so the server needs
# nothing more than Flask, waitress, requests and the Anthropic SDK.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WOM_DATA_DIR=/data

WORKDIR /app

# Just the web dependencies. requirements.txt also lists the desktop ones,
# which would drag in a compiler and 200 MB of image for nothing.
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
