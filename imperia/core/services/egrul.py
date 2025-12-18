from typing import Tuple
import json
import requests
from decimal import Decimal


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

        # ИНН и ОГРНИП
        inn = (
            attrs.get("ИННФЛ")
            or _get(ip, "СвУчетНО", "@attributes", "ИННФЛ", default="")
            or attrs.get("ИНН", "")
        )
        ogrnip = attrs.get("ОГРНИП", "") or _get(ip, "СвРегИП", "@attributes", "ОГРНИП", default="")

        # ФИО (лежит в СвФЛ → ФИОРус → @attributes)
        fio_attrs = _get(ip, "СвФЛ", "ФИОРус", "@attributes", default={}) or {}
        last = fio_attrs.get("Фамилия", "")
        first = fio_attrs.get("Имя", "")
        middle = fio_attrs.get("Отчество", "")
        fio = " ".join([p for p in (last, first, middle) if p]).strip()

        # Названия
        name = f"ИП {fio}".strip() if fio else "ИП"
        full_name = f"Индивидуальный предприниматель {fio}".strip() if fio else "Индивидуальный предприниматель"

        # Адрес (если есть)
        address = ""
        addr_rf = _get(ip, "СвАдресМЖ", "АдресРФ", default={})
        if addr_rf:
            address = _addr_rf_to_string(addr_rf)

        return {
            "inn": str(inn or "").strip(),
            "kpp": "",                         # у ИП нет КПП
            "ogrn": str(ogrnip or "").strip(), # используем именно ОГРНИП
            "name": name,                      # ИП + ФИО
            "full_name": full_name,            # Индивидуальный предприниматель + ФИО
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


class EgrulError(Exception):
    pass

def _to_dec(v):
    if v is None:
        return None
    # На сайте часто числа строками; уберём пробелы/запятые.
    s = str(v).replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        try:
            return Decimal(int(float(s)))
        except Exception:
            return None

def fetch_finance_by_inn(inn: str):
    """
    Возвращает кортеж: (fin_json, revenue_last, profit_last)
    - fin_json: исходный JSON (dict), как пришёл с сервера
    - revenue_last: Decimal | None  (income за последний год)
    - profit_last:  Decimal | None  (income - outcome за последний год)
    """
    url = f"https://egrul.itsoft.ru/fin/?{inn}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise EgrulError(f"Ошибка запроса финданных: {e}")

    try:
        data = resp.json()
    except ValueError:
        # иногда приходит текст с одинарными кавычками → попробуем вручную
        try:
            data = json.loads(resp.text.replace("'", '"'))
        except Exception as e:
            raise EgrulError(f"Не удалось распарсить JSON финданных: {e}")
    
    # Гарантируем, что data всегда будет словарем
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        data = {}

    # ожидаемый формат: {"2011":{"income":...,"outcome":...}, ...}
    revenue_last = profit_last = None
    if isinstance(data, dict):
        years = []
        for y, vals in data.items():
            try:
                yi = int(y)
            except Exception:
                continue
            if not isinstance(vals, dict):
                continue
            inc = _to_dec(vals.get("income"))
            out = _to_dec(vals.get("outcome"))
            years.append((yi, inc, out))

        if years:
            years.sort(key=lambda t: t[0])
            _, inc, out = years[-1]
            if inc is not None:
                revenue_last = inc
            if inc is not None and out is not None:
                profit_last = inc - out

    return data, revenue_last, profit_last
