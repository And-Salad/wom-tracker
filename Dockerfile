# The dashboard, the ten-minute update schedule and the summaries, in one
# container.
# The charts are drawn in the browser, so nothing here needs a display.
#
# Pinned rather than floating: an unpinned tag means the runtime can change
# under a test suite that never ran on it.
FROM python:3.12.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WOM_DATA_DIR=/data

WORKDIR /app

# One list, not two. This was spelled out here to stop a rebuild picking up
# something the tests never saw - but ">=" floors do not stop that, and having
# the same dependencies written twice only meant the copies could disagree,
# which they had. The floors live in requirements.txt, which the tests run
# against; the base image above is what is actually pinned.
#
# tzdata is asked for on top of that list because requirements.txt marks it
# Windows-only, and this image needs it just as much: a slim Debian carries no
# system zone database, and without one every day boundary silently falls back
# to UTC.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "tzdata>=2024.1"

COPY wom/ ./wom/
COPY assets/ ./assets/
COPY web_app.py wom_tracker.py ./

# The volume mounts here; the app writes its database, config, prompts and
# logs into it, so everything survives a redeploy.
VOLUME /data

EXPOSE 8000

# --with-scheduler is what makes this the whole application: one process runs
# the ten-minute updates and the summaries as well as serving the pages.
CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8000", "--with-scheduler"]
