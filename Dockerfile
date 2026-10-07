# LiveCity single-service production image
FROM node:22-alpine AS web-build
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web-build /web/dist ./web
EXPOSE 8000
ENV PORT=8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port \$PORT"]
