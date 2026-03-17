// ===== main.js =====

document.addEventListener("DOMContentLoaded", function () {
  console.log("main.js: DOMContentLoaded, L =", typeof L);

  // ===== Показать/Скрыть состав заказа =====
  const orderCards = document.querySelectorAll(".order-card");

  orderCards.forEach((card) => {
    const btnShow = card.querySelector(".btn-toggle-items");
    const itemsBlock = card.querySelector(".order-items");
    const btnHide = card.querySelector(".btn-hide-items");

    if (btnShow && itemsBlock && btnHide) {
      btnShow.addEventListener("click", () => {
        itemsBlock.style.display = "block";
        btnShow.style.display = "none";
      });

      btnHide.addEventListener("click", () => {
        itemsBlock.style.display = "none";
        btnShow.style.display = "inline-block";
      });
    }
  });

  // ===== Локальный маршрут (выбор заказов + карта) =====
  const selectedOrders = {};
  const routeBlock = document.getElementById("route-block");
  const routeList = document.getElementById("route-list");

  const btnStartRoute = document.getElementById("btn-start-route");

  let routeMap = null;
  let routeLayer = null;
  let routeMarkers = [];
  let currentRouteOrderIds = [];

  function clearRouteMap() {
    if (routeMap) {
      if (routeLayer) {
        routeMap.removeLayer(routeLayer);
        routeLayer = null;
      }
      if (routeMarkers && routeMarkers.length > 0) {
        routeMarkers.forEach((m) => {
          try {
            routeMap.removeLayer(m);
          } catch (e) {
            console.error("Ошибка удаления маркера:", e);
          }
        });
        routeMarkers = [];
      }
    }
  }

  function updateRouteMap(orderIds) {
    const mapContainer = document.getElementById("route-map");
    const summaryEl = document.getElementById("route-summary");
    if (!mapContainer || !summaryEl) return;

    if (!orderIds || orderIds.length === 0) {
      summaryEl.textContent = "";
      if (routeMap) {
        clearRouteMap();
        routeMap.remove();
        routeMap = null;
      }
      return;
    }

    console.log("updateRouteMap: orderIds =", orderIds);

    fetch("/route/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_ids: orderIds }),
    })
      .then((r) =>
        r.text().then((text) => {
          console.log("Ответ /route/preview raw:", text);
          try {
            return JSON.parse(text);
          } catch (e) {
            console.error("Ошибка парсинга JSON маршрута:", e, "Ответ:", text);
            throw e;
          }
        })
      )
      .then((data) => {
        console.log("Ответ /route/preview parsed:", data);

        if (!data.ok) {
          if (data.error === "different_restaurants") {
            summaryEl.textContent = "Выберите заказы из одного ресторана";
          } else if (data.error === "no_orders") {
            summaryEl.textContent = "";
          } else if (
            data.error === "no_restaurant_coords" ||
            data.error === "no_client_coords"
          ) {
            summaryEl.textContent = "Нет координат для построения маршрута";
          } else {
            summaryEl.textContent = "Не удалось построить маршрут";
          }

          if (routeMap) {
            clearRouteMap();
            routeMap.remove();
            routeMap = null;
          }
          return;
        }

        const distanceKm = (data.distance_m / 1000).toFixed(1);
        const durationMin = Math.round(data.duration_s / 60);
        summaryEl.textContent =
          "Общее расстояние: " +
          distanceKm +
          " км, время в пути: " +
          durationMin +
          " мин";

        const geojson = data.geojson;
        const waypoints = data.waypoints || [];
        console.log("GeoJSON маршрута:", geojson);
        console.log("Waypoints маршрута:", waypoints);

        if (typeof L === "undefined") {
          console.error("Leaflet (L) не загружен");
          summaryEl.textContent = "Карта недоступна (Leaflet не загружен)";
          return;
        }

        try {
          if (!routeMap) {
            routeMap = L.map("route-map");
            L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
              maxZoom: 19,
              attribution: "&copy; OpenStreetMap contributors",
            }).addTo(routeMap);
          }

          clearRouteMap();

          routeLayer = L.geoJSON(geojson).addTo(routeMap);

          routeMarkers = [];
          waypoints.forEach((wp, index) => {
            const lat = wp.lat;
            const lon = wp.lon;
            if (lat == null || lon == null) return;

            const marker = L.marker([lat, lon]).addTo(routeMap);
            const type = wp.type || "point";
            const label =
              wp.label ||
              (type === "restaurant"
                ? "Ресторан"
                : "Клиент " + (wp.order_id || index));
            marker.bindPopup(label);
            routeMarkers.push(marker);
          });

          const bounds = routeLayer.getBounds();
          routeMarkers.forEach((m) => bounds.extend(m.getLatLng()));
          routeMap.fitBounds(bounds);
        } catch (e) {
          console.error("Ошибка при отрисовке маршрута в Leaflet:", e);
          summaryEl.textContent = "Ошибка при отображении маршрута";
          if (routeMap) {
            clearRouteMap();
            routeMap.remove();
            routeMap = null;
          }
        }
      })
      .catch((err) => {
        console.error("Ошибка при запросе маршрута (fetch/catch):", err);
        if (summaryEl) {
          summaryEl.textContent = "Ошибка при запросе маршрута";
        }
        if (routeMap) {
          clearRouteMap();
          routeMap.remove();
          routeMap = null;
        }
      });
  }

  function renderRouteBlock() {
    const ids = Object.keys(selectedOrders);
    currentRouteOrderIds = ids.slice();

    if (!routeBlock || !routeList) {
      return;
    }

    if (ids.length === 0) {
      routeBlock.style.display = "none";
      routeList.innerHTML = "";

      const summaryEl = document.getElementById("route-summary");
      if (summaryEl) summaryEl.textContent = "";

      if (btnStartRoute) {
        btnStartRoute.disabled = true;
      }

      if (routeMap) {
        clearRouteMap();
        routeMap.remove();
        routeMap = null;
      }
      return;
    }

    routeBlock.style.display = "block";
    routeList.innerHTML = "";

    ids.forEach((orderId) => {
      const data = selectedOrders[orderId];
      const itemDiv = document.createElement("div");
      itemDiv.className = "route-item";

      const distKm = data.distanceM ? (data.distanceM / 1000).toFixed(1) : "n/a";
      const durMin = data.durationS ? Math.round(data.durationS / 60) : "n/a";

      itemDiv.innerHTML = `
        <div class="route-item-left">
          Заказ №${orderId}: из ${data.restaurant} до ${data.client}
        </div>
        <div class="route-item-right">
          ${distKm} км | ${durMin} мин
          <button type="button" class="btn-link btn-remove-from-route" data-order-id="${orderId}">
            Удалить
          </button>
        </div>
      `;

      routeList.appendChild(itemDiv);
    });

    const removeButtons = routeList.querySelectorAll(".btn-remove-from-route");
    removeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-order-id");
        if (id && selectedOrders[id]) {
          delete selectedOrders[id];
          const card = document.querySelector(
            `.order-card[data-order-id="${id}"]`
          );
          if (card) {
            const btnRoute = card.querySelector(".btn-route-toggle");
            if (btnRoute) {
              btnRoute.textContent = "Добавить в маршрут";
              btnRoute.classList.remove("in-route");
            }
          }
          renderRouteBlock();
        }
      });
    });

    if (btnStartRoute) {
      btnStartRoute.disabled = ids.length === 0;
    }

    updateRouteMap(ids);
  }

  // обработка кнопки "Добавить в маршрут"/"Убрать из маршрута"
  orderCards.forEach((card) => {
    const btnRoute = card.querySelector(".btn-route-toggle");
    if (!btnRoute) return;

    btnRoute.addEventListener("click", () => {
      const orderId = card.getAttribute("data-order-id");
      if (!orderId) return;

      const inRoute = btnRoute.classList.contains("in-route");

      if (inRoute) {
        delete selectedOrders[orderId];
        btnRoute.textContent = "Добавить в маршрут";
        btnRoute.classList.remove("in-route");
      } else {
        const restaurant = card.getAttribute("data-restaurant") || "";
        const client = card.getAttribute("data-client") || "";
        const distanceM = parseInt(
          card.getAttribute("data-distance-m") || "0",
          10
        );
        const durationS = parseInt(
          card.getAttribute("data-duration-s") || "0",
          10
        );

        selectedOrders[orderId] = {
          restaurant,
          client,
          distanceM,
          durationS,
        };

        btnRoute.textContent = "Убрать из маршрута";
        btnRoute.classList.add("in-route");
      }

      renderRouteBlock();
    });
  });

  // ===== "В ПУТЬ" — БЕЗ МОДАЛОК, СРАЗУ СТАРТ МАРШРУТА =====
  if (btnStartRoute) {
    btnStartRoute.addEventListener("click", () => {
      if (!currentRouteOrderIds || currentRouteOrderIds.length === 0) {
        alert("Добавьте в маршрут хотя бы один заказ");
        return;
      }

      const formData = new FormData();
      formData.append("order_ids", JSON.stringify(currentRouteOrderIds));

      fetch("/route/start", {
        method: "POST",
        body: formData,
      })
        .then((r) => r.json())
        .then((data) => {
          console.log("Ответ /route/start:", data);
          if (!data.ok) {
            alert("Не удалось начать маршрут: " + (data.error || "ошибка"));
            return;
          }

          const redirectUrl = data.redirect_url || "/on_route";
          window.location.href = redirectUrl;
        })
        .catch((err) => {
          console.error("Ошибка /route/start:", err);
          alert("Ошибка при старте маршрута");
        });
    });
  }

  // ===== Асинхронное получение координат заказов =====
  const coordSpans = document.querySelectorAll(
    ".coords-status[data-needs-geo='1']"
  );

  coordSpans.forEach((span) => {
    const orderId = span.getAttribute("data-order-id");
    if (!orderId) return;

    console.log("ensure_coords for order:", orderId);

    fetch(`/api/orders/${orderId}/ensure_coords`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({}),
    })
      .then((r) => r.json())
      .then((data) => {
        console.log("Ответ ensure_coords:", orderId, data);
        if (data.ok) {
          span.textContent = "Коорд-ты получены";
          span.classList.remove("coords-pending");
          span.classList.add("coords-ok");
          span.removeAttribute("data-needs-geo");
        } else {
          span.textContent = "Не удалось получить коорд-ты";
          span.classList.remove("coords-pending");
          span.classList.add("coords-error");
        }
      })
      .catch((err) => {
        console.error("Ошибка ensure_coords для order", orderId, err);
        span.textContent = "Не удалось получить коорд-ты";
        span.classList.remove("coords-pending");
        span.classList.add("coords-error");
      });
  });
});
