# app.py
import os
import datetime
import json
import logging
import uuid
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
)
from werkzeug.utils import secure_filename

import config
import db
import osm_service

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

logger = logging.getLogger(__name__)

# === каталог для картинок (скрины старта/конца маршрута) ===
UPLOAD_IMG_DIR = os.path.join(app.root_path, "static", "img")
os.makedirs(UPLOAD_IMG_DIR, exist_ok=True)


def save_uploaded_image(file_storage):
    """
    Сохраняет загруженный файл в static/img и возвращает относительный путь
    (например: 'img/uuid.jpg'), который можно писать в БД.
    Если файл не передан или ошибка — возвращает None.
    """
    if not file_storage or not file_storage.filename:
        return None

    try:
        # аккуратно берём расширение
        filename_secure = secure_filename(file_storage.filename)
        _, ext = os.path.splitext(filename_secure)
        ext = ext.lower() or ".jpg"

        new_name = f"{uuid.uuid4().hex}{ext}"
        full_path = os.path.join(UPLOAD_IMG_DIR, new_name)
        file_storage.save(full_path)

        rel_path = f"img/{new_name}"
        logger.info(
            "save_uploaded_image: file %r сохранён в %s (rel=%s)",
            file_storage.filename,
            full_path,
            rel_path,
        )
        return rel_path
    except Exception:
        logger.exception(
            "save_uploaded_image: ошибка сохранения файла %r",
            getattr(file_storage, "filename", None),
        )
        return None


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            logger.info("Неавторизованный доступ к %s, редирект на /login", request.path)
            return redirect(url_for("login_page"))
        return view_func(*args, **kwargs)

    return wrapper


@app.route("/", methods=["GET"])
def root():
    if "user_id" not in session:
        logger.info("root: пользователь не авторизован, редирект на /login")
        return redirect(url_for("login_page"))

    user_id = session.get("user_id")
    role = session.get("role")

    # если у курьера есть активный маршрут — сразу на страницу "На маршруте"
    if role == "courier":
        active_route = db.get_active_route_for_courier(user_id)
        if active_route:
            logger.info("root: курьер %s имеет активный маршрут, редирект на /on_route", user_id)
            return redirect(url_for("on_route"))

    logger.info("root: пользователь %s -> redirect /dashboard", user_id)
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_action():
    login_value = request.form.get("login", "").strip()
    password = request.form.get("password", "").strip()

    logger.info("login_action: попытка логина login=%r", login_value)

    user = db.get_user_by_credentials(login_value, password)
    if not user:
        logger.warning("login_action: неуспех для логина %r", login_value)
        return render_template("login.html", error="Неверный логин или пароль")

    session["user_id"] = user["id"]
    session["full_name"] = user["full_name"]
    session["role"] = user["role"]
    session["login"] = user["login"]

    logger.info(
        "login_action: успех user_id=%s, role=%s",
        user["id"],
        user["role"],
    )

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    logger.info("logout: user_id=%s выходит из системы", user_id)
    session.clear()
    return redirect(url_for("login_page"))


# ===== ХЕЛПЕР ДЛЯ ПРИМЕРНОЙ СТАТИСТИКИ ЗАКАЗА =====
def ensure_order_estimate_for_order(order: dict):
    """
    Гарантируем, что у заказа есть примерная дистанция/время в order_stats.
    Если route_distance_m / route_duration_sec пустые, пробуем:
      1) обеспечить координаты (рест + клиент),
      2) построить маршрут через OSRM,
      3) сохранить в order_stats,
      4) подставить значения в dict заказа (route_distance_m/route_duration_sec).
    Если что-то падает — просто логируем и оставляем n/a.
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
            res_rest = osm_service.geocode_address(order.get("restaurant_address") or "")
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
            res_client = osm_service.geocode_address(order.get("client_address") or "")
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


# ===== DASHBOARD =====
@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    user_id = session.get("user_id")
    user = db.get_user_by_id(user_id)
    if not user:
        logger.error("dashboard: пользователь %s не найден в БД, выходим", user_id)
        return redirect(url_for("logout"))

    role = user["role"]
    on_shift = bool(user["on_shift"])

    logger.info("dashboard: user_id=%s, role=%s, on_shift=%s", user_id, role, on_shift)

    # если у курьера есть активный маршрут — отправляем на /on_route
    if role == "courier":
        active_route = db.get_active_route_for_courier(user_id)
        if active_route:
            logger.info(
                "dashboard: курьер %s имеет активный маршрут id=%s, редирект на /on_route",
                user_id,
                active_route["id"],
            )
            return redirect(url_for("on_route"))

        # Для курьера: только новые заказы
        orders = db.get_new_orders_for_courier_view()
        logger.info(
            "dashboard: курьер %s, новых заказов: %d",
            user_id,
            len(orders),
        )

        # гарантируем статистику для каждого заказа
        for o in orders:
            ensure_order_estimate_for_order(o)

        # Разбор JSON состава заказа
        for o in orders:
            items_raw = o.get("items_json")
            try:
                o["items_list"] = json.loads(items_raw) if items_raw else []
            except Exception as e:
                logger.exception(
                    "dashboard: ошибка парсинга items_json для заказа id=%s: %s",
                    o.get("id"),
                    e,
                )
                o["items_list"] = []

    else:
        # Оператор видит все заказы
        orders = db.get_orders_for_operator()
        logger.info(
            "dashboard: оператор %s, заказов: %d",
            user_id,
            len(orders),
        )

        # тоже подтягиваем статистику
        for o in orders:
            ensure_order_estimate_for_order(o)

    return render_template(
        "dashboard.html",
        role=role,
        orders=orders,
        user_name=user["full_name"],
        on_shift=on_shift,
    )


# ===== ПРЕДПРОСМОТР МАРШРУТА ДЛЯ БЛОКА "МАРШРУТ" =====
@app.route("/route/preview", methods=["POST"])
@login_required
def route_preview():
    """
    Построение маршрута для выбранных заказов.
    Только для курьера.
    """
    if session.get("role") != "courier":
        logger.warning(
            "route_preview: доступ запрещён для роли %s",
            session.get("role"),
        )
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    order_ids = data.get("order_ids") or []
    logger.info("route_preview: запрос от user_id=%s order_ids=%r", session.get("user_id"), order_ids)

    if not isinstance(order_ids, list) or not order_ids:
        logger.warning("route_preview: пустой список order_ids")
        return jsonify({"ok": False, "error": "no_orders"}), 400

    try:
        order_ids = [int(x) for x in order_ids]
    except (TypeError, ValueError) as e:
        logger.warning("route_preview: некорректные order_ids=%r: %s", order_ids, e)
        return jsonify({"ok": False, "error": "bad_ids"}), 400

    orders = db.get_orders_by_ids(order_ids)
    logger.info("route_preview: найдено заказов в БД: %d", len(orders))

    if not orders:
        logger.warning("route_preview: заказы не найдены для %r", order_ids)
        return jsonify({"ok": False, "error": "orders_not_found"}), 400

    # Проверяем, что все заказы из одного ресторана (по координатам)
    first = orders[0]
    rest_lat = first["restaurant_lat"]
    rest_lon = first["restaurant_lon"]

    if rest_lat is None or rest_lon is None:
        logger.warning(
            "route_preview: нет координат ресторана для заказа id=%s",
            first.get("id"),
        )
        return jsonify({"ok": False, "error": "no_restaurant_coords"}), 200

    for o in orders[1:]:
        if o["restaurant_lat"] != rest_lat or o["restaurant_lon"] != rest_lon:
            logger.info(
                "route_preview: заказы из разных ресторанов: base_rest=(%s,%s), order_id=%s rest=(%s,%s)",
                rest_lat,
                rest_lon,
                o["id"],
                o["restaurant_lat"],
                o["restaurant_lon"],
            )
            # Разные рестораны — показываем специальную ошибку
            return jsonify({"ok": False, "error": "different_restaurants"}), 200

    # Собираем waypoints: ресторан, потом клиенты в порядке order_ids
    orders_by_id = {o["id"]: o for o in orders}
    waypoints = []
    waypoints_info = []

    # старт — ресторан
    waypoints.append((rest_lat, rest_lon))
    waypoints_info.append({
        "type": "restaurant",
        "lat": float(rest_lat),
        "lon": float(rest_lon),
        "label": first.get("restaurant_address") or "Ресторан",
    })

    for oid in order_ids:
        o = orders_by_id.get(oid)
        if not o:
            logger.warning("route_preview: заказ oid=%s отсутствует в orders_by_id", oid)
            return jsonify({"ok": False, "error": "order_missing"}), 400
        if o["client_lat"] is None or o["client_lon"] is None:
            logger.warning(
                "route_preview: нет координат клиента для заказа id=%s",
                o["id"],
            )
            return jsonify({"ok": False, "error": "no_client_coords"}), 200

        waypoints.append((o["client_lat"], o["client_lon"]))
        waypoints_info.append({
            "type": "client",
            "lat": float(o["client_lat"]),
            "lon": float(o["client_lon"]),
            "label": o.get("client_address") or f"Клиент #{o['id']}",
            "order_id": int(o["id"]),
        })

    logger.info("route_preview: waypoints=%r", waypoints)

    try:
        route = osm_service.build_route(waypoints)
    except Exception as e:
        logger.exception("route_preview: ошибка построения маршрута OSRM: %s", e)
        return jsonify({"ok": False, "error": "route_failed"}), 500

    logger.info(
        "route_preview: успех, distance=%s m, duration=%s s",
        route["distance_m"],
        route["duration_s"],
    )

    return jsonify({
        "ok": True,
        "geojson": route["geometry"],
        "distance_m": route["distance_m"],
        "duration_s": route["duration_s"],
        "waypoints": waypoints_info,
    })


# ===== СТАРТ МАРШРУТА ("В ПУТЬ") =====
@app.route("/route/start", methods=["POST"])
@login_required
def route_start():
    """
    Старт маршрута:
    - принимает order_ids (JSON) и опционально фото старта;
    - создаёт запись в routes;
    - проставляет route_id, status='on_way', delivery_started_at,
      start_screenshot_url у заказов;
    - после этого курьер попадает на /on_route.
    """
    if session.get("role") != "courier":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    courier_id = session.get("user_id")

    # проверяем, что нет активного маршрута
    active = db.get_active_route_for_courier(courier_id)
    if active:
        logger.warning(
            "route_start: у курьера %s уже есть активный маршрут id=%s",
            courier_id,
            active["id"],
        )
        return jsonify({"ok": False, "error": "already_on_route"}), 400

    order_ids_raw = request.form.get("order_ids", "")
    try:
        order_ids = json.loads(order_ids_raw)
        if not isinstance(order_ids, list) or not order_ids:
            raise ValueError("order_ids is not a non-empty list")
        order_ids = [int(x) for x in order_ids]
    except Exception as e:
        logger.warning("route_start: некорректный order_ids=%r: %s", order_ids_raw, e)
        return jsonify({"ok": False, "error": "bad_order_ids"}), 400

    logger.info("route_start: courier_id=%s, order_ids=%r", courier_id, order_ids)

    # файл фото старта -> static/img, ссылка в БД
    file = request.files.get("start_photo")
    start_photo_url = save_uploaded_image(file)  # 'img/uuid.ext' или None

    # проверки заказов
    orders = db.get_orders_by_ids(order_ids)
    if not orders:
        return jsonify({"ok": False, "error": "orders_not_found"}), 400

    # Проверяем, что все заказы из одного ресторана (как в route_preview)
    first = orders[0]
    rest_lat = first["restaurant_lat"]
    rest_lon = first["restaurant_lon"]

    if rest_lat is None or rest_lon is None:
        return jsonify({"ok": False, "error": "no_restaurant_coords"}), 400

    orders_by_id = {o["id"]: o for o in orders}
    waypoints = [(rest_lat, rest_lon)]

    for oid in order_ids:
        o = orders_by_id.get(oid)
        if not o:
            return jsonify({"ok": False, "error": "order_missing"}), 400
        if o["restaurant_lat"] != rest_lat or o["restaurant_lon"] != rest_lon:
            return jsonify({"ok": False, "error": "different_restaurants"}), 400
        if o["client_lat"] is None or o["client_lon"] is None:
            return jsonify({"ok": False, "error": "no_client_coords"}), 400
        waypoints.append((o["client_lat"], o["client_lon"]))

    # строим маршрут для общей статистики по маршруту
    try:
        route = osm_service.build_route(waypoints)
    except Exception as e:
        logger.exception("route_start: ошибка построения маршрута OSRM: %s", e)
        return jsonify({"ok": False, "error": "route_failed"}), 500

    total_dist = route["distance_m"]
    total_dur = route["duration_s"]

    # создаём маршрут и привязываем заказы
    try:
        route_id = db.create_route(
            courier_id=courier_id,
            total_distance_m=total_dist,
            total_duration_s=total_dur,
            start_photo_path=start_photo_url,
        )
        db.assign_orders_to_route(route_id, order_ids, start_screenshot_url=start_photo_url)
    except Exception as e:
        logger.exception("route_start: ошибка при записи в БД: %s", e)
        return jsonify({"ok": False, "error": "db_error"}), 500

    logger.info(
        "route_start: маршрут id=%s создан для courier_id=%s, заказов=%d",
        route_id,
        courier_id,
        len(order_ids),
    )

    return jsonify({"ok": True, "route_id": route_id, "redirect_url": url_for("on_route")})


# ===== СТРАНИЦА "НА МАРШРУТЕ" =====
@app.route("/on_route", methods=["GET"])
@login_required
def on_route():
    """
    Страница курьера, когда он уже "на маршруте".
    Показываем только карту + список заказов + кнопку "Закончить".
    """
    if session.get("role") != "courier":
        return redirect(url_for("dashboard"))

    courier_id = session.get("user_id")
    active = db.get_active_route_for_courier(courier_id)
    if not active:
        logger.info("on_route: у курьера %s нет активного маршрута, редирект /dashboard", courier_id)
        return redirect(url_for("dashboard"))

    route_id = active["id"]
    orders = db.get_orders_for_route(route_id)
    order_ids = [o["id"] for o in orders]

    logger.info(
        "on_route: courier_id=%s, route_id=%s, заказов в маршруте=%d",
        courier_id,
        route_id,
        len(order_ids),
    )

    return render_template(
        "on_route.html",
        route=active,
        orders=orders,
        order_ids=order_ids,
    )


# ===== ФИНИШ МАРШРУТА ("ЗАКОНЧИТЬ") =====
@app.route("/route/finish", methods=["POST"])
@login_required
def route_finish():
    """
    Завершение маршрута:
    - находит активный маршрут курьера;
    - принимает фото конца;
    - обновляет routes и orders;
    - возвращает redirect_url=/dashboard.
    """
    if session.get("role") != "courier":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    courier_id = session.get("user_id")
    active = db.get_active_route_for_courier(courier_id)
    if not active:
        logger.info("route_finish: у курьера %s нет активного маршрута", courier_id)
        return jsonify({"ok": False, "error": "no_active_route"}), 400

    route_id = active["id"]

    # фото завершения -> static/img
    file = request.files.get("end_photo")
    end_photo_url = save_uploaded_image(file)  # 'img/uuid.ext' или None

    try:
        db.finish_route(route_id, end_photo_url)
    except Exception as e:
        logger.exception("route_finish: ошибка при записи в БД: %s", e)
        return jsonify({"ok": False, "error": "db_error"}), 500

    logger.info("route_finish: маршрут id=%s завершён курьером %s", route_id, courier_id)

    return jsonify({"ok": True, "redirect_url": url_for("dashboard")})


# ===== СОЗДАНИЕ ПОЛЬЗОВАТЕЛЯ ОПЕРАТОРОМ =====
@app.route("/operator/create_user", methods=["POST"])
@login_required
def operator_create_user():
    if session.get("role") != "operator":
        logger.warning(
            "operator_create_user: доступ запрещён для роли %s",
            session.get("role"),
        )
        return "Доступ запрещён", 403

    full_name = request.form.get("full_name", "").strip()
    login_value = request.form.get("login", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "courier")

    logger.info(
        "operator_create_user: попытка создания пользователя full_name=%r login=%r role=%r",
        full_name,
        login_value,
        role,
    )

    if not full_name or not login_value or not password:
        logger.warning("operator_create_user: не указаны обязательные поля")
        return redirect(url_for("dashboard"))

    if role not in ("courier", "operator"):
        logger.warning("operator_create_user: некорректная роль %r, заменяем на 'courier'", role)
        role = "courier"

    db.create_user(full_name, login_value, email, password, role)
    logger.info("operator_create_user: пользователь %r успешно создан", login_value)
    return redirect(url_for("dashboard"))


# ===== НАЧАТЬ/ЗАКОНЧИТЬ СМЕНУ =====
@app.route("/toggle_shift", methods=["POST"])
@login_required
def toggle_shift():
    user_id = session.get("user_id")
    user = db.get_user_by_id(user_id)
    if not user:
        logger.error("toggle_shift: пользователь %s не найден, выходим", user_id)
        return redirect(url_for("logout"))

    if user["on_shift"]:
        logger.info("toggle_shift: user_id=%s завершает смену", user_id)
        db.end_shift(user_id)
    else:
        logger.info("toggle_shift: user_id=%s начинает смену", user_id)
        db.start_shift(user_id)

    return redirect(url_for("dashboard"))


# ===== АСИНХРОННОЕ ПОЛУЧЕНИЕ КООРДИНАТ ДЛЯ ОТДЕЛЬНОГО ЗАКАЗА =====
@app.route("/api/orders/<int:order_id>/ensure_coords", methods=["POST"])
@login_required
def ensure_order_coords(order_id: int):
    """
    Обеспечить наличие координат для заказа:
    если есть — просто вернуть статус,
    если нет — запросить у OSM и сохранить в БД.
    """
    if session.get("role") != "courier":
        logger.warning(
            "ensure_order_coords: доступ запрещён для роли %s",
            session.get("role"),
        )
        return jsonify({"ok": False, "error": "forbidden"}), 403

    logger.info(
        "ensure_order_coords: user_id=%s, order_id=%s",
        session.get("user_id"),
        order_id,
    )

    order = db.get_order_for_coords(order_id)
    if not order:
        logger.warning("ensure_order_coords: заказ id=%s не найден", order_id)
        return jsonify({"ok": False, "error": "not_found"}), 404

    r_lat = order["restaurant_lat"]
    r_lon = order["restaurant_lon"]
    c_lat = order["client_lat"]
    c_lon = order["client_lon"]

    # если уже есть все координаты — ничего не делаем
    if r_lat is not None and r_lon is not None and c_lat is not None and c_lon is not None:
        logger.info(
            "ensure_order_coords: координаты уже есть для заказа id=%s",
            order_id,
        )
        return jsonify({
            "ok": True,
            "status": "already",
            "restaurant_lat": r_lat,
            "restaurant_lon": r_lon,
            "client_lat": c_lat,
            "client_lon": c_lon,
        })

    # иначе нужно геокодировать
    try:
        if r_lat is None or r_lon is None:
            logger.info(
                "ensure_order_coords: геокод ресторана для заказа id=%s addr=%r",
                order_id,
                order["restaurant_address"],
            )
            res_rest = osm_service.geocode_address(order["restaurant_address"])
            if not res_rest:
                logger.warning(
                    "ensure_order_coords: не удалось геокодировать ресторан для заказа id=%s",
                    order_id,
                )
                return jsonify({"ok": False, "error": "restaurant_geocode_failed"}), 200
            r_lat, r_lon = res_rest

        if c_lat is None or c_lon is None:
            logger.info(
                "ensure_order_coords: геокод клиента для заказа id=%s addr=%r",
                order_id,
                order["client_address"],
            )
            res_client = osm_service.geocode_address(order["client_address"])
            if not res_client:
                logger.warning(
                    "ensure_order_coords: не удалось геокодировать клиента для заказа id=%s",
                    order_id,
                )
                return jsonify({"ok": False, "error": "client_geocode_failed"}), 200
            c_lat, c_lon = res_client

        # сохраняем в БД
        db.update_order_coords(order_id, r_lat, r_lon, c_lat, c_lon)
        logger.info(
            "ensure_order_coords: координаты обновлены для заказа id=%s",
            order_id,
        )

        return jsonify({
            "ok": True,
            "status": "updated",
            "restaurant_lat": r_lat,
            "restaurant_lon": r_lon,
            "client_lat": c_lat,
            "client_lon": c_lon,
        })

    except Exception as e:
        logger.exception(
            "ensure_order_coords: исключение при работе с координатами заказа id=%s: %s",
            order_id,
            e,
        )
        return jsonify({"ok": False, "error": "exception"}), 500


if __name__ == "__main__":
    app.run(debug=True)
