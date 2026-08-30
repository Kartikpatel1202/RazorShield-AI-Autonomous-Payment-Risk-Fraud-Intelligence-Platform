FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt

RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY database /app/database
COPY ml /app/ml
COPY agent /app/agent
COPY policy /app/policy
COPY config /app/config

ENV PYTHONPATH=/app
# Unbuffered so the bootstrap's progress reaches the platform log as it happens
# rather than in one block when the process exits.
ENV PYTHONUNBUFFERED=1

WORKDIR /app/backend

RUN chmod +x /app/backend/docker-entrypoint.sh

CMD ["/app/backend/docker-entrypoint.sh"]
