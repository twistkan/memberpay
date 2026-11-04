"""Monthly collections, statements, reminders, expenses and local history."""
import calendar
import io
import json
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from flask import request, render_template, redirect, url_for, flash, send_file, abort
import app as m


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Other',
            amount INTEGER NOT NULL CHECK(amount > 0),
            spent_date TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'Cash',
            payee TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(spent_date);
        CREATE TABLE IF NOT EXISTS activity (
            event_id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            record_id TEXT NOT NULL DEFAULT '',
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(occurred_at);
    """)
    # Triggers record changes in the same transaction, including bulk tools.
    for table in ('members', 'payments', 'expenses', 'settings'):
        columns = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
        key = 'key' if table == 'settings' else 'id'
        for operation, action in (('INSERT', 'Created'), ('UPDATE', 'Updated'), ('DELETE', 'Deleted')):
            def document(prefix):
                return 'json_object(' + ','.join(f"'{c}',{prefix}.{c}" for c in columns) + ')'
            before = document('OLD') if operation != 'INSERT' else "'{}'"
            after = document('NEW') if operation != 'DELETE' else "'{}'"
            ref = 'OLD' if operation == 'DELETE' else 'NEW'
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS audit_{table}_{operation.lower()}
                AFTER {operation} ON {table} BEGIN
                INSERT INTO activity(action,entity,record_id,before_json,after_json)
                VALUES('{action}','{table}',CAST({ref}.{key} AS TEXT),{before},{after});
                END""")
    db.commit()


def log_event(action, detail, db=None):
    target = db or m.get_db()
    target.execute('INSERT INTO activity(action,entity,detail) VALUES(?,?,?)', (action, 'workspace', detail))
    if db is None:
        target.commit()


def monthly_overview(month, as_of=None):
    end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    as_of = min(as_of or date.today(), end)
    start = m.parse_date(m.setting('circle_start_date', date.today().isoformat()))
    offset = (month.year - start.year) * 12 + month.month - start.month
    rows = []
    for member in m.query('SELECT * FROM members WHERE active=1 ORDER BY name COLLATE NOCASE'):
        summary = m.member_summary(member, as_of)
        member_start = m.dues_start(member)
        offset = (month.year - member_start.year) * 12 + month.month - member_start.month
        expected = member['monthly_target'] if offset >= 0 and as_of >= member_start and month <= as_of else 0
        covered = min(expected, max(0, summary['monthly_paid'] - max(0, offset) * member['monthly_target'])) if expected else 0
        rows.append({'member': member, 'expected': expected, 'covered': covered, 'remaining': expected - covered, 'balance': summary['balance']})
    cash = m.query("SELECT COALESCE(SUM(amount),0) total FROM payments WHERE payment_type='monthly' AND paid_date BETWEEN ? AND ?", (month.isoformat(), as_of.isoformat()), one=True)['total'] if month <= as_of else 0
    return {'rows': rows, 'expected': sum(r['expected'] for r in rows), 'covered': sum(r['covered'] for r in rows), 'remaining': sum(r['remaining'] for r in rows), 'cash': cash, 'as_of': as_of, 'month': month}


def funds(as_of):
    received = m.query('SELECT COALESCE(SUM(amount),0) total FROM payments WHERE paid_date<=?', (as_of.isoformat(),), one=True)['total']
    spent = m.query('SELECT COALESCE(SUM(amount),0) total FROM expenses WHERE spent_date<=?', (as_of.isoformat(),), one=True)['total']
    return {'received': received, 'spent': spent, 'available': received - spent}


def statement_data(member_id, as_of):
    member = m.query('SELECT * FROM members WHERE id=?', (member_id,), one=True)
    if not member:
        abort(404)
    summary = m.member_summary(member, as_of)
    summary['payments'] = [p for p in summary['payments'] if m.parse_date(p['paid_date']) <= as_of]
    return summary


def reminder_text(summary):
    return (f"Hello {summary['member']['name']}, a friendly reminder from {m.setting('circle_name', 'Your group')}. "
            f"As of {summary['as_of']:%d %b %Y}, your outstanding balance is {m.money(summary['balance'])}. "
            f"Registration balance: {m.money(max(0, summary['reg_due']))}; monthly contribution balance: {m.money(max(0, summary['monthly_due']))}. "
            "Please arrange your contribution when you can. If you have already paid, please share the payment details so we can update your account. Thank you.")


def statement_pdf(summary):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    styles['Title'].textColor = colors.HexColor('#102a43')
    styles['Heading2'].textColor = colors.HexColor('#102a43')
    styles['BodyText'].fontSize = 10
    styles['BodyText'].leading = 14
    def p(value, style='BodyText'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])
    story = [ p(m.setting('circle_name', 'Your group'), 'Title'), p('Member account statement', 'Heading2'), p(f"{summary['member']['name']} | As of {summary['as_of']:%d %b %Y}"), p(f"Member #{summary['member']['id']} | Phone: {summary['member']['phone'] or 'Not provided'}"), Spacer(1, 16)]
    from group_config import logo_bytes
    logo = logo_bytes()
    if m.setting("group_motto"):
        story.insert(1, p(m.setting("group_motto")))
    if logo:
        story.insert(0, Image(io.BytesIO(logo), 54, 54))
    balance = m.money(abs(summary['balance'])) + (' credit' if summary['balance'] < 0 else ' outstanding' if summary['balance'] > 0 else ' - paid up')
    overview = [[p('Registration paid / expected'), p(f"{m.money(summary['reg_paid'])} / {m.money(summary['member']['registration_target'])}")], [p('Monthly paid / expected'), p(f"{m.money(summary['monthly_paid'])} / {m.money(summary['monthly']['owed'])}")], [p('Total paid'), p(m.money(summary['paid']))], [p('Account balance'), p(balance)]]
    table = Table(overview, colWidths=[225, 270])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#eaf3fb')), ('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),10), ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([table, Spacer(1, 16)])
    coverage = [p('Monthly coverage', 'Heading2'), p('Contributions cover the oldest outstanding month first.'), p('Cleared: ' + (', '.join(summary['monthly']['cleared']) or 'None')), p('Partial: ' + (', '.join(summary['monthly']['partial']) or 'None')), p('Still owed: ' + (', '.join(summary['monthly']['owed_months']) or 'None')), Spacer(1, 14)]
    story.extend(coverage)
    story.append(p('Payment history', 'Heading2'))
    data = [[p(x) for x in ('Date', 'Payment for', 'Amount', 'Method / notes')]]
    for payment in summary['payments']:
        data.append([p(payment['paid_date']), p('Registration' if payment['payment_type'] == 'registration' else 'Monthly'), p(m.money(payment['amount'])), p(payment['method'] + ('\n' + payment['notes'] if payment['notes'] else ''))])
    if len(data) == 1:
        data.append([p('No payments recorded by this date.'), '', '', ''])
    history = Table(data, colWidths=[77, 92, 106, 220], repeatRows=1, splitInRow=1)
    history.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf3fb')), ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, colors.HexColor('#f6f8fa')]), ('VALIGN',(0,0),(-1,-1),'TOP'), ('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#dfe7ed')), ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.append(history)
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#6d7d8c'))
        canvas.drawString(50, 28, 'MemberPay | Powered by Twist')
        canvas.drawRightString(545, 28, f'Page {doc.page}')
    SimpleDocTemplate(output, pagesize=(595,842), rightMargin=50, leftMargin=50, topMargin=38, bottomMargin=48, title='MemberPay member statement').build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


CATEGORIES = ('Administration', 'Bank charges', 'Events', 'Member support', 'Supplies', 'Transport', 'Other')


def register(app):
    @app.route('/collections')
    @m.login_required
    def collections():
        try:
            month = date.fromisoformat(request.args.get('month', date.today().strftime('%Y-%m')) + '-01')
        except ValueError:
            flash('Choose a valid month.', 'danger')
            return redirect(url_for('collections'))
        return render_template('collections.html', overview=monthly_overview(month))

    @app.route('/members/<int:member_id>/statement')
    @m.login_required
    def statement(member_id):
        as_of = m.parse_date(request.args.get('as_of'), date.today())
        return render_template('statement.html', summary=statement_data(member_id, as_of))

    @app.route('/members/<int:member_id>/statement.pdf')
    @m.login_required
    def statement_export(member_id):
        as_of = m.parse_date(request.args.get('as_of'), date.today())
        payload = statement_pdf(statement_data(member_id, as_of))
        name = f'statement-{member_id:06d}-{as_of.isoformat()}.pdf'
        folder = m.setting('receipt_folder')
        saver = app.config.get('SAVE_RECEIPT')
        try:
            destination = Path(folder) / name if folder else saver(name) if saver else None
            if destination:
                Path(destination).write_bytes(payload)
                flash(f'Statement saved to {destination}.', 'success')
            if destination or saver:
                return redirect(url_for('statement', member_id=member_id, as_of=as_of.isoformat()))
        except OSError as exc:
            flash(f'Could not save statement: {exc}', 'danger')
            return redirect(url_for('statement', member_id=member_id, as_of=as_of.isoformat()))
        return send_file(io.BytesIO(payload), mimetype='application/pdf', as_attachment=True, download_name=name)

    @app.route('/reminders')
    @m.login_required
    def reminders():
        as_of = m.parse_date(request.args.get('as_of'), date.today())
        people = m.query('SELECT * FROM members WHERE active=1 ORDER BY name COLLATE NOCASE')
        rows = []
        for person in people:
            summary = m.member_summary(person, as_of)
            if summary['balance'] > 0:
                rows.append({'summary': summary, 'message': reminder_text(summary)})
        return render_template('reminders.html', rows=rows, as_of=as_of)

    @app.route('/expenses')
    @m.login_required
    def expenses():
        as_of = m.parse_date(request.args.get('as_of'), date.today())
        first = request.args.get('from') or date(as_of.year,as_of.month,1).isoformat()
        from_date = m.parse_date(first)
        to_date = m.parse_date(request.args.get('to'), as_of)
        if from_date > to_date:
            flash('The end date must be on or after the start date.', 'danger')
            return redirect(url_for('expenses'))
        rows = m.query('SELECT * FROM expenses WHERE spent_date BETWEEN ? AND ? ORDER BY spent_date DESC,id DESC', (from_date.isoformat(),to_date.isoformat()))
        return render_template('expenses.html', rows=rows, totals=funds(as_of), as_of=as_of, from_date=from_date, to_date=to_date, range_total=sum(r['amount'] for r in rows))

    @app.route('/expenses/new', methods=['GET','POST'])
    @app.route('/expenses/<int:expense_id>/edit', methods=['GET','POST'])
    @m.login_required
    def expense_form(expense_id=None):
        existing = m.query('SELECT * FROM expenses WHERE id=?', (expense_id,), one=True) if expense_id else None
        if expense_id and not existing:
            abort(404)
        data = dict(existing) if existing else {'description':'','category':'Other','amount':'','spent_date':date.today().isoformat(),'method':'Cash','payee':'','notes':''}
        if request.method == 'POST':
            data.update({key: request.form.get(key,'').strip() for key in data if key not in ('id','created_at')})
            try:
                amount = int(str(data['amount']).replace(',',''))
                if amount <= 0 or amount > 10**15:
                    raise ValueError('Enter an expense amount greater than zero.')
                date.fromisoformat(data['spent_date'])
                if not data['description'] or len(data['description']) > 200:
                    raise ValueError('Enter a description of 1-200 characters.')
                if data['category'] not in CATEGORIES or data['method'] not in ('Cash','Mobile Money','Bank Transfer','Other'):
                    raise ValueError('Choose a valid category and payment method.')
                values = (data['description'],data['category'],amount,data['spent_date'],data['method'],data['payee'],data['notes'])
                if expense_id:
                    m.execute('UPDATE expenses SET description=?,category=?,amount=?,spent_date=?,method=?,payee=?,notes=? WHERE id=?', values + (expense_id,))
                else:
                    m.execute('INSERT INTO expenses(description,category,amount,spent_date,method,payee,notes) VALUES(?,?,?,?,?,?,?)', values)
                flash('Expense saved.', 'success')
                return redirect(url_for('expenses', **{'from':data['spent_date'][:7]+'-01','to':data['spent_date']}))
            except (ValueError, OverflowError) as exc:
                flash(str(exc) if str(exc).startswith(('Enter','Choose')) else 'Enter a valid amount and date.', 'danger')
        return render_template('expense_form.html', data=data, expense_id=expense_id, categories=CATEGORIES)

    @app.route('/expenses/<int:expense_id>/delete', methods=['POST'])
    @m.login_required
    def delete_expense(expense_id):
        if request.form.get('confirmation') != 'yes':
            flash('Confirm before deleting an expense.', 'danger')
        else:
            m.execute('DELETE FROM expenses WHERE id=?', (expense_id,))
            flash('Expense deleted. Its details are kept in Activity history.', 'success')
        return redirect(url_for('expenses'))

    @app.route('/activity')
    @m.login_required
    def activity():
        try:
            page = max(1,int(request.args.get('page','1')))
        except ValueError:
            page = 1
        entity = request.args.get('entity','')
        if entity not in ('','members','payments','expenses','settings','workspace'):
            entity = ''
        where = ' WHERE entity=?' if entity else ''
        params = (entity,) if entity else ()
        count = m.query('SELECT COUNT(*) total FROM activity' + where,params,one=True)['total']
        pages = max(1,(count+49)//50)
        page = min(page,pages)
        events = m.query('SELECT * FROM activity' + where + ' ORDER BY occurred_at DESC,rowid DESC LIMIT 50 OFFSET ?',params+((page-1)*50,))
        rows = []
        for event in events:
            item = dict(event)
            before, after = json.loads(item['before_json']), json.loads(item['after_json'])
            item['changes'] = [{'field':key.replace('_',' ').title(), 'before':before.get(key,''), 'after':after.get(key,'')} for key in sorted(set(before)|set(after)) if before.get(key) != after.get(key)]
            item['label'] = after.get('name') or before.get('name') or after.get('description') or before.get('description') or (f"#{item['record_id']}" if item['record_id'] else 'Workspace')
            rows.append(item)
        return render_template('activity.html', rows=rows, page=page, pages=pages, count=count, entity=entity)
