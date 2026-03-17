# osm_service.py
import logging
import requests
import config

logger = logging.getLogger(__name__)


def geocode_address(address: str):
    """
    Геокодирование адреса через Nominatim (OpenStreetMap).
    Возвращает (lat, lon) как float или None, если не найдено.
    """
    if not address:
        logger.warning("geocode_address: пустой адрес")
        return None

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }

    try:
        logger.info("geocode_address: запрос к Nominatim q=%r", address)
        resp = requests.get(
            config.NOMINATIM_URL,
            params=params,
            headers=config.OSM_HEADERS,
            timeout=5,
        )
        logger.info(
            "geocode_address: ответ Nominatim status=%s url=%s",
            resp.status_code,
            resp.url,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("geocode_address: ошибка при запросе к Nominatim: %s", e)
        return None

    if not data:
        logger.warning("geocode_address: нет результатов для адреса %r", address)
        return None

    first = data[0]
    try:
        lat = float(first["lat"])
        lon = float(first["lon"])
    except Exception as e:
        logger.exception(
            "geocode_address: ошибка парсинга координат для адреса %r: %s",
            address,
            e,
        )
        return None

    logger.info(
        "geocode_address: координаты для %r -> lat=%s, lon=%s",
        address,
        lat,
        lon,
    )
    return lat, lon


def build_route(waypoints):
    """
    Построить маршрут по списку точек.
    waypoints: список (lat, lon), первая — ресторан, дальше — клиенты.
    Возвращает dict: distance_m, duration_s, geometry.
    """
    if not waypoints or len(waypoints) < 2:
        logger.warning("build_route: недостаточно точек: %r", waypoints)
        raise ValueError("Нужно минимум две точки для маршрута")

    coords_str = ";".join(f"{lon},{lat}" for (lat, lon) in waypoints)
    url = f"{config.OSRM_BASE_URL}/route/v1/driving/{coords_str}"
    params = {
        "overview": "full",
        "geometries": "geojson",
    }

    try:
        logger.info(
            "build_route: запрос к OSRM url=%s params=%r",
            url,
            params,
        )
        resp = requests.get(
            url,
            params=params,
            headers=config.OSM_HEADERS,
            timeout=5,
        )
        logger.info(
            "build_route: ответ OSRM status=%s final_url=%s",
            resp.status_code,
            resp.url,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("build_route: ошибка при запросе к OSRM: %s", e)
        raise

    routes = data.get("routes")
    if not routes:
        logger.error("build_route: OSRM не вернул маршруты: %r", data)
        raise ValueError("Маршрут не найден")

    route = routes[0]
    distance = route.get("distance")
    duration = route.get("duration")
    geometry = route.get("geometry")

    logger.info(
        "build_route: успех, distance=%s m, duration=%s s",
        distance,
        duration,
    )

    return {
        "distance_m": distance,
        "duration_s": duration,
        "geometry": geometry,
    }
