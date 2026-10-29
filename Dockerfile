FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GIGACHAT_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY certificates/russian_trusted_root_ca_2022.crt /usr/local/share/ca-certificates/russian_trusted_root_ca_2022.crt
COPY certificates/russian_trusted_sub_ca_2024.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca_2024.crt
RUN update-ca-certificates

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "run.py"]
