# TLS GigaChat в Docker и Bothost

## Причина и исправление

Bothost вернул `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
certificate chain`. В исходном `python:3.11-slim` не было явно установлено
доверие к российской цепочке, используемой GigaChat.

Официальная документация GigaChat требует сертификаты НУЦ Минцифры для OAuth
и API. Фактическая проверенная цепочка OAuth-узла 29.10.2026:

```text
ngw.devices.sberbank.ru
└── Russian Trusted Sub CA 2024
    └── Russian Trusted Root CA
```

Отпечаток промежуточного сертификата сервера совпал с официальным Sub CA 2024.
Root и Sub CA 2024 добавлены в `certificates/`, копируются в системное
хранилище Debian и регистрируются через `update-ca-certificates`.
`GIGACHAT_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt` явно направляет Requests
на итоговый bundle, поэтому решение не зависит от отдельного набора `certifi`.
Клиент также продолжает использовать `truststore`.

Источники:

- документация GigaChat: https://developers.sber.ru/docs/ru/gigachat/certificates
- портал сертификатов Госуслуг: https://www.gosuslugi.ru/crt
- официальное хранилище файлов и отпечатки: `certificates/README.md`

`verify=False`, `CERT_NONE` и другие обходы не используются.

## Проверка после пересборки

Без сети — проверка конфигурации и доступности bundle:

```bash
python check_gigachat_tls.py
```

Только DNS и проверенные TLS-handshake, без OAuth, ключа и вызова модели:

```bash
python check_gigachat_tls.py --network
```

Ожидаемый результат — `OK` для:

- `ngw.devices.sberbank.ru:9443`;
- `api.giga.chat:443`.

После двух `OK` можно отдельно выполнить один разрешённый рабочий цикл. Только
он подтверждает OAuth и запрос к модели из сети Bothost.

## Если ошибка сохраняется

Если проверка показывает другую цепочку или новый самоподписанный сертификат,
нужно проверить TLS-прокси, антивирусное/корпоративное посредничество и
маршрутизацию Bothost. Сертификат посредника нельзя автоматически добавлять в
доверенные: сначала требуется получить его у владельца инфраструктуры и
проверить назначение и отпечаток. TLS-проверку отключать нельзя.

`gaierror` указывает на DNS, `TimeoutError` — на сеть, firewall или порт; эти
ошибки установкой CA не исправляются. Локальный успех также не доказывает
доступность сети внутри контейнера Bothost.
