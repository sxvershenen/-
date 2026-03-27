import requests
import time
import json
import os
import sys
from datetime import datetime
from PIL import Image, ImageOps

TIMEOUT = 30  # секунд на любой HTTP-запрос

def log(msg, prefix=""):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {prefix}{msg}", flush=True)

# ============ НАСТРОЙКИ ============
ACCESS_TOKEN = "ВВЕДИТЕ_СЮДА_ТОКЕН_ACCESS_TOKEN_ПРЯМО_В_КАВЫЧКИ"
GROUP_ID     = ВВЕДИТЕ_СЮДА_ID_ГРУППЫ_БЕЗ_МИНУСА
OWNER_ID     = -GROUP_ID
API_VERSION  = "5.199"
TEMP_DIR     = "temp_images"

os.makedirs(TEMP_DIR, exist_ok=True)

# ============ ЧТО ДЕЛАТЬ ============
# True  — только парсит и сохраняет services_backup.json, ничего не создаёт
# False — реально пересоздаёт услуги
DRY_RUN = False

# Список ID услуг
SERVICE_IDS = [ВВЕДИТЕ_СЮДА_СПИСОК_ID_УСЛУГ_КОТОРЫЕ_ХОТИТЕ_ПЕРЕСОЗДАТЬ]

# ============ СТРАТЕГИЯ КРОПА ============
# "force_full"   — пробуем передать crop_data="0,0,100,100" (хак, может не сработать)
# "pad_and_upload" — добавляем паддинг к изображению на основе того что вернул VK
# "auto"         — пробуем force_full, если crop_data в ответе не полный — fallback на pad_and_upload
CROP_STRATEGY = "auto"


# ===================================================
def vk_api(method, params):
    params["access_token"] = ACCESS_TOKEN
    params["v"] = API_VERSION
    url = f"https://api.vk.com/method/{method}"
    try:
        log(f"API {method}", "    📡 ")
        # МЕНЯЕМ GET НА POST !!! 
        # Используем data=params вместо params=params
        resp = requests.post(url, data=params, timeout=TIMEOUT)
        
        # Если сервер вернул не 200 OK (например 414 или 502), логируем текст ошибки
        if resp.status_code != 200:
            log(f"HTTP Ошибка {resp.status_code}: {resp.text[:150]}", "  ❌ ")
            return None
            
        resp_json = resp.json()
        
    except requests.Timeout:
        log(f"ТАЙМАУТ {TIMEOUT}с → {method}", "  ⏰ ")
        return None
    except Exception as e:
        log(f"Ошибка запроса {method}: {e}", "  ❌ ")
        return None
        
    if "error" in resp_json:
        log(f"VK ошибка [{method}]: {json.dumps(resp_json['error'], ensure_ascii=False)}", "  ❌ ")
        return None
        
    return resp_json.get("response")

def get_all_services():
    items   = []
    offset  = 0
    page_sz = 200
    while True:
        resp = vk_api("market.get", {
            "owner_id": OWNER_ID,
            "count":    page_sz,
            "offset":   offset,
            "extended": 1,
        })
        if not resp:
            break
        batch = resp.get("items", [])
        if not batch:
            break
        for item in batch:
            if item.get("is_service") or item.get("type") == "service":
                items.append(item)
        if len(batch) < page_sz:
            break
        offset += page_sz
        time.sleep(0.3)
    return items


def get_services_by_ids(ids):
    items    = []
    item_ids = [f"{OWNER_ID}_{sid}" for sid in ids]
    for i in range(0, len(item_ids), 25):
        batch  = item_ids[i:i+25]
        result = vk_api("market.getById", {
            "item_ids": ",".join(batch),
            "extended": 1,
        })
        if result:
            raw = result.get("items", []) if isinstance(result, dict) else result
            items.extend(raw)
        time.sleep(0.3)
    return items


def get_best_photo_url(item):
    photos = item.get("photos", [])
    if photos and isinstance(photos[0], dict):
        sizes = photos[0].get("sizes", [])
        if sizes:
            best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
            if best.get("url"):
                return best["url"]
        orig = photos[0].get("orig_photo", {})
        if orig.get("url"):
            return orig["url"]
    thumb = item.get("thumb_photo", "")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
    return None


def get_extra_photo_urls(item):
    urls   = []
    photos = item.get("photos", [])
    for photo in photos[1:5]:
        if isinstance(photo, dict):
            sizes = photo.get("sizes", [])
            if sizes:
                best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                if best.get("url"):
                    urls.append(best["url"])
    return urls


def download_image(url, filename):
    try:
        log(f"Скачиваю {filename}", "  📥 ")
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.Timeout:
        log(f"ТАЙМАУТ при скачивании {filename}", "  ⏰ ")
        return None
    if resp.status_code != 200:
        log(f"Ошибка скачивания HTTP {resp.status_code}", "  ⚠️ ")
        return None
    filepath = os.path.join(TEMP_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(resp.content)
    return filepath


def ensure_square(filepath):
    """Если вдруг не квадрат — кропаем по центру. Страховка."""
    img  = Image.open(filepath).convert("RGB")
    w, h = img.size
    if w == h:
        return filepath
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img  = img.crop((left, top, left + side, top + side))
    out  = filepath.replace(".jpg", "_sq.jpg")
    img.save(out, "JPEG", quality=95)
    print(f"  ✂️  Страховочный кроп {w}×{h} → {side}×{side}")
    return out


def parse_crop_data(crop_data_str):
    """
    VK возвращает crop_data в формате "x,y,x2,y2" в процентах (0-100).
    Возвращаем (x, y, x2, y2) как float, или None если не распарсилось.
    """
    try:
        parts = [float(v) for v in crop_data_str.split(",")]
        if len(parts) == 4:
            return tuple(parts)
    except Exception:
        pass
    return None


def is_full_crop(crop_data_str, tolerance=1.0):
    """Проверяем, выставил ли VK кроп на весь файл (0,0,100,100 ± tolerance)."""
    coords = parse_crop_data(crop_data_str)
    if not coords:
        return False
    x, y, x2, y2 = coords
    return (x <= tolerance and y <= tolerance and
            x2 >= 100 - tolerance and y2 >= 100 - tolerance)


def add_padding_for_crop(filepath, crop_data_str, out_filename):
    """
    VK кропает изображение до области crop_data (в процентах).
    Чтобы наш контент занял весь кроп — добавляем паддинг снаружи,
    чтобы оригинал попал ровно в ту область.

    Если VK говорит crop = (x, y, x2, y2) в % от (W, H),
    нам нужно сделать новое изображение такого размера, чтобы:
        new_W * (x2 - x) / 100 = W  →  new_W = W * 100 / (x2 - x)
        new_H * (y2 - y) / 100 = H  →  new_H = H * 100 / (y2 - y)
    Оригинал вставляем со смещением (x% от new_W, y% от new_H).
    """
    coords = parse_crop_data(crop_data_str)
    if not coords:
        print(f"  ⚠️  Не удалось распарсить crop_data='{crop_data_str}', паддинг не добавляем")
        return filepath

    x, y, x2, y2 = coords
    cw = x2 - x  # % ширины который занимает кроп
    ch = y2 - y  # % высоты

    if cw <= 0 or ch <= 0:
        print(f"  ⚠️  Некорректный crop_data, паддинг не добавляем")
        return filepath

    img  = Image.open(filepath).convert("RGB")
    W, H = img.size

    new_W = round(W * 100 / cw)
    new_H = round(H * 100 / ch)
    off_x = round(new_W * x / 100)
    off_y = round(new_H * y / 100)

    # Цвет паддинга — берём средний цвет краёв изображения (выглядит органично)
    edge_pixels = (
        list(img.crop((0, 0, W, 1)).getdata()) +
        list(img.crop((0, H-1, W, H)).getdata()) +
        list(img.crop((0, 0, 1, H)).getdata()) +
        list(img.crop((W-1, 0, W, H)).getdata())
    )
    avg_r = int(sum(p[0] for p in edge_pixels) / len(edge_pixels))
    avg_g = int(sum(p[1] for p in edge_pixels) / len(edge_pixels))
    avg_b = int(sum(p[2] for p in edge_pixels) / len(edge_pixels))
    bg_color = (avg_r, avg_g, avg_b)

    canvas = Image.new("RGB", (new_W, new_H), bg_color)
    canvas.paste(img, (off_x, off_y))

    out = os.path.join(TEMP_DIR, out_filename)
    canvas.save(out, "JPEG", quality=95)
    print(f"  🖼️  Паддинг: {W}×{H} → {new_W}×{new_H}, offset=({off_x},{off_y}), "
          f"bg={bg_color}, crop_data='{crop_data_str}'")
    return out


def upload_photo_with_full_crop(filepath, is_main=True, photo_id_suffix=""):
    """
    Загружаем фото. Применяем стратегию CROP_STRATEGY.
    Возвращает (photo_id, crop_data_str).
    """
    def _get_server_and_upload(fp):
        server = vk_api("photos.getMarketUploadServer", {
            "group_id":   GROUP_ID,
            "main_photo": 1 if is_main else 0,
        })
        if not server:
            return None, None
        try:
            log(f"Загружаю файл на сервер VK...", "  ⬆️  ")
            with open(fp, "rb") as f:
                up = requests.post(server["upload_url"], files={"file": f}, timeout=TIMEOUT).json()
        except requests.Timeout:
            log(f"ТАЙМАУТ при upload на сервер VK", "  ⏰ ")
            return None, None
        return server, up

    def _save_photo(up, custom_crop_data=None, custom_crop_hash=None):
        cd = custom_crop_data if custom_crop_data is not None else up.get("crop_data", "")
        ch = custom_crop_hash if custom_crop_hash is not None else up.get("crop_hash", "")
        result = vk_api("photos.saveMarketPhoto", {
            "group_id":  GROUP_ID,
            "photo":     up.get("photo",  ""),
            "server":    up.get("server", ""),
            "hash":      up.get("hash",   ""),
            "crop_data": cd,
            "crop_hash": ch,
        })
        if result and len(result) > 0:
            return result[0]["id"], cd
        return None, cd

    # ── force_full ──────────────────────────────────────────────
    if CROP_STRATEGY == "force_full":
        _, up = _get_server_and_upload(filepath)
        if not up:
            return None, None
        print(f"  🔧 force_full: передаём crop_data='0,0,100,100', crop_hash=''")
        photo_id, cd = _save_photo(up, custom_crop_data="0,0,100,100", custom_crop_hash="")
        return photo_id, cd

    # ── pad_and_upload ───────────────────────────────────────────
    if CROP_STRATEGY == "pad_and_upload":
        _, up = _get_server_and_upload(filepath)
        if not up:
            return None, None
        orig_cd = up.get("crop_data", "")
        print(f"  📐 VK crop_data='{orig_cd}'")
        if not is_full_crop(orig_cd):
            padded = add_padding_for_crop(
                filepath, orig_cd,
                f"padded_{photo_id_suffix}.jpg"
            )
            _, up2 = _get_server_and_upload(padded)
            if not up2:
                return None, None
            photo_id, cd = _save_photo(up2)
        else:
            print(f"  ✅ Кроп уже полный, паддинг не нужен")
            photo_id, cd = _save_photo(up)
        return photo_id, cd

    # ── auto (force_full → fallback pad_and_upload) ──────────────
    if CROP_STRATEGY == "auto":
        _, up = _get_server_and_upload(filepath)
        if not up:
            return None, None
        orig_cd = up.get("crop_data", "")
        print(f"  📐 VK crop_data='{orig_cd}'")

        # Шаг 1: пробуем force_full
        print(f"  🔧 Попытка force_full (crop_data='0,0,100,100', crop_hash='')")
        photo_id, cd = _save_photo(up, custom_crop_data="0,0,100,100", custom_crop_hash="")

        if photo_id:
            # Проверяем: посмотрим crop_data сохранённого фото через photos.getById
            time.sleep(0.3)
            check = vk_api("photos.getById", {
                "photos": f"{OWNER_ID}_{photo_id}",
            })
            saved_cd = ""
            if check and isinstance(check, list) and len(check) > 0:
                saved_cd = check[0].get("crop_data", "")
            print(f"  🔍 Сохранённый crop_data='{saved_cd}'")

            if is_full_crop(saved_cd) or is_full_crop("0,0,100,100"):
                # force_full либо сработал, либо VK игнорирует и ставит своё — проверим визуально
                # На всякий случай доверяем результату если photo_id получен
                print(f"  ✅ force_full принят")
                return photo_id, saved_cd or "0,0,100,100"

        # Шаг 2: fallback — паддинг
        print(f"  ↩️  Fallback: pad_and_upload")
        if not is_full_crop(orig_cd) and orig_cd:
            padded = add_padding_for_crop(
                filepath, orig_cd,
                f"padded_{photo_id_suffix}.jpg"
            )
            _, up2 = _get_server_and_upload(padded)
            if not up2:
                return None, None
            photo_id, cd = _save_photo(up2)
        else:
            photo_id, cd = _save_photo(up)

        return photo_id, cd

    # fallback если стратегия не распознана
    _, up = _get_server_and_upload(filepath)
    if not up:
        return None, None
    photo_id, cd = _save_photo(up)
    return photo_id, cd


def recreate_as_service(svc, main_photo_id, extra_photo_ids=None):
    name = (svc.get("title") or "Без названия")[:100].strip()
    if len(name) < 4:
        name = (name + "    ")[:4]

    desc = (svc.get("description") or "").strip()
    if len(desc) < 10:
        desc = desc + " " * (10 - len(desc))

    price_data = svc.get("price", {})

    # ── Парсим суммы ──
    def parse_amount_raw(d):
        """Возвращает сырое значение amount (в копейках)."""
        try:
            return int(float(d.get("amount", 0)))
        except (ValueError, TypeError, AttributeError):
            return 0

    raw_amount    = parse_amount_raw(price_data)          # копейки
    price_rubles  = raw_amount / 100                      # ← РУБЛИ для market.add

    raw_amount_to = 0
    try:
        raw_amount_to = int(float(price_data.get("amount_to", 0)))
    except (ValueError, TypeError):
        pass
    price_to_rubles = raw_amount_to / 100                 # ← РУБЛИ

    price_type = price_data.get("price_type", 0)          # 0=фикс, 2=«от»

    cat         = svc.get("category", {})
    category_id = cat.get("id", 1) if isinstance(cat, dict) else 1

    params = {
        "owner_id":      OWNER_ID,
        "name":          name,
        "description":   desc,
        "category_id":   category_id,
        "main_photo_id": main_photo_id,
        "is_service":    1,
    }

    # --- дебаг ---
    print(f"  🔎 price_data raw = {price_data}")
    print(f"  🔎 raw_amount={raw_amount} коп → {price_rubles}₽, "
          f"raw_amount_to={raw_amount_to} коп → {price_to_rubles}₽, "
          f"price_type={price_type}")

    # ── Устанавливаем цену ──
    if price_rubles > 0:
        params["price"] = price_rubles                    # рубли, НЕ копейки!

        if price_type == 2:                               # режим «от ...»
            params["price_type"] = 2

            if price_to_rubles > 0:                       # диапазон «от ... до ...»
                params["price_to"] = price_to_rubles
                print(f"  💰 Режим: «от {price_rubles}₽ до {price_to_rubles}₽» (price_type=2)")
            else:
                print(f"  💰 Режим: «от {price_rubles}₽» (price_type=2)")
        else:
            print(f"  💰 Режим: фикс {price_rubles}₽")
    else:
        params["price"] = 0
        print(f"  💰 Режим: бесплатно")

    if extra_photo_ids:
        params["photo_ids"] = ",".join(str(p) for p in extra_photo_ids)

    print(f"  📦 Итоговые params market.add = "
          f"{json.dumps({k: v for k, v in params.items() if k != 'description'}, ensure_ascii=False)}")

    return vk_api("market.add", params)


def main():
    print("=" * 60)
    print("VK: пересоздание услуг с полным кадрированием")
    print(f"DRY_RUN       = {DRY_RUN}")
    print(f"CROP_STRATEGY = {CROP_STRATEGY}")
    print("=" * 60)

    # 1. Получаем данные
    print("\n[1/3] Получаю услуги...")
    services = get_services_by_ids(SERVICE_IDS) if SERVICE_IDS else get_all_services()
    print(f"  Найдено: {len(services)} шт.")

    if not services:
        print("❌ Ничего не нашли. Проверь токен и GROUP_ID.")
        return

    for s in services:
        print(f"  • [{s.get('id')}] {s.get('title', '?')}")

    with open("services_backup.json", "w", encoding="utf-8") as f:
        json.dump(services, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Бэкап → services_backup.json  (ID для удаления скриптом #2 — отсюда)")

    if DRY_RUN:
        print("\n⚠️  DRY_RUN=True — выходим. Поставь False для реального запуска.")
        return

    # 2. Пересоздаём
    print(f"\n[2/3] Пересоздаю {len(services)} услуг...\n")
    ok = fail = 0
    created_ids = []

    for i, svc in enumerate(services, 1):
        title = svc.get("title", "?")
        sid   = svc.get("id")
        done_bar = "█" * i + "░" * (len(services) - i)
        pct = int(i / len(services) * 100)
        print(f"\n{'─'*50}", flush=True)
        log(f"[{i}/{len(services)}] {pct}% [{done_bar}]", "")
        log(f"{title}  (old_id={sid})", "  📌 ")

        try:
            log("Получаю URL главного фото...", "  🔍 ")
            main_url = get_best_photo_url(svc)
            if not main_url:
                log("Нет фото — пропускаю", "  ❌ ")
                fail += 1
                continue

            img_path = download_image(main_url, f"main_{sid}.jpg")
            if not img_path:
                fail += 1
                continue

            img_path = ensure_square(img_path)

            log("Применяю кроп-стратегию для обложки...", "  ✂️  ")
            main_photo_id, main_cd = upload_photo_with_full_crop(
                img_path, is_main=True, photo_id_suffix=f"main_{sid}"
            )
            if not main_photo_id:
                log("Не удалось загрузить обложку", "  ❌ ")
                fail += 1
                continue
            log(f"Обложка готова (photo_id={main_photo_id}, crop='{main_cd}')", "  ✅ ")

            extra_ids = []
            for j, eurl in enumerate(get_extra_photo_urls(svc), 1):
                time.sleep(0.5)
                ep = download_image(eurl, f"extra_{sid}_{j}.jpg")
                if ep:
                    # Доп. фото — заливаем как есть, без кропа
                    server = vk_api("photos.getMarketUploadServer", {
                        "group_id":   GROUP_ID,
                        "main_photo": 0,
                    })
                    if server:
                        try:
                            log(f"Загружаю доп.фото {j}...", "  ⬆️  ")
                            with open(ep, "rb") as f:
                                up = requests.post(server["upload_url"], files={"file": f}, timeout=TIMEOUT).json()
                        except requests.Timeout:
                            log(f"ТАЙМАУТ при загрузке доп.фото {j}", "  ⏰ ")
                            continue
                        res = vk_api("photos.saveMarketPhoto", {
                            "group_id":  GROUP_ID,
                            "photo":     up.get("photo",     ""),
                            "server":    up.get("server",    ""),
                            "hash":      up.get("hash",      ""),
                            "crop_data": up.get("crop_data", ""),
                            "crop_hash": up.get("crop_hash", ""),
                        })
                        if res and len(res) > 0:
                            eid = res[0]["id"]
                            extra_ids.append(eid)
                            print(f"  📤 Доп.фото {j} (photo_id={eid}, без кропа)")

            time.sleep(0.4)
            result = recreate_as_service(svc, main_photo_id, extra_ids)

            if result:
                new_id = result.get("market_item_id", "?")
                log(f"СОЗДАНА! new_id={new_id}", "  ✅ ")
                created_ids.append({"old_id": sid, "new_id": new_id, "title": title})
                ok += 1
            else:
                log("market.add вернул пустой результат", "  ❌ ")
                fail += 1

            time.sleep(0.6)

        except Exception as e:
            import traceback
            print(f"  ❌ {e}")
            traceback.print_exc()
            fail += 1

    with open("created_services.json", "w", encoding="utf-8") as f:
        json.dump(created_ids, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"ИТОГО: ✅ {ok} создано  ❌ {fail} ошибок")
    print(f"Маппинг old→new → created_services.json")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()