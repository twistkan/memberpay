"""First-run configuration and credentials for independent local group workspaces."""
import base64
import io
import json
from datetime import date
from pathlib import Path
from flask import request, redirect, url_for, render_template, session, flash, send_file, abort
from werkzeug.security import generate_password_hash
import app as m


def save_password(password, confirmation):
    if len(password) < 10 or password != password.strip() or '\n' in password or '\r' in password:
        raise ValueError('Use a password of at least 10 characters, without outside spaces or line breaks.')
    if password != confirmation:
        raise ValueError('The passwords do not match.')
    target = m.APP_DIR / 'credentials.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps({'password_hash': generate_password_hash(password)}), encoding='utf8')
    temp.replace(target)


def logo_bytes():
    value = m.setting('logo_base64')
    return base64.b64decode(value, validate=True) if value else None


def save_settings(form, upload=None, first_run=False):
    if m.setting('setup_complete') != '1' and any(not form.get(key) for key in ('circle_name','circle_start_date','default_registration','default_monthly','dues_policy','currency')):
        raise ValueError('Complete all required group settings before starting.')
    name = form.get('circle_name', '').strip()
    if not name or len(name) > 120:
        raise ValueError('Enter a group name of 1–120 characters.')
    start = form.get('circle_start_date', '')
    date.fromisoformat(start)
    values = {'circle_name': name, 'circle_start_date': start}
    for key, minimum in [('default_registration', 0), ('default_monthly', 1)]:
        raw = form.get(key, m.setting(key, str(minimum)))
        if not raw.isascii() or not raw.isdigit() or not minimum <= int(raw) <= 10**12:
            raise ValueError('Enter valid whole-shilling amounts; monthly contributions must exceed zero.')
        values[key] = str(int(raw))
    policy = form.get('dues_policy', m.setting('dues_policy', 'shared'))
    if policy not in ('shared', 'joining'):
        raise ValueError('Choose when monthly dues begin.')
    if form.get('currency', 'UGX') != 'UGX':
        raise ValueError('This edition supports UGX, in whole shillings.')
    values.update(dues_policy=policy, currency='UGX', group_motto=form.get('group_motto', '').strip()[:200])
    locked = m.query('SELECT id FROM members LIMIT 1', one=True)
    if locked:
        for key in ('circle_start_date', 'dues_policy'):
            if values[key] != m.setting(key, 'shared' if key == 'dues_policy' else ''):
                raise ValueError('The start date and dues policy are locked after members are added to preserve balances.')
    if upload and upload.filename:
        from PIL import Image
        raw = upload.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Choose a PNG or JPEG logo under 2 MB.')
        try:
            image = Image.open(io.BytesIO(raw))
            if image.format not in ('PNG', 'JPEG') or image.width * image.height > 16_000_000:
                raise ValueError('Choose a PNG or JPEG logo under 16 megapixels.')
            image.load()
            image.thumbnail((512, 512))
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            values['logo_base64'] = base64.b64encode(output.getvalue()).decode('ascii')
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError('The selected logo is not a supported image.') from exc
    elif form.get('remove_logo') == 'yes':
        values['logo_base64'] = ''
    if first_run:
        values['setup_complete'] = '1'
    else:
        import settings_tools
        settings_tools.snapshot('before-settings')
    with m.get_db() as db:
        for key, value in values.items():
            db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def fill_legacy_settings(db):
    """Complete configuration only after an explicit, authenticated restore."""
    first = db.execute('SELECT registration_target,monthly_target FROM members ORDER BY id LIMIT 1').fetchone()
    defaults = {'setup_complete': '1', 'circle_name': 'Imported group', 'dues_policy': 'shared', 'currency': 'UGX',
                'default_registration': str(first[0] if first else 0), 'default_monthly': str(first[1] if first else 1)}
    for key, value in defaults.items():
        db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, value))


def register(app):
    @app.before_request
    def require_setup():
        # Reject cross-origin writes to this loopback application.
        if request.host.split(':')[0] not in ('localhost', '127.0.0.1'):
            abort(400)
        origin = request.headers.get('Origin')
        if request.method == 'POST' and origin and origin != request.host_url.rstrip('/'):
            abort(403)
        if request.endpoint not in ('onboarding', 'static', 'health', 'group_logo') and m.setting('setup_complete') != '1':
            session.clear()
            return redirect(url_for('onboarding'))

    @app.route('/setup', methods=['GET', 'POST'])
    def onboarding():
        if m.setting('setup_complete') == '1':
            return redirect(url_for('dashboard' if session.get('unlocked') else 'unlock'))
        if request.method == 'POST':
            try:
                if request.form.get('reviewed') != 'yes':
                    raise ValueError('Review and confirm your group settings before continuing.')
                # Validate fields before persisting credentials. Failed validation rolls
                # back the surrounding transaction and setup remains unavailable.
                password = request.form.get('new_password', '')
                confirmation = request.form.get('confirm_password', '')
                if len(password) < 10 or password != confirmation or password != password.strip() or '\n' in password or '\r' in password:
                    raise ValueError('Choose matching passwords of at least 10 characters, without outside spaces or line breaks.')
                save_settings(request.form, request.files.get('logo'))
                save_password(password, confirmation)
                m.execute("INSERT INTO settings VALUES('setup_complete','1') ON CONFLICT(key) DO UPDATE SET value='1'")
                session.clear()
                session['unlocked'] = True
                return redirect(url_for('dashboard'))
            except (ValueError, OSError) as exc:
                flash(str(exc), 'danger')
        return render_template('setup.html')

    @app.route('/group-logo')
    def group_logo():
        raw = logo_bytes()
        if raw:
            return send_file(io.BytesIO(raw), mimetype='image/png', max_age=0)
        return send_file(Path(app.static_folder) / 'logo.svg', mimetype='image/svg+xml', max_age=0)
