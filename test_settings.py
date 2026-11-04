"""Integration checks use temporary databases only."""
import io
import sqlite3
import tempfile
from pathlib import Path
import app as m
from test_support import configure_fixture
_original_init = m.init_db
def _fixture_init():
    _original_init()
    configure_fixture(m)
m.init_db = _fixture_init

with tempfile.TemporaryDirectory() as root:
    root = Path(root)
    m.DB_PATH = root / 'memberpay.db'
    m.PASSWORD_PATH = root / 'password.txt'
    m.BACKUP_DIR = root / 'backups'
    m.init_db()
    client = m.app.test_client()
    assert client.get('/settings').status_code == 302
    client.post('/unlock', data={'password': 'fixture-password-2026'})
    assert client.get('/settings').status_code == 200
    def post(**data):
        return client.post('/settings', data=data, follow_redirects=True)
    assert b'Group settings saved' in post(action='circle', circle_name='Test Circle', circle_start_date='2026-01-01', default_registration='20000', default_monthly='80000', dues_policy='shared', currency='UGX').data
    post(action='circle', circle_name='Bad', circle_start_date='invalid')
    with m.app.app_context():
        assert m.setting('circle_name') == 'Test Circle'
        assert m.setting('circle_start_date') == '2026-01-01'
    post(action='password', current_password='wrong', new_password='changed123', confirm_password='changed123')
    assert m.password_matches('fixture-password-2026')
    post(action='password', current_password='fixture-password-2026', new_password='changed123', confirm_password='changed123')
    client.get('/logout')
    client.post('/unlock', data={'password': 'fixture-password-2026'})
    assert client.get('/settings').status_code == 302
    client.post('/unlock', data={'password': 'changed123'})
    post(action='demo')
    with m.app.app_context():
        assert not m.query('SELECT * FROM members')
    post(action='demo', confirmation='yes')
    with m.app.app_context():
        assert len(m.query('SELECT * FROM members')) == 3
        payment_id = m.query('SELECT id FROM payments', one=True)['id']
    pdf = client.get(f'/receipts/{payment_id}/pdf')
    assert pdf.data.startswith(b'%PDF-')
    post(action='folder', receipt_folder=str(root))
    assert client.get(f'/receipts/{payment_id}/pdf').status_code == 302
    assert (root / f'receipt-{payment_id:06d}.pdf').read_bytes().startswith(b'%PDF-')
    backup = client.get('/backup').data
    post(action='clear', confirmation='yes')
    assert m.password_matches('changed123')
    with m.app.app_context():
        assert not m.query('SELECT * FROM members')
        assert m.setting('circle_name') == 'Test Circle'
    def upload(action, payload):
        return client.post('/settings/database', data={'action': action, 'confirmation': 'yes', 'database': (io.BytesIO(payload), 'memberpay.db')}, follow_redirects=True)
    assert b'Backup restored' in upload('restore', backup).data
    assert b'IDs overlap' in upload('import', backup).data
    upload('restore', b'broken database')
    with m.app.app_context():
        assert len(m.query('SELECT * FROM members')) == 3
    post(action='clear', confirmation='yes')
    assert b'Imported 3 members and 5 payments' in upload('import', backup).data
    for _ in range(10):
        m.settings_tools.automatic_backup()
    assert len(list(m.BACKUP_DIR.glob('auto-*.db'))) == 7
    assert list(m.BACKUP_DIR.glob('before-restore-*.db'))
    # Native dialog hooks: cancellation and explicit save paths.
    m.app.config['SAVE_RECEIPT'] = lambda name: None
    post(action='reset_folder')
    assert client.get(f'/receipts/{payment_id}/pdf').status_code == 302
    m.app.config['SAVE_RECEIPT'] = lambda name: root / name
    assert client.get(f'/receipts/{payment_id}/pdf').status_code == 302
    m.app.config['SAVE_BACKUP'] = lambda name: root / name
    assert client.get('/backup').status_code == 302
    check = m.settings_tools.validate_database(root / 'memberpay-backup.db')
    check.close()
print('SETTINGS INTEGRATION CHECKS PASSED')
