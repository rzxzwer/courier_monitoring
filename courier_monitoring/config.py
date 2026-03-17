# config.py
import os
import logging
from logging.handlers import RotatingFileHandler

# ===== Flask =====
SECRET_KEY = "change_this_to_a_random_secret_key"

# ===== MySQL =====
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "dreddred"
DB_NAME = "courier_monitoring"  # твоя БД

# ===== Прочее =====
# Для пагинации, если понадобится
PAGE_SIZE = 20

# ===== ЛОГИРОВАНИЕ =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")

# Настройка логгера: ротация по 5 МБ, 5 файлов
_logger = logging.getLogger()
_logger.setLevel(logging.INFO)

# Проверяем, нет ли уже хэндлера (чтобы не дублировать при перезапусках)
if not any(isinstance(h, RotatingFileHandler) for h in _logger.handlers):
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)

# Также можно выводить в консоль (по желанию):
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_logger.addHandler(console_handler)

# ===== OSM / OSRM / Nominatim =====

OSRM_BASE_URL = "https://router.project-osrm.org"

# Обязательно укажи реальный email — так требует политика OSM/Nominatim
OSM_USER_AGENT = "courier-monitoring-app/1.0 (nefritful@gmail.com)"

OSM_HEADERS = {
    "User-Agent": OSM_USER_AGENT
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
