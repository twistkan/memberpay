"""Synthetic configured fixture for the legacy regression checks."""
def configure_fixture(m):
    import group_config
    with m.app.app_context():
        m.APP_DIR = m.DB_PATH.parent
        defaults = {'setup_complete':'1','circle_name':'Test Circle','circle_start_date':'2026-06-01','dues_policy':'shared','default_registration':'20000','default_monthly':'80000','currency':'UGX'}
        with m.get_db() as db:
            for key, value in defaults.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(key,value))
        if not (m.APP_DIR/'credentials.json').exists():
            group_config.save_password('fixture-password-2026','fixture-password-2026')
