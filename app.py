import os, json, sqlite3, hashlib, calendar
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from groq import Groq

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "budgetwise.db"

st.set_page_config(page_title="BudgetWise AI", page_icon="💰", layout="wide")

# -----------------------------
# Database
# -----------------------------
def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT 'User',
        currency TEXT DEFAULT 'PKR',
        monthly_income REAL DEFAULT 0,
        savings_target REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        opening_balance REAL DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        parent TEXT DEFAULT 'Other',
        budget REAL DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        account_id INTEGER,
        category_id INTEGER,
        type TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT,
        tx_date TEXT NOT NULL,
        merchant TEXT DEFAULT '',
        description TEXT DEFAULT '',
        payment_method TEXT DEFAULT '',
        planned_status TEXT DEFAULT 'Planned',
        tags TEXT DEFAULT '',
        linked_tx_id INTEGER,
        recurring_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS budget_periods(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        budget REAL NOT NULL,
        rollover REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS bills(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        amount REAL NOT NULL,
        due_date TEXT NOT NULL,
        frequency TEXT DEFAULT 'One-time',
        account_id INTEGER,
        paid INTEGER DEFAULT 0,
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS goals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        target REAL NOT NULL,
        saved REAL DEFAULT 0,
        deadline TEXT,
        priority TEXT DEFAULT 'Medium',
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS planned_purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        estimated REAL NOT NULL,
        actual REAL DEFAULT 0,
        desired_date TEXT,
        priority TEXT DEFAULT 'Medium',
        status TEXT DEFAULT 'Considering',
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS recurring(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        amount REAL NOT NULL,
        kind TEXT NOT NULL,
        category_id INTEGER,
        account_id INTEGER,
        frequency TEXT NOT NULL,
        next_date TEXT NOT NULL,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS debts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        person TEXT NOT NULL,
        original REAL NOT NULL,
        remaining REAL NOT NULL,
        kind TEXT NOT NULL,
        due_date TEXT,
        interest REAL DEFAULT 0,
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings(
        user_id INTEGER PRIMARY KEY,
        alerts INTEGER DEFAULT 1,
        budget_alert REAL DEFAULT 80,
        daily_alert REAL DEFAULT 120
    );
    """)
    con.commit()
    con.close()

def q(sql, params=(), one=False):
    con = db()
    cur = con.execute(sql, params)
    rows = cur.fetchone() if one else cur.fetchall()
    con.close()
    return rows

def exec_sql(sql, params=()):
    con = db()
    cur = con.execute(sql, params)
    con.commit()
    last = cur.lastrowid
    con.close()
    return last

init_db()

# -----------------------------
# Helpers
# -----------------------------
def money(x):
    return f"{float(x or 0):,.2f}"

def user_id():
    if "user_id" not in st.session_state:
        row = q("SELECT id FROM users ORDER BY id LIMIT 1", one=True)
        if row:
            st.session_state.user_id = row["id"]
        else:
            uid = exec_sql("INSERT INTO users(name) VALUES(?)", ("User",))
            st.session_state.user_id = uid
            seed_defaults(uid)
    return st.session_state.user_id

def seed_defaults(uid):
    cats = [
        ("Groceries","Food",2000),("Restaurants","Food",0),("Snacks","Food",0),
        ("Mess","Food",0),("Travel","Transportation",1000),("Fuel","Transportation",0),
        ("Rent","Housing",0),("Utilities","Housing",0),("Internet","Housing",0),
        ("Extra Needs","Personal",2000),("Healthcare","Personal",0),
        ("Education","Education",0),("Entertainment","Entertainment",0),
        ("Shopping","Personal",0),("Debt Payment","Financial",0),
        ("Family","Family",0),("Unplanned / Impulse","Miscellaneous",0),("Other","Miscellaneous",0)
    ]
    for n,p,b in cats:
        exec_sql("INSERT INTO categories(user_id,name,parent,budget) VALUES(?,?,?,?)",(uid,n,p,b))
    for n,k in [("Cash","Cash"),("Bank","Bank"),("Savings","Savings"),("Wallet","Digital Wallet")]:
        exec_sql("INSERT INTO accounts(user_id,name,kind) VALUES(?,?,?)",(uid,n,k))
    exec_sql("INSERT OR IGNORE INTO settings(user_id) VALUES(?)",(uid,))

uid = user_id()
user = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
currency = user["currency"] or "PKR"

def cats_df():
    return pd.DataFrame([dict(x) for x in q("SELECT * FROM categories WHERE user_id=? AND active=1 ORDER BY name",(uid,))])

def accounts_df():
    return pd.DataFrame([dict(x) for x in q("SELECT * FROM accounts WHERE user_id=? AND active=1 ORDER BY name",(uid,))])

def tx_df():
    rows = [dict(x) for x in q("SELECT t.*, c.name category, a.name account FROM transactions t LEFT JOIN categories c ON t.category_id=c.id LEFT JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? ORDER BY tx_date DESC,id DESC",(uid,))]
    return pd.DataFrame(rows)

def current_month_bounds():
    today = date.today()
    return date(today.year,today.month,1), date(today.year,today.month,calendar.monthrange(today.year,today.month)[1])

def month_txs():
    d=tx_df()
    if d.empty: return d
    s,e=current_month_bounds()
    d["tx_date"]=pd.to_datetime(d["tx_date"]).dt.date
    return d[(d.tx_date>=s)&(d.tx_date<=e)]

def actual_expenses(df=None):
    df = month_txs() if df is None else df
    return float(df.loc[df.type.isin(["expense","bill_payment","debt_payment"]),"amount"].sum()) if not df.empty else 0

def income_total(df=None):
    df=month_txs() if df is None else df
    return float(df.loc[df.type=="income","amount"].sum()) if not df.empty else 0

def own_balance(account_id):
    a=q("SELECT opening_balance FROM accounts WHERE id=? AND user_id=?",(account_id,uid),one=True)
    bal=float(a["opening_balance"] if a else 0)
    rows=q("SELECT type,amount FROM transactions WHERE account_id=? AND user_id=?",(account_id,uid))
    for r in rows:
        if r["type"] in ("income","refund"): bal+=r["amount"]
        elif r["type"] in ("expense","bill_payment","debt_payment","withdrawal","transfer_out"): bal-=r["amount"]
        elif r["type"] in ("deposit","transfer_in","saving"): bal+=r["amount"]
    return bal

def period_for(d=date.today()):
    rows=q("SELECT * FROM budget_periods WHERE user_id=? ORDER BY start_date",(uid,))
    for r in rows:
        if date.fromisoformat(r["start_date"])<=d<=date.fromisoformat(r["end_date"]):
            return r
    return None

def create_default_periods():
    y,m=date.today().year,date.today().month
    first_end=min(15,calendar.monthrange(y,m)[1])
    second_start=date(y,m,16)
    last=calendar.monthrange(y,m)[1]
    if not q("SELECT id FROM budget_periods WHERE user_id=? AND start_date=?",(uid,date(y,m,1).isoformat()),one=True):
        exec_sql("INSERT INTO budget_periods(user_id,name,start_date,end_date,budget) VALUES(?,?,?,?,?)",
                 (uid,"First Half",date(y,m,1).isoformat(),date(y,m,first_end).isoformat(),float(user["monthly_income"] or 0)/2))
        exec_sql("INSERT INTO budget_periods(user_id,name,start_date,end_date,budget) VALUES(?,?,?,?,?)",
                 (uid,"Second Half",second_start.isoformat(),date(y,m,last).isoformat(),float(user["monthly_income"] or 0)/2))

def category_id(name):
    r=q("SELECT id FROM categories WHERE user_id=? AND lower(name)=lower(?)",(uid,name),one=True)
    return r["id"] if r else None

# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.title("💰 BudgetWise AI")
st.sidebar.caption("Personal finance companion")

page = st.sidebar.radio("Go to",[
    "Dashboard","Add Transaction","Budgets & Periods","Accounts & Cash",
    "Bills","Goals & Wishlist","Debts","Analytics","AI Coach","Settings"
])

st.sidebar.divider()
st.sidebar.metric("Monthly income", f"{currency} {money(user['monthly_income'])}")
st.sidebar.metric("Monthly expenses", f"{currency} {money(actual_expenses())}")
net_savings = income_total()-actual_expenses()
st.sidebar.metric("Current savings", f"{currency} {money(net_savings)}")

# -----------------------------
# Dashboard
# -----------------------------
if page=="Dashboard":
    st.title("💰 Financial Dashboard")
    st.caption(f"{date.today().strftime('%d %B %Y')} • {currency}")

    df=month_txs()
    income=income_total(df); exp=actual_expenses(df); save=income-exp
    cols=st.columns(5)
    cols[0].metric("Income",f"{currency} {money(income)}")
    cols[1].metric("Expenses",f"{currency} {money(exp)}")
    cols[2].metric("Savings",f"{currency} {money(save)}")
    cols[3].metric("Savings rate",f"{(save/income*100 if income else 0):.1f}%")
    cols[4].metric("Monthly target",f"{currency} {money(user['savings_target'])}")

    p=period_for()
    if p:
        pstart=date.fromisoformat(p["start_date"]); pend=date.fromisoformat(p["end_date"])
        pdf=df[(df.tx_date>=pstart)&(df.tx_date<=pend)] if not df.empty else df
        spent=actual_expenses(pdf)
        remaining=float(p["budget"])-spent
        days=max(1,(pend-date.today()).days+1)
        daily=max(0,remaining/days)
        st.subheader(f"📦 Current period: {p['name']}")
        c=st.columns(4)
        c[0].metric("Period budget",f"{currency} {money(p['budget'])}")
        c[1].metric("Spent",f"{currency} {money(spent)}")
        c[2].metric("Remaining",f"{currency} {money(remaining)}")
        c[3].metric("Suggested daily limit",f"{currency} {money(daily)}")
        ratio=(spent/float(p["budget"])*100) if p["budget"] else 0
        if ratio>=100:
            st.error(f"⚠️ Period budget exceeded by {currency} {money(spent-float(p['budget']))}.")
        elif ratio>=80:
            st.warning(f"⚠️ {ratio:.0f}% of this period budget used. Spending thora slow rakhein.")
        else:
            st.success(f"✅ Spending pace looks manageable ({ratio:.0f}% used).")

    st.subheader("📊 Spending by category")
    if not df.empty:
        cdf=df[df.type.isin(["expense","bill_payment","debt_payment"])].groupby("category",dropna=False)["amount"].sum().reset_index()
        if not cdf.empty:
            fig=px.pie(cdf,names="category",values="amount",hole=.45)
            st.plotly_chart(fig,use_container_width=True)
    else:
        st.info("Abhi transactions nahi hain. Add Transaction se start karein.")

    st.subheader("🔔 Upcoming")
    bills=q("SELECT * FROM bills WHERE user_id=? AND paid=0 ORDER BY due_date LIMIT 8",(uid,))
    if bills:
        for b in bills:
            st.write(f"• **{b['name']}** — {currency} {money(b['amount'])} — due {b['due_date']}")
    else: st.caption("No unpaid bills.")

# -----------------------------
# Add Transaction
# -----------------------------
elif page=="Add Transaction":
    st.title("➕ Add Money Movement")
    st.info("Important: ATM withdrawal ≠ expense. Withdrawal records cash movement; spending is recorded separately.")

    typ=st.selectbox("Transaction type",[
        "expense","income","withdrawal","transfer_in","transfer_out","saving","refund","deposit","debt_payment"
    ])
    adf=accounts_df(); cdf=cats_df()
    with st.form("txform"):
        amount=st.number_input(f"Amount ({currency})",min_value=0.01,value=100.0,step=50.0)
        txdate=st.date_input("Date",date.today())
        account_name=st.selectbox("Account",adf.name.tolist() if not adf.empty else ["Cash"])
        cat_name=st.selectbox("Category",cdf.name.tolist() if not cdf.empty else ["Other"]) if typ in ["expense","refund","debt_payment"] else "Other"
        merchant=st.text_input("Merchant / person")
        desc=st.text_input("Description / notes")
        payment=st.selectbox("Payment method",["Cash","Debit card","Credit card","Bank transfer","Digital wallet","Other"])
        status=st.selectbox("Planning status",["Planned","Necessary","Optional","Unplanned","Impulse","Emergency"])
        tags=st.text_input("Tags (comma separated)")
        submitted=st.form_submit_button("Save transaction",type="primary")
    if submitted:
        aid=int(adf.loc[adf.name==account_name,"id"].iloc[0]) if not adf.empty else None
        cid=category_id(cat_name)
        exec_sql("""INSERT INTO transactions
            (user_id,account_id,category_id,type,amount,currency,tx_date,merchant,description,payment_method,planned_status,tags)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,aid,cid,typ,float(amount),currency,txdate.isoformat(),merchant,desc,payment,status,tags))
        st.success(f"✅ {currency} {money(amount)} {typ.replace('_',' ')} recorded.")
        st.rerun()

    st.divider()
    st.subheader("🧾 Recent transactions")
    d=tx_df()
    if not d.empty:
        st.dataframe(d[["id","tx_date","type","amount","category","account","merchant","planned_status","description"]].head(20),use_container_width=True)

# -----------------------------
# Budgets
# -----------------------------
elif page=="Budgets & Periods":
    st.title("📦 Budgets & Envelopes")
    st.write("Monthly, weekly, biweekly ya custom periods create karein. No fixed hostel rules are imposed.")
    create_default_periods()

    with st.form("period"):
        name=st.text_input("Period name","First Half")
        s=st.date_input("Start date",date.today().replace(day=1))
        e=st.date_input("End date",date.today().replace(day=min(15,calendar.monthrange(date.today().year,date.today().month)[1])))
        budget=st.number_input(f"Budget ({currency})",min_value=0.0,value=float(user["monthly_income"] or 0)/2,step=100.0)
        if st.form_submit_button("Add period"):
            if e<s: st.error("End date must be after start date.")
            else:
                exec_sql("INSERT INTO budget_periods(user_id,name,start_date,end_date,budget) VALUES(?,?,?,?,?)",(uid,name,s.isoformat(),e.isoformat(),budget))
                st.success("Period created."); st.rerun()

    rows=q("SELECT * FROM budget_periods WHERE user_id=? ORDER BY start_date DESC",(uid,))
    for p in rows:
        pdf=tx_df()
        if not pdf.empty:
            pdf["tx_date"]=pd.to_datetime(pdf.tx_date).dt.date
            pdf=pdf[(pdf.tx_date>=date.fromisoformat(p["start_date"]))&(pdf.tx_date<=date.fromisoformat(p["end_date"]))]
        spent=actual_expenses(pdf)
        rem=float(p["budget"])-spent
        st.write(f"**{p['name']}** | {p['start_date']} → {p['end_date']} | Budget {currency} {money(p['budget'])} | Spent {currency} {money(spent)} | Remaining {currency} {money(rem)}")

    st.subheader("Category budgets")
    cdf=cats_df()
    edited=st.data_editor(cdf[["id","name","parent","budget"]],hide_index=True,use_container_width=True,
                          column_config={"budget":st.column_config.NumberColumn("Budget",min_value=0)})
    if st.button("Save category budgets"):
        for _,r in edited.iterrows():
            exec_sql("UPDATE categories SET budget=?,parent=? WHERE id=? AND user_id=?",(float(r["budget"]),r["parent"],int(r["id"]),uid))
        st.success("Budgets updated."); st.rerun()

# -----------------------------
# Accounts / cash
# -----------------------------
elif page=="Accounts & Cash":
    st.title("🏦 Accounts, Cash & ATM")
    adf=accounts_df()
    cols=st.columns(max(1,min(4,len(adf))))
    for i,(_,a) in enumerate(adf.iterrows()):
        cols[i%len(cols)].metric(a["name"],f"{currency} {money(own_balance(int(a['id'])))}")
    st.subheader("Add account")
    with st.form("account"):
        n=st.text_input("Account name")
        k=st.selectbox("Type",["Cash","Bank","Savings","Digital Wallet","Credit Card","Other"])
        opening=st.number_input("Opening balance",min_value=0.0,value=0.0,step=100.0)
        if st.form_submit_button("Create account") and n:
            exec_sql("INSERT INTO accounts(user_id,name,kind,opening_balance) VALUES(?,?,?,?)",(uid,n,k,opening))
            st.success("Account added."); st.rerun()

    st.subheader("Cash / ATM summary")
    d=tx_df()
    if not d.empty:
        wd=float(d.loc[d.type=="withdrawal","amount"].sum())
        cash_sp=float(d.loc[(d.account.str.lower()=="cash") & d.type.isin(["expense","bill_payment","debt_payment"]),"amount"].sum()) if "account" in d else 0
        st.write(f"Withdrawals recorded: **{currency} {money(wd)}**")
        st.write(f"Cash-account spending recorded: **{currency} {money(cash_sp)}**")
        st.caption("Agar withdrawal aur cash expenses match nahi karte, reconciliation karein. Difference automatically expense nahi mana jayega.")

# -----------------------------
# Bills
# -----------------------------
elif page=="Bills":
    st.title("🧾 Bills & Recurring Payments")
    adf=accounts_df()
    with st.form("bill"):
        n=st.text_input("Bill name")
        amt=st.number_input("Amount",min_value=0.01,value=1000.0,step=100.0)
        due=st.date_input("Due date",date.today())
        freq=st.selectbox("Frequency",["One-time","Weekly","Monthly","Quarterly","Yearly","Custom"])
        acc=st.selectbox("Payment account",adf.name.tolist() if not adf.empty else ["Cash"])
        notes=st.text_input("Notes")
        if st.form_submit_button("Add bill") and n:
            aid=int(adf.loc[adf.name==acc,"id"].iloc[0]) if not adf.empty else None
            exec_sql("INSERT INTO bills(user_id,name,amount,due_date,frequency,account_id,notes) VALUES(?,?,?,?,?,?,?)",(uid,n,amt,due.isoformat(),freq,aid,notes))
            st.success("Bill added."); st.rerun()
    rows=q("SELECT b.*,a.name account FROM bills b LEFT JOIN accounts a ON b.account_id=a.id WHERE b.user_id=? ORDER BY b.paid,b.due_date",(uid,))
    for b in rows:
        c=st.columns([3,2,2,1])
        c[0].write(f"**{b['name']}** — {currency} {money(b['amount'])}")
        c[1].write(f"Due: {b['due_date']}")
        c[2].write("Paid" if b["paid"] else "Unpaid")
        if not b["paid"] and c[3].button("Mark paid",key=f"bill{b['id']}"):
            exec_sql("UPDATE bills SET paid=1 WHERE id=? AND user_id=?",(b["id"],uid))
            exec_sql("""INSERT INTO transactions(user_id,account_id,type,amount,currency,tx_date,description,planned_status)
                        VALUES(?,?,?,?,?,?,?,?)""",(uid,b["account_id"],"bill_payment",b["amount"],currency,date.today().isoformat(),b["name"],"Planned"))
            st.rerun()

# -----------------------------
# Goals / Wishlist
# -----------------------------
elif page=="Goals & Wishlist":
    st.title("🎯 Goals & Planned Purchases")
    left,right=st.columns(2)
    with left:
        st.subheader("Savings goals")
        with st.form("goal"):
            n=st.text_input("Goal name")
            target=st.number_input("Target amount",min_value=1.0,value=10000.0,step=500.0)
            saved=st.number_input("Already saved",min_value=0.0,value=0.0,step=500.0)
            deadline=st.date_input("Deadline",date.today()+timedelta(days=90))
            pri=st.selectbox("Priority",["Low","Medium","High"])
            if st.form_submit_button("Add goal") and n:
                exec_sql("INSERT INTO goals(user_id,name,target,saved,deadline,priority) VALUES(?,?,?,?,?,?)",(uid,n,target,saved,deadline.isoformat(),pri))
                st.rerun()
        for g in q("SELECT * FROM goals WHERE user_id=? ORDER BY priority DESC",(uid,)):
            rem=max(0,float(g["target"])-float(g["saved"]))
            days=max(1,(date.fromisoformat(g["deadline"])-date.today()).days)
            monthly=rem/(days/30.44)
            st.write(f"**{g['name']}** — {currency} {money(g['saved'])} / {money(g['target'])}")
            st.progress(min(1,float(g["saved"])/float(g["target"]) if g["target"] else 0))
            st.caption(f"Remaining {currency} {money(rem)} • roughly {currency} {money(monthly)}/month to deadline")
    with right:
        st.subheader("Wishlist / planned purchase")
        with st.form("wish"):
            n=st.text_input("Item name")
            est=st.number_input("Estimated amount",min_value=1.0,value=5000.0,step=500.0)
            desired=st.date_input("Desired date",date.today()+timedelta(days=30))
            pri=st.selectbox("Priority",["Low","Medium","High"],key="wp")
            if st.form_submit_button("Add planned purchase") and n:
                exec_sql("INSERT INTO planned_purchases(user_id,name,estimated,desired_date,priority) VALUES(?,?,?,?,?)",(uid,n,est,desired.isoformat(),pri))
                st.rerun()
        for w in q("SELECT * FROM planned_purchases WHERE user_id=? ORDER BY desired_date",(uid,)):
            st.write(f"**{w['name']}** — estimate {currency} {money(w['estimated'])} • {w['status']} • {w['desired_date']}")

# -----------------------------
# Debts
# -----------------------------
elif page=="Debts":
    st.title("🤝 Debts, Lending & Borrowing")
    with st.form("debt"):
        person=st.text_input("Person / institution")
        original=st.number_input("Original amount",min_value=1.0,value=5000.0,step=500.0)
        kind=st.selectbox("Type",["I owe them","They owe me"])
        due=st.date_input("Due date",date.today()+timedelta(days=30))
        interest=st.number_input("Interest %",min_value=0.0,value=0.0,step=.5)
        notes=st.text_input("Notes")
        if st.form_submit_button("Add debt") and person:
            exec_sql("INSERT INTO debts(user_id,person,original,remaining,kind,due_date,interest,notes) VALUES(?,?,?,?,?,?,?,?)",(uid,person,original,original,kind,due.isoformat(),interest,notes))
            st.rerun()
    rows=q("SELECT * FROM debts WHERE user_id=? ORDER BY due_date",(uid,))
    if rows:
        st.dataframe(pd.DataFrame([dict(x) for x in rows]),use_container_width=True)
    else: st.info("No debts recorded.")

# -----------------------------
# Analytics
# -----------------------------
elif page=="Analytics":
    st.title("📈 Analytics & Monthly Review")
    d=tx_df()
    if d.empty:
        st.info("Add some transactions first."); st.stop()
    d["tx_date"]=pd.to_datetime(d.tx_date)
    exp=d[d.type.isin(["expense","bill_payment","debt_payment"])].copy()
    exp["month"]=exp.tx_date.dt.to_period("M").astype(str)
    monthly=exp.groupby("month").amount.sum().reset_index()
    st.subheader("Monthly spending")
    st.plotly_chart(px.bar(monthly,x="month",y="amount"),use_container_width=True)

    st.subheader("Budget vs actual by category")
    cdf=cats_df()
    actual=exp.groupby("category").amount.sum().reset_index()
    merged=cdf[["name","budget"]].merge(actual,left_on="name",right_on="category",how="left").fillna(0)
    long=pd.concat([
        merged[["name","budget"]].rename(columns={"name":"category","budget":"amount"}).assign(kind="Budget"),
        merged[["name","amount"]].assign(kind="Actual")
    ])
    st.plotly_chart(px.bar(long,x="category",y="amount",color="kind",barmode="group"),use_container_width=True)

    st.subheader("Planned vs unplanned")
    plan=exp.groupby("planned_status").amount.sum().reset_index()
    st.plotly_chart(px.pie(plan,names="planned_status",values="amount",hole=.4),use_container_width=True)

    st.subheader("Where did the extra money go?")
    month=month_txs()
    cat=month[month.type.isin(["expense","bill_payment","debt_payment"])].groupby("category").amount.sum().sort_values(ascending=False)
    for k,v in cat.head(8).items():
        st.write(f"• {k}: **{currency} {money(v)}**")

# -----------------------------
# AI Coach
# -----------------------------
elif page=="AI Coach":
    st.title("🤖 AI Financial Coach")
    st.caption("Uses your stored transaction/budget data. Predictions are estimates, not facts.")

    api_key=st.secrets.get("GROQ_API_KEY",os.getenv("GROQ_API_KEY",""))
    model=st.secrets.get("GROQ_MODEL",os.getenv("GROQ_MODEL","llama-3.1-8b-instant"))

    if not api_key:
        st.warning("GROQ_API_KEY set nahi hai. Local/Streamlit secrets mein add karein.")
    prompt=st.chat_input("Ask: Where did my money go? Can I afford 3000? How much can I spend today?")
    if prompt:
        d=tx_df()
        snapshot={
            "today":str(date.today()),"currency":currency,
            "user":dict(user),
            "monthly_income":income_total(),
            "monthly_expenses":actual_expenses(),
            "monthly_savings":income_total()-actual_expenses(),
            "transactions":d.head(200).to_dict("records") if not d.empty else [],
            "accounts":[{"name":r["name"],"kind":r["kind"],"balance":own_balance(r["id"])} for r in q("SELECT * FROM accounts WHERE user_id=? AND active=1",(uid,))],
            "goals":[dict(r) for r in q("SELECT * FROM goals WHERE user_id=?",(uid,))],
            "bills":[dict(r) for r in q("SELECT * FROM bills WHERE user_id=? AND paid=0 ORDER BY due_date",(uid,))],
            "wishlist":[dict(r) for r in q("SELECT * FROM planned_purchases WHERE user_id=? AND status!='Purchased'",(uid,))]
        }
        if api_key:
            try:
                client=Groq(api_key=api_key)
                system="""You are BudgetWise AI, a careful personal budgeting assistant.
Answer in the user's language (English, Roman Urdu, or Urdu). Be concise and supportive.
Use ONLY the supplied financial snapshot for factual numbers. Never invent transactions.
Distinguish actual, planned, estimated, and hypothetical/simulated values.
A withdrawal is not an expense; transfers between own accounts are not income/expense.
For what-if questions, calculate a hypothetical outcome and do not alter data.
You can recommend budgeting actions, but do not guarantee financial outcomes or provide personalized regulated investment advice.
"""
                resp=client.chat.completions.create(
                    model=model,
                    temperature=.2,
                    messages=[{"role":"system","content":system},
                              {"role":"user","content":json.dumps({"question":prompt,"snapshot":snapshot},default=str)}]
                )
                st.chat_message("assistant").write(resp.choices[0].message.content)
            except Exception as e:
                st.error(f"Groq error: {e}")
        else:
            st.info("API key missing.")

# -----------------------------
# Settings
# -----------------------------
elif page=="Settings":
    st.title("⚙️ Settings")
    with st.form("settings"):
        name=st.text_input("Name",user["name"])
        cur=st.text_input("Currency",user["currency"] or "PKR")
        income=st.number_input("Monthly income / available budget",min_value=0.0,value=float(user["monthly_income"] or 0),step=500.0)
        target=st.number_input("Monthly savings target",min_value=0.0,value=float(user["savings_target"] or 0),step=500.0)
        if st.form_submit_button("Save settings",type="primary"):
            exec_sql("UPDATE users SET name=?,currency=?,monthly_income=?,savings_target=? WHERE id=?",(name,cur,income,target,uid))
            st.success("Settings saved."); st.rerun()

    st.subheader("Categories")
    cdf=cats_df()
    with st.form("newcat"):
        n=st.text_input("New category")
        parent=st.text_input("Parent/category group","Other")
        budget=st.number_input("Budget",min_value=0.0,value=0.0,step=100.0)
        if st.form_submit_button("Add category") and n:
            exec_sql("INSERT INTO categories(user_id,name,parent,budget) VALUES(?,?,?,?)",(uid,n,parent,budget))
            st.rerun()
    st.dataframe(cdf[["id","name","parent","budget"]],use_container_width=True)

    st.subheader("AI configuration")
    st.code("GROQ_API_KEY=your_key_here\nGROQ_MODEL=llama-3.1-8b-instant")
    st.caption("For deployment, put these in Streamlit Secrets or environment variables. Never commit your API key to GitHub.")

    st.subheader("Export")
    d=tx_df()
    if not d.empty:
        st.download_button("Download transactions CSV",d.to_csv(index=False).encode("utf-8"),"transactions.csv","text/csv")
        xlsx=Path("/tmp/budgetwise.xlsx")
        with pd.ExcelWriter(xlsx,engine="openpyxl") as writer:
            d.to_excel(writer,index=False,sheet_name="Transactions")
            cats_df().to_excel(writer,index=False,sheet_name="Categories")
        st.download_button("Download Excel",xlsx.read_bytes(),"budgetwise.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
