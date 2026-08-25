# Build context is the repository root so the image can include database/migrations.
FROM python:3.11-slim AS runtime

# The ML wheels are large (numpy, pandas, scikit-learn, xgboost). pip's
# 15-second default read timeout is not enough for them on a slow link, and one
# timed-out download fails the entire layer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /srv/backend

# Dependencies first so application edits do not invalidate the layer.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY database/ /srv/database/

# The risk API imports the sibling `ml` package to load the trained model. Both
# trees put each other on sys.path at import time, so no PYTHONPATH is needed -
# they only have to sit side by side, exactly as they do in the repository.
COPY ml/ /srv/ml/

# The investigation API imports the sibling `agent` package the same way.
COPY agent/ /srv/agent/

# The decision API imports the sibling `policy` package, and the policy loader
# resolves its configuration relative to the repository root, so `config/` has
# to sit beside it exactly as it does in the repository.
COPY policy/ /srv/policy/
COPY config/ /srv/config/

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 razorshield \
    && chown -R razorshield:razorshield /srv
USER razorshield

EXPOSE 8000

ENTRYPOINT ["/srv/backend/scripts/entrypoint.sh"]
# `--no-server-header`: Uvicorn appends its own `Server: uvicorn` at the
# transport layer, *after* the application has produced the response, so the
# security middleware cannot remove it - the wire ends up carrying both
# headers and advertising the exact server after all. This is the only place
# it can be turned off.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
