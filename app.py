from __future__ import annotations

import csv
import sys
import io
import os
import secrets
import sqlite3
import threading
import time
import webbrowser
from datetime import date, datetime
from functools import wraps
from contextlib import closing
from pathlib import Path

if (Path(__file__).resolve().parent / '.vendor').is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parent / '.vendor'))

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)


APP_DIR = Path(os.environ.get('MEMBERPAY_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MemberPayGroups' / 'workspace')))
DB_PATH = APP_DIR / "memberpay.db"
PASSWORD_PATH = APP_DIR / "password.txt"
BACKUP_DIR = APP_DIR / "backups"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MEMBERPAY_SECRET", "memberpay-local-" + secrets.token_hex(16))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

_last_seen = time.time()
_browser_seen = False


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=(), one=False):
    cur = get_db().execute(sql, params)
    rows = cur.fetchone() if one else cur.fetchall()
    cur.close()
    return rows


def execute(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.lastrowid


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            join_date TEXT NOT NULL,
            registration_target INTEGER NOT NULL DEFAULT 0,
            monthly_target INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            amount INTEGER NOT NULL,
            payment_type TEXT NOT NULL DEFAULT 'monthly',
            paid_date TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'Cash',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_payments_member ON payments(member_id);
        CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(paid_date);
        """
    )
    # Group settings are created only after the first-run review.
    db.commit()
    import finance
    finance.migrate(db)
    db.close()
    # No default password or automatic import of another workspace.
    BACKUP_DIR.mkdir(exist_ok=True)


def setting(key, default=""):
    row = query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return row["value"] if row else default


def money(value):
    return f"UGX {int(value or 0):,}"


def parse_money(value, default=0):
    try:
        return max(0, int(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def parse_date(value, default=None):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default or date.today()


def month_count(start: date, end: date) -> int:
    if end < start:
        return 0
    return (end.year - start.year) * 12 + end.month - start.month + 1


def month_label(year, month):
    return date(year, month, 1).strftime("%b %Y")


def dues_start(member=None):
    start = parse_date(setting("circle_start_date"))
    if member is not None and setting("dues_policy", "shared") == "joining":
        start = max(start, parse_date(member["join_date"]))
    return start.replace(day=1)


def monthly_status(monthly_paid, monthly_target, as_of, member=None):
    start = dues_start(member)
    total_months = month_count(start, as_of)
    owed = total_months * monthly_target
    cleared = []
    partial = []
    remaining = max(0, monthly_paid)
    for offset in range(total_months):
        y = start.year + (start.month - 1 + offset) // 12
        m = (start.month - 1 + offset) % 12 + 1
        if remaining >= monthly_target and monthly_target > 0:
            cleared.append(month_label(y, m))
            remaining -= monthly_target
        elif remaining > 0:
            partial.append(month_label(y, m))
            remaining = 0
        else:
            break
    owed_months = []
    for offset in range(len(cleared) + len(partial), total_months):
        y = start.year + (start.month - 1 + offset) // 12
        m = (start.month - 1 + offset) % 12 + 1
        owed_months.append(month_label(y, m))
    return {"owed": owed, "cleared": cleared, "partial": partial, "owed_months": owed_months}


def member_summary(member, as_of=None):
    as_of = as_of or date.today()
    payments = query("SELECT * FROM payments WHERE member_id=? ORDER BY paid_date, id", (member["id"],))
    reg_paid = sum(p["amount"] for p in payments if p["payment_type"] == "registration" and parse_date(p["paid_date"]) <= as_of)
    monthly_paid = sum(p["amount"] for p in payments if p["payment_type"] == "monthly" and parse_date(p["paid_date"]) <= as_of)
    reg_target = member["registration_target"]
    monthly_target = member["monthly_target"]
    monthly = monthly_status(monthly_paid, monthly_target, as_of, member)
    owed = reg_target + monthly["owed"]
    paid = reg_paid + monthly_paid
    balance = owed - paid
    if monthly_target <= 0:
        status = "FIX MONTHLY AMOUNT"
        status_class = "danger"
    elif balance > 0:
        status = "Owed"
        status_class = "warning"
    elif balance < 0:
        status = "Ahead"
        status_class = "success"
    else:
        status = "Paid up"
        status_class = "success"
    return {
        "member": member,
        "payments": payments,
        "reg_paid": reg_paid,
        "monthly_paid": monthly_paid,
        "reg_due": reg_target - reg_paid,
        "monthly_due": monthly["owed"] - monthly_paid,
        "owed": owed,
        "paid": paid,
        "balance": balance,
        "status": status,
        "status_class": status_class,
        "monthly": monthly,
        "as_of": as_of,
    }


def password_matches(value):
    from werkzeug.security import check_password_hash
    import json
    path = APP_DIR / 'credentials.json'
    try:
        return check_password_hash(json.loads(path.read_text(encoding='utf8'))['password_hash'], value)
    except (FileNotFoundError, ValueError, KeyError):
        return False


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("unlocked"):
            return redirect(url_for("unlock", next=request.path))
        return fn(*args, **kwargs)

    return wrapped


@app.before_request
def track_browser():
    global _last_seen, _browser_seen
    if request.endpoint not in ("static", "heartbeat"):
        _last_seen = time.time()
        if request.endpoint not in ("unlock", "health"):
            _browser_seen = True


@app.context_processor
def template_helpers():
    return {"money": money, "today": date.today().isoformat(), "circle_start": setting("circle_start_date", date.today().isoformat()), "circle_name": setting("circle_name", "Your group"), "group_motto": setting("group_motto"), "dues_policy": setting("dues_policy", "shared"), "default_registration": setting("default_registration", "0"), "default_monthly": setting("default_monthly", ""), "rules_locked": bool(query("SELECT id FROM members LIMIT 1", one=True))}


@app.route("/health")
def health():
    return "ok"


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    global _last_seen, _browser_seen
    _last_seen = time.time()
    _browser_seen = True
    return "ok"


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    if session.get("unlocked"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if password_matches(request.form.get("password", "")):
            session["unlocked"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("That password was not correct.", "danger")
    return render_template("unlock.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("unlock"))


@app.route("/")
@login_required
def dashboard():
    as_of = parse_date(request.args.get("as_of"), date.today())
    members = query("SELECT * FROM members WHERE active=1 ORDER BY name COLLATE NOCASE")
    summaries = [member_summary(m, as_of) for m in members]
    follow_up = request.args.get("follow_up") == "1"
    visible = [s for s in summaries if not follow_up or s["balance"] > 0]
    visible.sort(key=lambda s: (-s["balance"], s["member"]["name"].lower()))
    total_owed = sum(max(s["balance"], 0) for s in summaries)
    total_ahead = sum(min(s["balance"], 0) for s in summaries)
    import finance
    return render_template("dashboard.html", summaries=visible, all_count=len(summaries), follow_up=follow_up, as_of=as_of, total_owed=total_owed, total_ahead=total_ahead, funds=finance.funds(as_of))


@app.route("/members")
@login_required
def members():
    rows = query("SELECT * FROM members ORDER BY active DESC, name COLLATE NOCASE")
    summaries = [member_summary(m) for m in rows]
    return render_template("members.html", summaries=summaries)


@app.route("/members/settings", methods=["POST"])
@login_required
def update_settings():
    start = request.form.get("circle_start_date", "")
    try:
        date.fromisoformat(start)
    except ValueError:
        flash("Enter a valid circle start date.", "danger")
        return redirect(url_for("members"))
    if query("SELECT id FROM members LIMIT 1", one=True):
        flash("The start date is locked after members are added to preserve balances.", "danger")
        return redirect(url_for("members"))
    execute("INSERT INTO settings(key,value) VALUES('circle_start_date',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (start,))
    flash("Circle start date updated. Balances recalculated.", "success")
    return redirect(url_for("members"))


def member_form_data(form, existing=None):
    return {
        "name": (form.get("name") or (existing["name"] if existing else "")).strip(),
        "phone": (form.get("phone") if "phone" in form else (existing["phone"] if existing else "")).strip(),
        "join_date": form.get("join_date") or (existing["join_date"] if existing else date.today().isoformat()),
        "registration_target": parse_money(form.get("registration_target"), existing["registration_target"] if existing else int(setting("default_registration", "0"))),
        "monthly_target": parse_money(form.get("monthly_target"), existing["monthly_target"] if existing else int(setting("default_monthly", "1"))),
    }


@app.route("/members/new", methods=["GET", "POST"])
@login_required
def new_member():
    if request.method == "POST":
        data = member_form_data(request.form)
        if not data["name"] or data["monthly_target"] <= 0:
            flash("Name and a monthly contribution greater than zero are required.", "danger")
            return render_template("member_form.html", data=data, is_edit=False, initial_preview=True)
        member_id = execute(
            "INSERT INTO members(name,phone,join_date,registration_target,monthly_target) VALUES(?,?,?,?,?)",
            (data["name"], data["phone"], data["join_date"], data["registration_target"], data["monthly_target"]),
        )
        initial_reg = parse_money(request.form.get("initial_registration"))
        initial_monthly = parse_money(request.form.get("initial_monthly"))
        paid_date = request.form.get("initial_paid_date") or date.today().isoformat()
        method = request.form.get("initial_method") or "Cash"
        notes = (request.form.get("initial_notes") or "Initial payment").strip()
        if initial_reg:
            execute("INSERT INTO payments(member_id,amount,payment_type,paid_date,method,notes) VALUES(?,?,?,?,?,?)", (member_id, initial_reg, "registration", paid_date, method, notes))
        if initial_monthly:
            execute("INSERT INTO payments(member_id,amount,payment_type,paid_date,method,notes) VALUES(?,?,?,?,?,?)", (member_id, initial_monthly, "monthly", paid_date, method, notes))
        flash(f"{data['name']} was registered.", "success")
        return redirect(url_for("member_detail", member_id=member_id))
    data = member_form_data({})
    return render_template("member_form.html", data=data, is_edit=False, initial_preview=False)


@app.route("/members/<int:member_id>")
@login_required
def member_detail(member_id):
    member = query("SELECT * FROM members WHERE id=?", (member_id,), one=True)
    if not member:
        flash("Member not found.", "danger")
        return redirect(url_for("members"))
    summary = member_summary(member)
    return render_template("member_detail.html", summary=summary)


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
def edit_member(member_id):
    member = query("SELECT * FROM members WHERE id=?", (member_id,), one=True)
    if not member:
        return redirect(url_for("members"))
    if request.method == "POST":
        data = member_form_data(request.form, member)
        if not data["name"] or data["monthly_target"] <= 0:
            flash("Name is required and monthly contribution must be greater than zero.", "danger")
            return render_template("member_form.html", data=data, is_edit=True, member=member, initial_preview=False)
        changed = any(data[k] != member[k] for k in ("registration_target", "monthly_target", "join_date"))
        if changed and query("SELECT id FROM payments WHERE member_id=? LIMIT 1", (member_id,), one=True):
            flash("Targets and join dates are locked for members with payments to preserve their history. Contact support for a planned correction.", "danger")
            return render_template("member_form.html", data=data, is_edit=True, member=member, initial_preview=False)
        execute("UPDATE members SET name=?, phone=?, join_date=?, registration_target=?, monthly_target=?, active=? WHERE id=?", (data["name"], data["phone"], data["join_date"], data["registration_target"], data["monthly_target"], 1 if request.form.get("active") else 0, member_id))
        flash("Member details updated.", "success")
        return redirect(url_for("member_detail", member_id=member_id))
    return render_template("member_form.html", data=dict(member), is_edit=True, member=member, initial_preview=False)


@app.route("/members/<int:member_id>/record-payment", methods=["GET", "POST"])
@app.route("/payments/new", methods=["GET", "POST"])
@login_required
def record_payment(member_id=None):
    preselected = query("SELECT * FROM members WHERE id=?", (member_id,), one=True) if member_id else None
    if request.method == "POST":
        chosen_id = int(request.form.get("member_id") or member_id or 0)
        member = query("SELECT * FROM members WHERE id=?", (chosen_id,), one=True)
        amount = parse_money(request.form.get("amount"))
        payment_type = request.form.get("payment_type") if request.form.get("payment_type") in ("registration", "monthly") else "monthly"
        paid_date = request.form.get("paid_date") or date.today().isoformat()
        if not member or not amount:
            flash("Choose a member and enter an amount greater than zero.", "danger")
        else:
            payment_id = execute("INSERT INTO payments(member_id,amount,payment_type,paid_date,method,notes) VALUES(?,?,?,?,?,?)", (chosen_id, amount, payment_type, paid_date, request.form.get("method") or "Cash", (request.form.get("notes") or "").strip()))
            return redirect(url_for("receipt", payment_id=payment_id))
        preselected = member or preselected
    people = query("SELECT * FROM members WHERE active=1 ORDER BY name COLLATE NOCASE")
    return render_template("payment_form.html", people=people, preselected=preselected, default_type=request.args.get("type", "monthly"), default_date=request.args.get("date", date.today().isoformat()))


@app.route("/payments/<int:payment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_payment(payment_id):
    payment = query("SELECT p.*, m.name FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (payment_id,), one=True)
    if not payment:
        return redirect(url_for("reports"))
    if request.method == "POST":
        amount = parse_money(request.form.get("amount"))
        if not amount:
            flash("Amount must be greater than zero.", "danger")
        else:
            execute("UPDATE payments SET amount=?, payment_type=?, paid_date=?, method=?, notes=? WHERE id=?", (amount, request.form.get("payment_type", "monthly"), request.form.get("paid_date") or date.today().isoformat(), request.form.get("method") or "Cash", (request.form.get("notes") or "").strip(), payment_id))
            flash("Payment updated.", "success")
            return redirect(url_for("member_detail", member_id=payment["member_id"]))
    return render_template("payment_edit.html", payment=payment)


@app.route("/payments/<int:payment_id>/delete", methods=["POST"])
@login_required
def delete_payment(payment_id):
    payment = query("SELECT member_id FROM payments WHERE id=?", (payment_id,), one=True)
    if payment:
        execute("DELETE FROM payments WHERE id=?", (payment_id,))
        flash("Payment deleted.", "success")
        return redirect(url_for("member_detail", member_id=payment["member_id"]))
    return redirect(url_for("reports"))


def report_data(as_of, from_date, to_date):
    people = query("SELECT * FROM members ORDER BY name COLLATE NOCASE")
    summaries = [member_summary(m, as_of) for m in people]
    transactions = query("SELECT p.*, m.name FROM payments p JOIN members m ON m.id=p.member_id WHERE p.paid_date BETWEEN ? AND ? ORDER BY p.paid_date DESC, p.id DESC", (from_date.isoformat(), to_date.isoformat()))
    total_collected = sum(p["amount"] for p in transactions)
    return summaries, transactions, total_collected


@app.route("/reports")
@login_required
def reports():
    as_of = parse_date(request.args.get("as_of"), date.today())
    first = query("SELECT MIN(paid_date) AS first_date FROM payments", one=True)
    default_from = parse_date(first["first_date"], as_of) if first and first["first_date"] else as_of
    from_date = parse_date(request.args.get("from"), default_from)
    to_date = parse_date(request.args.get("to"), as_of)
    summaries, transactions, total_collected = report_data(as_of, from_date, to_date)
    behind = [s for s in summaries if s["balance"] > 0]
    share = f"{setting('circle_name')} payment summary ({from_date:%d %b %Y} to {to_date:%d %b %Y})\nTotal collected: {money(total_collected)}\nMembers with payments/owed: {len(summaries) - len(behind)}/{len(behind)}\n" + "\n".join(f"- {s['member']['name']}: {s['status']} ({money(s['balance'])})" for s in summaries)
    return render_template("reports.html", summaries=summaries, transactions=transactions, total_collected=total_collected, as_of=as_of, from_date=from_date, to_date=to_date, share=share)


@app.route("/reports.csv")
@login_required
def reports_csv():
    as_of = parse_date(request.args.get("as_of"), date.today())
    from_date = parse_date(request.args.get("from"), as_of)
    to_date = parse_date(request.args.get("to"), as_of)
    _, transactions, _ = report_data(as_of, from_date, to_date)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Member", "Type", "Amount (UGX)", "Method", "Notes"])
    for p in transactions:
        writer.writerow([p["paid_date"], p["name"], "Registration Fee" if p["payment_type"] == "registration" else "Monthly Contribution", p["amount"], p["method"], p["notes"]])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=memberpay-payments.csv"})


@app.route("/receipts/<int:payment_id>")
@login_required
def receipt(payment_id):
    payment = query("SELECT p.*, m.name, m.phone FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (payment_id,), one=True)
    if not payment:
        return redirect(url_for("reports"))
    summary = member_summary(query("SELECT * FROM members WHERE id=?", (payment["member_id"],), one=True), parse_date(payment["paid_date"]))
    return render_template("receipt.html", payment=payment, summary=summary)


@app.route("/backup")
@login_required
def backup():
    saver = app.config.get('SAVE_BACKUP')
    if saver:
        destination = saver('memberpay-backup.db')
        if destination:
            try:
                if Path(destination).resolve() == DB_PATH.resolve():
                    raise ValueError('Choose a backup file different from the active database.')
                with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(destination)) as target:
                    source.backup(target)
                flash('Backup saved.', 'success')
            except (ValueError, OSError, sqlite3.Error) as exc:
                flash(f'Could not save backup: {exc}', 'danger')
        return redirect(url_for('settings'))
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_DIR / f"memberpay-{stamp}.db"
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(destination)
    with target:
        source.backup(target)
    target.close()
    source.close()
    return send_file(destination, as_attachment=True, download_name=destination.name)


def shutdown_watcher():
    time.sleep(8)
    while True:
        time.sleep(5)
        if _browser_seen and time.time() - _last_seen > 30:
            from settings_tools import automatic_backup
            automatic_backup()
            os._exit(0)


import sys
sys.modules.setdefault('app', sys.modules[__name__])
import group_config
group_config.register(app)
import settings_tools
settings_tools.register(app)
import finance
finance.register(app)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=shutdown_watcher, daemon=True).start()
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
