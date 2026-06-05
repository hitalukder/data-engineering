# ── Stage 1: build deps ────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Java is required by JPype1 / jaydebeapi for DB2 JDBC
RUN apt-get update && apt-get install -y --no-install-recommends \
        default-jdk-headless \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ─────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Only the JRE is needed at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application source
COPY app.py .
COPY customTypes/ customTypes/

# DB2 JDBC driver — must be present in the build context
# Download from: https://www.ibm.com/support/pages/db2-jdbc-driver-versions
COPY db2jcc4.jar .

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 4050

# Disable Python output buffering so Cloud Engine sees logs immediately
ENV PYTHONUNBUFFERED=1
ENV JAVA_HOME=/usr/lib/jvm/default-java

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4050", \
     "--workers", "2", "--log-level", "info"]
