FROM python:3.12-slim

WORKDIR /app

# cryptography/grpcio occasionally need to build from source on platforms
# without a prebuilt wheel; keeping build tools avoids a flaky image build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .
RUN mkdir -p data out uploads

EXPOSE 5001

# Set FLASK_SECRET_KEY and INTERNSHIP_AGENT_SECRET_KEY at run time — the app
# falls back to insecure dev defaults (with a warning) if they're unset, so
# it still boots without them, but don't run it that way anywhere shared.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--threads", "4", "web_app:app"]
