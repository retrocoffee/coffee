"""Atomic CSV import/export for the Telegram menu editor."""
import copy
import csv
import io
import secrets
from bot.menu import prices, validate

HEADERS = ['раздел', 'группа', 'id', 'название', 'цены', 'описание']
MAX_BYTES = 512 * 1024
MAX_ROWS = 1000


def normalized(value):
    return ' '.join(value.split()).casefold()


def scoped_groups(menu, scope=None):
    for section in menu['sections']:
        for group in section['groups']:
            if scope is None or (section['id'], group['id']) == tuple(scope):
                yield section, group


def export_csv(menu, scope=None):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';', lineterminator='\r\n')
    writer.writerow(HEADERS)
    for section, group in scoped_groups(menu, scope):
        for item in group['items']:
            writer.writerow([section['name'], group['caption'], item['id'], item['name'],
                             ' | '.join(item['prices']), '\n'.join(item['notes'])])
        # Keep the target available without turning a sample into a real menu item.
        if not group['items']:
            writer.writerow([section['name'], group['caption'], '', '', '', ''])
    return '\ufeff' + stream.getvalue()


def apply_csv(menu, raw, mode='items', scope=None):
    """Return a complete draft or raise; never mutate the original menu."""
    if mode not in ('items', 'prices'):
        raise ValueError('Неизвестный режим загрузки.')
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Файл слишком большой: максимум 512 КБ.')
    raw = raw.lstrip('\ufeff')
    reader = None
    for delimiter in (';', '\t', ','):
        candidate = csv.DictReader(io.StringIO(raw, newline=''), delimiter=delimiter, strict=True)
        try:
            headers = candidate.fieldnames
        except csv.Error:
            continue
        if headers and [v.strip().lower() for v in headers] == HEADERS:
            candidate.fieldnames = HEADERS
            reader = candidate
            break
    if reader is None:
        raise ValueError('Заголовок не соответствует шаблону: ' + ';'.join(HEADERS))
    draft = copy.deepcopy(menu)
    groups = list(scoped_groups(draft, scope))
    changes, seen = [], set()
    added = updated = unchanged = total = 0
    try:
        for row in reader:
            if all(v is None or not str(v).strip() for v in row.values()):
                continue
            if None not in row and all(row.get(key) is not None for key in HEADERS) and not any(row[key].strip() for key in HEADERS[2:]):
                continue  # An empty-group template row has no item yet.
            total += 1
            line = reader.line_num
            if total > MAX_ROWS:
                raise ValueError(f'Не более {MAX_ROWS} позиций за загрузку.')
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('Неверное количество полей. Используйте CSV из бота.')
                row = {key: value.strip() for key, value in row.items()}
                targets = [(s,g) for s,g in groups if normalized(s['name']) == normalized(row['раздел'])
                           and normalized(g['caption']) == normalized(row['группа'])]
                if len(targets) != 1:
                    raise ValueError('Раздел/группа не найдены или название неоднозначно. Скачайте свежий шаблон.')
                section, group = targets[0]
                if not row['название']:
                    raise ValueError('Укажите название позиции.')
                if row['id']:
                    matches = [i for i in group['items'] if i['id'] == row['id']]
                    if not matches:
                        raise ValueError('ID позиции не найден. Для новой позиции оставьте id пустым.')
                else:
                    matches = [i for i in group['items'] if normalized(i['name']) == normalized(row['название'])]
                    if len(matches) > 1:
                        raise ValueError('Несколько позиций с таким названием. Укажите id из шаблона.')
                item = matches[0] if matches else None
                if mode == 'prices' and item is None:
                    raise ValueError('В режиме цен нельзя создавать позиции. Выберите «Позиции и цены».')
                identity = (section['id'], group['id'], item['id'] if item else normalized(row['название']))
                if identity in seen:
                    raise ValueError('Одна позиция указана несколько раз.')
                seen.add(identity)
                new_prices = prices(row['цены'].replace('|', ';'), max(1, len(group['columns'])))
                before = copy.deepcopy(item)
                if item is None:
                    item = {'id': secrets.token_hex(6), 'name': row['название'], 'notes': [], 'prices': new_prices}
                    group['items'].append(item)
                    added += 1
                item['prices'] = new_prices
                if mode == 'items':
                    item['name'] = row['название']
                    # Empty description preserves existing notes. '-' explicitly clears them.
                    if row['описание']:
                        item['notes'] = [] if row['описание'] == '-' else row['описание'].splitlines()
                validate({'version': 1, 'sections': [section]})
                if before == item:
                    unchanged += 1
                    continue
                if before is not None:
                    updated += 1
                volumes = group['columns'] or ['Цена']
                old = '; '.join(f'{v}: {p}' for v,p in zip(volumes, before['prices'])) if before else 'новая позиция'
                new = '; '.join(f'{v}: {p}' for v,p in zip(volumes, item['prices']))
                detail = f'{section["name"]} / {group["caption"]}\n'
                detail += (f'{before["name"]} → {item["name"]}' if before and before['name'] != item['name'] else item['name'])
                detail += f'\n{old} → {new}'
                if before and before['notes'] != item['notes']:
                    detail += '\nОписание изменено.'
                changes.append(detail)
                # Prevent a blank-ID duplicate following a newly created row.
                seen.add((section['id'], group['id'], item['id']))
            except ValueError as error:
                raise ValueError(f'Строка {line}: {error}') from None
    except csv.Error:
        raise ValueError('Неверный CSV: проверьте кавычки и разделители.') from None
    if not total:
        raise ValueError('Файл не содержит позиций.')
    validate(draft)
    summary = f'Добавлено: {added}. Изменено: {updated}. Без изменений: {unchanged}.'
    return draft, summary, changes
