FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy module source
COPY . .

# Create writable config directory
RUN mkdir -p /config

# Module connects to core via WebSocket bus — no port needed
CMD ["python", "main.py"]
