"""Financial and migration integration checks, isolated from user records."""
import hashlib
import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from datetime import date
import app as m
from test_support import configure_fixture
_original_init = m.init_db
def _fixture_init():
    _original_init()
    configure_fixture(m)
m.init_db = _fixture_init
import finance


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    m.DB_PATH = root / 'memberpay.db'
    m.PASSWORD_PATH = root / 'password.txt'
    m.BACKUP_DIR = root / 'backups'
    m.init_db()
    # Running migrations twice must preserve records and avoid duplicate triggers.
    m.init_db()
    client = m.app.test_client()
    for route in ('/collections','/reminders','/expenses','/activity','/members/1/statement','/members/1/statement.pdf'):
        assert client.get(route).status_code == 302
    client.post('/unlock',data={'password':'fixture-password-2026'})
    with m.app.app_context():
        m.execute("UPDATE settings SET value='2026-06-01' WHERE key='circle_start_date'")
        member = m.execute("INSERT INTO members(name,phone,join_date,registration_target,monthly_target) VALUES('Amina <Test>','0700000000','2026-06-01',20000,80000)")
        inactive = m.execute("INSERT INTO members(name,join_date,active,registration_target,monthly_target) VALUES('Inactive','2026-06-01',0,20000,80000)")
        reg = m.execute("INSERT INTO payments(member_id,amount,payment_type,paid_date) VALUES(?,20000,'registration','2026-06-01')",(member,))
        monthly = m.execute("INSERT INTO payments(member_id,amount,paid_date,notes) VALUES(?,200000,'2026-06-20','June, July and half August')",(member,))
        future = m.execute("INSERT INTO payments(member_id,amount,paid_date) VALUES(?,80000,'2027-01-01')",(member,))
        m.execute("INSERT INTO payments(member_id,amount,paid_date) VALUES(?,10000,'2026-08-10')",(inactive,))
        overview = finance.monthly_overview(date(2026,8,1),date(2026,8,31))
        assert (overview['expected'],overview['covered'],overview['remaining'],overview['cash']) == (80000,40000,40000,10000)
        assert finance.monthly_overview(date(2026,5,1),date(2026,5,31))['expected'] == 0
        assert finance.monthly_overview(date(2027,2,1),date(2026,10,1))['expected'] == 0
        assert len(finance.statement_data(member,date(2026,8,31))['payments']) == 2
        reminder = finance.reminder_text(finance.statement_data(member,date(2026,8,31)))
        assert 'UGX 40,000' in reminder
    assert client.get('/members/99999/statement').status_code == 404
    for route in ('/','/collections?month=2026-08','/reminders?as_of=2026-08-31','/expenses','/activity',f'/members/{member}/statement?as_of=2026-08-31'):
        response = client.get(route)
        assert response.status_code == 200, (route,response.status_code)
    assert b'Amina &lt;Test&gt;' in client.get('/reminders?as_of=2026-08-31').data
    expense_data = {'description':'Meeting supplies','category':'Supplies','amount':'30,000','spent_date':'2026-08-15','method':'Cash','payee':'Vendor','notes':'Paper and stationery'}
    assert client.post('/expenses/new',data=expense_data).status_code == 302
    with m.app.app_context():
        expense = m.query('SELECT id FROM expenses',one=True)['id']
        assert finance.funds(date(2026,8,31)) == {'received':230000,'spent':30000,'available':200000}
    assert client.post('/expenses/new',data={**expense_data,'amount':'-50'}).status_code == 200
    assert client.post('/expenses/new',data={**expense_data,'spent_date':'bad'}).status_code == 200
    client.post(f'/expenses/{expense}/edit',data={**expense_data,'amount':'40,000'})
    client.post(f'/expenses/{expense}/delete',data={})
    with m.app.app_context():
        assert m.query('SELECT amount FROM expenses WHERE id=?',(expense,),one=True)['amount'] == 40000
        audit = m.query("SELECT * FROM activity WHERE entity='expenses' AND action='Updated'",one=True)
        assert json.loads(audit['before_json'])['amount'] == 30000
        assert json.loads(audit['after_json'])['amount'] == 40000
    client.post(f'/expenses/{expense}/delete',data={'confirmation':'yes'})
    client.post(f'/payments/{monthly}/edit',data={'amount':'210000','payment_type':'monthly','paid_date':'2026-06-20','method':'Cash','notes':'Corrected'})
    client.post(f'/payments/{monthly}/delete')
    with m.app.app_context():
        audit = m.query("SELECT * FROM activity WHERE entity='payments' AND action='Deleted' AND record_id=?",(str(monthly),),one=True)
        assert json.loads(audit['before_json'])['amount'] == 210000
    # Restore and legacy import must retain audit history and remain compatible.
    client.post('/expenses/new',data=expense_data)
    backup = client.get('/backup').data
    client.post('/expenses/new',data={**expense_data,'description':'After backup'})
    def upload(action, payload):
        return client.post('/settings/database',data={'action':action,'confirmation':'yes','database':(io.BytesIO(payload),'memberpay.db')},follow_redirects=True)
    assert b'Backup restored' in upload('restore',backup).data
    with m.app.app_context():
        assert len(m.query('SELECT * FROM expenses')) == 1
        assert m.query("SELECT * FROM activity WHERE after_json LIKE '%After backup%'",one=True)
        assert m.query("SELECT * FROM activity WHERE action='Backup restored'",one=True)
    legacy = root / 'legacy.db'
    legacy.write_bytes(backup)
    with closing(sqlite3.connect(legacy)) as old:
        for row in old.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            old.execute(f'DROP TRIGGER {row[0]}')
        old.execute('DROP TABLE expenses')
        old.execute('DROP TABLE activity')
        old.commit()
    legacy_data = legacy.read_bytes()
    source_hash = hashlib.sha256(legacy_data).hexdigest()
    assert b'Backup restored' in upload('restore',legacy_data).data
    with m.app.app_context():
        assert not m.query('SELECT * FROM expenses')
        assert m.query("SELECT * FROM activity WHERE action='Backup restored'",one=True)
    client.post('/settings',data={'action':'clear','confirmation':'yes'})
    assert b'Imported' in upload('import',legacy_data).data
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == source_hash
    with m.app.app_context():
        assert m.query('SELECT id FROM members WHERE id=?',(member,),one=True)
        assert m.query("SELECT * FROM activity WHERE action='Database imported'",one=True)
        # Long multi-page statement exercises page breaks and wrapping.
        for index in range(70):
            m.execute("INSERT INTO payments(member_id,amount,paid_date,notes) VALUES(?,1000,'2026-08-15',?)",(member, 'Payment note ' + str(index) + ': ' + 'Contribution confirmed. '*6))
        summary = finance.statement_data(member,date(2026,8,31))
        payload = finance.statement_pdf(summary)
        assert payload.startswith(b'%PDF-')
        destination = Path('tmp/pdfs')
        destination.mkdir(parents=True,exist_ok=True)
        (destination / 'statement-qa.pdf').write_bytes(payload)
        (destination / 'expected.txt').write_text(str(summary['paid']),encoding='utf-8')
    assert client.get(f'/members/{member}/statement.pdf?as_of=2026-08-31').data.startswith(b'%PDF-')
    assert client.get('/activity?page=2').status_code == 200
    assert client.get('/activity?entity=payments').status_code == 200
    client.post('/settings',data={'action':'clear','confirmation':'yes'})
    with m.app.app_context():
        assert not m.query('SELECT * FROM members') and not m.query('SELECT * FROM payments') and not m.query('SELECT * FROM expenses')
        assert m.query("SELECT * FROM activity WHERE action='Workspace cleared'",one=True)
        assert 'fixture-password-2026' not in str([tuple(r) for r in m.query('SELECT * FROM activity')])
print('FINANCE INTEGRATION CHECKS PASSED')
