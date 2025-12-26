"""
Модуль для заполнения Универсального передаточного документа (УПД) в Excel
по готовому шаблону Blank-UPD.xlsx
"""
import os
from datetime import date
from decimal import Decimal
from typing import List, Dict, Optional

import openpyxl
from openpyxl import utils


# Путь к шаблону УПД
TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "Blank-UPD.xlsx"
)

# Константы с адресами ячеек в шаблоне УПД
# Эти адреса нужно определить, открыв Blank-UPD.xlsx и посмотрев структуру
CELLS = {
    # Номер и дата документа (поле (1))
    "doc_number": "O2",  # Номер документа
    "doc_date": "Y2",    # Дата документа (обычно после "от")
    
    # Продавец (поля (2), (2а), (2б))
    "seller_name": "H5",      # Наименование продавца
    "seller_address": "H6",   # Адрес продавца
    "seller_inn_kpp": "H8",   # ИНН/КПП продавца
    
    # Покупатель (поля (6), (6а), (6б))
    "buyer_name": "H12",      # Наименование покупателя
    "buyer_address": "H13",   # Адрес покупателя
    "buyer_inn_kpp": "H14",   # ИНН/КПП покупателя
    
    # Валюта (поле (7))
    "currency": "H15",        # Валюта
    
    # Таблица товаров - начало первой строки данных (где стоят нули)
    "table_start_row": 24,    # Номер строки, с которой начинаются данные товаров
    
    # Колонки таблицы товаров
    "col_num": 2,             # B - Номер п/п
    "col_name": 5,            # E - Наименование товара
    "col_unit": 14,           # N - Единица измерения (условное обозначение)
    "col_qty": 18,            # R - Количество
    "col_price": 23,          # W - Цена за единицу
    "col_total_no_vat": 27,   # AA - Стоимость без НДС
    "col_vat_rate": 37,       # AK - Ставка НДС
    "col_vat_amount": 41,     # AO - Сумма НДС
    "col_total_with_vat": 45, # AS - Стоимость с НДС
    
    # Итоговая строка "Всего к оплате (9)"
    "total_row_offset": 1,    # Смещение от последней строки товаров до строки итогов
}


def safe_set_cell(worksheet, cell_address: str, value):
    """
    Безопасно устанавливает значение ячейки, обрабатывая объединенные ячейки.
    
    Args:
        worksheet: Рабочий лист openpyxl
        cell_address: Адрес ячейки (например, "H5")
        value: Значение для установки
    """
    try:
        cell = worksheet[cell_address]
        cell.value = value
    except Exception:
        # Если ячейка объединена, пробуем найти верхнюю левую ячейку
        try:
            row = worksheet[cell_address].row
            col = worksheet[cell_address].column
            
            # Проверяем объединенные диапазоны
            for merged_range in list(worksheet.merged_cells.ranges):
                if row >= merged_range.min_row and row <= merged_range.max_row and \
                   col >= merged_range.min_col and col <= merged_range.max_col:
                    # Находим верхнюю левую ячейку
                    top_left_coord = f"{utils.get_column_letter(merged_range.min_col)}{merged_range.min_row}"
                    top_left_cell = worksheet[top_left_coord]
                    top_left_cell.value = value
                    return
            
            # Если не объединена, устанавливаем напрямую
            cell.value = value
        except Exception:
            pass


def safe_set_cell_by_coords(worksheet, row: int, col: int, value):
    """
    Безопасно устанавливает значение ячейки по координатам (row, col).
    
    Args:
        worksheet: Рабочий лист openpyxl
        row: Номер строки (начиная с 1)
        col: Номер колонки (начиная с 1)
        value: Значение для установки
    """
    try:
        cell_coord = f"{utils.get_column_letter(col)}{row}"
        safe_set_cell(worksheet, cell_coord, value)
    except Exception:
        try:
            cell = worksheet.cell(row=row, column=col)
            cell.value = value
        except Exception:
            pass


def fill_upd(
    seller_name: str,
    seller_address: str,
    seller_inn_kpp: str,
    buyer_name: str,
    buyer_address: str,
    buyer_inn_kpp: str,
    doc_number: str,
    doc_date: date,
    items: List[Dict],
    currency_name_code: str = "Российский рубль, 643",
    output_path: Optional[str] = None,
) -> str:
    """
    Заполняет шаблон УПД данными и сохраняет результат.
    
    Args:
        seller_name: Наименование продавца
        seller_address: Адрес продавца
        seller_inn_kpp: ИНН/КПП продавца (формат: "ИНН / КПП" или просто "ИНН")
        buyer_name: Наименование покупателя
        buyer_address: Адрес покупателя
        buyer_inn_kpp: ИНН/КПП покупателя
        doc_number: Номер документа
        doc_date: Дата документа
        items: Список товаров/услуг, каждый элемент - словарь с ключами:
            - name: наименование
            - unit: единица измерения (условное обозначение)
            - qty: количество
            - price: цена за единицу
            - vat_rate: ставка НДС ("20%", "0%", "без НДС")
        currency_name_code: Валюта (по умолчанию "Российский рубль, 643")
        output_path: Путь для сохранения файла (если None, создается UPD-filled.xlsx)
    
    Returns:
        str: Путь к сохраненному файлу
    """
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Шаблон УПД не найден: {TEMPLATE_PATH}")
    
    # Загружаем шаблон
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    
    # Открываем лист "Счет-фактура"
    if "Счет-фактура" in wb.sheetnames:
        ws = wb["Счет-фактура"]
    else:
        # Если лист не найден, используем активный
        ws = wb.active
    
    # --- Заполнение шапки УПД ---
    
    # Номер и дата документа (поле (1))
    safe_set_cell(ws, CELLS["doc_number"], doc_number)
    safe_set_cell(ws, CELLS["doc_date"], doc_date.strftime("%d.%m.%Y"))
    
    # Продавец (поля (2), (2а), (2б))
    safe_set_cell(ws, CELLS["seller_name"], seller_name)
    safe_set_cell(ws, CELLS["seller_address"], seller_address)
    safe_set_cell(ws, CELLS["seller_inn_kpp"], seller_inn_kpp)
    
    # Покупатель (поля (6), (6а), (6б))
    safe_set_cell(ws, CELLS["buyer_name"], buyer_name)
    safe_set_cell(ws, CELLS["buyer_address"], buyer_address)
    safe_set_cell(ws, CELLS["buyer_inn_kpp"], buyer_inn_kpp)
    
    # Валюта (поле (7))
    safe_set_cell(ws, CELLS["currency"], currency_name_code)
    
    # --- Заполнение таблицы товаров ---
    
    table_start_row = CELLS["table_start_row"]
    current_row = table_start_row
    
    total_without_vat = Decimal("0")
    total_vat = Decimal("0")
    total_with_vat = Decimal("0")
    
    # Заполняем строки товаров
    for idx, item in enumerate(items, start=1):
        name = item.get("name", "")
        unit = item.get("unit", "шт")
        qty = float(item.get("qty", 0))
        price = float(item.get("price", 0))
        vat_rate_str = item.get("vat_rate", "20%")
        
        # Вычисляем суммы
        amount_without_vat = Decimal(str(qty * price))
        
        # Парсим ставку НДС
        if isinstance(vat_rate_str, str):
            if vat_rate_str.endswith("%"):
                vat_percent = Decimal(vat_rate_str.strip("%")) / Decimal("100")
            elif "без" in vat_rate_str.lower() or "ноль" in vat_rate_str.lower():
                vat_percent = Decimal("0")
            else:
                vat_percent = Decimal("0.20")  # По умолчанию 20%
        else:
            vat_percent = Decimal(str(vat_rate_str)) / Decimal("100") if vat_rate_str else Decimal("0.20")
        
        vat_amount = amount_without_vat * vat_percent
        amount_with_vat = amount_without_vat + vat_amount
        
        # Суммируем итоги
        total_without_vat += amount_without_vat
        total_vat += vat_amount
        total_with_vat += amount_with_vat
        
        # Заполняем ячейки строки товара
        safe_set_cell_by_coords(ws, current_row, CELLS["col_num"], idx)  # Номер п/п
        safe_set_cell_by_coords(ws, current_row, CELLS["col_name"], name)  # Наименование
        safe_set_cell_by_coords(ws, current_row, CELLS["col_unit"], unit)  # Единица измерения
        safe_set_cell_by_coords(ws, current_row, CELLS["col_qty"], qty)  # Количество
        safe_set_cell_by_coords(ws, current_row, CELLS["col_price"], price)  # Цена за единицу
        safe_set_cell_by_coords(ws, current_row, CELLS["col_total_no_vat"], float(amount_without_vat))  # Стоимость без НДС
        safe_set_cell_by_coords(ws, current_row, CELLS["col_vat_rate"], vat_rate_str)  # Ставка НДС
        safe_set_cell_by_coords(ws, current_row, CELLS["col_vat_amount"], float(vat_amount))  # Сумма НДС
        safe_set_cell_by_coords(ws, current_row, CELLS["col_total_with_vat"], float(amount_with_vat))  # Стоимость с НДС
        
        current_row += 1
    
    # --- Заполнение итоговой строки "Всего к оплате (9)" ---
    
    total_row = current_row + CELLS["total_row_offset"]
    
    # Заполняем итоговые значения
    safe_set_cell_by_coords(ws, total_row, CELLS["col_total_no_vat"], float(total_without_vat))
    safe_set_cell_by_coords(ws, total_row, CELLS["col_vat_amount"], float(total_vat))
    safe_set_cell_by_coords(ws, total_row, CELLS["col_total_with_vat"], float(total_with_vat))
    
    # --- Сохранение файла ---
    
    if output_path is None:
        # Создаем имя файла на основе номера документа и даты
        output_path = os.path.join(
            os.path.dirname(TEMPLATE_PATH),
            f"UPD-filled-{doc_number}-{doc_date.strftime('%Y%m%d')}.xlsx"
        )
    
    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    # Пример использования
    
    # Тестовые данные
    items_data = [
        {
            "name": "Услуги по разработке сайта",
            "unit": "усл",
            "qty": 1,
            "price": 50000.00,
            "vat_rate": "20%",
        },
        {
            "name": "SEO сопровождение",
            "unit": "мес",
            "qty": 1,
            "price": 15000.00,
            "vat_rate": "20%",
        },
        {
            "name": "Консультационные услуги",
            "unit": "час",
            "qty": 5,
            "price": 2000.00,
            "vat_rate": "20%",
        },
    ]
    
    # Заполняем УПД
    output_file = fill_upd(
        seller_name='ООО "Ромашка"',
        seller_address="197000, г. Санкт-Петербург, ул. Примерная, д. 1",
        seller_inn_kpp="7700000000 / 770001001",
        buyer_name='ООО "Покупатель"',
        buyer_address="101000, г. Москва, ул. Клиента, д. 2",
        buyer_inn_kpp="7711111111 / 771101001",
        doc_number="15",
        doc_date=date.today(),
        items=items_data,
    )
    
    print(f"УПД успешно создан: {output_file}")

