# core/services/upd_xml.py
import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal
from datetime import datetime


class Upd970:
    """Генератор УПД в формате XML согласно приказу ФНС России от 19.12.2018 № ММВ‑7‑15/820"""

    def __init__(self, head, buyer, seller, table, docs=None, delivery_address=None):
        self.head = head
        self.buyer = buyer
        self.seller = seller
        self.table = table
        self.docs = docs or []
        self.delivery_address = delivery_address  # Адрес грузополучателя

        self.upd_date_russian = head["upd_date"].strftime("%d.%m.%Y")
        self.upd_date_yyyymmdd = head["upd_date"].strftime("%Y%m%d")

    def org_info(self, parent, infodict):
        """Добавляет информацию об организации (продавец/покупатель)"""
        sv = ET.SubElement(parent, "ИдСв")

        if len(infodict["ИНН"]) == 10:
            # Юридическое лицо
            svul = ET.SubElement(sv, "СвЮЛУч", {
                "НаимОрг": infodict["НаимОрг"],
                "ИННЮЛ": infodict["ИНН"],
                "КПП": infodict.get("КПП", "")
            })
        elif len(infodict["ИНН"]) == 12:
            # Индивидуальный предприниматель
            svul = ET.SubElement(sv, "СвИП", {
                "ИННФЛ": infodict["ИНН"]
            })
            # Парсим ФИО из наименования
            name_parts = infodict["НаимОрг"].split()
            if len(name_parts) >= 3:
                fio = ET.SubElement(svul, "ФИО", {
                    "Фамилия": name_parts[0],
                    "Имя": name_parts[1],
                    "Отчество": name_parts[2]
                })
            elif len(name_parts) == 2:
                fio = ET.SubElement(svul, "ФИО", {
                    "Фамилия": name_parts[0],
                    "Имя": name_parts[1]
                })

        adr0 = ET.SubElement(parent, "Адрес")

        fields = ["КодРегион", "НаимРегион", "Район", "Город", "Индекс", "Улица", "Дом", "Корпус", "Кварт", "НаселПункт"]
        addr_fields = {}
        for f in fields:
            if infodict.get(f):
                addr_fields[f] = str(infodict[f])

        if addr_fields:
            adr = ET.SubElement(adr0, "АдрРФ", addr_fields)

    def tovar_info(self, parent):
        """Добавляет таблицу товаров"""
        for tov in self.table:
            sv = ET.SubElement(parent, "СведТов", {
                "СтТовУчНал": str(tov["Стоимость"]),
                "СтТовБезНДС": str(tov["СтоимостьБезНДС"]),
                "ЦенаТов": str(tov["Цена"]),
                "КолТов": str(tov["Кол"]),
                "НаимЕдИзм": tov["НаимЕдИзм"],
                "ОКЕИ_Тов": tov["ОКЕИ"],
                "НаимТов": tov["Товар"],
                "НалСт": tov.get("НалСт", "20%"),
                "НомСтр": str(tov["НомСтр"])
            })
            
            dop = ET.SubElement(sv, "ДопСведТов")
            sumnal = ET.SubElement(dop, "СумНалВосст")
            beznds = ET.SubElement(sumnal, "БезНДС")
            beznds.text = "без НДС"

            akciz = ET.SubElement(sv, "Акциз")
            bezakciz = ET.SubElement(akciz, "БезАкциз")
            bezakciz.text = "без акциза"

            sumnal_1 = ET.SubElement(sv, "СумНал")
            sumnal_2 = ET.SubElement(sumnal_1, "СумНал")
            sumnal_2.text = str(tov["СуммаНалога"])

        totalsum = ET.SubElement(parent, "ВсегоОпл", {
            "КолНеттоВс": "0",
            "СтТовУчНалВсего": str(self.head["СтоимВсего"]),
            "СтТовБезНДСВсего": str(self.head["СтоимБезНДСВсего"])
        })
        totalsumnal = ET.SubElement(totalsum, "СумНалВсего")
        totalsumnal_2 = ET.SubElement(totalsumnal, "СумНал")
        totalsumnal_2.text = str(self.head["СумНал"])

    def create_xml(self, path=None):
        """Создает XML файл УПД"""
        h1 = self.buyer["guid"]
        h2 = self.seller["guid"]
        h3 = self.upd_date_yyyymmdd
        h4 = self.head["guid_doc"]

        filename = f"ON_NSCHFDOPPR_{h1}_{h2}_{h3}_{h4}_0_0_0_0_0_00"
        root = ET.Element("Файл", {
            "ИдФайл": filename,
            "ВерсФорм": "5.03",
            "ВерсПрог": "Imperia 1.0"
        })

        document = ET.SubElement(root, "Документ", {
            "ВремИнфПр": "12.00.01",
            "ДатаИнфПр": self.upd_date_russian,
            "КНД": "1115131",
            "НаимДокОпр": "Универсальный передаточный документ",
            "НаимЭконСубСост": self.seller["НаимОрг"],
            "ПоФактХЖ": "Документ об отгрузке товаров (выполнении работ), передаче имущественных прав (документ об оказании услуг)",
            "Функция": "СЧФДОП"
        })

        schf = ET.SubElement(document, "СвСчФакт", {
            "ДатаДок": self.upd_date_russian,
            "НомерДок": self.head["upd_number"]
        })

        seller = ET.SubElement(schf, "СвПрод")
        self.org_info(seller, self.seller)

        gruzot = ET.SubElement(schf, "ГрузОт")
        gruzot_info = ET.SubElement(gruzot, "ОнЖе")
        gruzot_info.text = "он же"

        gruzpol = ET.SubElement(schf, "ГрузПолуч")
        # Используем адрес доставки для грузополучателя, если указан
        if self.delivery_address:
            delivery_info = self.buyer.copy()
            delivery_info.update(self.delivery_address)
            self.org_info(gruzpol, delivery_info)
        else:
            self.org_info(gruzpol, self.buyer)

        if self.head.get("ПП_Дата") and self.head.get("ПП_Номер"):
            pp_data = self.head["ПП_Дата"].strftime("%d.%m.%Y")
            pp_num = str(self.head["ПП_Номер"])
            pp = ET.SubElement(schf, "СвПРД", {"ДатаПРД": pp_data, "НомерПРД": pp_num})

        punkt5 = ET.SubElement(schf, "ДокПодтвОтгрНом", {
            "РеквДатаДок": self.upd_date_russian,
            "РеквНомерДок": self.head["upd_number"],
            "РеквНаимДок": "Универсальный передаточный документ"
        })

        buyer = ET.SubElement(schf, "СвПокуп")
        self.org_info(buyer, self.buyer)

        currency = ET.SubElement(schf, "ДенИзм", {
            "НаимОКВ": "Российский рубль",
            "КодОКВ": "643"
        })

        main_table = ET.SubElement(document, "ТаблСчФакт")
        self.tovar_info(main_table)

        osn_pered = ET.SubElement(document, "СвПродПер")
        pered = ET.SubElement(osn_pered, "СвПер", {
            "ДатаПер": self.upd_date_russian,
            "СодОпер": "Товары переданы"
        })

        for osn in self.docs:
            ET.SubElement(pered, "ОснПер", osn)

        signer = ET.SubElement(document, "Подписант", {"СпосПодтПолном": "1"})
        if self.seller.get("ФИО"):
            fio_data = self.seller["ФИО"]
            ET.SubElement(signer, "ФИО", {
                "Имя": fio_data.get("Имя", ""),
                "Отчество": fio_data.get("Отчество", ""),
                "Фамилия": fio_data.get("Фамилия", "")
            })
        elif self.seller.get("Должн"):
            ET.SubElement(signer, "ЮЛ", {
                "Должн": self.seller["Должн"],
                "ИННЮЛ": self.seller.get("ИНН", ""),
                "НаимОрг": self.seller["НаимОрг"]
            })

        tree = ET.ElementTree(root)

        if path:
            tree.write(path, encoding="windows-1251", xml_declaration=True)
            return path
        else:
            # Возвращаем XML как байты в кодировке windows-1251
            import io
            output = io.BytesIO()
            # Используем метод write с правильной кодировкой
            ET.ElementTree(root).write(
                output,
                encoding="windows-1251",
                xml_declaration=True
            )
            return output.getvalue()


def parse_address(address_str):
    """Парсит адрес на компоненты для УПД"""
    import re
    result = {
        "КодРегион": "",
        "НаимРегион": "",
        "Район": None,
        "Город": None,
        "Индекс": "",
        "Улица": "",
        "Дом": "",
        "Корпус": None,
        "Кварт": None,
        "НаселПункт": None
    }
    
    if not address_str:
        return result
    
    # Ищем индекс (6 цифр)
    index_match = re.search(r'\b(\d{6})\b', address_str)
    if index_match:
        result["Индекс"] = index_match.group(1)
    
    # Разбиваем адрес по запятым
    parts = [p.strip() for p in address_str.split(",")]
    
    # Ищем регион (обычно содержит "область", "край", "республика" или код региона)
    region_match = re.search(r'(\d{2})\s*-\s*([^,]+)', address_str)
    if region_match:
        result["КодРегион"] = region_match.group(1)
        result["НаимРегион"] = region_match.group(2).strip()
    else:
        for part in parts:
            if any(word in part.lower() for word in ["область", "край", "республика", "автономный"]):
                result["НаимРегион"] = part
                # Пытаемся извлечь код региона из начала адреса
                code_match = re.search(r'^(\d{2})', address_str)
                if code_match:
                    result["КодРегион"] = code_match.group(1)
                break
    
    # Ищем город
    for part in parts:
        if re.match(r'^г\.?\s+', part, re.IGNORECASE) or "город" in part.lower():
            city = re.sub(r'^г\.?\s+', '', part, flags=re.IGNORECASE).strip()
            result["Город"] = city
            if not result["НаимРегион"]:
                result["НаимРегион"] = part
            break
    
    # Ищем район
    for part in parts:
        if "район" in part.lower() or "р-н" in part.lower():
            result["Район"] = part.strip()
            break
    
    # Ищем улицу
    street_patterns = [
        r'(ул\.?|улица|пр\.?|проспект|пер\.?|переулок|бул\.?|бульвар|ш\.?|шоссе|наб\.?|набережная)\s+([^,]+)',
        r'([^,]+)\s+(ул\.?|улица|пр\.?|проспект)',
    ]
    for pattern in street_patterns:
        street_match = re.search(pattern, address_str, re.IGNORECASE)
        if street_match:
            result["Улица"] = street_match.group(0).strip()
            break
    
    # Ищем дом, корпус, квартиру
    house_pattern = r'д\.?\s*(\d+)(?:\s*,\s*к\.?\s*(\d+))?(?:\s*,\s*кв\.?\s*(\d+))?'
    house_match = re.search(house_pattern, address_str, re.IGNORECASE)
    if house_match:
        result["Дом"] = house_match.group(1)
        if house_match.group(2):
            result["Корпус"] = house_match.group(2)
        if house_match.group(3):
            result["Кварт"] = house_match.group(3)
    else:
        # Простой поиск дома без корпуса и квартиры
        simple_house = re.search(r'д\.?\s*(\d+)', address_str, re.IGNORECASE)
        if simple_house:
            result["Дом"] = simple_house.group(1)
    
    # Если ничего не найдено, используем весь адрес как улицу
    if not result["Улица"] and not result["Дом"]:
        # Берем последнюю значимую часть как улицу
        for part in reversed(parts):
            if part and not any(word in part.lower() for word in ["область", "край", "республика", "город", "район"]):
                result["Улица"] = part
                break
    
    return result

