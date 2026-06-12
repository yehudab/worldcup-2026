FROM python:3.12-slim

WORKDIR /app

# On-disk cache for fetched JSON (mounted as a volume in docker-compose).
ENV CACHE_DIR=/data
RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

EXPOSE 5001
CMD ["python", "app.py"]
