"""Regression checks for two independent groups and first-run safeguards."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from datetime import date
from PIL import Image
import app as m
import group_config


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.select('a')
    def tearDown(self):
        self.temp.cleanup()
    def select(self,name):
        m.APP_DIR=self.root/name;m.DB_PATH=m.APP_DIR/'memberpay.db'
        m.PASSWORD_PATH=m.APP_DIR/'password.txt';m.BACKUP_DIR=m.APP_DIR/'backups'
        m.init_db();self.client=m.app.test_client()
    def setup_group(self,name='Sunrise Group',policy='joining',monthly='50000',**extra):
        form=dict(circle_name=name,group_motto='Growing together',circle_start_date='2026-06-01',default_registration='5000',default_monthly=monthly,dues_policy=policy,currency='UGX',new_password='synthetic-password-123',confirm_password='synthetic-password-123',reviewed='yes')
        form.update(extra)
        return self.client.post('/setup',data=form)
    def test_setup_required_and_no_shared_credentials(self):
        self.assertEqual(self.client.get('/members/new').location,'/setup')
        self.assertFalse((m.APP_DIR/'credentials.json').exists())
        self.assertFalse(m.PASSWORD_PATH.exists())
        self.setup_group(default_monthly='0')
        with m.app.app_context():self.assertNotEqual(m.setting('setup_complete'),'1')
        self.assertFalse((m.APP_DIR/'credentials.json').exists())
        self.setup_group(reviewed='no')
        self.assertFalse((m.APP_DIR/'credentials.json').exists())
        self.setup_group(default_monthly='')
        self.assertFalse((m.APP_DIR/'credentials.json').exists())
        self.assertEqual(self.setup_group().status_code,302)
        credentials=(m.APP_DIR/'credentials.json').read_text()
        self.assertNotIn('synthetic-password-123',credentials)
        self.assertTrue(m.password_matches('synthetic-password-123'))
        self.assertFalse(m.password_matches('incorrect'))
        self.assertEqual(self.client.get('/setup').location,'/')
    def test_independent_groups_and_no_inherited_records(self):
        self.setup_group()
        self.client.post('/members/new',data={'name':'Group A member','join_date':'2026-08-01'})
        first_db=m.DB_PATH
        self.select('b')
        self.setup_group(name='Lakeside Group',monthly='10000',policy='shared')
        with m.app.app_context():
            self.assertEqual(m.setting('circle_name'),'Lakeside Group')
            self.assertEqual(m.query('SELECT COUNT(*) n FROM members',one=True)['n'],0)
        self.assertNotIn(b'Sunrise Group',self.client.get('/').data)
        with closing(sqlite3.connect(first_db)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM members').fetchone()[0],1)
    def test_joining_policy_defaults_and_historical_protection(self):
        self.setup_group()
        self.client.post('/members/new',data={'name':'New Member','join_date':'2026-08-01'})
        with m.app.app_context():
            member=m.query('SELECT * FROM members',one=True)
            self.assertEqual(member['monthly_target'],50000)
            self.assertEqual(m.member_summary(member,date(2026,8,31))['monthly']['owed'],50000)
            import finance
            self.assertEqual(finance.monthly_overview(date(2026,7,1),date(2026,7,31))['expected'],0)
        self.client.post('/settings',data={'action':'circle','circle_name':'Sunrise Group','circle_start_date':'2026-01-01','dues_policy':'shared','default_registration':'0','default_monthly':'90000'})
        with m.app.app_context():self.assertEqual(m.setting('circle_start_date'),'2026-06-01')
        self.client.post('/settings',data={'action':'circle','circle_name':'Sunrise Group','circle_start_date':'2026-06-01','dues_policy':'joining','default_registration':'0','default_monthly':'90000'})
        self.client.post('/members/new',data={'name':'Second Member','join_date':'2026-08-01'})
        with m.app.app_context():
            members=m.query('SELECT * FROM members ORDER BY id')
            self.assertEqual([r['monthly_target'] for r in members],[50000,90000])
        self.client.post('/payments/new',data={'member_id':str(member['id']),'amount':'50000','paid_date':'2026-08-01','payment_type':'monthly'})
        self.client.post(f"/members/{member['id']}/edit",data={'name':'New Member','phone':'','join_date':'2026-01-01','registration_target':'5000','monthly_target':'100000','active':'on'})
        with m.app.app_context():self.assertEqual(m.query('SELECT monthly_target FROM members WHERE id=?',(member['id'],),one=True)['monthly_target'],50000)
    def test_logo_backup_and_restore_preserves_credentials(self):
        output=io.BytesIO();Image.new('RGB',(20,20),'blue').save(output,format='PNG');output.seek(0)
        self.setup_group(logo=(output,'group.png'))
        self.assertEqual(self.client.get('/group-logo').mimetype,'image/png')
        backup=self.client.get('/backup').data
        credentials=(m.APP_DIR/'credentials.json').read_bytes()
        self.client.post('/settings',data={'action':'circle','circle_name':'Changed','circle_start_date':'2026-06-01','dues_policy':'joining','default_registration':'0','default_monthly':'1','remove_logo':'yes'})
        response=self.client.post('/settings/database',data={'action':'restore','confirmation':'yes','database':(io.BytesIO(backup),'backup.db')},follow_redirects=True)
        self.assertIn(b'Backup restored',response.data)
        self.assertEqual(self.client.get('/group-logo').mimetype,'image/png')
        self.assertEqual((m.APP_DIR/'credentials.json').read_bytes(),credentials)
        self.assertTrue(list(m.BACKUP_DIR.glob('before-restore-*.db')))
    def test_cross_origin_changes_rejected(self):
        self.assertEqual(self.client.post('/setup',headers={'Origin':'https://example.com'}).status_code,403)


if __name__=='__main__':unittest.main()
