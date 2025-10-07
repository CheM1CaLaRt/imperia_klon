import requests
from typing import Tuple

BASE_INFO_URL = "https://egrul.itsoft.ru/{inn}.json"
BASE_FIN_URL  = "https://egrul.itsoft.ru/fin/?{inn}"

DEFAULT_TIMEOUT = 8  # сек

class EgrulError(RuntimeError):
    pass

def fetch_by_inn(inn: str) -> dict:
    url = BASE_INFO_URL.format(inn=inn)
    try:
        r = requests.get(url, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not data:
            raise EgrulError("Пустой ответ ЕГРЮЛ")
        return data
    except requests.RequestException as e:
        raise EgrulError(f"Ошибка запроса ЕГРЮЛ: {e}") from e

def _get(d: dict, *path, default=""):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

def _addr_rf_to_string(addr_rf: dict) -> str:
    if not isinstance(addr_rf, dict):
        return ""
    a = addr_rf.get("@attributes", {}) if isinstance(addr_rf.get("@attributes"), dict) else {}
    parts = []
    # Индекс, регион, город/нас.пункт могут лежать в разных ключах.
    # Берём всё, что есть, по порядку.
    for k in ("Индекс",):
        if a.get(k):
            parts.append(str(a[k]))
    # Регион обычно отдельным объектом
    reg = _get(addr_rf, "Регион", "@attributes", "НаимРегион", default="")
    if reg:
        parts.append(reg)
    # Улица
    street_type = _get(addr_rf, "Улица", "@attributes", "ТипУлица", default="")
    street_name = _get(addr_rf, "Улица", "@attributes", "НаимУлица", default="")
    street = " ".join([street_type, street_name]).strip()
    if street:
        parts.append(street)
    # Дом/корпус/квартира
    for k in ("Дом", "Корпус", "Кварт"):
        v = a.get(k)
        if v:
            parts.append(str(v))
    # Иногда кладут “КодАдрКладр” и т.п. — их игнорируем для человекочитаемости
    return ", ".join(parts)

def parse_counterparty_payload(payload: dict) -> dict:
    """
    Универсальный парсер egrul.itsoft.ru → наши поля.
    Поддерживает форматы СвЮЛ (юр.лицо) и СвИП (инд.предприниматель).
    """
    if not isinstance(payload, dict):
        return {
            "inn": "", "kpp": "", "ogrn": "", "name": "", "full_name": "",
            "address": "", "registration_country": "РОССИЯ", "meta_json": payload
        }

    # ----- Юр. лицо -----
    if "СвЮЛ" in payload:
        ul = payload["СвЮЛ"] or {}
        attrs = ul.get("@attributes", {}) or {}

        # ИНН/КПП иногда лежат в СвУчетНО.@attributes.{ИНН,КПП}
        inn = _get(ul, "СвУчетНО", "@attributes", "ИНН", default="") or attrs.get("ИНН", "")
        kpp = _get(ul, "СвУчетНО", "@attributes", "КПП", default="") or attrs.get("КПП", "")
        ogrn = attrs.get("ОГРН", "") or _get(ul, "СвОбрЮЛ", "@attributes", "ОГРН", default="")

        full_name = _get(ul, "СвНаимЮЛ", "@attributes", "НаимЮЛПолн", default="")
        short_name = _get(ul, "СвНаимЮЛ", "СвНаимЮЛСокр", "@attributes", "НаимСокр", default="")
        name = short_name or full_name

        addr_rf = _get(ul, "СвАдресЮЛ", "АдресРФ", default={})
        address = _addr_rf_to_string(addr_rf)

        return {
            "inn": str(inn or "").strip(),
            "kpp": str(kpp or "").strip(),
            "ogrn": str(ogrn or "").strip(),
            "name": str(name or "").strip(),
            "full_name": str(full_name or "").strip(),
            "address": str(address or "").strip(),
            "registration_country": "РОССИЯ",
            "meta_json": payload,
        }

    # ----- Индивидуальный предприниматель -----
    if "СвИП" in payload or "ИП" in payload:
        ip = payload.get("СвИП") or payload.get("ИП") or {}
        attrs = ip.get("@attributes", {}) or {}
        inn = attrs.get("ИННФЛ", "") or attrs.get("ИНН", "")
        ogrn = attrs.get("ОГРНИП", "") or attrs.get("ОГРН", "")

        # ФИО
        fio = [
            _get(ip, "СвФЛ", "@attributes", "Фамилия", default=""),
            _get(ip, "СвФЛ", "@attributes", "Имя", default=""),
            _get(ip, "СвФЛ", "@attributes", "Отчество", default=""),
        ]
        full_name = " ".join([p for p in fio if p]).strip()
        name = f"ИП {full_name}" if full_name else "ИП"

        # Адрес у ИП встречается реже; пытаемся собрать, если есть
        address = ""
        addr_rf = _get(ip, "СвАдресМЖ", "АдресРФ", default={})
        if addr_rf:
            address = _addr_rf_to_string(addr_rf)

        return {
            "inn": str(inn or "").strip(),
            "kpp": "",  # у ИП нет КПП
            "ogrn": str(ogrn or "").strip(),
            "name": name,
            "full_name": name if not full_name else full_name,
            "address": str(address or "").strip(),
            "registration_country": "РОССИЯ",
            "meta_json": payload,
        }

    # Если встретился неизвестный формат — вернём “сырой” минимум
    return {
        "inn": str(payload.get("ИНН") or payload.get("inn") or "").strip(),
        "kpp": str(payload.get("КПП") or payload.get("kpp") or "").strip(),
        "ogrn": str(payload.get("ОГРН") or payload.get("ogrn") or payload.get("ОГРНИП") or "").strip(),
        "name": str(payload.get("Наименование") or payload.get("name") or "").strip(),
        "full_name": str(payload.get("ПолноеНаименование") or payload.get("full_name") or "").strip(),
        "address": str(payload.get("Адрес") or payload.get("address") or "").strip(),
        "registration_country": "РОССИЯ",
        "meta_json": payload,
    }


def fetch_finance_by_inn(inn: str) -> Tuple[dict, int | None, int | None]:
    url = BASE_FIN_URL.format(inn=inn)
    try:
        r = requests.get(url, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json() or {}
    except requests.RequestException as e:
        raise EgrulError(f"Ошибка запроса финансов: {e}") from e

    # Попробуем вытащить последние показатели, если структура позволяет
    revenue_last = None
    profit_last = None
    # Примеры: {"years": [{"year":2023,"revenue":..., "profit":...}, ...]}
    years = data.get("years") or data.get("data") or []
    if isinstance(years, list) and years:
        last = sorted(years, key=lambda x: x.get("year", 0))[-1]
        revenue_last = last.get("revenue") or last.get("Выручка")
        profit_last = last.get("profit") or last.get("ЧистаяПрибыль")

    return data, revenue_last, profit_last
