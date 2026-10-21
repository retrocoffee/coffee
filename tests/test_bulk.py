import copy
import csv
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bot.bulk import HEADERS, apply_csv, export_csv
from support import sample_menu as load
from bot.telegram_bot import Bot, TelegramError


def csv_data(rows):
    output=io.StringIO(newline='')
    writer=csv.writer(output, delimiter=';')
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return output.getvalue()


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.menu=load()
        self.section=self.menu['sections'][0]
        self.group=self.section['groups'][0]
        self.item=self.group['items'][0]

    def row(self, iid=None, name=None, price='999', description=''):
        return [self.section['name'], self.group['caption'], self.item['id'] if iid is None else iid,
                self.item['name'] if name is None else name, price, description]

    def test_export_import_is_lossless(self):
        for mode in ['items','prices']:
            draft, _, changes=apply_csv(self.menu,export_csv(self.menu),mode)
            self.assertEqual(self.menu,draft)
            self.assertEqual(changes,[])

    def test_price_only_preserves_name_description_and_unlisted_rows(self):
        original=copy.deepcopy(self.menu)
        draft, summary, changes=apply_csv(self.menu,csv_data([self.row(name='Ignored',description='Ignored')]),'prices')
        original['sections'][0]['groups'][0]['items'][0]['prices']=['999 ₽']
        self.assertEqual(original,draft)
        self.assertNotEqual(self.menu,draft)
        self.assertEqual(len(changes),1)
        self.assertIn('Изменено: 1',summary)

    def test_upsert_and_rename_by_id(self):
        rows=[self.row(name='Новое название',description='Новое описание'),self.row(iid='',name='Новая позиция',price='100')]
        draft, summary, _=apply_csv(self.menu,csv_data(rows))
        items=draft['sections'][0]['groups'][0]['items']
        self.assertEqual(items[0]['name'],'Новое название')
        self.assertEqual(items[0]['notes'],['Новое описание'])
        self.assertEqual(items[-1]['name'],'Новая позиция')
        self.assertIn('Добавлено: 1',summary)

    def test_blank_id_matches_existing_name(self):
        draft,_,_=apply_csv(self.menu,csv_data([self.row(iid='')]))
        self.assertEqual(len(draft['sections'][0]['groups'][0]['items']),len(self.group['items']))

    def test_blank_description_preserves_and_dash_clears(self):
        for description,expected in [('',self.item['notes']),('-',[])]:
            draft,_,_=apply_csv(self.menu,csv_data([self.row(description=description)]))
            self.assertEqual(draft['sections'][0]['groups'][0]['items'][0]['notes'],expected)

    def test_invalid_second_row_is_atomic(self):
        original=copy.deepcopy(self.menu)
        with self.assertRaisesRegex(ValueError,'Строка 3'):
            apply_csv(self.menu,csv_data([self.row(),self.row(iid='',name='Новая',price='NaN')]))
        self.assertEqual(self.menu,original)

    def test_duplicate_rows_rejected(self):
        for rows in [[self.row(),self.row()], [self.row(iid='',name='New'),self.row(iid='',name='New')]]:
            with self.assertRaisesRegex(ValueError,'несколько раз'):
                apply_csv(self.menu,csv_data(rows))

    def test_unknown_id_or_target_rejected(self):
        row=self.row()
        row[0]='Unknown section'
        for rows in [[row],[self.row(iid='unknown')]]:
            with self.assertRaises(ValueError): apply_csv(self.menu,csv_data(rows))

    def test_prices_cannot_add_and_scope_cannot_escape(self):
        with self.assertRaises(ValueError): apply_csv(self.menu,csv_data([self.row(iid='',name='New')]),'prices')
        with self.assertRaises(ValueError): apply_csv(self.menu,csv_data([self.row()]),scope=('coffee','g1'))

    def test_multiple_volumes_and_multiline_csv(self):
        scope=('coffee','g1')
        section=next(s for s in self.menu['sections'] if s['id']=='coffee')
        group=section['groups'][0]
        item=group['items'][0]
        value=' | '.join(['333']*max(1,len(group['columns'])))
        row=[section['name'],group['caption'],item['id'],'Название; с кавычкой "',value,'Строка 1\nСтрока 2']
        draft,_,_=apply_csv(self.menu,csv_data([row]),scope=scope)
        result=next(s for s in draft['sections'] if s['id']=='coffee')['groups'][0]['items'][0]
        self.assertEqual(result['notes'],['Строка 1','Строка 2'])
        self.assertEqual(result['prices'],['333 ₽']*max(1,len(group['columns'])))

    def test_wrong_header_empty_or_bad_price_count(self):
        for text in ['name;price\nCoffee;100',csv_data([]),csv_data([self.row(price='100 | 200')])]:
            with self.assertRaises(ValueError): apply_csv(self.menu,text)

    def test_empty_group_template_does_not_create_a_sample_item(self):
        self.menu['sections'][-1]['groups'][0]['items'] = []
        draft, _, changes = apply_csv(self.menu, export_csv(self.menu))
        self.assertEqual(draft, self.menu)
        self.assertEqual(changes, [])

    def test_file_size_limit(self):
        with self.assertRaisesRegex(ValueError, '512 КБ'):
            apply_csv(self.menu, 'x' * (512 * 1024 + 1))


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test',TELEGRAM_ADMIN_IDS='123'):
            self.bot=Bot()
        self.bot.message_file=Path(self.tmp.name)/'messages.json'
        self.bot.messages={}
        self.calls=[]
        def api(method,payload):
            self.calls.append((method,payload))
            return {'message_id':42}
        self.bot.api=api
        self.bot.sessions[123]={'menu':load(),'base':'test'}

    def test_navigation_edits_one_message(self):
        self.bot.send(123,'First',[('Next',('home',))])
        self.bot.send(123,'Second',[('Back',('home',))])
        self.assertEqual([method for method,_ in self.calls],['sendMessage','editMessageText'])
        self.assertEqual(self.calls[-1][1]['message_id'],42)

    def test_deleted_screen_gets_replacement(self):
        self.bot.messages={'123':10}
        def api(method,payload):
            self.calls.append((method,payload))
            if method=='editMessageText': raise TelegramError(400)
            return {'message_id':42}
        self.bot.api=api
        self.bot.send(123,'New screen')
        self.assertEqual([m for m,_ in self.calls],['editMessageText','sendMessage','deleteMessage'])
        self.assertEqual(self.bot.messages['123'],42)

    def test_import_requires_confirmation_and_preview_preserves_draft(self):
        self.bot.bulk_start(123,'items')
        menu=self.bot.sessions[123]['menu']
        row=[menu['sections'][0]['name'],menu['sections'][0]['groups'][0]['caption'],'','Новая позиция','123','']
        self.bot.input(123,csv_data([row]))
        self.assertNotEqual(self.bot.sessions[123]['menu'],self.bot.sessions[123]['draft'][0])
        draft=copy.deepcopy(self.bot.sessions[123]['draft'])
        self.bot.action(123,('bulk-preview',0),1)
        self.bot.action(123,('bulk-confirm',),2)
        self.assertEqual(draft,self.bot.sessions[123]['draft'])

    def test_unauthorized_file_never_downloaded(self):
        with patch.object(self.bot,'read_document') as download:
            self.bot.handle({'update_id':1,'message':{'from':{'id':999},'chat':{'id':999,'type':'private'},'document':{'file_id':'x'}}})
            download.assert_not_called()

    def test_csv_template_disappears_on_next_screen(self):
        self.bot.documents[123]=[33]
        self.bot.send(123,'Next')
        self.assertIn(('deleteMessage',{'chat_id':123,'message_id':33}),self.calls)
