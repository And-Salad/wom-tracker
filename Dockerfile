# The dashboard, the ten-minute update schedule and the summaries, in one
# container.
# The charts are drawn in the browser, so nothing here needs a display.
#
# Pinned rather than floating: an unpinned tag means the runtime can change
# under a test suite that never ran on it.
FROM python:3.14.7-slim

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
# tzdata used to be named again on the end of this line, because that file
# marked it Windows-only while a slim Debian needs it just as much - no system
# zone database, and every day boundary silently falls back to UTC without
# one. It is unmarked there now, so this is one list again in fact and not
# only in the comment.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY wom/ ./wom/
COPY assets/ ./assets/
COPY web_app.py wom_tracker.py ./

# Which commit this image is, so the running app can say so and a deploy can
# be confirmed rather than assumed - see wom/build.py. A container has no git
# and no repository to ask, so it has to be baked in.
#
# Last, deliberately. An ENV invalidates every layer after it, and this one
# changes on every single commit; up beside the pip install it would throw
# away the dependency cache each time and turn a thirty second build into a
# two minute one.
ARG GIT_SHA=""
ENV WOM_BUILD_SHA=$GIT_SHA

# The volume mounts here; the app writes its database, config, prompts and
# logs into it, so everything survives a redeploy.
VOLUME /data

EXPOSE 8000

# --with-scheduler is what makes this the whole application: one process runs
# the ten-minute updates and the summaries as well as serving the pages.
CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8000", "--with-scheduler"]
