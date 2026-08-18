
FROM python:3.10-slim

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .



RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir $(grep -v '^torch' requirements.txt | grep -v '^torchaudio') && \
    pip install --no-cache-dir gradio==5.38.0


COPY . .


RUN mkdir -p /app/outputs


EXPOSE 7860


HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860')" || exit 1


CMD ["python", "app.py"]
