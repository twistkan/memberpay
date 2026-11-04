import pathlib
import tempfile

import app as m
from test_support import configure_fixture
_original_init = m.init_db
def _fixture_init():
    _original_init()
    configure_fixture(m)
m.init_db = _fixture_init


temp = pathlib.Path(tempfile.mkdtemp())
m.DB_PATH = temp / "memberpay.db"
m.PASSWORD_PATH = temp / "password.txt"
m.BACKUP_DIR = temp / "backups"
m.init_db()

client = m.app.test_client()
assert client.get("/").status_code == 302
assert client.post("/unlock", data={"password": "fixture-password-2026"}).status_code == 302
assert client.get("/").status_code == 200
assert client.post(
    "/members/new",
    data={
        "name": "Amina Nakato",
        "phone": "0700000000",
        "join_date": "2026-06-01",
        "registration_target": "20000",
        "monthly_target": "80000",
        "initial_registration": "20000",
        "initial_monthly": "160000",
        "initial_paid_date": "2026-06-01",
        "initial_method": "Cash",
        "initial_notes": "June and July",
    },
).status_code == 302
assert client.get("/members").status_code == 200
assert client.get("/reports").status_code == 200
with m.app.app_context():
    member_id = m.query("SELECT id FROM members", one=True)["id"]
assert client.post(
    f"/members/{member_id}/record-payment",
    data={"amount": "80000", "payment_type": "monthly", "paid_date": "2026-08-19", "method": "Mobile Money", "notes": "August"},
).status_code == 302
with m.app.app_context():
    payment_id = m.query("SELECT id FROM payments ORDER BY id DESC", one=True)["id"]
assert client.get(f"/receipts/{payment_id}").status_code == 200
assert client.get("/reports.csv").status_code == 200
print("SMOKE TEST PASSED")
