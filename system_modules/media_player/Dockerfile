FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    vlc \
    libvlc-dev \
    libvlccore-dev \
    cifs-utils \
    nfs-common \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
WORKDIR /app

ENV PYTHONUNBUFFERED=1
