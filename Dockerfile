# Use a supported platform (Linux x64)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies + Node.js (for Decibel sidecar)
RUN apt-get update && apt-get install -y git curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Copy local files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install "urllib3<2.0"

# Install Decibel sidecar dependencies
RUN cd decibel && npm install --omit=dev && cd ..

# Run the bot
CMD ["python", "main.py"]
