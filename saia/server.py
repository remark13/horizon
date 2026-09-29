"""Запуск локального Docker-сервиса после проверки миграций."""
from saia import db


def main() -> None:
    import uvicorn
    db.migrate()
    uvicorn.run('saia.api:app', host='0.0.0.0', port=8080, reload=False)


if __name__ == '__main__':
    main()
