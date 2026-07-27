FROM python:3.12-slim

WORKDIR /app

COPY requirements/web.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY logger_setup.py db.py mongo_db.py otel_utils.py schema.sql ./

RUN mkdir -p /app/logs /app/data /app/output

EXPOSE 5001

CMD ["opentelemetry-instrument", "gunicorn", "-w", "4", "-b", "0.0.0.0:5001", "--access-logfile", "-", "--error-logfile", "-", "app.main:app"]
