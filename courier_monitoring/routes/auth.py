# routes/auth.py
import logging

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
)

import db
from .common import login_required

logger = logging.getLogger(__name__)


def register_auth_routes(app):
    """
    Роуты авторизации, корневой маршрут и управление сменой.
    """

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
                logger.info(
                    "root: курьер %s имеет активный маршрут, редирект на /on_route",
                    user_id,
                )
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
