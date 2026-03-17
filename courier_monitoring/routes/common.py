# routes/common.py
import logging
from functools import wraps

from flask import session, redirect, url_for, request

import db
import osm_service

logger = logging.getLogger(__name__)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            logger.info(
                "Неавторизованный доступ к %s, редирект на /login", request.path
            )
            return redirect(url_for("login_page"))
        return view_func(*args, **kwargs)

    return wrapper


def ensure_order_estimate_for_order(order: dict):
    """
    Гарантируем, что у заказа есть примерная дистанция/время (route_distance_m / route_duration_sec).
    Если нет —:
      1) обеспечиваем координаты (рест + клиент),
      2) строим маршрут через OSRM,
      3) сохраняем в order_stats,
      4) подставляем значения в dict заказа.
    При ошибках просто логируем и оставляем n/a.
    """
    order_id = order.get("id")
    if not order_id:
        return

    dist = order.get("route_distance_m")
    dur = order.get("route_duration_sec")

    # Если статистика уже есть и не нулевая — ничего не делаем
    try:
        if dist and float(dist) > 0 and dur and float(dur) > 0:
            return
    except Exception:
        # если что-то странное с типами — просто пересчитаем
        pass

    logger.info(
        "ensure_order_estimate_for_order: пересчитываем статистику для заказа id=%s",
        order_id,
    )

    # 1. Координаты
    r_lat = order.get("restaurant_lat")
    r_lon = order.get("restaurant_lon")
    c_lat = order.get("client_lat")
    c_lon = order.get("client_lon")

    # если нет координат ресторана/клиента — пробуем геокодировать
    try:
        if r_lat is None or r_lon is None:
            logger.info(
                "ensure_order_estimate_for_order: геокод ресторана для заказа id=%s addr=%r",
                order_id,
                order.get("restaurant_address"),
            )
            res_rest = osm_service.geocode_address(
                order.get("restaurant_address") or ""
            )
            if not res_rest:
                logger.warning(
                    "ensure_order_estimate_for_order: не удалось геокодировать ресторан для заказа id=%s",
                    order_id,
                )
                return
            r_lat, r_lon = res_rest
            db.update_order_coords(order_id, r_lat, r_lon, c_lat, c_lon)

        if c_lat is None or c_lon is None:
            logger.info(
                "ensure_order_estimate_for_order: геокод клиента для заказа id=%s addr=%r",
                order_id,
                order.get("client_address"),
            )
            res_client = osm_service.geocode_address(
                order.get("client_address") or ""
            )
            if not res_client:
                logger.warning(
                    "ensure_order_estimate_for_order: не удалось геокодировать клиента для заказа id=%s",
                    order_id,
                )
                return
            c_lat, c_lon = res_client
            db.update_order_coords(order_id, r_lat, r_lon, c_lat, c_lon)
    except Exception as e:
        logger.exception(
            "ensure_order_estimate_for_order: ошибка при получении координат заказа id=%s: %s",
            order_id,
            e,
        )
        return

    # Если после геокодинга всё ещё нет координат — выходим
    if r_lat is None or r_lon is None or c_lat is None or c_lon is None:
        logger.warning(
            "ensure_order_estimate_for_order: нет координат даже после геокодинга, заказ id=%s",
            order_id,
        )
        return

    # 2. Строим маршрут (рест -> клиент)
    try:
        waypoints = [(r_lat, r_lon), (c_lat, c_lon)]
        route = osm_service.build_route(waypoints)
    except Exception as e:
        logger.exception(
            "ensure_order_estimate_for_order: ошибка построения маршрута для заказа id=%s: %s",
            order_id,
            e,
        )
        return

    distance_m = route["distance_m"]
    duration_s = route["duration_s"]

    logger.info(
        "ensure_order_estimate_for_order: успех, order_id=%s, dist=%s m, dur=%s s",
        order_id,
        distance_m,
        duration_s,
    )

    # 3. Сохраняем в order_stats
    try:
        db.upsert_order_estimate(order_id, distance_m, duration_s)
    except Exception as e:
        logger.exception(
            "ensure_order_estimate_for_order: не удалось сохранить статистику в БД для заказа id=%s: %s",
            order_id,
            e,
        )
        # Даже если не записали в БД, на страницу подставим значения
        pass

    # 4. Подставляем в dict заказа, чтобы шаблон не показывал n/a
    order["route_distance_m"] = distance_m
    order["route_duration_sec"] = duration_s
