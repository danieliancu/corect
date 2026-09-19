FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && waitress-serve --listen=0.0.0.0:8080 --url-scheme=https config.wsgi:application"]
