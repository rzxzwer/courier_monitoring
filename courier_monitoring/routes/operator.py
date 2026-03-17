import json
import logging

from flask import (
    request,
    redirect,
    url_for,
    session,
    jsonify,
)

import db
from .common import login_required

logger = logging.getLogger(__name__)


def register_operator_routes(app):
    """
    Роуты, специфичные для оператора.
    """

    # ===== СОЗДАНИЕ ПОЛЬЗОВАТЕЛЯ (уже было в шаблоне) =====
    @app.route("/operator/create_user", methods=["POST"])
    @login_required
    def operator_create_user():
        if session.get("role") != "operator":
            return redirect(url_for("dashboard"))

        full_name = request.form.get("full_name", "").strip()
        login_val = request.form.get("login", "").strip()
        email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "courier")

        if not full_name or not login_val or not password:
            # для простоты просто вернёмся на дашборд
            logger.warning("operator_create_user: не заполнены поля")
            return redirect(url_for("dashboard"))

        try:
            db.create_user(full_name, login_val, email, password, role)
        except Exception as e:
            logger.exception("operator_create_user: ошибка при создании пользователя: %s", e)

        return redirect(url_for("dashboard"))

    # ===== СОЗДАНИЕ ЗАКАЗА ИЗ ВЕБ-ИНТЕРФЕЙСА ОПЕРАТОРА =====
    @app.route("/operator/orders/create", methods=["POST"])
    @login_required
    def operator_create_order():
        if session.get("role") != "operator":
            return jsonify({"ok": False, "error": "forbidden"}), 403

        operator_id = session.get("user_id")

        data = request.get_json(silent=True) or {}
        external_id = (data.get("external_id") or "").strip() or None
        rest_addr = (data.get("restaurant_address") or "").strip()
        client_addr = (data.get("client_address") or "").strip()
        due_at_raw = (data.get("due_at") or "").strip()
        comment = (data.get("comment") or "").strip() or None

        if not rest_addr or not client_addr:
            return jsonify({"ok": False, "error": "missing_addresses"}), 400

        due_at = None
        if due_at_raw:
            # ожидаем строку из <input type="datetime-local">: "YYYY-MM-DDTHH:MM"
            from datetime import datetime

            try:
                # добавим ":00", если нет секунд
                if len(due_at_raw) == 16:
                    due_at_raw = due_at_raw + ":00"
                due_at = datetime.fromisoformat(due_at_raw)
            except Exception as e:
                logger.warning("operator_create_order: bad due_at %r: %s", due_at_raw, e)
                due_at = None

        # items_json: сделаем простой объект из комментария, чтобы поле не было пустым
        items = []
        if comment:
            items.append({"name": "Комментарий оператора", "qty": 1, "price": 0, "comment": comment})

        items_json = json.dumps(items, ensure_ascii=False)

        sql = """
            INSERT INTO orders (
                external_id,
                items_json,
                restaurant_address, restaurant_lat, restaurant_lon,
                client_address, client_lat, client_lon,
                created_at, due_at,
                operator_id, courier_id,
                status
            )
            VALUES (%s, %s,
                    %s, NULL, NULL,
                    %s, NULL, NULL,
                    NOW(), %s,
                    %s, NULL,
                    'new')
        """
        try:
            with db.db_cursor() as cur:
                cur.execute(
                    sql,
                    (
                        external_id,
                        items_json,
                        rest_addr,
                        client_addr,
                        due_at,
                        operator_id,
                    ),
                )
                new_id = cur.lastrowid
        except Exception as e:
            logger.exception("operator_create_order: ошибка вставки в БД: %s", e)
            return jsonify({"ok": False, "error": "db_error"}), 500

        logger.info("operator_create_order: создан заказ id=%s оператором %s", new_id, operator_id)
        return jsonify({"ok": True, "order_id": new_id})

    # ===== ИЗМЕНЕНИЕ НАЗНАЧЕННОГО ОПЕРАТОРА/КУРЬЕРА =====
    @app.route("/operator/orders/assign", methods=["POST"])
    @login_required
    def operator_update_order_assignments():
        if session.get("role") != "operator":
            # не JSON-ответ, а обычный редирект, т.к. форма теперь обычная
            return redirect(url_for("dashboard"))

        # читаем из обычной формы
        order_id_raw = request.form.get("order_id")
        operator_id_raw = request.form.get("operator_id")
        courier_id_raw = request.form.get("courier_id")  # может быть ""

        app.logger.info(
            "operator_update_order_assignments: FORM order_id=%r, operator_id=%r, courier_id=%r",
            order_id_raw, operator_id_raw, courier_id_raw
        )

        try:
            order_id = int(order_id_raw)
        except (TypeError, ValueError):
            app.logger.warning("operator_update_order_assignments: bad_order_id=%r", order_id_raw)
            # просто назад на дашборд
            return redirect(url_for("dashboard", tab="orders"))

        try:
            operator_id = int(operator_id_raw)
        except (TypeError, ValueError):
            app.logger.warning("operator_update_order_assignments: bad_operator_id=%r", operator_id_raw)
            return redirect(url_for("dashboard", tab="orders"))

        if courier_id_raw in ("", None):
            courier_id_int = None
        else:
            try:
                courier_id_int = int(courier_id_raw)
            except (TypeError, ValueError):
                app.logger.warning("operator_update_order_assignments: bad_courier_id=%r", courier_id_raw)
                return redirect(url_for("dashboard", tab="orders"))

        try:
            db.update_order_assignments(order_id, operator_id, courier_id_int)
        except Exception as e:
            logger.exception("operator_update_order_assignments: ошибка БД: %s", e)
            # при ошибке тоже вернёмся в заказы
            return redirect(url_for("dashboard", tab="orders"))

        logger.info(
            "operator_update_order_assignments: OK order_id=%s, operator_id=%s, courier_id=%s",
            order_id,
            operator_id,
            courier_id_int,
        )

        # после сохранения — обратно на дашборд, вкладка "Заказы"
        return redirect(url_for("dashboard", tab="orders"))

