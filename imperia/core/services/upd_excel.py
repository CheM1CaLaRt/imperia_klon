"""
Сервис для заполнения УПД по шаблону Excel
"""
import os
from copy import copy
from datetime import date
from decimal import Decimal
from typing import List, Dict, Optional

import openpyxl
from openpyxl import utils
from openpyxl.styles import Font, Alignment, Border, Side
from django.conf import settings


# Путь к шаблону УПД
# Шаблон должен быть скачан с https://spmag.ru/download/file/4430
# и размещен в корне проекта imperia/ как Blank-UPD.xlsx
TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "Blank-UPD.xlsx"
)


def fill_upd_excel(
    seller_name: str,
    seller_full_name: str,
    seller_address: str,
    seller_inn: str,
    seller_kpp: str = "",
    buyer_name: str = "",
    buyer_full_name: str = "",
    buyer_address: str = "",
    buyer_inn: str = "",
    buyer_kpp: str = "",
    doc_number: str = "",
    doc_date: date = None,
    items: List[Dict] = None,
    currency_name_code: str = "Российский рубль, 643",
    output_path: Optional[str] = None,
) -> bytes:
    """
    Заполняет шаблон УПД данными и возвращает байты файла
    
    Args:
        seller_name: Краткое наименование продавца
        seller_full_name: Полное наименование продавца
        seller_address: Адрес продавца
        seller_inn: ИНН продавца
        seller_kpp: КПП продавца
        buyer_name: Краткое наименование покупателя
        buyer_full_name: Полное наименование покупателя
        buyer_address: Адрес покупателя
        buyer_inn: ИНН покупателя
        buyer_kpp: КПП покупателя
        doc_number: Номер документа
        doc_date: Дата документа
        items: Список товаров/услуг
        currency_name_code: Валюта (по умолчанию "Российский рубль, 643")
        output_path: Путь для сохранения файла (опционально)
    
    Returns:
        bytes: Байты Excel файла
    """
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Шаблон УПД не найден: {TEMPLATE_PATH}")
    
    if doc_date is None:
        doc_date = date.today()
    
    if items is None:
        items = []
    
    # Загружаем шаблон
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    
    # Ищем лист с УПД (может называться "Счет-фактура" или "УПД" или первый лист)
    sheet_names = wb.sheetnames
    ws = None
    for name in ["Счет-фактура", "УПД", "Sheet1"]:
        if name in sheet_names:
            ws = wb[name]
            break
    
    if ws is None:
        ws = wb.active
    
    def safe_set_cell(worksheet, row, col, value):
        """Безопасно устанавливает значение ячейки, обрабатывая объединенные ячейки"""
        try:
            # Проверяем, является ли ячейка частью объединенного диапазона
            for merged_range in worksheet.merged_cells.ranges:
                if row >= merged_range.min_row and row <= merged_range.max_row and \
                   col >= merged_range.min_col and col <= merged_range.max_col:
                    # Если ячейка в объединенном диапазоне, устанавливаем значение в верхнюю левую ячейку
                    top_left = worksheet.cell(row=merged_range.min_row, column=merged_range.min_col)
                    top_left.value = value
                    return
            
            # Обычная ячейка - просто устанавливаем значение
            cell = worksheet.cell(row=row, column=col)
            cell.value = value
        except Exception:
            # Если не удалось установить, пробуем альтернативный способ
            try:
                cell_coordinate = f"{utils.get_column_letter(col)}{row}"
                worksheet[cell_coordinate] = value
            except Exception:
                # В крайнем случае просто игнорируем ошибку
                pass
    
    # --- Заполнение шапки УПД ---
    # Номер и дата УПД (поле (1))
    # Ищем ячейки с текстом "Счет-фактура" или "УПД"
    for row in ws.iter_rows(min_row=1, max_row=10):
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                if "Счет-фактура" in cell.value or "УПД" in cell.value:
                    # Пытаемся найти соседние ячейки для номера и даты
                    col = cell.column
                    # Номер документа обычно справа
                    current_value = ws.cell(row=cell.row, column=col + 1).value
                    if current_value is None or current_value == "":
                        safe_set_cell(ws, cell.row, col + 1, doc_number)
                    # Дата обычно еще правее
                    date_col = col + 3
                    date_cell_value = ws.cell(row=cell.row, column=date_col).value
                    prev_cell_value = ws.cell(row=cell.row, column=date_col - 1).value
                    if date_cell_value is None or (prev_cell_value and "от" in str(prev_cell_value)):
                        safe_set_cell(ws, cell.row, date_col, doc_date.strftime("%d.%m.%Y"))
                    break
    
    # Продавец (2) - ищем ячейки с метками "Продавец" или "Грузоотправитель"
    seller_found = False
    for row in ws.iter_rows(min_row=1, max_row=20):
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                if "Продавец" in cell.value or "Грузоотправитель" in cell.value:
                    seller_row = cell.row
                    seller_col = cell.column
                    # Заполняем данные продавца в соседних ячейках
                    safe_set_cell(ws, seller_row + 1, seller_col, seller_full_name or seller_name)
                    safe_set_cell(ws, seller_row + 2, seller_col, seller_address)
                    inn_kpp = seller_inn
                    if seller_kpp:
                        inn_kpp += f" / {seller_kpp}"
                    safe_set_cell(ws, seller_row + 3, seller_col, inn_kpp)
                    seller_found = True
                    break
        if seller_found:
            break
    
    # Покупатель (6) - ищем ячейки с метками "Покупатель" или "Грузополучатель"
    buyer_found = False
    for row in ws.iter_rows(min_row=1, max_row=30):
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                if "Покупатель" in cell.value or "Грузополучатель" in cell.value:
                    buyer_row = cell.row
                    buyer_col = cell.column
                    # Заполняем данные покупателя
                    safe_set_cell(ws, buyer_row + 1, buyer_col, buyer_full_name or buyer_name)
                    safe_set_cell(ws, buyer_row + 2, buyer_col, buyer_address)
                    buyer_inn_kpp = buyer_inn
                    if buyer_kpp:
                        buyer_inn_kpp += f" / {buyer_kpp}"
                    safe_set_cell(ws, buyer_row + 3, buyer_col, buyer_inn_kpp)
                    buyer_found = True
                    break
        if buyer_found:
            break
    
    # Валюта (7)
    currency_found = False
    for row in ws.iter_rows(min_row=1, max_row=30):
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                if "Валюта" in cell.value or "Денежная единица" in cell.value:
                    safe_set_cell(ws, cell.row, cell.column + 1, currency_name_code)
                    currency_found = True
                    break
        if currency_found:
            break
    
    # --- Заполнение таблицы товаров ---
    # Ищем начало таблицы товаров (обычно строка с заголовками)
    table_start_row = None
    header_keywords = ["Наименование", "Единица", "Количество", "Цена", "Стоимость", "НДС"]
    
    for row_idx, row in enumerate(ws.iter_rows(min_row=15, max_row=50), start=15):
        row_values = [str(cell.value or "").lower() for cell in row if cell.value]
        if any(keyword.lower() in " ".join(row_values) for keyword in header_keywords):
            table_start_row = row_idx + 1  # Следующая строка после заголовков
            break
    
    if table_start_row is None:
        # Если не нашли заголовки, начинаем с 24 строки (как в примере)
        table_start_row = 24
    
    total_without_vat = Decimal("0")
    total_vat = Decimal("0")
    total_with_vat = Decimal("0")
    
    # Определяем колонки по заголовкам (если найдены)
    col_num = None  # N п/п
    col_name = None  # Наименование
    col_unit_code = None  # Код ед. изм.
    col_unit = None  # Единица измерения
    col_qty = None  # Количество
    col_price = None  # Цена
    col_total_no_vat = None  # Стоимость без НДС
    col_vat_rate = None  # Ставка НДС
    col_vat_amount = None  # Сумма НДС
    col_total_with_vat = None  # Стоимость с НДС
    
    # Пытаемся определить колонки по заголовкам
    if table_start_row > 1:
        header_row = ws[table_start_row - 1]
        for idx, cell in enumerate(header_row, start=1):
            cell_value = str(cell.value or "").lower()
            if "п/п" in cell_value or "номер" in cell_value:
                col_num = idx
            elif "наименование" in cell_value or "товар" in cell_value:
                col_name = idx
            elif "единица" in cell_value and "код" not in cell_value:
                col_unit = idx
            elif "количество" in cell_value:
                col_qty = idx
            elif "цена" in cell_value and "стоимость" not in cell_value:
                col_price = idx
            elif "без ндс" in cell_value or "стоимость" in cell_value:
                if col_total_no_vat is None:
                    col_total_no_vat = idx
            elif "ставка" in cell_value and "ндс" in cell_value:
                col_vat_rate = idx
            elif "сумма" in cell_value and "ндс" in cell_value:
                col_vat_amount = idx
            elif "с ндс" in cell_value or "всего" in cell_value:
                col_total_with_vat = idx
    
    # Если не определили колонки, используем значения по умолчанию из примера
    if col_num is None:
        col_num = 2  # B
    if col_name is None:
        col_name = 5  # E
    if col_unit is None:
        col_unit = 14  # N
    if col_qty is None:
        col_qty = 18  # R
    if col_price is None:
        col_price = 23  # W
    if col_total_no_vat is None:
        col_total_no_vat = 27  # AA
    if col_vat_rate is None:
        col_vat_rate = 37  # AK
    if col_vat_amount is None:
        col_vat_amount = 41  # AO
    if col_total_with_vat is None:
        col_total_with_vat = 45  # AS
    
    # Заполняем строки товаров
    current_row = table_start_row
    for idx, item in enumerate(items, start=1):
        name = item.get("name", item.get("title", ""))
        unit = item.get("unit", "шт")
        qty = float(item.get("qty", item.get("quantity", 0)))
        price = float(item.get("price", 0))
        vat_rate_str = item.get("vat_rate", "20%")
        
        amount_without_vat = Decimal(str(qty * price))
        
        # Парсим ставку НДС
        if isinstance(vat_rate_str, str) and vat_rate_str.endswith("%"):
            vat_percent = Decimal(vat_rate_str.strip("%")) / Decimal("100")
        elif isinstance(vat_rate_str, (int, float, Decimal)):
            vat_percent = Decimal(str(vat_rate_str)) / Decimal("100")
        else:
            vat_percent = Decimal("0.20")  # По умолчанию 20%
        
        vat_amount = amount_without_vat * vat_percent
        amount_with_vat = amount_without_vat + vat_amount
        
        total_without_vat += amount_without_vat
        total_vat += vat_amount
        total_with_vat += amount_with_vat
        
        # Заполняем ячейки (используем safe_set_cell для обработки объединенных ячеек)
        safe_set_cell(ws, current_row, col_num, idx)
        safe_set_cell(ws, current_row, col_name, name)
        safe_set_cell(ws, current_row, col_unit, unit)
        safe_set_cell(ws, current_row, col_qty, qty)
        safe_set_cell(ws, current_row, col_price, price)
        safe_set_cell(ws, current_row, col_total_no_vat, float(amount_without_vat))
        safe_set_cell(ws, current_row, col_vat_rate, vat_rate_str)
        safe_set_cell(ws, current_row, col_vat_amount, float(vat_amount))
        safe_set_cell(ws, current_row, col_total_with_vat, float(amount_with_vat))
        
        current_row += 1
    
    # --- Итоговая строка "Всего к оплате" ---
    # Ищем строку с итогами
    total_row = None
    for row_idx in range(current_row, current_row + 5):
        row_values = [str(ws.cell(row=row_idx, column=col).value or "").lower() 
                     for col in range(1, ws.max_column + 1)]
        if any(keyword in " ".join(row_values) for keyword in ["всего", "итого", "к оплате"]):
            total_row = row_idx
            break
    
    if total_row is None:
        total_row = current_row + 1
    
    # Заполняем итоговые значения
    safe_set_cell(ws, total_row, col_total_no_vat, float(total_without_vat))
    safe_set_cell(ws, total_row, col_vat_amount, float(total_vat))
    safe_set_cell(ws, total_row, col_total_with_vat, float(total_with_vat))
    
    # Сохраняем файл
    if output_path:
        wb.save(output_path)
        with open(output_path, "rb") as f:
            return f.read()
    else:
        # Возвращаем байты
        from io import BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.read()

