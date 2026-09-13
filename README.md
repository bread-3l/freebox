# Liquid Inbox — серверная заготовка

Это готовая серверная часть для существующего `index.html`.

## Запуск

Нужен Python 3.9+.

```bash
python server.py
```

После запуска открой:

http://localhost:8000

## Что уже работает

- раздача `index.html`;
- `/api/health`;
- `/api/state`;
- `/api/admin/login`;
- `/api/admin/logout`;
- `/api/admin/state`;
- `/api/admin/regenerate-code`;
- локальное хранение в `data/database.json`;
- локальные резервные копии;
- заготовка Google Drive;
- папка Google Drive уже указана через `GOOGLE_DRIVE_FOLDER_ID`.

## Google Drive

Google Drive пока специально отключён.

Позже подключим его через серверные секреты, а не через HTML и не через GitHub-код.

API-ключи и приватные ключи нельзя хранить в репозитории.
