FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create data directory for database
RUN mkdir -p /data /var/log/app

EXPOSE 8000

CMD ["uvicorn", "app.main:main", "--host", "0.0.0.0", "--port", "8000"]
