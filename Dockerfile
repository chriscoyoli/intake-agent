FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (the .dockerignore keeps secrets out of the image)
COPY . .

ENV PYTHONUNBUFFERED=1

# Socket Mode worker: no inbound port, just a long-running process.
CMD ["python", "app.py"]
