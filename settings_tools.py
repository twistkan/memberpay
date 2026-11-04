"""Settings and data maintenance for the local MemberPay app."""
import io
import os
import secrets
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from datetime import date, datetime
from flask import request, render_template, redirect, url_for, flash, send_file
import app as m
import finance


def snapshot(prefix="manual"):
    m.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = m.BACKUP_DIR / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    with closing(sqlite3.connect(m.DB_PATH)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    return target


def automatic_backup():
    snapshot("auto")
    for old in sorted(m.BACKUP_DIR.glob("auto-*.db"), reverse=True)[7:]:
        old.unlink()


def validate_database(path):
    db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The database is damaged.")
        required = {
            "members": {"id", "name", "phone", "join_date", "registration_target", "monthly_target", "active", "created_at"},
            "payments": {"id", "member_id", "amount", "payment_type", "paid_date", "method", "notes", "created_at"},
            "settings": {"key", "value"},
        }
        for table, columns in required.items():
            found = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not columns <= found:
                raise ValueError("Choose a compatible MemberPay database or backup.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("The database contains payments without valid members.")
        for row in db.execute("SELECT join_date, registration_target, monthly_target FROM members"):
            date.fromisoformat(row[0])
            if row[1] < 0 or row[2] <= 0:
                raise ValueError("The database contains invalid contribution amounts.")
        for row in db.execute("SELECT paid_date, amount, payment_type FROM payments"):
            date.fromisoformat(row[0])
            if row[1] <= 0 or row[2] not in ("monthly", "registration"):
                raise ValueError("The database contains invalid payments.")
        start = db.execute("SELECT value FROM settings WHERE key='circle_start_date'").fetchone()
        if start:
            date.fromisoformat(start[0])
        if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expenses'").fetchone():
            for spent, amount in db.execute('SELECT spent_date,amount FROM expenses'):
                date.fromisoformat(spent)
                if not isinstance(amount, int) or amount <= 0:
                    raise ValueError('The backup contains an invalid expense.')
        return db
    except Exception:
        db.close()
        raise


def register(app):
    @app.route("/settings", methods=["GET", "POST"])
    @m.login_required
    def settings():
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "circle":
                    import group_config
                    group_config.save_settings(request.form, request.files.get('logo'))
                    flash("Group settings saved. New defaults apply only to new members.", "success")
                elif action == "password":
                    if not m.password_matches(request.form.get("current_password", "")):
                        raise ValueError("The current password is incorrect.")
                    import group_config
                    group_config.save_password(request.form.get('new_password', ''), request.form.get('confirm_password', ''))
                    finance.log_event('Password changed', 'App password changed. Credentials are never recorded in history.')
                    flash("Password changed.", "success")
                elif action == "folder":
                    chooser = app.config.get("CHOOSE_FOLDER")
                    folder = chooser() if chooser else request.form.get("receipt_folder", "").strip()
                    if folder:
                        path = Path(folder)
                        if not path.is_absolute() or not path.is_dir():
                            raise ValueError("Choose an existing folder using its full path.")
                        with tempfile.TemporaryFile(dir=path):
                            pass
                        m.execute("INSERT INTO settings VALUES('receipt_folder',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(path),))
                        flash("Receipt folder saved.", "success")
                elif action == "reset_folder":
                    m.execute("DELETE FROM settings WHERE key='receipt_folder'")
                    flash("Receipts will ask where to save each time.", "success")
                elif action == "open_folder":
                    folder = Path(m.setting("receipt_folder"))
                    if not m.setting("receipt_folder") or not folder.is_dir():
                        raise ValueError("Choose a receipt folder first.")
                    os.startfile(str(folder))
                elif action in ("clear", "demo"):
                    if request.form.get("confirmation") != "yes":
                        raise ValueError("Please confirm this action.")
                    if action == "demo" and (m.query("SELECT id FROM members LIMIT 1", one=True) or m.query('SELECT id FROM expenses LIMIT 1', one=True)):
                        raise ValueError("Demo data can only be loaded into an empty workspace. Back up and clear first.")
                    snapshot("before-" + action)
                    with m.get_db() as db:
                        db.execute("DELETE FROM payments")
                        db.execute("DELETE FROM members")
                        db.execute('DELETE FROM expenses')
                        if action == "demo":
                            start = m.setting("circle_start_date", date.today().isoformat())
                            for name, months in (("Demo · Amina Nakato", 1), ("Demo · David Okello", 2), ("Demo · Grace Namusoke", 0)):
                                paid = months * int(m.setting("default_monthly", "1"))
                                member = db.execute("INSERT INTO members(name,join_date,registration_target,monthly_target) VALUES(?,?,?,?)", (name, start, int(m.setting("default_registration", "0")), int(m.setting("default_monthly", "1")))).lastrowid
                                registration = int(m.setting("default_registration", "0"))
                                if registration:
                                    db.execute("INSERT INTO payments(member_id,amount,payment_type,paid_date) VALUES(?,?,'registration',?)", (member, registration, start))
                                if paid:
                                    db.execute("INSERT INTO payments(member_id,amount,paid_date) VALUES(?,?,?)", (member, paid, start))
                        finance.log_event('Demo loaded' if action == 'demo' else 'Workspace cleared', 'Safety backup saved. Circle details and password kept.', db)
                    flash("Demo data loaded." if action == "demo" else "Members, payments and expenses cleared. Circle details and password kept. Activity history retained.", "success")
                else:
                    raise ValueError("Unknown settings action.")
            except (ValueError, OSError, sqlite3.Error) as exc:
                flash(str(exc), "danger")
            return redirect(url_for("settings"))
        return render_template("settings.html", receipt_folder=m.setting("receipt_folder"), database_path=str(m.DB_PATH.resolve()), native=bool(app.config.get("CHOOSE_FOLDER")))

    @app.route("/settings/database", methods=["POST"])
    @m.login_required
    def database_tools():
        action = request.form.get("action")
        upload = request.files.get("database")
        temp_path = None
        try:
            if action not in ("restore", "import") or request.form.get("confirmation") != "yes" or not upload or not upload.filename:
                raise ValueError("Choose a database and confirm the action.")
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
                temp_path = Path(handle.name)
            upload.save(temp_path)
            source = validate_database(temp_path)
            try:
                if action == "restore":
                    snapshot("before-restore")
                    history = [tuple(row) for row in m.query('SELECT * FROM activity')]
                    with m.get_db() as target:
                        source.backup(target)
                        finance.migrate(target)
                        import group_config
                        group_config.fill_legacy_settings(target)
                        target.executemany('INSERT OR IGNORE INTO activity(event_id,occurred_at,action,entity,record_id,before_json,after_json,detail) VALUES(?,?,?,?,?,?,?,?)', history)
                        finance.log_event('Backup restored', 'Database restored from a selected backup. Safety backup saved; previous activity history retained.', target)
                    flash("Backup restored. Your password is unchanged.", "success")
                else:
                    members = source.execute("SELECT * FROM members").fetchall()
                    payments = source.execute("SELECT * FROM payments").fetchall()
                    ids = {r[0] for r in m.query("SELECT id FROM members")}
                    payment_ids = {r[0] for r in m.query("SELECT id FROM payments")}
                    if any(r[0] in ids for r in members) or any(r[0] in payment_ids for r in payments):
                        raise ValueError("IDs overlap with this workspace. Import into an empty workspace to preserve IDs.")
                    snapshot("before-import")
                    with m.get_db() as db:
                        for table, rows in (("members", members), ("payments", payments)):
                            columns = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
                            for row in rows:
                                db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", row)
                        finance.log_event('Database imported', f'Imported {len(members)} members and {len(payments)} payments, preserving IDs. Original database unchanged.', db)
                    flash(f"Imported {len(members)} members and {len(payments)} payments. Original database unchanged.", "success")
            finally:
                source.close()
        except (TypeError, ValueError, OSError, sqlite3.Error) as exc:
            flash(str(exc), "danger")
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
        return redirect(url_for("settings"))

    @app.route("/receipts/<int:payment_id>/pdf")
    @m.login_required
    def receipt_pdf(payment_id):
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import simpleSplit
        payment = m.query("SELECT p.*, m.name FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (payment_id,), one=True)
        if not payment:
            return redirect(url_for("reports"))
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(595, 842))
        pdf.setTitle(f"MemberPay receipt {payment_id}")
        from group_config import logo_bytes
        from reportlab.lib.utils import ImageReader
        logo = logo_bytes()
        if logo:
            pdf.drawImage(ImageReader(io.BytesIO(logo)), 266, 740, 64, 64, mask="auto", preserveAspectRatio=True)
        pdf.setFillColorRGB(.06,.16,.26)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawCentredString(297, 710, m.setting("circle_name", "Your group"))
        if m.setting("group_motto"):
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(297, 728, m.setting("group_motto")[:100])
        pdf.setFont("Helvetica", 12)
        pdf.drawCentredString(297, 685, f"Payment receipt · #{payment_id:06d}")
        pdf.setFont("Helvetica-Bold", 26)
        pdf.drawCentredString(297, 630, m.money(payment["amount"]))
        summary = m.member_summary(m.query("SELECT * FROM members WHERE id=?", (payment["member_id"],), one=True), m.parse_date(payment["paid_date"]))
        y = 575
        for label, value in (("Received from",payment["name"]),("Date",payment["paid_date"]),("Payment for", "Registration Fee" if payment["payment_type"] == "registration" else "Monthly Contribution"),("Method",payment["method"]),("Notes",payment["notes"]),("Balance",m.money(abs(summary["balance"])) + (" credit" if summary["balance"] < 0 else ""))):
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(55,y,label)
            pdf.setFont("Helvetica", 11)
            for line in simpleSplit(str(value), "Helvetica", 11, 330):
                if y < 70:
                    pdf.showPage()
                    y = 780
                    pdf.setFont("Helvetica", 11)
                pdf.drawString(190,y,line)
                y -= 16
            y -= 18
        pdf.save()
        payload = output.getvalue()
        name = f"receipt-{payment_id:06d}.pdf"
        folder = m.setting("receipt_folder")
        saver = app.config.get("SAVE_RECEIPT")
        try:
            destination = Path(folder) / name if folder else (saver(name) if saver else None)
            if destination:
                Path(destination).write_bytes(payload)
                flash(f"Receipt saved to {destination}.", "success")
                return redirect(url_for("receipt", payment_id=payment_id))
            if saver:
                return redirect(url_for("receipt", payment_id=payment_id))
        except OSError as exc:
            flash(f"Could not save receipt: {exc}", "danger")
            return redirect(url_for("receipt", payment_id=payment_id))
        return send_file(io.BytesIO(payload), mimetype="application/pdf", as_attachment=True, download_name=name)
