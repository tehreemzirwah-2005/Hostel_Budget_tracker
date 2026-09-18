import os
import json
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from calendar import monthrange

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from groq import Groq
except ImportError:
    Groq = None


# ============================================================
# APP CONFIG
# ============================================================

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "budgetwise.db"

st.set_page_config(
    page_title="BudgetWise AI",
    page_icon="💰",
    layout="wide",
)


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def execute(sql, params=(), fetch=False, many=False):
    conn = get_db()
    cur = conn.cursor()

    if many:
        cur.executemany(sql, params)
    else:
        cur.execute(sql, params)

    result = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return result


def scalar(sql, params=()):
    rows = execute(sql, params, fetch=True)
    return rows[0][0] if rows else None


def now():
    return datetime.now().isoformat(timespec="seconds")


def initialize_database():

    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT 'User',
        currency TEXT DEFAULT 'PKR',
        default_monthly_money REAL DEFAULT 0,
        default_savings_target REAL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        account_type TEXT DEFAULT 'cash',
        opening_balance REAL DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        category_type TEXT DEFAULT 'expense',
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS monthly_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month_key TEXT NOT NULL UNIQUE,
        planned_income REAL DEFAULT 0,
        savings_target REAL DEFAULT 0,
        rollover_in REAL DEFAULT 0,
        status TEXT DEFAULT 'open',
        created_at TEXT NOT NULL,
        closed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS budget_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        monthly_budget_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        planned_amount REAL DEFAULT 0,
        UNIQUE(monthly_budget_id, category_id),
        FOREIGN KEY(monthly_budget_id)
            REFERENCES monthly_budgets(id) ON DELETE CASCADE,
        FOREIGN KEY(category_id)
            REFERENCES categories(id)
    );

    CREATE TABLE IF NOT EXISTS budget_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        monthly_budget_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        start_day INTEGER NOT NULL,
        end_day INTEGER NOT NULL,
        planned_amount REAL DEFAULT 0,
        UNIQUE(monthly_budget_id, name),
        FOREIGN KEY(monthly_budget_id)
            REFERENCES monthly_budgets(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        monthly_budget_id INTEGER NOT NULL,
        transaction_date TEXT NOT NULL,
        transaction_type TEXT NOT NULL,
        amount REAL NOT NULL,
        category_id INTEGER,
        account_id INTEGER,
        to_account_id INTEGER,
        description TEXT DEFAULT '',
        planned INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY(monthly_budget_id)
            REFERENCES monthly_budgets(id) ON DELETE CASCADE,
        FOREIGN KEY(category_id)
            REFERENCES categories(id),
        FOREIGN KEY(account_id)
            REFERENCES accounts(id),
        FOREIGN KEY(to_account_id)
            REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        amount REAL NOT NULL,
        due_day INTEGER DEFAULT 1,
        category_id INTEGER,
        account_id INTEGER,
        recurring INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(category_id)
            REFERENCES categories(id),
        FOREIGN KEY(account_id)
            REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS bill_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id INTEGER NOT NULL,
        monthly_budget_id INTEGER NOT NULL,
        payment_date TEXT NOT NULL,
        amount REAL NOT NULL,
        transaction_id INTEGER,
        UNIQUE(bill_id, monthly_budget_id),
        FOREIGN KEY(bill_id)
            REFERENCES bills(id) ON DELETE CASCADE,
        FOREIGN KEY(monthly_budget_id)
            REFERENCES monthly_budgets(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        target_amount REAL NOT NULL,
        initial_saved REAL DEFAULT 0,
        target_date TEXT,
        priority TEXT DEFAULT 'Medium',
        active INTEGER DEFAULT 1,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS goal_contributions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id INTEGER NOT NULL,
        monthly_budget_id INTEGER NOT NULL,
        contribution_date TEXT NOT NULL,
        amount REAL NOT NULL,
        notes TEXT DEFAULT '',
        FOREIGN KEY(goal_id)
            REFERENCES goals(id) ON DELETE CASCADE,
        FOREIGN KEY(monthly_budget_id)
            REFERENCES monthly_budgets(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS planned_purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        estimated_amount REAL NOT NULL,
        target_date TEXT,
        priority TEXT DEFAULT 'Medium',
        saved_amount REAL DEFAULT 0,
        status TEXT DEFAULT 'planned',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        debt_type TEXT DEFAULT 'owed_by_me',
        original_amount REAL NOT NULL,
        remaining_amount REAL NOT NULL,
        due_date TEXT,
        active INTEGER DEFAULT 1,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS recurring_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        item_type TEXT NOT NULL,
        amount REAL NOT NULL,
        category_id INTEGER,
        account_id INTEGER,
        day_of_month INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        last_generated_month TEXT,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """)

    if scalar("SELECT COUNT(*) FROM users") == 0:
        cur.execute("""
            INSERT INTO users
            (name, currency, default_monthly_money,
             default_savings_target, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("User", "PKR", 0, 0, now()))

    categories = [
        ("Groceries", "expense"),
        ("Food & Dining", "expense"),
        ("Transport", "expense"),
        ("Bills & Utilities", "expense"),
        ("Rent / Housing", "expense"),
        ("Health", "expense"),
        ("Education", "expense"),
        ("Shopping", "expense"),
        ("Entertainment", "expense"),
        ("Personal / Needs", "expense"),
        ("Family", "expense"),
        ("Travel", "expense"),
        ("Subscriptions", "expense"),
        ("Debt Payment", "expense"),
        ("Other", "expense"),
        ("Savings", "saving"),
    ]

    for name, category_type in categories:
        cur.execute("""
            INSERT OR IGNORE INTO categories
            (name, category_type)
            VALUES (?, ?)
        """, (name, category_type))

    if scalar("SELECT COUNT(*) FROM accounts") == 0:
        for name, account_type in [
            ("Cash", "cash"),
            ("Bank", "bank"),
            ("Savings", "savings"),
        ]:
            cur.execute("""
                INSERT INTO accounts
                (name, account_type, opening_balance, created_at)
                VALUES (?, ?, ?, ?)
            """, (name, account_type, 0, now()))

    conn.commit()
    conn.close()


initialize_database()


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_user():
    return execute(
        "SELECT * FROM users WHERE id=1",
        fetch=True
    )[0]


def currency():
    return get_user()["currency"]


def month_key_today():
    return date.today().strftime("%Y-%m")


def month_name(key):
    return datetime.strptime(
        key + "-01",
        "%Y-%m-%d"
    ).strftime("%B %Y")


def previous_month(key):
    d = datetime.strptime(key + "-01", "%Y-%m")

    if d.month == 1:
        return f"{d.year - 1}-12"

    return f"{d.year}-{d.month - 1:02d}"


def next_month(key):
    d = datetime.strptime(key + "-01", "%Y-%m")

    if d.month == 12:
        return f"{d.year + 1}-01"

    return f"{d.year}-{d.month + 1:02d}"


def month_dates(key):
    y, m = map(int, key.split("-"))
    last = monthrange(y, m)[1]

    return (
        f"{key}-01",
        f"{key}-{last:02d}"
    )


def get_budget(key):
    rows = execute("""
        SELECT *
        FROM monthly_budgets
        WHERE month_key=?
    """, (key,), fetch=True)

    return rows[0] if rows else None


def create_month(
    key,
    copy_previous=False,
    rollover=0
):

    if get_budget(key):
        return get_budget(key)["id"]

    user = get_user()

    planned_income = float(
        user["default_monthly_money"] or 0
    )

    savings_target = float(
        user["default_savings_target"] or 0
    )

    execute("""
        INSERT INTO monthly_budgets
        (month_key, planned_income, savings_target,
         rollover_in, status, created_at)
        VALUES (?, ?, ?, ?, 'open', ?)
    """, (
        key,
        planned_income,
        savings_target,
        rollover,
        now()
    ))

    budget_id = scalar("""
        SELECT id
        FROM monthly_budgets
        WHERE month_key=?
    """, (key,))

    source = None

    if copy_previous:
        source_key = previous_month(key)
        source = get_budget(source_key)

    if source:

        old_categories = execute("""
            SELECT category_id, planned_amount
            FROM budget_categories
            WHERE monthly_budget_id=?
        """, (source["id"],), fetch=True)

        for row in old_categories:
            execute("""
                INSERT INTO budget_categories
                (monthly_budget_id, category_id, planned_amount)
                VALUES (?, ?, ?)
            """, (
                budget_id,
                row["category_id"],
                row["planned_amount"]
            ))

        old_periods = execute("""
            SELECT name, start_day, end_day, planned_amount
            FROM budget_periods
            WHERE monthly_budget_id=?
        """, (source["id"],), fetch=True)

        for row in old_periods:
            execute("""
                INSERT INTO budget_periods
                (monthly_budget_id, name, start_day,
                 end_day, planned_amount)
                VALUES (?, ?, ?, ?, ?)
            """, (
                budget_id,
                row["name"],
                row["start_day"],
                row["end_day"],
                row["planned_amount"]
            ))

    if scalar("""
        SELECT COUNT(*)
        FROM budget_periods
        WHERE monthly_budget_id=?
    """, (budget_id,)) == 0:

        y, m = map(int, key.split("-"))
        last_day = monthrange(y, m)[1]

        first_half = planned_income / 2
        second_half = planned_income - first_half

        execute("""
            INSERT INTO budget_periods
            (monthly_budget_id, name, start_day,
             end_day, planned_amount)
            VALUES (?, ?, ?, ?, ?)
        """, (
            budget_id,
            "First Half",
            1,
            15,
            first_half
        ))

        execute("""
            INSERT INTO budget_periods
            (monthly_budget_id, name, start_day,
             end_day, planned_amount)
            VALUES (?, ?, ?, ?, ?)
        """, (
            budget_id,
            "Second Half",
            16,
            last_day,
            second_half
        ))

    return budget_id


def auto_create_current_month():

    key = month_key_today()

    if not get_budget(key):

        previous = previous_month(key)

        create_month(
            key,
            copy_previous=bool(get_budget(previous)),
            rollover=0
        )


auto_create_current_month()


# ============================================================
# FINANCIAL CALCULATIONS
# ============================================================

def income_total(key):

    budget = get_budget(key)

    return float(scalar("""
        SELECT COALESCE(SUM(amount),0)
        FROM transactions
        WHERE monthly_budget_id=?
        AND transaction_type='income'
    """, (budget["id"],)) or 0)


def spending_total(key):

    budget = get_budget(key)

    return float(scalar("""
        SELECT COALESCE(SUM(amount),0)
        FROM transactions
        WHERE monthly_budget_id=?
        AND transaction_type IN
        ('expense','bill_payment','debt_payment')
    """, (budget["id"],)) or 0)


def savings_total(key):

    budget = get_budget(key)

    return float(scalar("""
        SELECT COALESCE(SUM(amount),0)
        FROM transactions
        WHERE monthly_budget_id=?
        AND transaction_type='saving'
    """, (budget["id"],)) or 0)


def available_money(key):

    budget = get_budget(key)

    return (
        float(budget["rollover_in"] or 0)
        + income_total(key)
        - spending_total(key)
    )


def transactions(key):

    budget = get_budget(key)

    rows = execute("""
        SELECT
            t.*,
            c.name AS category_name,
            a.name AS account_name,
            ta.name AS to_account_name
        FROM transactions t
        LEFT JOIN categories c
            ON c.id=t.category_id
        LEFT JOIN accounts a
            ON a.id=t.account_id
        LEFT JOIN accounts ta
            ON ta.id=t.to_account_id
        WHERE t.monthly_budget_id=?
        ORDER BY t.transaction_date DESC, t.id DESC
    """, (budget["id"],), fetch=True)

    return pd.DataFrame([dict(x) for x in rows])


def get_categories():
    return execute("""
        SELECT *
        FROM categories
        WHERE active=1
        ORDER BY name
    """, fetch=True)


def get_accounts():
    return execute("""
        SELECT *
        FROM accounts
        WHERE active=1
        ORDER BY name
    """, fetch=True)


def category_actuals(key):

    budget = get_budget(key)

    return execute("""
        SELECT
            c.name,
            COALESCE(SUM(t.amount),0) AS actual
        FROM categories c
        LEFT JOIN transactions t
            ON t.category_id=c.id
            AND t.monthly_budget_id=?
            AND t.transaction_type IN
            ('expense','bill_payment','debt_payment')
        WHERE c.active=1
        GROUP BY c.id
        ORDER BY c.name
    """, (budget["id"],), fetch=True)


# ============================================================
# MONTH SELECTOR
# ============================================================

months = execute("""
    SELECT month_key
    FROM monthly_budgets
    ORDER BY month_key DESC
""", fetch=True)

month_options = [x["month_key"] for x in months]

if "selected_month" not in st.session_state:
    st.session_state.selected_month = month_key_today()

if st.session_state.selected_month not in month_options:
    month_options.insert(0, st.session_state.selected_month)

st.sidebar.title("💰 BudgetWise AI")

selected_month = st.sidebar.selectbox(
    "Select Month",
    month_options,
    index=month_options.index(
        st.session_state.selected_month
    ),
    format_func=month_name
)

st.session_state.selected_month = selected_month

budget = get_budget(selected_month)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.metric(
    "Available",
    f"{currency()} {available_money(selected_month):,.2f}"
)

st.sidebar.caption(
    f"Status: {budget['status'].title()}"
)

page = st.sidebar.radio(
    "Menu",
    [
        "Dashboard",
        "Add Transaction",
        "Transactions",
        "Monthly Budget",
        "Accounts & Cash",
        "Bills",
        "Goals & Wishlist",
        "Debts",
        "Recurring",
        "Analytics",
        "AI Coach",
        "Settings",
    ]
)


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":

    st.title(
        f"Dashboard — {month_name(selected_month)}"
    )

    income = income_total(selected_month)
    spent = spending_total(selected_month)
    saved = savings_total(selected_month)
    available = available_money(selected_month)

    a, b1, c, d = st.columns(4)

    a.metric(
        "Actual Income",
        f"{currency()} {income:,.2f}"
    )

    b1.metric(
        "Actual Spending",
        f"{currency()} {spent:,.2f}"
    )

    c.metric(
        "Savings",
        f"{currency()} {saved:,.2f}"
    )

    d.metric(
        "Available",
        f"{currency()} {available:,.2f}"
    )

    st.divider()

    savings_target = float(
        budget["savings_target"] or 0
    )

    if savings_target:

        progress = min(
            saved / savings_target,
            1
        )

        st.subheader("Savings Progress")

        st.progress(progress)

        st.write(
            f"{currency()} {saved:,.2f} / "
            f"{currency()} {savings_target:,.2f}"
        )

    st.subheader("Budget vs Actual")

    actuals = {
        x["name"]: float(x["actual"] or 0)
        for x in category_actuals(selected_month)
    }

    budget_rows = execute("""
        SELECT
            c.name,
            bc.planned_amount
        FROM budget_categories bc
        JOIN categories c
            ON c.id=bc.category_id
        WHERE bc.monthly_budget_id=?
        ORDER BY c.name
    """, (budget["id"],), fetch=True)

    data = []

    for row in budget_rows:
        data.append({
            "Category": row["name"],
            "Budget": float(row["planned_amount"]),
            "Actual": actuals.get(row["name"], 0)
        })

    if data:
        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(
            "No category budgets have been configured yet."
        )

    st.subheader("Recent Transactions")

    df = transactions(selected_month)

    if df.empty:
        st.info("No transactions yet.")
    else:
        st.dataframe(
            df.head(10),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# ADD TRANSACTION
# ============================================================

elif page == "Add Transaction":

    st.title(
        f"Add Transaction — {month_name(selected_month)}"
    )

    if budget["status"] == "closed":
        st.warning(
            "This month is closed. Reopen it from Monthly Budget before adding transactions."
        )

    transaction_type = st.selectbox(
        "Transaction Type",
        [
            "expense",
            "income",
            "withdrawal",
            "transfer",
            "saving",
            "refund",
            "deposit",
            "debt_payment",
        ],
        disabled=budget["status"] == "closed"
    )

    account_rows = get_accounts()
    account_names = [
        x["name"] for x in account_rows
    ]
    account_map = {
        x["name"]: x["id"]
        for x in account_rows
    }

    category_rows = get_categories()
    category_names = [
        x["name"] for x in category_rows
    ]
    category_map = {
        x["name"]: x["id"]
        for x in category_rows
    }

    left, right = st.columns(2)

    with left:

        transaction_date = st.date_input(
            "Date",
            value=date.today()
        )

        amount = st.number_input(
            f"Amount ({currency()})",
            min_value=0.0,
            step=100.0
        )

        description = st.text_input(
            "Description"
        )

        planned = st.checkbox(
            "Mark as planned"
        )

    with right:

        category = st.selectbox(
            "Category",
            ["None"] + category_names
        )

        account = st.selectbox(
            "From / Main Account",
            account_names
        )

        destination = None

        if transaction_type in [
            "withdrawal",
            "transfer",
            "saving",
            "deposit"
        ]:

            destinations = [
                x for x in account_names
                if x != account
            ]

            if destinations:
                destination = st.selectbox(
                    "To Account",
                    destinations
                )

        notes = st.text_area(
            "Notes"
        )

    st.info(
        "ATM withdrawal, own-account transfer and savings movement "
        "are NOT counted as actual spending."
    )

    if st.button(
        "Save Transaction",
        type="primary",
        disabled=budget["status"] == "closed"
    ):

        start, end = month_dates(selected_month)

        tx_date = transaction_date.isoformat()

        if not start <= tx_date <= end:
            st.error(
                "The transaction date must belong to the selected month."
            )

        elif amount <= 0:
            st.error(
                "Amount must be greater than zero."
            )

        elif transaction_type in [
            "withdrawal",
            "transfer",
            "saving",
            "deposit"
        ] and not destination:

            st.error(
                "A destination account is required."
            )

        else:

            category_id = (
                category_map.get(category)
                if category != "None"
                else None
            )

            from_id = account_map[account]

            to_id = (
                account_map[destination]
                if destination
                else None
            )

            execute("""
                INSERT INTO transactions
                (
                    monthly_budget_id,
                    transaction_date,
                    transaction_type,
                    amount,
                    category_id,
                    account_id,
                    to_account_id,
                    description,
                    planned,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                budget["id"],
                tx_date,
                transaction_type,
                amount,
                category_id,
                from_id,
                to_id,
                description,
                int(planned),
                notes,
                now(),
                now()
            ))

            st.success(
                "Transaction saved successfully."
            )

            st.rerun()


# ============================================================
# TRANSACTION EDIT / DELETE
# ============================================================

elif page == "Transactions":

    st.title(
        f"Transactions — {month_name(selected_month)}"
    )

    df = transactions(selected_month)

    if df.empty:
        st.info("No transactions.")
    else:

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

        transaction_id = st.selectbox(
            "Select Transaction",
            df["id"].tolist()
        )

        row = execute("""
            SELECT *
            FROM transactions
            WHERE id=?
        """, (transaction_id,), fetch=True)[0]

        st.subheader(
            f"Edit Transaction #{transaction_id}"
        )

        transaction_date = st.date_input(
            "Date",
            datetime.strptime(
                row["transaction_date"],
                "%Y-%m-%d"
            ).date()
        )

        amount = st.number_input(
            "Amount",
            min_value=0.0,
            value=float(row["amount"]),
            step=100.0
        )

        transaction_type = st.selectbox(
            "Type",
            [
                "expense",
                "income",
                "withdrawal",
                "transfer",
                "saving",
                "refund",
                "deposit",
                "debt_payment",
            ],
            index=[
                "expense",
                "income",
                "withdrawal",
                "transfer",
                "saving",
                "refund",
                "deposit",
                "debt_payment",
            ].index(row["transaction_type"])
        )

        description = st.text_input(
            "Description",
            value=row["description"] or ""
        )

        notes = st.text_area(
            "Notes",
            value=row["notes"] or ""
        )

        c1, c2 = st.columns(2)

        with c1:

            if st.button(
                "Update Transaction",
                type="primary"
            ):

                start, end = month_dates(
                    selected_month
                )

                tx_date = transaction_date.isoformat()

                if not start <= tx_date <= end:
                    st.error(
                        "Date must belong to selected month."
                    )

                elif amount <= 0:
                    st.error(
                        "Amount must be positive."
                    )

                else:

                    execute("""
                        UPDATE transactions
                        SET
                            transaction_date=?,
                            transaction_type=?,
                            amount=?,
                            description=?,
                            notes=?,
                            updated_at=?
                        WHERE id=?
                    """, (
                        tx_date,
                        transaction_type,
                        amount,
                        description,
                        notes,
                        now(),
                        transaction_id
                    ))

                    st.success(
                        "Transaction updated."
                    )

                    st.rerun()

        with c2:

            if st.button(
                "Delete Transaction"
            ):

                execute(
                    "DELETE FROM transactions WHERE id=?",
                    (transaction_id,)
                )

                st.success(
                    "Transaction deleted."
                )

                st.rerun()


# ============================================================
# MONTHLY BUDGET
# ============================================================

elif page == "Monthly Budget":

    st.title(
        f"Monthly Budget — {month_name(selected_month)}"
    )

    st.write(
        "This month is independent. Editing it will not modify older months."
    )

    with st.form("monthly_budget"):

        planned_income = st.number_input(
            "Planned Monthly Money",
            min_value=0.0,
            value=float(
                budget["planned_income"] or 0
            ),
            step=500.0
        )

        savings_target = st.number_input(
            "Savings Target",
            min_value=0.0,
            value=float(
                budget["savings_target"] or 0
            ),
            step=500.0
        )

        rollover = st.number_input(
            "Explicit Rollover",
            min_value=0.0,
            value=float(
                budget["rollover_in"] or 0
            ),
            step=500.0
        )

        save = st.form_submit_button(
            "Save Monthly Settings",
            type="primary"
        )

    if save:

        execute("""
            UPDATE monthly_budgets
            SET
                planned_income=?,
                savings_target=?,
                rollover_in=?
            WHERE id=?
        """, (
            planned_income,
            savings_target,
            rollover,
            budget["id"]
        ))

        st.success(
            "Monthly budget updated."
        )

        st.rerun()

    st.subheader("Category Budgets")

    categories = get_categories()

    existing = execute("""
        SELECT category_id, planned_amount
        FROM budget_categories
        WHERE monthly_budget_id=?
    """, (budget["id"],), fetch=True)

    existing_map = {
        x["category_id"]: float(x["planned_amount"])
        for x in existing
    }

    with st.form("category_budgets"):

        values = {}

        columns = st.columns(2)

        for i, category in enumerate(categories):

            with columns[i % 2]:

                values[category["id"]] = st.number_input(
                    category["name"],
                    min_value=0.0,
                    value=existing_map.get(
                        category["id"],
                        0.0
                    ),
                    step=100.0
                )

        save_categories = st.form_submit_button(
            "Save Category Budgets"
        )

    if save_categories:

        for category_id, amount in values.items():

            execute("""
                INSERT OR REPLACE INTO budget_categories
                (
                    monthly_budget_id,
                    category_id,
                    planned_amount
                )
                VALUES (?, ?, ?)
            """, (
                budget["id"],
                category_id,
                amount
            ))

        st.success(
            "Category budgets saved."
        )

        st.rerun()

    st.subheader("Half-Month Budgets")

    periods = execute("""
        SELECT *
        FROM budget_periods
        WHERE monthly_budget_id=?
        ORDER BY start_day
    """, (budget["id"],), fetch=True)

    with st.form("period_budgets"):

        period_values = {}

        for period in periods:

            period_values[period["id"]] = st.number_input(
                period["name"],
                min_value=0.0,
                value=float(
                    period["planned_amount"]
                ),
                step=100.0
            )

        save_periods = st.form_submit_button(
            "Save Half-Month Budgets"
        )

    if save_periods:

        for period_id, amount in period_values.items():

            execute("""
                UPDATE budget_periods
                SET planned_amount=?
                WHERE id=?
            """, (
                amount,
                period_id
            ))

        st.success(
            "Half-month budgets updated."
        )

        st.rerun()

    st.divider()

    st.subheader("Month Lifecycle")

    if budget["status"] == "open":

        if st.button("Close This Month"):

            execute("""
                UPDATE monthly_budgets
                SET status='closed',
                    closed_at=?
                WHERE id=?
            """, (
                now(),
                budget["id"]
            ))

            st.success(
                "Month closed."
            )

            st.rerun()

    else:

        if st.button(
            "Reopen This Month",
            type="primary"
        ):

            execute("""
                UPDATE monthly_budgets
                SET status='open',
                    closed_at=NULL
                WHERE id=?
            """, (budget["id"],))

            st.success(
                "Month reopened."
            )

            st.rerun()

    st.divider()

    st.subheader("Create Future Month")

    future = date.today().replace(day=1) + timedelta(days=32)
    future = future.replace(day=1)

    future_date = st.date_input(
        "Future Month",
        future
    )

    future_key = future_date.strftime(
        "%Y-%m"
    )

    copy_previous = st.checkbox(
        "Copy previous month's category and half-month budgets",
        value=True
    )

    if st.button("Create Future Month"):

        if get_budget(future_key):

            st.warning(
                "That month already exists."
            )

        else:

            create_month(
                future_key,
                copy_previous=copy_previous,
                rollover=0
            )

            st.success(
                f"{month_name(future_key)} created."
            )

            st.rerun()

    st.subheader("Explicit Rollover")

    previous = previous_month(
        selected_month
    )

    if get_budget(previous):

        previous_available = available_money(
            previous
        )

        st.info(
            f"{month_name(previous)} currently has "
            f"{currency()} {previous_available:,.2f} available. "
            "This is NOT transferred automatically."
        )

        if st.button(
            "Apply Previous Month Available Money as Rollover"
        ):

            if previous_available > 0:

                execute("""
                    UPDATE monthly_budgets
                    SET rollover_in=?
                    WHERE id=?
                """, (
                    previous_available,
                    budget["id"]
                ))

                st.success(
                    "Rollover applied."
                )

                st.rerun()

            else:

                st.warning(
                    "There is no positive amount to roll over."
                )


# ============================================================
# ACCOUNTS
# ============================================================

elif page == "Accounts & Cash":

    st.title("Accounts & Cash")

    st.info(
        "ATM withdrawals and transfers move money between accounts. "
        "They are not automatically treated as spending."
    )

    account_data = []

    for account in get_accounts():

        account_data.append({
            "Account": account["name"],
            "Type": account["account_type"],
            "Opening Balance": account["opening_balance"],
            "Note": "Balance tracking enabled"
        })

    st.dataframe(
        pd.DataFrame(account_data),
        use_container_width=True,
        hide_index=True
    )

    st.subheader("Add Account")

    with st.form("account_form"):

        name = st.text_input(
            "Account Name"
        )

        account_type = st.selectbox(
            "Account Type",
            [
                "cash",
                "bank",
                "savings",
                "wallet",
                "other"
            ]
        )

        opening = st.number_input(
            "Opening Balance",
            min_value=0.0,
            step=500.0
        )

        submit = st.form_submit_button(
            "Add Account"
        )

    if submit:

        if not name.strip():

            st.error(
                "Account name is required."
            )

        else:

            execute("""
                INSERT INTO accounts
                (name, account_type,
                 opening_balance, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                name.strip(),
                account_type,
                opening,
                now()
            ))

            st.success(
                "Account created."
            )

            st.rerun()


# ============================================================
# BILLS
# ============================================================

elif page == "Bills":

    st.title(
        f"Bills — {month_name(selected_month)}"
    )

    bills = execute("""
        SELECT *
        FROM bills
        ORDER BY active DESC, name
    """, fetch=True)

    if bills:

        data = []

        for bill in bills:

            paid = scalar("""
                SELECT COUNT(*)
                FROM bill_payments
                WHERE bill_id=?
                AND monthly_budget_id=?
            """, (
                bill["id"],
                budget["id"]
            ))

            data.append({
                "ID": bill["id"],
                "Bill": bill["name"],
                "Amount": bill["amount"],
                "Due Day": bill["due_day"],
                "Paid This Month": "Yes" if paid else "No",
                "Active": bool(bill["active"])
            })

        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True
        )

        active_bills = [
            x for x in bills
            if x["active"]
        ]

        if active_bills:

            st.subheader("Record Bill Payment")

            selected_bill = st.selectbox(
                "Bill",
                active_bills,
                format_func=lambda x:
                    f"{x['name']} — "
                    f"{currency()} {x['amount']:,.2f}"
            )

            payment_amount = st.number_input(
                "Payment Amount",
                min_value=0.0,
                value=float(
                    selected_bill["amount"]
                ),
                step=100.0
            )

            if st.button(
                "Record Payment",
                disabled=budget["status"] == "closed"
            ):

                already_paid = scalar("""
                    SELECT COUNT(*)
                    FROM bill_payments
                    WHERE bill_id=?
                    AND monthly_budget_id=?
                """, (
                    selected_bill["id"],
                    budget["id"]
                ))

                if already_paid:

                    st.warning(
                        "This bill is already marked paid for this month."
                    )

                elif payment_amount <= 0:

                    st.error(
                        "Payment must be greater than zero."
                    )

                else:

                    tx_id = scalar("""
                        INSERT INTO transactions
                        (
                            monthly_budget_id,
                            transaction_date,
                            transaction_type,
                            amount,
                            category_id,
                            account_id,
                            description,
                            planned,
                            notes,
                            created_at
                        )
                        VALUES (?, ?, 'bill_payment',
                                ?, ?, ?, ?, 0, ?, ?)
                    """, (
                        budget["id"],
                        date.today().isoformat(),
                        payment_amount,
                        selected_bill["category_id"],
                        selected_bill["account_id"],
                        selected_bill["name"],
                        "Bill payment",
                        now()
                    ))

                    execute("""
                        INSERT INTO bill_payments
                        (
                            bill_id,
                            monthly_budget_id,
                            payment_date,
                            amount,
                            transaction_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        selected_bill["id"],
                        budget["id"],
                        date.today().isoformat(),
                        payment_amount,
                        tx_id
                    ))

                    st.success(
                        "Bill payment recorded."
                    )

                    st.rerun()

    st.subheader("Add Bill")

    categories = get_categories()
    accounts = get_accounts()

    with st.form("bill_form"):

        name = st.text_input(
            "Bill Name"
        )

        amount = st.number_input(
            "Expected Amount",
            min_value=0.0,
            step=100.0
        )

        due_day = st.number_input(
            "Due Day",
            min_value=1,
            max_value=31,
            value=1
        )

        category = st.selectbox(
            "Category",
            categories,
            format_func=lambda x: x["name"]
        )

        account = st.selectbox(
            "Payment Account",
            accounts,
            format_func=lambda x: x["name"]
        )

        notes = st.text_area(
            "Notes"
        )

        submit = st.form_submit_button(
            "Add Bill"
        )

    if submit:

        if not name.strip() or amount <= 0:

            st.error(
                "Bill name and positive amount are required."
            )

        else:

            execute("""
                INSERT INTO bills
                (
                    name,
                    amount,
                    due_day,
                    category_id,
                    account_id,
                    recurring,
                    active,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)
            """, (
                name.strip(),
                amount,
                due_day,
                category["id"],
                account["id"],
                notes,
                now()
            ))

            st.success(
                "Bill added."
            )

            st.rerun()


# ============================================================
# GOALS & WISHLIST
# ============================================================

elif page == "Goals & Wishlist":

    st.title("Goals & Wishlist")

    st.subheader("Savings Goals")

    goals = execute("""
        SELECT *
        FROM goals
        ORDER BY active DESC, priority, name
    """, fetch=True)

    if goals:

        goal_data = []

        for goal in goals:

            contributions = scalar("""
                SELECT COALESCE(SUM(amount),0)
                FROM goal_contributions
                WHERE goal_id=?
            """, (goal["id"],)) or 0

            current = (
                float(goal["initial_saved"] or 0)
                + float(contributions)
            )

            goal_data.append({
                "Goal": goal["name"],
                "Target": goal["target_amount"],
                "Saved": current,
                "Remaining": max(
                    float(goal["target_amount"]) - current,
                    0
                ),
                "Priority": goal["priority"],
                "Target Date": goal["target_date"]
            })

        st.dataframe(
            pd.DataFrame(goal_data),
            use_container_width=True,
            hide_index=True
        )

        active_goals = [
            x for x in goals
            if x["active"]
        ]

        if active_goals:

            selected_goal = st.selectbox(
                "Goal",
                active_goals,
                format_func=lambda x: x["name"]
            )

            contribution = st.number_input(
                "Contribution",
                min_value=0.0,
                step=100.0
            )

            if st.button(
                "Add Contribution"
            ):

                if contribution <= 0:

                    st.error(
                        "Contribution must be positive."
                    )

                else:

                    execute("""
                        INSERT INTO goal_contributions
                        (
                            goal_id,
                            monthly_budget_id,
                            contribution_date,
                            amount,
                            notes
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        selected_goal["id"],
                        budget["id"],
                        date.today().isoformat(),
                        contribution,
                        "Goal contribution"
                    ))

                    st.success(
                        "Contribution added."
                    )

                    st.rerun()

    with st.form("goal_form"):

        st.subheader("Create Goal")

        name = st.text_input(
            "Goal Name"
        )

        target = st.number_input(
            "Target Amount",
            min_value=0.0,
            step=500.0
        )

        initial = st.number_input(
            "Already Saved",
            min_value=0.0,
            step=500.0
        )

        target_date = st.date_input(
            "Target Date",
            date.today() + timedelta(days=90)
        )

        priority = st.selectbox(
            "Priority",
            ["High", "Medium", "Low"]
        )

        notes = st.text_area(
            "Notes"
        )

        submit = st.form_submit_button(
            "Create Goal"
        )

    if submit:

        if not name.strip() or target <= 0:

            st.error(
                "Goal name and positive target are required."
            )

        else:

            execute("""
                INSERT INTO goals
                (
                    name,
                    target_amount,
                    initial_saved,
                    target_date,
                    priority,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                name.strip(),
                target,
                initial,
                target_date.isoformat(),
                priority,
                notes,
                now()
            ))

            st.success(
                "Goal created."
            )

            st.rerun()

    st.divider()

    st.subheader(
        "Wishlist / Planned Purchases"
    )

    wishes = execute("""
        SELECT *
        FROM planned_purchases
        ORDER BY status, priority, target_date
    """, fetch=True)

    if wishes:

        st.dataframe(
            pd.DataFrame([
                {
                    "ID": x["id"],
                    "Item": x["name"],
                    "Estimated Amount": x["estimated_amount"],
                    "Saved": x["saved_amount"],
                    "Target Date": x["target_date"],
                    "Priority": x["priority"],
                    "Status": x["status"]
                }
                for x in wishes
            ]),
            use_container_width=True,
            hide_index=True
        )

        planned_items = [
            x for x in wishes
            if x["status"] == "planned"
        ]

        if planned_items:

            selected_item = st.selectbox(
                "Wishlist Item",
                planned_items,
                format_func=lambda x: x["name"]
            )

            actual_amount = st.number_input(
                "Actual Purchase Amount",
                min_value=0.0,
                value=float(
                    selected_item["estimated_amount"]
                ),
                step=100.0
            )

            accounts = get_accounts()

            purchase_account = st.selectbox(
                "Purchase Account",
                accounts,
                format_func=lambda x: x["name"]
            )

            if st.button(
                "Convert to Actual Expense",
                disabled=budget["status"] == "closed"
            ):

                shopping_id = scalar("""
                    SELECT id
                    FROM categories
                    WHERE name='Shopping'
                """)

                execute("""
                    INSERT INTO transactions
                    (
                        monthly_budget_id,
                        transaction_date,
                        transaction_type,
                        amount,
                        category_id,
                        account_id,
                        description,
                        planned,
                        notes,
                        created_at
                    )
                    VALUES (?, ?, 'expense', ?, ?, ?, ?,
                            0, ?, ?)
                """, (
                    budget["id"],
                    date.today().isoformat(),
                    actual_amount,
                    shopping_id,
                    purchase_account["id"],
                    selected_item["name"],
                    "Converted from wishlist",
                    now()
                ))

                execute("""
                    UPDATE planned_purchases
                    SET status='purchased'
                    WHERE id=?
                """, (selected_item["id"],))

                st.success(
                    "Wishlist item converted to an actual expense."
                )

                st.rerun()

    with st.form("wishlist_form"):

        st.subheader("Add Wishlist Item")

        name = st.text_input(
            "Item Name"
        )

        estimate = st.number_input(
            "Estimated Amount",
            min_value=0.0,
            step=500.0
        )

        target_date = st.date_input(
            "Target Date",
            date.today() + timedelta(days=30)
        )

        priority = st.selectbox(
            "Priority",
            ["High", "Medium", "Low"],
            key="wishlist_priority"
        )

        notes = st.text_area(
            "Notes",
            key="wishlist_notes"
        )

        submit = st.form_submit_button(
            "Add Wishlist Item"
        )

    if submit:

        if not name.strip() or estimate <= 0:

            st.error(
                "Name and positive estimate are required."
            )

        else:

            execute("""
                INSERT INTO planned_purchases
                (
                    name,
                    estimated_amount,
                    target_date,
                    priority,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name.strip(),
                estimate,
                target_date.isoformat(),
                priority,
                notes,
                now()
            ))

            st.success(
                "Wishlist item added."
            )

            st.rerun()


# ============================================================
# DEBTS
# ============================================================

elif page == "Debts":

    st.title("Debts & Lending")

    debts = execute("""
        SELECT *
        FROM debts
        ORDER BY active DESC, due_date
    """, fetch=True)

    if debts:

        st.dataframe(
            pd.DataFrame([
                {
                    "ID": x["id"],
                    "Name": x["name"],
                    "Type": x["debt_type"],
                    "Original": x["original_amount"],
                    "Remaining": x["remaining_amount"],
                    "Due Date": x["due_date"],
                    "Active": bool(x["active"])
                }
                for x in debts
            ]),
            use_container_width=True,
            hide_index=True
        )

        active_debts = [
            x for x in debts
            if x["active"]
            and x["remaining_amount"] > 0
        ]

        if active_debts:

            selected_debt = st.selectbox(
                "Debt",
                active_debts,
                format_func=lambda x: x["name"]
            )

            payment = st.number_input(
                "Payment",
                min_value=0.0,
                step=100.0
            )

            if st.button(
                "Update Debt Balance"
            ):

                if payment <= 0:

                    st.error(
                        "Payment must be positive."
                    )

                elif payment > selected_debt["remaining_amount"]:

                    st.error(
                        "Payment cannot exceed remaining debt."
                    )

                else:

                    execute("""
                        UPDATE debts
                        SET remaining_amount=?
                        WHERE id=?
                    """, (
                        float(
                            selected_debt["remaining_amount"]
                        ) - payment,
                        selected_debt["id"]
                    ))

                    st.success(
                        "Debt balance updated."
                    )

                    st.rerun()

    with st.form("debt_form"):

        st.subheader("Add Debt")

        name = st.text_input(
            "Name"
        )

        debt_type = st.selectbox(
            "Type",
            [
                "owed_by_me",
                "owed_to_me"
            ]
        )

        amount = st.number_input(
            "Original Amount",
            min_value=0.0,
            step=500.0
        )

        due_date = st.date_input(
            "Due Date",
            date.today() + timedelta(days=30)
        )

        notes = st.text_area(
            "Notes"
        )

        submit = st.form_submit_button(
            "Add Debt"
        )

    if submit:

        if not name.strip() or amount <= 0:

            st.error(
                "Name and positive amount are required."
            )

        else:

            execute("""
                INSERT INTO debts
                (
                    name,
                    debt_type,
                    original_amount,
                    remaining_amount,
                    due_date,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                name.strip(),
                debt_type,
                amount,
                amount,
                due_date.isoformat(),
                notes,
                now()
            ))

            st.success(
                "Debt added."
            )

            st.rerun()


# ============================================================
# RECURRING
# ============================================================

elif page == "Recurring":

    st.title("Recurring Income & Expenses")

    st.caption(
        "Recurring items are templates. They become actual transactions only when generated."
    )

    items = execute("""
        SELECT *
        FROM recurring_items
        ORDER BY active DESC, item_type, name
    """, fetch=True)

    if items:

        st.dataframe(
            pd.DataFrame([
                {
                    "ID": x["id"],
                    "Name": x["name"],
                    "Type": x["item_type"],
                    "Amount": x["amount"],
                    "Day": x["day_of_month"],
                    "Active": bool(x["active"]),
                    "Last Generated": x["last_generated_month"]
                }
                for x in items
            ]),
            use_container_width=True,
            hide_index=True
        )

        if st.button(
            f"Generate Recurring Items for {month_name(selected_month)}",
            disabled=budget["status"] == "closed"
        ):

            generated = 0

            for item in items:

                if not item["active"]:
                    continue

                if item["last_generated_month"] == selected_month:
                    continue

                y, m = map(
                    int,
                    selected_month.split("-")
                )

                day = min(
                    int(item["day_of_month"]),
                    monthrange(y, m)[1]
                )

                transaction_date = (
                    f"{selected_month}-{day:02d}"
                )

                transaction_type = (
                    "income"
                    if item["item_type"] == "income"
                    else "expense"
                )

                execute("""
                    INSERT INTO transactions
                    (
                        monthly_budget_id,
                        transaction_date,
                        transaction_type,
                        amount,
                        category_id,
                        account_id,
                        description,
                        planned,
                        notes,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    budget["id"],
                    transaction_date,
                    transaction_type,
                    item["amount"],
                    item["category_id"],
                    item["account_id"],
                    item["name"],
                    "Generated from recurring item",
                    now()
                ))

                execute("""
                    UPDATE recurring_items
                    SET last_generated_month=?
                    WHERE id=?
                """, (
                    selected_month,
                    item["id"]
                ))

                generated += 1

            st.success(
                f"{generated} recurring item(s) generated."
            )

            st.rerun()

    with st.form("recurring_form"):

        st.subheader(
            "Create Recurring Item"
        )

        name = st.text_input(
            "Name"
        )

        item_type = st.selectbox(
            "Type",
            ["expense", "income"]
        )

        amount = st.number_input(
            "Amount",
            min_value=0.0,
            step=100.0
        )

        day = st.number_input(
            "Day of Month",
            min_value=1,
            max_value=31,
            value=1
        )

        categories = get_categories()
        accounts = get_accounts()

        category = st.selectbox(
            "Category",
            categories,
            format_func=lambda x: x["name"]
        )

        account = st.selectbox(
            "Account",
            accounts,
            format_func=lambda x: x["name"]
        )

        notes = st.text_area(
            "Notes"
        )

        submit = st.form_submit_button(
            "Create Recurring Item"
        )

    if submit:

        if not name.strip() or amount <= 0:

            st.error(
                "Name and positive amount are required."
            )

        else:

            execute("""
                INSERT INTO recurring_items
                (
                    name,
                    item_type,
                    amount,
                    category_id,
                    account_id,
                    day_of_month,
                    active,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (
                name.strip(),
                item_type,
                amount,
                category["id"],
                account["id"],
                day,
                notes,
                now()
            ))

            st.success(
                "Recurring item created."
            )

            st.rerun()


# ============================================================
# ANALYTICS
# ============================================================

elif page == "Analytics":

    st.title(
        f"Analytics — {month_name(selected_month)}"
    )

    df = transactions(selected_month)

    if df.empty:

        st.info(
            "There are no transactions to analyze."
        )

    else:

        expenses = df[
            df["transaction_type"].isin(
                [
                    "expense",
                    "bill_payment",
                    "debt_payment"
                ]
            )
        ]

        if not expenses.empty:

            category_chart = (
                expenses
                .groupby("category_name")["amount"]
                .sum()
                .reset_index()
            )

            fig = px.pie(
                category_chart,
                names="category_name",
                values="amount",
                title="Spending by Category"
            )

            st.plotly_chart(
                fig,
                use_container_width=True
            )

            daily = (
                expenses
                .groupby("transaction_date")["amount"]
                .sum()
                .reset_index()
            )

            fig2 = px.line(
                daily,
                x="transaction_date",
                y="amount",
                markers=True,
                title="Daily Spending"
            )

            st.plotly_chart(
                fig2,
                use_container_width=True
            )

    st.subheader("Monthly History")

    history = execute("""
        SELECT month_key
        FROM monthly_budgets
        ORDER BY month_key
    """, fetch=True)

    history_data = []

    for row in history:

        key = row["month_key"]
        b = get_budget(key)

        history_data.append({
            "Month": month_name(key),
            "Planned Income": b["planned_income"],
            "Actual Income": income_total(key),
            "Spending": spending_total(key),
            "Savings": savings_total(key),
            "Available": available_money(key),
            "Status": b["status"]
        })

    st.dataframe(
        pd.DataFrame(history_data),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# AI COACH
# ============================================================

elif page == "AI Coach":

    st.title(
        f"AI Financial Coach — {month_name(selected_month)}"
    )

    st.caption(
        "AI analyzes the selected month only."
    )

    api_key = (
        st.secrets.get("GROQ_API_KEY")
        if hasattr(st, "secrets")
        else None
    )

    api_key = (
        api_key
        or os.getenv("GROQ_API_KEY")
    )

    model = (
        (
            st.secrets.get("GROQ_MODEL")
            if hasattr(st, "secrets")
            else None
        )
        or os.getenv("GROQ_MODEL")
        or "llama-3.1-8b-instant"
    )

    df = transactions(selected_month)

    snapshot = {
        "month": selected_month,
        "currency": currency(),
        "planned_income": budget["planned_income"],
        "savings_target": budget["savings_target"],
        "explicit_rollover": budget["rollover_in"],
        "actual_income": income_total(selected_month),
        "actual_spending": spending_total(selected_month),
        "savings": savings_total(selected_month),
        "available": available_money(selected_month),
        "transactions": (
            df.fillna("").to_dict("records")
            if not df.empty
            else []
        )
    }

    question = st.text_area(
        "Ask your AI financial coach",
        placeholder=(
            "Where did my money go this month?\n"
            "Am I overspending?\n"
            "How much can I safely spend for the rest of the month?\n"
            "Can I afford my wishlist purchase?"
        )
    )

    if st.button(
        "Ask Groq",
        type="primary"
    ):

        if not question.strip():

            st.error(
                "Please enter a question."
            )

        elif not api_key:

            st.error(
                "GROQ_API_KEY is missing. Add it to Streamlit Secrets."
            )

        elif Groq is None:

            st.error(
                "The Groq Python package is not installed."
            )

        else:

            system_prompt = """
You are BudgetWise AI, a personal finance coach.

Only analyze the selected month's data.

Accounting rules:

1. Income is money actually received.
2. Expenses are money actually spent.
3. ATM withdrawals are NOT expenses.
4. Transfers between the user's own accounts are NOT income or expenses.
5. Savings movements are NOT expenses.
6. Refunds increase available money.
7. Wishlist amounts are estimates until actually purchased.
8. Budgets are plans.
9. Transactions are actual records.
10. Never invent financial data.
11. Clearly distinguish actual, planned,
    estimated and simulated amounts.
12. If information is missing, say so.
13. Give practical and conservative advice.
14. Use the user's currency.
15. Answer in clear English.

Useful analyses include:

- spending by category
- budget vs actual
- overspending
- savings progress
- spending velocity
- safe remaining spending
- unnecessary spending
- goal affordability
- wishlist affordability
- financial risks
- what-if scenarios
"""

            try:

                client = Groq(
                    api_key=api_key
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content":
                                "MONTH DATA:\n"
                                + json.dumps(
                                    snapshot,
                                    default=str,
                                    indent=2
                                )
                                + "\n\nQUESTION:\n"
                                + question
                        }
                    ],
                    temperature=0.2
                )

                answer = (
                    response
                    .choices[0]
                    .message
                    .content
                )

                st.subheader(
                    "AI Coach Response"
                )

                st.write(answer)

            except Exception as error:

                st.error(
                    f"Groq request failed: {error}"
                )

                st.caption(
                    f"Configured model: {model}"
                )


# ============================================================
# SETTINGS
# ============================================================

elif page == "Settings":

    st.title("Settings")

    user = get_user()

    with st.form("settings"):

        name = st.text_input(
            "Name",
            value=user["name"]
        )

        currency_value = st.text_input(
            "Currency",
            value=user["currency"]
        )

        default_money = st.number_input(
            "Default Monthly Money",
            min_value=0.0,
            value=float(
                user["default_monthly_money"]
            ),
            step=500.0
        )

        default_savings = st.number_input(
            "Default Savings Target",
            min_value=0.0,
            value=float(
                user["default_savings_target"]
            ),
            step=500.0
        )

        save = st.form_submit_button(
            "Save Settings",
            type="primary"
        )

    if save:

        execute("""
            UPDATE users
            SET
                name=?,
                currency=?,
                default_monthly_money=?,
                default_savings_target=?
            WHERE id=1
        """, (
            name,
            currency_value,
            default_money,
            default_savings
        ))

        st.success(
            "Settings saved."
        )

        st.rerun()

    st.subheader("Add Custom Category")

    with st.form("custom_category"):

        category_name = st.text_input(
            "Category Name"
        )

        category_type = st.selectbox(
            "Category Type",
            ["expense", "saving"]
        )

        submit = st.form_submit_button(
            "Add Category"
        )

    if submit:

        if not category_name.strip():

            st.error(
                "Category name is required."
            )

        else:

            try:

                execute("""
                    INSERT INTO categories
                    (name, category_type)
                    VALUES (?, ?)
                """, (
                    category_name.strip(),
                    category_type
                ))

                st.success(
                    "Category added."
                )

                st.rerun()

            except sqlite3.IntegrityError:

                st.error(
                    "That category already exists."
                )

    st.divider()

    st.subheader(
        f"Export {month_name(selected_month)}"
    )

    df = transactions(selected_month)

    csv_data = df.to_csv(
        index=False
    ).encode("utf-8")

    st.download_button(
        "Download Selected Month CSV",
        data=csv_data,
        file_name=f"budgetwise_{selected_month}.csv",
        mime="text/csv"
    )

    st.subheader("Full SQLite Backup")

    if DB_PATH.exists():

        with open(DB_PATH, "rb") as file:

            database_data = file.read()

        st.download_button(
            "Download Full Database Backup",
            data=database_data,
            file_name="budgetwise_backup.db",
            mime="application/octet-stream"
        )

    st.divider()

    st.subheader("Groq Configuration")

    st.code(
        """GROQ_API_KEY="your_groq_api_key"
GROQ_MODEL="your_selected_groq_model"
""",
        language="toml"
    )

    st.warning(
        "Never put your real Groq API key directly inside app.py or commit it to GitHub."
    )

    st.info(
        "For a multi-user production deployment, use a persistent "
        "PostgreSQL/Supabase database instead of local SQLite."
    )


# ============================================================
# FOOTER
# ============================================================

st.sidebar.divider()

st.sidebar.caption(
    "BudgetWise AI\n"
    "Month-based personal finance management\n\n"
    "Income ≠ Withdrawal ≠ Transfer ≠ Expense"
)
