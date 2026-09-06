FROM python:3.11-slim

# Tesseract is a real OCR engine (not a Python package) -- this is why the
# app needs a Docker deploy on Render rather than its plain Python runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
        && rm -rf /var/lib/apt/lists/*

        WORKDIR /app

        COPY requirements.txt .
        RUN pip install --no-cache-dir -r requirements.txt

        COPY . .

        # One worker keeps the Sunday-night scheduler from firing more than once
        # (each gunicorn worker would otherwise run its own copy of the job).
        # Plenty of headroom for under 15 contractors.
        CMD gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app
        
