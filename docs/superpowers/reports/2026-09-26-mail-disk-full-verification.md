# Заполненный диск: разбор и публикация писем

26.09.2026, parser `mail-sources-v20`. Проверены реальные ошибки ENOSPC
на отдельном Linux tmpfs размером 8 MiB. Рабочие данные не заполнялись.
Контейнер без сети, source mount read-only, UID1000; SQL metadata находятся
вне заполненного раздела. LLM заменена детерминированным test double,
Qdrant — in-memory. Это проверка инфраструктурных отказов, не качества модели.

## Дефекты и исправления

Первоначальный прогон: **2 failed, 1 passed**. Upload уже возвращал HTTP507,
но supervisor передавал ENOSPC как `parse_failed`. Родитель терял тип ошибки,
а перегенерация переходила в `error` вместо `paused/storage_full`.
Теперь worker передаёт отдельный код, родитель создаёт безопасный
`StorageFullError`; произвольный текст ошибки не служит основанием для этого кода.

Следующий прогон для EML/вложенного EML/MSG: **2 failed, 1 passed**.
Обработчики повреждённых вложений перехватывали ENOSPC и возвращали
`skipped_parse`, позволяя завершить неполный разбор успешно. В обработчиках
embedded и MSG типизированные ошибки нехватки места теперь пробрасываются.
Новый parser helper распознаёт ENOSPC/EDQUOT и Windows39/112, включая цепочку
причин и защиту от циклов; сообщения EACCES со словами «нет места» не подходят.

## Проверенные результаты

- Реальный tmpfs: **5 passed, 5 warnings, 18.22s**, exit0.
  Upload507/storage_full без строки документа и частичного файла, health200.
  Spawn EML, nested EML и MSG: правильная ошибка, cleanup, отсутствие живого
  дочернего процесса; повтор после освобождения места проходит.
  Regenerate: paused/storage_full, прежние опубликованные bytes/concepts/
  generation ID и point IDs сохранены; resume публикует новый факт24дня.
- Полный parser suite: Windows **369 passed, 1 skipped, 10.50s**;
  Linux **370 passed, 5.92s**. Windows skip относится к POSIX symlink.
- Backend supervisor/error/publication/privacy/upload suite:
  Windows **98 passed, 5 skipped, 2 warnings, 111.37s**;
  Linux **95 passed, 8 skipped, 28 warnings, 71.57s**. Skips только по платформе;
  все103 разных testcase прошли хотя бы на одной из двух ОС.
- Ruff для семи изменённых Python файлов: exit0, `All checks passed!`.

Warnings сохранены в логах: deprecation httpx/authlib, отсутствие возможности
проверить remote Qdrant version в offline контейнере, локальные payload indices.
Они не скрывают errors/failures: JUnit зелёных прогонов содержит нули.
Первый Windows parser запуск имел collection error из-за импорта helper;
исправлен на принятый в suite `tests.mail_fixtures`, затем весь suite пройден.

## Артефакты и воспроизведение

Ignored каталог `tests/tmp/mail-disk-full-v20/`: `red.{log,xml}`,
`green.{log,xml}`, `nested-red.{log,xml}`, `final.{log,xml}`;
`parser-{windows,linux}-final.{log,xml}`, `backend-windows.{log,xml}`,
`backend-linux-final.{log,xml}`. Исходный collection log сохранён отдельно.

Из корня репозитория, с подготовленным тестовым образом:

```powershell
docker run --rm --network none --read-only --user 1000:1000 `
  --tmpfs /tmp:rw,size=384m,mode=1777 --tmpfs /full:rw,size=8m,mode=1777 `
  --mount 'type=bind,source=C:/Users/alexey/ai-workspace/ai-knowledge,target=/workspace,readonly' `
  --mount 'type=bind,source=C:/Users/alexey/ai-workspace/ai-knowledge/tests/tmp/mail-disk-full-v20,target=/reports' `
  -e FULL_STORAGE_TEST_ROOT=/full -e LITELLM_LOCAL_MODEL_COST_MAP=True `
  -e PYTHONPATH=/workspace/backend:/workspace/doc-parser/src -w /workspace/backend `
  okf-mail-verify-tests:20260926-fonts python -m pytest tests/test_mail_disk_full.py -q `
  --basetemp=/tmp/disk-repeat -o cache_dir=/tmp/pytest-cache --junitxml=/reports/repeat.xml
```

Тест отказывается заполнять раздел больше16MiB или тот же раздел, что tmp_path.
Обычный pytest без opt-in пропускает эти пять сценариев. Образ и ignored
артефакты локальны; это пока не самодостаточный clean-checkout CI рецепт.

## Ограничения и решение о версии

Реально заполнены uploads/attachments/publication storage, но не native Windows
диск и не хранилища PostgreSQL/Qdrant. Типизированные DB ошибки покрыты
`test_error_codes`; фактический отказ полного DB/Qdrant раздела этим drill
не доказан. L10 остаётся частичным для расширенной инфраструктурной матрицы.

Parser version не повышена: успешные canonical text/source IDs/offsets не
менялись; исправлена классификация инфраструктурного отказа. Ранее ошибочно
опубликованные частичные результаты автоматически не исправляются — нужен
отдельный выбор документов для regenerate. Массовой перегенерации не было.
Предыдущие2084 backend tests доказывают свой зафиксированный срез до этих
изменений; новый полный backend suite здесь не заявляется.
Модели, mail flag и рабочие сервисы не менялись; commit/push/deploy не выполнялись.
