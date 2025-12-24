# core/services/bank_search.py
"""
Справочник банков России с БИК
Источники:
- https://www.bankodrom.ru/banki-rossii/spravochnik-bik-bankov-rf/
- https://bik-info.ru/base.html (API для получения корреспондентского счета)
"""
import requests
from typing import Optional, Dict

# Список популярных банков России с БИК
# Источник: https://www.bankodrom.ru/banki-rossii/spravochnik-bik-bankov-rf/
BANKS_DATA = [
    {"name": "Альфа-банк", "bik": "044525593"},
    {"name": "Сбербанк России", "bik": "044525225"},
    {"name": "ПАО Сбербанк", "bik": "044525225"},
    {"name": "ВТБ", "bik": "044525187"},
    {"name": "ПАО ВТБ", "bik": "044525187"},
    {"name": "ВТБ 24", "bik": "044525187"},
    {"name": "Газпромбанк", "bik": "044525823"},
    {"name": "ПАО Газпромбанк", "bik": "044525823"},
    {"name": "Россельхозбанк", "bik": "044525111"},
    {"name": "АО Россельхозбанк", "bik": "044525111"},
    {"name": "Райффайзенбанк", "bik": "044525700"},
    {"name": "АО Райффайзенбанк", "bik": "044525700"},
    {"name": "ЮниКредит Банк", "bik": "044525787"},
    {"name": "АО ЮниКредит Банк", "bik": "044525787"},
    {"name": "Росбанк", "bik": "044525256"},
    {"name": "АО Росбанк", "bik": "044525256"},
    {"name": "Открытие", "bik": "044525192"},
    {"name": "ПАО Банк ФК Открытие", "bik": "044525192"},
    {"name": "Банк ФК Открытие", "bik": "044525192"},
    {"name": "Промсвязьбанк", "bik": "044525220"},
    {"name": "ПАО Промсвязьбанк", "bik": "044525220"},
    {"name": "МКБ", "bik": "044525201"},
    {"name": "АО МКБ", "bik": "044525201"},
    {"name": "Ак Барс", "bik": "049205805"},
    {"name": "АО Ак Барс Банк", "bik": "049205805"},
    {"name": "Тинькофф Банк", "bik": "044525974"},
    {"name": "АО Тинькофф Банк", "bik": "044525974"},
    {"name": "Совкомбанк", "bik": "044525411"},
    {"name": "ПАО Совкомбанк", "bik": "044525411"},
    {"name": "Хоум Кредит Банк", "bik": "044525911"},
    {"name": "АО Хоум Кредит энд Финанс Банк", "bik": "044525911"},
    {"name": "Ренессанс Кредит", "bik": "044525174"},
    {"name": "АО Ренессанс Кредит", "bik": "044525174"},
    {"name": "ОТП Банк", "bik": "044525121"},
    {"name": "АО ОТП Банк", "bik": "044525121"},
    {"name": "Русский Стандарт", "bik": "044525416"},
    {"name": "АО Русский Стандарт", "bik": "044525416"},
    {"name": "МТС Банк", "bik": "044525503"},
    {"name": "ПАО МТС Банк", "bik": "044525503"},
    {"name": "Почта Банк", "bik": "044525106"},
    {"name": "ПАО Почта Банк", "bik": "044525106"},
    {"name": "Абсолют Банк", "bik": "044525976"},
    {"name": "ПАО Абсолют Банк", "bik": "044525976"},
    {"name": "Авангард", "bik": "044525201"},
    {"name": "АО Авангард", "bik": "044525201"},
    {"name": "Америкэн Экспресс Банк", "bik": "044525717"},
    {"name": "АО Америкэн Экспресс Банк", "bik": "044525717"},
    {"name": "Банк Санкт-Петербург", "bik": "044030790"},
    {"name": "АО Банк Санкт-Петербург", "bik": "044030790"},
    {"name": "Банк Возрождение", "bik": "044525181"},
    {"name": "ПАО Банк Возрождение", "bik": "044525181"},
    {"name": "Банк Зенит", "bik": "044525093"},
    {"name": "ПАО Банк Зенит", "bik": "044525093"},
    {"name": "Банк Интеза", "bik": "044525700"},
    {"name": "АО Банк Интеза", "bik": "044525700"},
    {"name": "Банк ЦентрИнвест", "bik": "046015207"},
    {"name": "АО Банк ЦентрИнвест", "bik": "046015207"},
    {"name": "Яндекс.Банк", "bik": "044525974"},
    {"name": "АО Яндекс.Банк", "bik": "044525974"},
]


def search_banks(query: str, limit: int = 20) -> list:
    """
    Поиск банков по первым символам названия
    
    Args:
        query: Поисковый запрос (минимум 3 символа)
        limit: Максимальное количество результатов
    
    Returns:
        Список словарей с ключами 'name', 'bik' и 'ks' (корреспондентский счет)
    """
    if len(query) < 3:
        return []
    
    query_lower = query.lower()
    results = []
    
    for bank in BANKS_DATA:
        if query_lower in bank["name"].lower():
            result = bank.copy()
            # Пытаемся получить корреспондентский счет из API для каждого найденного банка
            try:
                api_data = fetch_bank_info_from_api(bank["bik"])
                if api_data and api_data.get("ks"):
                    result["ks"] = api_data["ks"]
            except Exception:
                pass
            results.append(result)
            if len(results) >= limit:
                break
    
    return results


def get_bank_by_bik(bik: str) -> Optional[Dict]:
    """
    Получить банк по БИК с использованием API bik-info.ru
    
    Args:
        bik: БИК банка (9 цифр)
    
    Returns:
        Словарь с ключами 'name', 'bik', 'ks' (корреспондентский счет) или None
    """
    # Сначала проверяем локальный справочник
    for bank in BANKS_DATA:
        if bank["bik"] == bik:
            result = bank.copy()
            # Пытаемся получить корреспондентский счет из API
            try:
                api_data = fetch_bank_info_from_api(bik)
                if api_data and api_data.get("ks"):
                    result["ks"] = api_data["ks"]
                    if api_data.get("name"):
                        result["name"] = api_data["name"]
            except Exception:
                pass
            return result
    
    # Если не найден в локальном справочнике, пробуем получить из API
    try:
        api_data = fetch_bank_info_from_api(bik)
        if api_data:
            return {
                "name": api_data.get("name", ""),
                "bik": bik,
                "ks": api_data.get("ks", "")
            }
    except Exception:
        pass
    
    return None


def fetch_bank_info_from_api(bik: str) -> Optional[Dict]:
    """
    Получить информацию о банке из API bik-info.ru
    
    Args:
        bik: БИК банка (9 цифр)
    
    Returns:
        Словарь с данными банка или None
    """
    try:
        url = f"https://bik-info.ru/api.html?type=json&bik={bik}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # API возвращает данные в формате, где ключ - это БИК
        if bik in data:
            bank_data = data[bik]
            return {
                "name": bank_data.get("namemini") or bank_data.get("name", ""),
                "bik": bik,
                "ks": bank_data.get("ks", ""),  # Корреспондентский счет
                "address": bank_data.get("address", ""),
                "city": bank_data.get("city", ""),
            }
        elif isinstance(data, dict) and "bik" in data:
            # Альтернативный формат ответа (данные напрямую в корне)
            return {
                "name": data.get("namemini") or data.get("name", ""),
                "bik": bik,
                "ks": data.get("ks", ""),
                "address": data.get("address", ""),
                "city": data.get("city", ""),
            }
    except Exception as e:
        # В случае ошибки просто возвращаем None
        # Используем logging вместо print для продакшена
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Ошибка получения данных из API bik-info.ru для БИК {bik}: {e}")
        return None
    
    return None

