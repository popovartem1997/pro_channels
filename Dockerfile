FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps:
# - build tools for mysqlclient
# - weasyprint deps (cairo/pango/gdk-pixbuf) + fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config \
    default-libmysqlclient-dev \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    shared-mime-info fonts-dejavu \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["gunicorn", "pro_channels.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]

