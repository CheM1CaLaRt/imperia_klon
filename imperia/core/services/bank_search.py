# core/services/bank_search.py
"""
Справочник банков России с БИК
Источники:
- https://bik-info.ru/base/base.xml (полная база данных банков)
- https://bik-info.ru/base.html (API для получения корреспондентского счета)
"""
import os
import xml.etree.ElementTree as ET
import html
import requests
from typing import Optional, Dict, List
from django.conf import settings

# Путь к XML файлу с базой банков
XML_FILE_PATH = os.path.join(os.path.dirname(__file__), "banks_base.xml")

# Кэш для загруженных данных банков
_banks_cache: Optional[List[Dict]] = None
_banks_by_bik_cache: Optional[Dict[str, Dict]] = None


def _load_banks_from_xml() -> List[Dict]:
    """
    Загружает базу банков из XML файла
    Возвращает список словарей с данными банков
    """
    global _banks_cache
    
    if _banks_cache is not None:
        return _banks_cache
    
    banks = []
    
    try:
        if not os.path.exists(XML_FILE_PATH):
            # Если файл не найден, возвращаем пустой список
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"XML файл с банками не найден: {XML_FILE_PATH}")
            return []
        
        # Парсим XML с правильной кодировкой
        # Используем ET.parse, который автоматически обрабатывает кодировку из XML декларации
        parser = ET.XMLParser(encoding='utf-8')
        tree = ET.parse(XML_FILE_PATH, parser=parser)
        root = tree.getroot()
        
        for bik_elem in root.findall('bik'):
            bik = bik_elem.get('bik', '').strip()
            ks = bik_elem.get('ks', '').strip()
            # Получаем названия и обрабатываем HTML-сущности
            name_raw = bik_elem.get('name', '').strip()
            namemini_raw = bik_elem.get('namemini', '').strip()
            
            # Декодируем HTML-сущности (&quot; -> ", &amp; -> & и т.д.)
            name = html.unescape(name_raw) if name_raw else ''
            namemini = html.unescape(namemini_raw) if namemini_raw else ''
            
            # Используем краткое наименование, если есть, иначе полное
            display_name = namemini if namemini else name
            
            if bik and display_name:
                banks.append({
                    "name": display_name,
                    "bik": bik,
                    "ks": ks,  # Корреспондентский счет
                    "full_name": name,  # Полное наименование
                })
        
        _banks_cache = banks
        return banks
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка загрузки XML файла с банками: {e}")
        return []


def _get_banks_by_bik_dict() -> Dict[str, Dict]:
    """
    Возвращает словарь банков, индексированный по БИК
    """
    global _banks_by_bik_cache
    
    if _banks_by_bik_cache is not None:
        return _banks_by_bik_cache
    
    banks = _load_banks_from_xml()
    _banks_by_bik_cache = {bank["bik"]: bank for bank in banks}
    return _banks_by_bik_cache


def search_banks(query: str, limit: int = 20) -> List[Dict]:
    """
    Поиск банков по первым символам названия из XML базы
    
    Args:
        query: Поисковый запрос (минимум 3 символа)
        limit: Максимальное количество результатов
    
    Returns:
        Список словарей с ключами 'name', 'bik' и 'ks' (корреспондентский счет)
    """
    if len(query) < 3:
        return []
    
    banks = _load_banks_from_xml()
    if not banks:
        return []
    
    query_lower = query.lower().strip()
    query_upper = query.upper().strip()
    results = []
    
    # Поиск по краткому и полному наименованию (без учета регистра)
    for bank in banks:
        name = bank["name"].strip()
        full_name = bank.get("full_name", "").strip()
        
        name_lower = name.lower()
        full_name_lower = full_name.lower()
        name_upper = name.upper()
        full_name_upper = full_name.upper()
        
        # Поиск по подстроке в названии (проверяем и нижний, и верхний регистр)
        if (query_lower in name_lower or query_lower in full_name_lower or
            query_upper in name_upper or query_upper in full_name_upper):
            results.append(bank.copy())
            if len(results) >= limit:
                break
    
    return results


def get_bank_by_bik(bik: str) -> Optional[Dict]:
    """
    Получить банк по БИК из XML базы
    
    Args:
        bik: БИК банка (9 цифр)
    
    Returns:
        Словарь с ключами 'name', 'bik', 'ks' (корреспондентский счет) или None
    """
    banks_dict = _get_banks_by_bik_dict()
    
    if bik in banks_dict:
        return banks_dict[bik].copy()
    
    # Если не найден в XML, пробуем получить из API как fallback
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


def update_banks_xml():
    """
    Обновить XML файл с банками с сайта bik-info.ru
    Вызывается вручную или по расписанию для обновления базы
    """
    try:
        url = "https://bik-info.ru/base/base.xml"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Сохраняем файл
        with open(XML_FILE_PATH, 'wb') as f:
            f.write(response.content)
        
        # Сбрасываем кэш
        global _banks_cache, _banks_by_bik_cache
        _banks_cache = None
        _banks_by_bik_cache = None
        
        return True
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка обновления XML файла с банками: {e}")
        return False

