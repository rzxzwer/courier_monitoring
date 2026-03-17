# db.py
import pymysql
from contextlib import contextmanager


def connect_db():
    # при необходимости поправь креды
    return pymysql.connect(
        host="localhost",
        user="root",
        password="123456",
        database="diplomchik",
        port=3306,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@contextmanager
def db_cursor():
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===== Пользователи =====

def get_user_by_credentials(login_value: str, password: str):
    sql = """
        SELECT *
        FROM users
        WHERE login = %s AND password = %s
        LIMIT 1
    """
    with db_cursor() as cur:
        cur.execute(sql, (login_value, password))
        return cur.fetchone()


def get_user_by_id(user_id: int):
    sql = "SELECT * FROM users WHERE id = %s"
    with db_cursor() as cur:
        cur.execute(sql, (user_id,))
        return cur.fetchone()


def create_user(full_name: str, login_value: str, email: str | None,
                password: str, role: str):
    sql = """
        INSERT INTO users (full_name, login, email, password, role, on_shift)
        VALUES (%s, %s, %s, %s, %s, 0)
    """
    with db_cursor() as cur:
        cur.execute(sql, (full_name, login_value, email, password, role))


def start_shift(user_id: int):
    with db_cursor() as cur:
        cur.execute("UPDATE users SET on_shift = 1 WHERE id = %s", (user_id,))
        cur.execute(
            "INSERT INTO shifts (user_id, shift_start) VALUES (%s, NOW())",
            (user_id,),
        )


def end_shift(user_id: int):
    with db_cursor() as cur:
        cur.execute("UPDATE users SET on_shift = 0 WHERE id = %s", (user_id,))
        # закрываем последнюю смену без end
        cur.execute(
            """
            UPDATE shifts
            SET shift_end = NOW()
            WHERE user_id = %s AND shift_end IS NULL
            ORDER BY shift_start DESC
            LIMIT 1
            """,
            (user_id,),
        )


# ===== Заказы =====

def get_new_orders_for_courier_view():
    """
    Новые заказы для курьера, с подставленными оценочными дистанцией/временем.
    """
    sql = """
        SELECT
            o.*,
            s.estimated_distance_m  AS route_distance_m,
            s.estimated_duration_sec AS route_duration_sec
        FROM orders o
        LEFT JOIN order_stats s ON s.order_id = o.id
        WHERE o.status = 'new'
        ORDER BY o.due_at IS NULL, o.due_at
    """
    with db_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def get_orders_for_operator():
    """
    Все НЕ доставленные заказы для оператора.
    Оператор может менять operator_id и courier_id.
    """
    sql = """
        SELECT
            o.*,
            s.estimated_distance_m   AS route_distance_m,
            s.estimated_duration_sec AS route_duration_sec,
            c.full_name              AS courier_name,
            op.full_name             AS operator_name
        FROM orders o
        LEFT JOIN order_stats s ON s.order_id = o.id
        LEFT JOIN users c  ON c.id  = o.courier_id
        LEFT JOIN users op ON op.id = o.operator_id
        WHERE o.status <> 'delivered'
        ORDER BY o.created_at DESC
    """
    with db_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()

def update_order_assignments(order_id: int, operator_id: int, courier_id: int | None):
    """
    Обновить назначенного оператора и курьера для заказа.
    """
    sql = """
        UPDATE orders
        SET operator_id = %s,
            courier_id  = %s
        WHERE id = %s
    """
    with db_cursor() as cur:
        cur.execute(sql, (operator_id, courier_id, order_id))


def get_orders_by_ids(order_ids: list[int]):
    if not order_ids:
        return []

    placeholders = ", ".join(["%s"] * len(order_ids))
    sql = f"""
        SELECT *
        FROM orders
        WHERE id IN ({placeholders})
        ORDER BY id
    """
    with db_cursor() as cur:
        cur.execute(sql, order_ids)
        return cur.fetchall()


def get_order_for_coords(order_id: int):
    sql = """
        SELECT id,
               restaurant_address, restaurant_lat, restaurant_lon,
               client_address,     client_lat,     client_lon
        FROM orders
        WHERE id = %s
    """
    with db_cursor() as cur:
        cur.execute(sql, (order_id,))
        return cur.fetchone()


def update_order_coords(order_id: int,
                        restaurant_lat, restaurant_lon,
                        client_lat, client_lon):
    sql = """
        UPDATE orders
        SET restaurant_lat = %s,
            restaurant_lon = %s,
            client_lat     = %s,
            client_lon     = %s
        WHERE id = %s
    """
    with db_cursor() as cur:
        cur.execute(
            sql,
            (restaurant_lat, restaurant_lon, client_lat, client_lon, order_id),
        )


# ===== Статистика заказов (order_stats) =====

def upsert_order_estimate(order_id: int,
                          estimated_distance_m: float | None,
                          estimated_duration_sec: int | None):
    """
    Обновить / создать оценочную дистанцию и время для заказа.
    """
    sql = """
        INSERT INTO order_stats (order_id, estimated_distance_m, estimated_duration_sec)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            estimated_distance_m = VALUES(estimated_distance_m),
            estimated_duration_sec = VALUES(estimated_duration_sec)
    """
    with db_cursor() as cur:
        cur.execute(sql, (order_id, estimated_distance_m, estimated_duration_sec))


# ===== Маршруты =====

def get_active_route_for_courier(courier_id: int):
    """
    Активный маршрут курьера (status='on_way').
    """
    sql = """
        SELECT *
        FROM routes
        WHERE courier_id = %s
          AND status = 'on_way'
        ORDER BY id DESC
        LIMIT 1
    """
    with db_cursor() as cur:
        cur.execute(sql, (courier_id,))
        return cur.fetchone()


def create_route(courier_id: int,
                 total_distance_m: int,
                 total_duration_s: int,
                 geometry: str):
    """
    Создать маршрут (уже "on_way").
    geometry — строка с GeoJSON.
    """
    sql = """
        INSERT INTO routes (
            courier_id,
            status,
            geometry,
            distance_planned_m,
            duration_planned_sec,
            created_at,
            started_at
        )
        VALUES (%s, 'on_way', %s, %s, %s, NOW(), NOW())
    """
    with db_cursor() as cur:
        cur.execute(sql, (courier_id, geometry, total_distance_m, total_duration_s))
        return cur.lastrowid


def assign_orders_to_route(route_id: int, courier_id: int, order_ids: list[int]):
    """
    Привязать заказы к маршруту, проставить courier_id, status='on_way',
    delivery_started_at = NOW().
    """
    if not order_ids:
        return

    placeholders = ", ".join(["%s"] * len(order_ids))
    sql = f"""
        UPDATE orders
        SET route_id = %s,
            courier_id = %s,
            status = 'on_way',
            delivery_started_at = NOW()
        WHERE id IN ({placeholders})
    """
    params = [route_id, courier_id] + order_ids
    with db_cursor() as cur:
        cur.execute(sql, params)

def get_operators():
    """
    Список всех операторов.
    """
    sql = """
        SELECT id, full_name
        FROM users
        WHERE role = 'operator'
        ORDER BY full_name
    """
    with db_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def get_orders_for_route(route_id: int):
    """
    Заказы, входящие в маршрут.
    """
    sql = """
        SELECT
            o.*,
            s.estimated_distance_m  AS route_distance_m,
            s.estimated_duration_sec AS route_duration_sec
        FROM orders o
        LEFT JOIN order_stats s ON s.order_id = o.id
        WHERE o.route_id = %s
        ORDER BY o.id
    """
    with db_cursor() as cur:
        cur.execute(sql, (route_id,))
        return cur.fetchall()


def finish_route(route_id: int, distance_actual_m: int | None, comment: str | None):
    """
    Завершение маршрута:
    - routes: status -> 'finished', finished_at = NOW()
    - orders: status -> 'delivered', delivered_at = NOW()
    - order_stats: distance_actual_m, duration_actual_sec, comment
      для всех заказов маршрута.

    distance_actual_m:
      - если None/0 — берём плановую дистанцию маршрута (routes.distance_planned_m)
    duration_actual_sec:
      - всегда считается как TIMESTAMPDIFF(SECOND, routes.created_at, routes.finished_at)
    """
    with db_cursor() as cur:
        # 1. достанем плановую дистанцию маршрута
        cur.execute(
            "SELECT distance_planned_m FROM routes WHERE id = %s",
            (route_id,),
        )
        r = cur.fetchone()
        if not r:
            # такого маршрута нет — выходим
            return

        planned = r["distance_planned_m"] or 0
        if not distance_actual_m:
            distance_actual_m = planned

        # 2. помечаем маршрут завершённым и фиксируем время завершения
        cur.execute(
            """
            UPDATE routes
            SET status = 'finished',
                finished_at = NOW()
            WHERE id = %s
            """,
            (route_id,),
        )

        # 3. считаем фактическую продолжительность маршрута в секундах
        cur.execute(
            """
            SELECT TIMESTAMPDIFF(SECOND, created_at, finished_at) AS dur_sec
            FROM routes
            WHERE id = %s
            """,
            (route_id,),
        )
        r2 = cur.fetchone()
        duration_actual_sec = (r2["dur_sec"] or 0) if r2 and r2["dur_sec"] is not None else 0

        # 4. собираем заказы маршрута
        cur.execute("SELECT id FROM orders WHERE route_id = %s", (route_id,))
        order_rows = cur.fetchall()
        if not order_rows:
            return

        order_ids = [row["id"] for row in order_rows]
        placeholders = ", ".join(["%s"] * len(order_ids))

        # 4.1. обновляем статусы заказов
        cur.execute(
            f"""
            UPDATE orders
            SET status = 'delivered',
                delivered_at = NOW()
            WHERE id IN ({placeholders})
            """,
            order_ids,
        )

        # 4.2. пишем статистику по дистанции, длительности и комментарию
        params_stats = [distance_actual_m, duration_actual_sec, comment] + order_ids
        cur.execute(
            f"""
            UPDATE order_stats
            SET distance_actual_m   = %s,
                duration_actual_sec = %s,
                comment             = %s
            WHERE order_id IN ({placeholders})
            """,
            params_stats,
        )

def get_couriers():
    """
    Список всех пользователей-курьеров (для выпадающего списка у оператора).
    """
    sql = """
        SELECT id, full_name
        FROM users
        WHERE role = 'courier'
        ORDER BY full_name
    """
    with db_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()

def get_report_orders(courier_id: int | None,
                      date_from: str,
                      date_to: str):
    """
    Отчёт по маршрутам и заказам.
    Если courier_id is None — берём все курьеры.

    Возвращает строки по каждому заказу маршрута, с данными маршрута
    и фактической статистикой из order_stats.
    """
    base_sql = """
        SELECT
            r.id                    AS route_id,
            r.created_at,
            r.finished_at,
            r.distance_planned_m,
            r.duration_planned_sec,

            os.distance_actual_m    AS distance_actual_m,
            os.duration_actual_sec  AS duration_actual_sec,
            os.comment              AS comment,

            o.id                    AS order_id,
            o.external_id,
            o.client_address,
            o.delivered_at,

            u.full_name             AS courier_name
        FROM routes r
        JOIN orders      o  ON o.route_id = r.id
        LEFT JOIN order_stats os ON os.order_id = o.id
        JOIN users       u  ON u.id = r.courier_id
        WHERE r.status = 'finished'
          AND r.created_at BETWEEN %s AND %s
    """
    params: list = [date_from, date_to]

    if courier_id is not None:
        base_sql += " AND r.courier_id = %s"
        params.append(courier_id)

    base_sql += " ORDER BY r.created_at, r.id, o.id"

    with db_cursor() as cur:
        cur.execute(base_sql, params)
        return cur.fetchall()

