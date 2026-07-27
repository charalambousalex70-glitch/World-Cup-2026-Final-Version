FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, in their own layer — Docker then reuses the installed
# packages whenever only application code changed, which makes deploys fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY data ./data

# Railway supplies $PORT at runtime; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
