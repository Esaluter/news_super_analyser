# Сертификаты TLS GigaChat

Файлы получены только из официального хранилища сертификатов Госуслуг,
на которое ссылается документация GigaChat:

- `russian_trusted_root_ca_2022.crt` —
  `https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt`;
- `russian_trusted_sub_ca_2024.crt` —
  `https://gu-st.ru/content/lending/russian_trusted_sub_ca_2024_pem.crt`.

Контрольные SHA-256 отпечатки DER-сертификатов:

```text
D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31  Russian Trusted Root CA
2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2  Russian Trusted Sub CA 2024
```

Root действует с 01.03.2022 по 27.02.2032, Sub CA 2024 — с 15.07.2024 по
19.07.2029. Подпись Sub CA проверена по Root. Проверенная цепочка
`ngw.devices.sberbank.ru:9443` на 29.10.2026 содержит именно Sub CA 2024 с
указанным отпечатком.

При обновлении сертификатов нужно повторно скачать их с официального источника,
проверить субъект, издателя, срок действия, подпись цепочки и обновить
зафиксированные отпечатки вместе с тестами.
