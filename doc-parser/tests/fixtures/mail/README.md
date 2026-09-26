# Корпус MSG

Хэши, происхождение и лицензии — в `manifest.json`. Корпус не содержит
пользовательского MSG из Documents.

`synthetic-unicode-attachment.msg` создан кодом проекта (`tests/mail_fixtures.py`,
MIT). В нём кириллическая тема «Решение 3509», тестовые адреса example.test и XLSX
со строкой «3509 / 12». Генератор CFB проверяет каждый записанный поток через
независимый `olefile` reader. Это синтетический контейнер, не квалификация экспорта
конкретной версии Outlook. Он проходит настоящий CFB reader, без mock provider.

`test_mail_binary_contract.py` дополнительно генерирует MSG с разными родителем
и ребёнком, защищёнными/неподдерживаемыми классами. Для бинарных вложений тест
сравнивает скачиваемые bytes с исходным CFB stream. Синтетические маркеры S/MIME
и IRM проверяют диагностику, а не криптографическое содержимое или подписи.

У `rtf-simple-sent.msg` есть `1000001E` plain body и `10090102` RTF. Название
файла не означает, что он RTF-only. В `nested-rtf.msg` текст ребёнка —
«This is a testmail.», а «Mail in mail.» относится к родителю; прежнее
ожидание последней фразы у ребёнка закрепляло ошибку provider scope.

`synthetic-rtf-only-compressed.msg` и `synthetic-rtf-only-uncompressed.msg`
созданы `rtf_only_msg()` в `tests/mail_fixtures.py` (MIT) с MIT encoder
`compressed-rtf`. В каждом ровно 4096 bytes, нет plain/HTML body, есть поток
RTF `10090102` и контрольное вложение `proof.bin`. Тест запрещает открытие RTF
потока, проверяет `unsupported_rtf_body` и точные bytes вложения. Это проверка
явного отказа, не поддержка конвертации RTF.

Структура тестового CFB построена по
[MS-CFB header](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea).
