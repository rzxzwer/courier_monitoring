import json
import logging
from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_file,
)
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import db
import osm_service
from .common import login_required, ensure_order_estimate_for_order

logger = logging.getLogger(__name__)


def register_courier_routes(app):
    """
    Роуты, связанные с курьером и dashboard.
    """

    # ===== DASHBOARD (заказы + отчётность) =====
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

        # какая вкладка активна (orders / report / create_user)
        active_tab = request.args.get("tab") or "orders"

        logger.info(
            "dashboard: user_id=%s, role=%s, on_shift=%s, active_tab=%s",
            user_id,
            role,
            on_shift,
            active_tab,
        )

        couriers = None
        operators_list = None

        # ----- Блок заказов -----
        if role == "courier":
            # если у курьера есть активный маршрут — отправляем на /on_route
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

            for o in orders:
                ensure_order_estimate_for_order(o)

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
            # Оператор видит активные заказы и может менять назначения
            orders = db.get_orders_for_operator()
            couriers = db.get_couriers()
            operators_list = db.get_operators()

            logger.info(
                "dashboard: оператор %s, активных заказов: %d",
                user_id,
                len(orders),
            )

            for o in orders:
                ensure_order_estimate_for_order(o)

        # ===== БЛОК ОТЧЁТНОСТИ =====
        report_from = request.args.get("report_from") or ""
        report_to = request.args.get("report_to") or ""
        # для оператора: 'all' | <id>; для курьера поле не используется
        report_courier_id_raw = request.args.get("report_courier_id")
        if report_courier_id_raw is None:
            report_courier_id_raw = "all"

        report_rows = []
        report_summary = None

        # список id курьеров, по которым строим отчёт
        effective_courier_ids: list[int] = []

        if role == "courier":
            effective_courier_ids = [user_id]
            report_courier_id_for_template = None
        else:
            # для оператора список курьеров нужен и в отчётности
            if couriers is None:
                couriers = db.get_couriers()

            if report_courier_id_raw == "all" or not report_courier_id_raw:
                # "Все" курьеры
                effective_courier_ids = [c["id"] for c in couriers]
            else:
                try:
                    cid = int(report_courier_id_raw)
                    effective_courier_ids = [cid]
                except ValueError:
                    effective_courier_ids = []

            report_courier_id_for_template = report_courier_id_raw

        # если указан период и есть хоть один курьер — считаем отчёт
        if report_from and report_to and effective_courier_ids:
            try:
                date_from_dt = datetime.strptime(report_from, "%Y-%m-%d")
                date_to_dt = datetime.strptime(report_to, "%Y-%m-%d")
                # включительно по конец дня
                date_to_dt = date_to_dt + timedelta(days=1) - timedelta(seconds=1)

                date_from_str = date_from_dt.strftime("%Y-%m-%d %H:%M:%S")
                date_to_str = date_to_dt.strftime("%Y-%m-%d %H:%M:%S")

                # собираем строки отчёта по всем выбранным курьерам
                for cid in effective_courier_ids:
                    part = db.get_report_orders(cid, date_from_str, date_to_str)
                    report_rows.extend(part)

            except Exception as e:
                logger.exception("dashboard: ошибка формирования отчёта: %s", e)
                report_rows = []

            # группировка по маршрутам
            routes_map: dict[int, dict] = {}
            total_orders = 0
            total_routes = 0
            total_planned_m = 0
            total_actual_m = 0
            total_duration_sec = 0
            courier_names_set = set()

            for r in report_rows:
                rid = r["route_id"]
                route = routes_map.get(rid)
                if not route:
                    route = {
                        "route_id": rid,
                        "created_at": r["created_at"],
                        "finished_at": r["finished_at"],
                        "distance_planned_m": r["distance_planned_m"] or 0,
                        "distance_actual_m": r["distance_actual_m"] or 0,
                        "duration_planned_sec": r["duration_planned_sec"] or 0,
                        "duration_actual_sec": r["duration_actual_sec"] or 0,
                        "comment": r["comment"],
                        "courier_name": r.get("courier_name"),
                        "orders": [],
                    }
                    routes_map[rid] = route

                    total_routes += 1
                    total_planned_m += route["distance_planned_m"]
                    total_actual_m += route["distance_actual_m"]
                    total_duration_sec += route["duration_actual_sec"]

                order_info = {
                    "order_id": r["order_id"],
                    "external_id": r["external_id"],
                    "client_address": r["client_address"],
                    "delivered_at": r["delivered_at"],
                }
                route["orders"].append(order_info)
                total_orders += 1

                if r.get("courier_name"):
                    courier_names_set.add(r["courier_name"])

            routes_list = list(routes_map.values())

            def _sec_to_hm(sec: int) -> tuple[int, int]:
                h = sec // 3600
                m = (sec % 3600) // 60
                return h, m

            h_total, m_total = _sec_to_hm(total_duration_sec)

            # подпись "Курьер" в сводке
            if role == "courier":
                courier_label = user["full_name"]
            else:
                if not courier_names_set:
                    courier_label = "Курьеры не найдены"
                elif len(courier_names_set) == 1:
                    courier_label = list(courier_names_set)[0]
                else:
                    # несколько курьеров
                    courier_label = ", ".join(sorted(courier_names_set))

            report_summary = {
                "courier_name": courier_label,
                "routes": routes_list,
                "total_orders": total_orders,
                "total_routes": total_routes,
                "total_planned_km": round(total_planned_m / 1000.0, 1)
                if total_planned_m
                else 0,
                "total_actual_km": round(total_actual_m / 1000.0, 1)
                if total_actual_m
                else 0,
                "total_duration_h": h_total,
                "total_duration_m": m_total,
            }

        return render_template(
            "dashboard.html",
            role=role,
            orders=orders,
            user_name=user["full_name"],
            on_shift=on_shift,
            couriers=couriers,
            operators_list=operators_list,
            report_from=report_from,
            report_to=report_to,
            report_courier_id=report_courier_id_for_template
            if role == "operator"
            else None,
            report_summary=report_summary,
            active_tab=active_tab,
        )

    # ===== ПРЕДПРОСМОТР МАРШРУТА =====
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
        logger.info(
            "route_preview: запрос от user_id=%s order_ids=%r",
            session.get("user_id"),
            order_ids,
        )

        if not isinstance(order_ids, list) or not order_ids:
            logger.warning("route_preview: пустой список order_ids")
            return jsonify({"ok": False, "error": "no_orders"}), 400

        try:
            order_ids = [int(x) for x in order_ids]
        except (TypeError, ValueError) as e:
            logger.warning(
                "route_preview: некорректные order_ids=%r: %s", order_ids, e
            )
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
                return jsonify({"ok": False, "error": "different_restaurants"}), 200

        # Собираем waypoints: ресторан, потом клиенты в порядке order_ids
        orders_by_id = {o["id"]: o for o in orders}
        waypoints = []
        waypoints_info = []

        waypoints.append((rest_lat, rest_lon))
        waypoints_info.append(
            {
                "type": "restaurant",
                "lat": float(rest_lat),
                "lon": float(rest_lon),
                "label": first.get("restaurant_address") or "Ресторан",
            }
        )

        for oid in order_ids:
            o = orders_by_id.get(oid)
            if not o:
                logger.warning(
                    "route_preview: заказ oid=%s отсутствует в orders_by_id", oid
                )
                return jsonify({"ok": False, "error": "order_missing"}), 400
            if o["client_lat"] is None or o["client_lon"] is None:
                logger.warning(
                    "route_preview: нет координат клиента для заказа id=%s",
                    o["id"],
                )
                return jsonify({"ok": False, "error": "no_client_coords"}), 200

            waypoints.append((o["client_lat"], o["client_lon"]))
            waypoints_info.append(
                {
                    "type": "client",
                    "lat": float(o["client_lat"]),
                    "lon": float(o["client_lon"]),
                    "label": o.get("client_address") or (f"Клиент #{o['id']}"),
                    "order_id": int(o["id"]),
                }
            )

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

        return jsonify(
            {
                "ok": True,
                "geojson": route["geometry"],
                "distance_m": route["distance_m"],
                "duration_s": route["duration_s"],
                "waypoints": waypoints_info,
            }
        )

    # ===== СТАРТ МАРШРУТА ("В ПУТЬ") =====
    @app.route("/route/start", methods=["POST"])
    @login_required
    def route_start():
        """
        Старт маршрута:
        - принимает order_ids (FormData с JSON-строкой),
        - создаёт запись в routes,
        - проставляет route_id, courier_id, status='on_way', delivery_started_at,
        - возвращает redirect_url на /on_route.
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
            logger.warning(
                "route_start: некорректный order_ids=%r: %s", order_ids_raw, e
            )
            return jsonify({"ok": False, "error": "bad_order_ids"}), 400

        logger.info(
            "route_start: courier_id=%s, order_ids=%r", courier_id, order_ids
        )

        # проверки заказов
        orders = db.get_orders_by_ids(order_ids)
        if not orders:
            return jsonify({"ok": False, "error": "orders_not_found"}), 400

        # Проверяем, что все заказы из одного ресторана
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
                return jsonify(
                    {"ok": False, "error": "different_restaurants"}
                ), 400
            if o["client_lat"] is None or o["client_lon"] is None:
                return jsonify({"ok": False, "error": "no_client_coords"}), 400
            waypoints.append((o["client_lat"], o["client_lon"]))

        # строим маршрут для общей статистики
        try:
            route = osm_service.build_route(waypoints)
        except Exception as e:
            logger.exception("route_start: ошибка построения маршрута OSRM: %s", e)
            return jsonify({"ok": False, "error": "route_failed"}), 500

        geometry_json = json.dumps(route["geometry"])
        total_dist = route["distance_m"]
        total_dur = route["duration_s"]

        # создаём маршрут и привязываем заказы
        try:
            route_id = db.create_route(
                courier_id=courier_id,
                total_distance_m=total_dist,
                total_duration_s=total_dur,
                geometry=geometry_json,
            )
            db.assign_orders_to_route(route_id, courier_id, order_ids)
        except Exception as e:
            logger.exception("route_start: ошибка при записи в БД: %s", e)
            return jsonify({"ok": False, "error": "db_error"}), 500

        logger.info(
            "route_start: маршрут id=%s создан для courier_id=%s, заказов=%d",
            route_id,
            courier_id,
            len(order_ids),
        )

        return jsonify(
            {"ok": True, "route_id": route_id, "redirect_url": url_for("on_route")}
        )

    # ===== СТРАНИЦА "НА МАРШРУТЕ" =====
    @app.route("/on_route", methods=["GET"])
    @login_required
    def on_route():
        """
        Страница курьера, когда он уже "на маршруте".
        Показываем карту + список заказов + кнопку "Закончить".
        """
        if session.get("role") != "courier":
            return redirect(url_for("dashboard"))

        courier_id = session.get("user_id")
        active = db.get_active_route_for_courier(courier_id)
        if not active:
            logger.info(
                "on_route: у курьера %s нет активного маршрута, редирект /dashboard",
                courier_id,
            )
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

        ожидает JSON:
          {
            "distance_changed": false
          }
          или
          {
            "distance_changed": true,
            "actual_distance_km": "12.3",
            "comment": "..."
          }
        """
        if session.get("role") != "courier":
            return jsonify({"ok": False, "error": "forbidden"}), 403

        courier_id = session.get("user_id")
        active = db.get_active_route_for_courier(courier_id)
        if not active:
            logger.info(
                "route_finish: у курьера %s нет активного маршрута", courier_id
            )
            return jsonify({"ok": False, "error": "no_active_route"}), 400

        route_id = active["id"]
        data = request.get_json(silent=True) or {}

        distance_changed = bool(data.get("distance_changed"))
        distance_actual_m = None
        comment = None

        if distance_changed:
            raw_km = str(data.get("actual_distance_km", "")).strip()
            if not raw_km:
                return jsonify({"ok": False, "error": "actual_distance_required"}), 400
            try:
                raw_km = raw_km.replace(",", ".")
                km_val = float(raw_km)
            except ValueError:
                return jsonify({"ok": False, "error": "bad_distance_format"}), 400

            if km_val <= 0:
                return jsonify(
                    {"ok": False, "error": "distance_must_be_positive"}
                ), 400

            distance_actual_m = int(round(km_val * 1000))
            comment = (data.get("comment") or "").strip() or None
        else:
            # расстояние не изменилось — фактическая дистанция = плановой
            distance_actual_m = active.get("distance_planned_m")
            comment = None

        try:
            db.finish_route(route_id, distance_actual_m, comment)
        except Exception as e:
            logger.exception("route_finish: ошибка при записи в БД: %s", e)
            return jsonify({"ok": False, "error": "db_error"}), 500

        logger.info(
            "route_finish: маршрут id=%s завершён курьером %s, distance_actual_m=%s, comment=%r",
            route_id,
            courier_id,
            distance_actual_m,
            comment,
        )

        return jsonify({"ok": True, "redirect_url": url_for("dashboard")})

    # ===== PDF-отчёт =====
    # ===== ЭКСПОРТ ОТЧЁТА В EXCEL =====
    @app.route("/reports/excel", methods=["GET"])
    @login_required
    def reports_excel():
        role = session.get("role")
        user_id = session.get("user_id")
        user = db.get_user_by_id(user_id)
        if not user:
            return redirect(url_for("logout"))

        report_from = request.args.get("report_from") or ""
        report_to = request.args.get("report_to") or ""
        report_courier_id_raw = request.args.get("report_courier_id") or ""

        # --- определяем, по какому курьеру делать отчёт ---
        if role == "courier":
            # курьер всегда получает отчёт только по себе
            effective_courier_id: int | None = user_id
        elif role == "operator":
            # оператор: "" или "all" = все курьеры (courier_id = None)
            if report_courier_id_raw in ("", "all"):
                effective_courier_id = None
            else:
                try:
                    effective_courier_id = int(report_courier_id_raw)
                except ValueError:
                    effective_courier_id = None
        else:
            return "Нет доступа к отчётам", 403

        # --- проверяем только даты, курьера для оператора не требуем ---
        if not (report_from and report_to):
            return redirect(
                url_for("dashboard", report_from=report_from, report_to=report_to, tab="report")
            )

        # преобразуем даты в datetime-диапазон
        try:
            date_from_dt = datetime.strptime(report_from, "%Y-%m-%d")
            date_to_dt = datetime.strptime(report_to, "%Y-%m-%d")
            # включаем весь день "до": +1 день - 1 секунда
            date_to_dt = date_to_dt + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            return redirect(url_for("dashboard", tab="report"))

        date_from_str = date_from_dt.strftime("%Y-%m-%d %H:%M:%S")
        date_to_str = date_to_dt.strftime("%Y-%m-%d %H:%M:%S")

        # --- берём строки отчёта (courier_id может быть None = все курьеры) ---
        rows = db.get_report_orders(effective_courier_id, date_from_str, date_to_str)

        # --- подпись для строки "Курьер" в шапке ---
        if role == "courier":
            courier_label = user["full_name"]
        else:
            # оператор
            if effective_courier_id is None:
                names = sorted({r["courier_name"] for r in rows if r.get("courier_name")})
                if not names:
                    courier_label = "Все курьеры"
                elif len(names) == 1:
                    courier_label = names[0]
                else:
                    courier_label = ", ".join(names)
            else:
                courier_label = rows[0]["courier_name"] if rows else f"курьер id={effective_courier_id}"

        # --- формируем Excel ---
        wb = Workbook()
        ws = wb.active
        ws.title = "Отчёт"

        # Заголовок
        ws["A1"] = "Отчёт по маршрутам курьеров"
        ws["A2"] = f"Курьер(ы): {courier_label}"
        ws["A3"] = f"Период: {report_from} — {report_to}"

        # Шапка таблицы
        header_row = 5
        headers = [
            "Курьер",
            "ID маршрута",
            "Начат",
            "Завершён",
            "План, км",
            "Факт, км",
            "План, мин",
            "Факт, мин",
            "ID заказа",
            "Внешний ID",
            "Адрес клиента",
            "Доставлен",
            "Комментарий",
        ]
        for col_idx, title in enumerate(headers, start=1):
            ws.cell(row=header_row, column=col_idx, value=title)

        # Данные
        row_idx = header_row + 1
        for r in rows:
            plan_km = round((r["distance_planned_m"] or 0) / 1000.0, 1)
            fact_km = round((r["distance_actual_m"] or 0) / 1000.0, 1) if r.get("distance_actual_m") else 0
            plan_min = round((r["duration_planned_sec"] or 0) / 60)
            fact_min = round((r["duration_actual_sec"] or 0) / 60) if r.get("duration_actual_sec") else 0

            ws.cell(row=row_idx, column=1, value=r["courier_name"])
            ws.cell(row=row_idx, column=2, value=r["route_id"])
            ws.cell(row=row_idx, column=3, value=str(r["created_at"]))
            ws.cell(row=row_idx, column=4, value=str(r["finished_at"]))
            ws.cell(row=row_idx, column=5, value=plan_km)
            ws.cell(row=row_idx, column=6, value=fact_km)
            ws.cell(row=row_idx, column=7, value=plan_min)
            ws.cell(row=row_idx, column=8, value=fact_min)
            ws.cell(row=row_idx, column=9, value=r["order_id"])
            ws.cell(row=row_idx, column=10, value=r["external_id"])
            ws.cell(row=row_idx, column=11, value=r["client_address"])
            ws.cell(row=row_idx, column=12, value=str(r["delivered_at"]))
            ws.cell(row=row_idx, column=13, value=r.get("comment"))
            row_idx += 1

        # авто-ширина колонок
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = 0
            for cell in ws[col_letter]:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_len + 2, 50)

        # Отдаём как файл
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)

        filename = f"report_{report_from}_{report_to}.xlsx"
        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ===== АСИНХРОННОЕ ПОЛУЧЕНИЕ КООРДИНАТ ДЛЯ ОДНОГО ЗАКАЗА =====
    @app.route("/api/orders/<int:order_id>/ensure_coords", methods=["POST"])
    @login_required
    def ensure_order_coords(order_id: int):
        """
        Обеспечить наличие координат для заказа.
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
        if (
            r_lat is not None
            and r_lon is not None
            and c_lat is not None
            and c_lon is not None
        ):
            logger.info(
                "ensure_order_coords: координаты уже есть для заказа id=%s",
                order_id,
            )
            return jsonify(
                {
                    "ok": True,
                    "status": "already",
                    "restaurant_lat": r_lat,
                    "restaurant_lon": r_lon,
                    "client_lat": c_lat,
                    "client_lon": c_lon,
                }
            )

        # иначе нужно геокодировать
        try:
            if r_lat is None or r_lon is None:
                logger.info(
                    "ensure_order_coords: геокод ресторана для заказа id=%s addr=%r",
                    order_id,
                    order["restaurant_address"],
                )
                res_rest = osm_service.geocode_address(
                    order["restaurant_address"]
                )
                if not res_rest:
                    logger.warning(
                        "ensure_order_coords: не удалось геокодировать ресторан для заказа id=%s",
                        order_id,
                    )
                    return jsonify(
                        {"ok": False, "error": "restaurant_geocode_failed"}
                    ), 200
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
                    return jsonify(
                        {"ok": False, "error": "client_geocode_failed"}
                    ), 200
                c_lat, c_lon = res_client

            # сохраняем в БД
            db.update_order_coords(order_id, r_lat, r_lon, c_lat, c_lon)
            logger.info(
                "ensure_order_coords: координаты обновлены для заказа id=%s",
                order_id,
            )

            return jsonify(
                {
                    "ok": True,
                    "status": "updated",
                    "restaurant_lat": r_lat,
                    "restaurant_lon": r_lon,
                    "client_lat": c_lat,
                    "client_lon": c_lon,
                }
            )

        except Exception as e:
            logger.exception(
                "ensure_order_coords: исключение при работе с координатами заказа id=%s: %s",
                order_id,
                e,
            )
            return jsonify({"ok": False, "error": "exception"}), 500
