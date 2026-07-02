FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY VERSION /app/VERSION
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLYMAIL_DATA_DIR=/app/data
ENV FLYMAIL_UI_DIR=/app/frontend-dist
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8080

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/ /app/backend/
COPY VERSION /app/VERSION
COPY --from=frontend-builder /app/dist/ui /app/frontend-dist

EXPOSE 8080

CMD ["python", "main.py"]
