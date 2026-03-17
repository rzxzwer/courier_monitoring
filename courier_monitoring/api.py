# api.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

import db
import pymysql
import json

app = FastAPI(title="Courier Monitoring API")


class OrderItem(BaseModel):
    name: str
    qty: int
    price: float


class OrderCreate(BaseModel):
    external_id: Optional[str] = None
    items: List[OrderItem]
    restaurant_address: str
    restaurant_lat: Optional[float] = None
    restaurant_lon: Optional[float] = None
    client_address: str
    client_lat: Optional[float] = None
    client_lon: Optional[float] = None
    due_at: Optional[datetime] = None
    operator_id: int
    courier_id: Optional[int] = None


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Courier Monitoring API is running"}


@app.post("/orders")
def create_order(order: OrderCreate):
    """
    Приём заказа от внешней системы.
    INSERT в orders, триггеры сами создадут запись в order_stats
    и, при наличии курьеров на смене, проставят courier_id/stats_id.
    """
    conn = db.connect_db()
    try:
        with conn.cursor() as cur:
            items_json = json.dumps(
                [i.model_dump() for i in order.items],
                ensure_ascii=False,
            )

            sql = """
                INSERT INTO orders (
                    external_id,
                    items_json,
                    restaurant_address, restaurant_lat, restaurant_lon,
                    client_address,    client_lat,    client_lon,
                    created_at, due_at,
                    operator_id, courier_id,
                    status
                )
                VALUES (
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    NOW(), %s,
                    %s, %s,
                    'new'
                )
            """
            cur.execute(
                sql,
                (
                    order.external_id,
                    items_json,
                    order.restaurant_address,
                    order.restaurant_lat,
                    order.restaurant_lon,
                    order.client_address,
                    order.client_lat,
                    order.client_lon,
                    order.due_at,
                    order.operator_id,
                    order.courier_id,
                ),
            )
            new_id = cur.lastrowid

        # ВАЖНО: коммитим, чтобы INSERT и триггеры зафиксировались
        conn.commit()
        return {"status": "ok", "order_id": new_id}

    except pymysql.MySQLError as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/courier/{courier_id}/orders")
def get_courier_orders(courier_id: int):
    """
    Выгрузка заказов для курьера (для отчётности).
    """
    orders = db.get_orders_for_courier(courier_id)
    return {"courier_id": courier_id, "orders": orders}
