FROM python:3.13-alpine

RUN adduser -D -u 10001 app
USER app
WORKDIR /app
COPY --chown=app:app app.py .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["python", "app.py"]
