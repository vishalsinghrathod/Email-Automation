# ==============================================================================
# Production Dockerfile
# Smart Vendor Email & Inventory Automation System
# ==============================================================================

FROM python:3.11-slim

# Set environment variables for performance and clean output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production

# Set working directory inside container
WORKDIR /app

# Install system dependencies (build tools, libffi, openssl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy all project source code
COPY . .

# Ensure storage directories exist
RUN mkdir -p data/downloads data/processed logs reports

# Expose Web Portal HTTP Port
EXPOSE 8080

# Health check to ensure web server is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

# Default execution: Launch Employee Web Portal & Background Automation Engine
CMD ["python", "main.py", "--ui", "--port", "8080", "--no-browser"]

