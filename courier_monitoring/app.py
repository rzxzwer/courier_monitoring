# app.py
import logging

from flask import Flask

import config
from routes import init_routes


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Базовая настройка логов
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Регистрируем все маршруты
    init_routes(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
