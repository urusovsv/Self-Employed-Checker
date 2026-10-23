"""Самопроверка правил сканера (sanity-check, НЕ боевой код).

Эти образцы содержат НАМЕРЕННО уязвимый код для подтверждения того, что
правила SQLi/XSS/CSRF в semgrep_sql_xss_csrf.yml действительно срабатывают.
Файл изолирован в tests/security/ и никогда не импортируется рантаймом.
"""
import sqlite3  # noqa: F401  (sql-library-usage)

from flask import Flask, render_template_string
from markupsafe import Markup  # noqa: F401  (xss-unescaped-html-output)

app = Flask(__name__)


def unsafe_query(user_id):
    conn = sqlite3.connect("db.sqlite")
    # sql-injection-string-concat
    conn.execute(f"SELECT * FROM users WHERE id = {user_id}")
    conn.execute("SELECT * FROM users WHERE name = '%s'" % user_id)


@app.route("/search")
def search(q):
    # xss-unescaped-html-output
    return render_template_string(f"<h1>Результат: {q}</h1>")


@app.route("/transfer", methods=["POST"])  # csrf-state-changing-endpoint
def transfer():
    return "ok"


@app.route("/login", methods=["POST"])  # csrf-state-changing-endpoint
def login():
    resp = app.make_response("ok")
    resp.set_cookie("session", "abc", httponly=False)  # insecure-cookie-settings
    return resp
