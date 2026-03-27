import requests
import time
import json
 
# ============ НАСТРОЙКИ ============
ACCESS_TOKEN = "ВВЕДИТЕ_СЮДА_ТОКЕН_ACCESS_TOKEN_ПРЯМО_В_КАВЫЧКИ"
GROUP_ID     = ВВЕДИТЕ_СЮДА_ID_ГРУППЫ_БЕЗ_МИНУСА
OWNER_ID     = -GROUP_ID
API_VERSION  = "5.199"
 
# ============ ID УСЛУГ ДЛЯ УДАЛЕНИЯ ============
# Вариант А — вписать руками:
# SERVICE_IDS = [12817868,12817869,12817870]
#
# Вариант Б — взять автоматически из бэкапа скрипта #1:
# (раскомментируй блок ниже и закомментируй SERVICE_IDS = [...])
#
# import json
# with open("services_backup.json", encoding="utf-8") as f:
#     backup = json.load(f)
# SERVICE_IDS = [item["id"] for item in backup]
 
SERVICE_IDS = [ВВЕДИТЕ_СЮДА_СПИСОК_ID_УСЛУГ_КОТОРЫЕ_ХОТИТЕ_УДАЛИТЬ]
 
# True  — только покажет что будет удалено, ничего не трогает
# False — реально удаляет (необратимо!)
DRY_RUN = False
 
 
# ===================================================
def vk_api(method, params):
    params["access_token"] = ACCESS_TOKEN
    params["v"] = API_VERSION
    url  = f"https://api.vk.com/method/{method}"
    resp = requests.get(url, params=params).json()
    if "error" in resp:
        print(f"  ❌ VK API [{method}]: {json.dumps(resp['error'], ensure_ascii=False)}")
        return None
    return resp.get("response")
 
 
def get_service_titles(ids):
    """Получаем названия для удобного лога."""
    titles   = {}
    item_ids = [f"{OWNER_ID}_{sid}" for sid in ids]
    for i in range(0, len(item_ids), 25):
        batch  = item_ids[i:i+25]
        result = vk_api("market.getById", {
            "item_ids": ",".join(batch),
            "extended": 0,
        })
        if result:
            raw = result.get("items", []) if isinstance(result, dict) else result
            for item in raw:
                titles[item["id"]] = item.get("title", "?")
        time.sleep(0.3)
    return titles
 
 
def main():
    print("=" * 55)
    print("VK: удаление старых услуг")
    print(f"DRY_RUN = {DRY_RUN}  "
          f"{'(только список, ничего не удаляем)' if DRY_RUN else '(РЕАЛЬНОЕ УДАЛЕНИЕ!)'}")
    print("=" * 55)
 
    if not SERVICE_IDS:
        print("\n❌ SERVICE_IDS пустой!")
        print("   Заполни руками или раскомментируй блок 'Вариант Б' для загрузки из бэкапа.")
        return
 
    print(f"\nПолучаю названия для {len(SERVICE_IDS)} услуг...")
    titles = get_service_titles(SERVICE_IDS)
 
    print(f"\nСписок на удаление ({len(SERVICE_IDS)} шт.):")
    for sid in SERVICE_IDS:
        print(f"  • [{sid}] {titles.get(sid, '(не найдено)')}")
 
    if DRY_RUN:
        print("\n⚠️  DRY_RUN=True — выходим. Поставь False чтобы удалить.")
        return
 
    confirm = input(f"\nУдалить {len(SERVICE_IDS)} услуг? Это необратимо. Введи YES: ")
    if confirm.strip() != "YES":
        print("Отменено.")
        return
 
    print(f"\nУдаляю...\n")
    ok   = 0
    fail = 0
 
    for i, sid in enumerate(SERVICE_IDS, 1):
        title = titles.get(sid, "?")
        print(f"[{i}/{len(SERVICE_IDS)}] [{sid}] {title}", end="  ")
 
        result = vk_api("market.delete", {
            "owner_id": OWNER_ID,
            "item_id":  sid,
        })
 
        if result == 1:
            print("✅ Удалена")
            ok += 1
        else:
            print("❌ Ошибка")
            fail += 1
 
        time.sleep(0.4)
 
    print(f"\n{'='*55}")
    print(f"ИТОГО: ✅ {ok} удалено  ❌ {fail} ошибок")
    print(f"{'='*55}")
 
 
if __name__ == "__main__":
    main()