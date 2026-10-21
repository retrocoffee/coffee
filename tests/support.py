"""Independent editor fixture: production menu edits must not break bot startup."""
def sample_menu():
    sections = []
    for ident, name in [('seasonal', 'Сезонное меню'), ('coffee', 'Кофе'), ('not-coffee', 'Не кофе'),
                        ('iced-milk', 'Холодное с молоком'), ('cold-drinks', 'Очень холодные напитки'), ('bubble', 'Бабл напитки')]:
        columns = ['200 мл', '300 мл'] if ident == 'coffee' else ['300 мл']
        sections.append({'id': ident, 'name': name, 'hidden': False, 'groups': [{
            'id': 'g1', 'kind': 'drinks', 'heading': '', 'caption': name, 'columns': columns,
            'items': [{'id': 'i1', 'name': 'Первый напиток', 'notes': ['Описание'], 'prices': ['100 ₽'] * len(columns)},
                      {'id': 'i2', 'name': 'Второй напиток', 'notes': [], 'prices': ['200 ₽'] * len(columns)}]}]})
    return {'version': 1, 'sections': sections}
