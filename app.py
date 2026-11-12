import streamlit as st
import sqlite3
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import time

st.set_page_config(page_title="Remind Pro Teams V8", page_icon="⏰", layout="wide", initial_sidebar_state="expanded")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DB_PATH = "reminders.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT, remind_at TEXT, recurrence TEXT DEFAULT 'Không lặp', status TEXT DEFAULT 'Pending', priority TEXT DEFAULT 'P3', assignees_json TEXT DEFAULT '[]', created_by_name TEXT, checklist_json TEXT DEFAULT '[]', handover_count INTEGER DEFAULT 0, last_handover_at TEXT, completed_at TEXT, webhook_url TEXT, created_at TEXT, notes TEXT, overdue_remind_count INTEGER DEFAULT 0, last_overdue_remind_at TEXT, handover_log TEXT DEFAULT '[]')''')
    c.execute('''CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, email TEXT, role TEXT, team TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS webhooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, type TEXT DEFAULT 'channel')''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    for col in ["overdue_remind_count INTEGER DEFAULT 0", "last_overdue_remind_at TEXT", "handover_log TEXT DEFAULT '[]'"]:
        try: c.execute(f"ALTER TABLE reminders ADD COLUMN {col}")
        except: pass
    conn.commit()
    conn.close()

init_db()

def get_conn(): return sqlite3.connect(DB_PATH)
def now_vn(): return datetime.now(VN_TZ)
def now_str(): return now_vn().strftime("%Y-%m-%d %H:%M")

def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

if 'default_webhook' not in st.session_state:
    st.session_state.default_webhook = get_setting("default_webhook", "")

def check_and_fire_reminders():
    try:
        conn = get_conn()
        c = conn.cursor()
        now_s = now_str()
        rows = c.execute("SELECT * FROM reminders WHERE status IN ('Pending', 'In Handover') AND remind_at <= ? AND remind_at != ''", (now_s,)).fetchall()
        cols = [d[0] for d in c.description]
        for row in rows:
            task = dict(zip(cols, row))
            send_teams_card(task, True)
            c.execute("UPDATE reminders SET overdue_remind_count = COALESCE(overdue_remind_count,0)+1, last_overdue_remind_at=? WHERE id=?", (now_s, task['id']))
        conn.commit()
        conn.close()
    except Exception as e:
        print(e)

if 'scheduler_started' not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_fire_reminders, 'interval', minutes=1)
    scheduler.start()
    st.session_state.scheduler_started = True

def send_teams_card(task, is_overdue=False):
    webhook = task.get('webhook_url') or st.session_state.get('default_webhook') or get_setting("default_webhook")
    if not webhook: return False
    assignees = json.loads(task.get('assignees_json') or '[]')
    checklist = json.loads(task.get('checklist_json') or '[]')
    priority = task.get('priority', 'P3')
    color = "Attention" if "P1" in str(priority) or is_overdue else "Accent"
    prefix = "🚨 QUÁ HẠN - " if is_overdue else "⏰ "
    cl_text = "\n".join([f"- [{'✅' if i.get('done') else '⬜'}] {i.get('text','')}" for i in checklist]) or "Không có checklist"
    scope = "👤 **Người thực hiện:** " + (", ".join(assignees) if assignees else "Chưa gán")
    if task.get('created_by_name'): scope += f"\n\n👑 **Người giao:** {task.get('created_by_name')}"
    payload = {"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{"$schema":"http://adaptivecards.io/schemas/adaptive-card.json","type":"AdaptiveCard","version":"1.4","body":[{"type":"TextBlock","text":f"{prefix}NHẮC VIỆC - Remind Pro","weight":"Bolder","color":color,"size":"Medium"},{"type":"TextBlock","text":task.get('title',''),"wrap":True,"size":"Large","weight":"Bolder"},{"type":"FactSet","facts":[{"title":"Thời gian:","value":task.get('remind_at','')},{"title":"Ưu tiên:","value":priority},{"title":"Trạng thái:","value":task.get('status','Pending')},{"title":"Bàn giao:","value":str(task.get('handover_count',0))}]},{"type":"TextBlock","text":scope,"wrap":True},{"type":"TextBlock","text":f"**Checklist:**\n{cl_text}","wrap":True},{"type":"TextBlock","text":task.get('description') or '',"wrap":True,"isSubtle":True}]}}]}
    try:
        return requests.post(webhook, json=payload, timeout=10).status_code < 400
    except: return False

st.sidebar.title("⏰ Remind Pro V8")
st.sidebar.caption("Hệ thống nhắc việc + Bàn giao ca")
menu = st.sidebar.radio("Menu", ["📌 Board View", "➕ Tạo việc mới", "👥 Danh bạ & Webhook", "📜 Nhật ký bàn giao", "📊 Thống kê", "❓ Hướng dẫn"])
if 'current_user' not in st.session_state: st.session_state.current_user = ""

if menu == "📌 Board View":
    st.title("📌 Board View - Không gian làm việc theo vai trò")
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM reminders ORDER BY remind_at", conn)
    contacts_df = pd.read_sql("SELECT name FROM contacts ORDER BY name", conn)
    conn.close()
    names = [""] + contacts_df['name'].tolist() if not contacts_df.empty else [""]
    c1, c2, c3 = st.columns([2,1,1])
    with c1:
        user = st.selectbox("👤 Chọn tài khoản", names)
        st.session_state.current_user = user
    with c2: status_f = st.selectbox("Trạng thái", ["Tất cả","Pending","In Handover","Completed","Quá hạn"])
    with c3: prio_f = st.selectbox("Ưu tiên", ["Tất cả","P1","P2","P3"])
    if not df.empty:
        if status_f != "Tất cả":
            if status_f == "Quá hạn": df = df[(df.remind_at <= now_str()) & (df.status.isin(['Pending','In Handover']))]
            else: df = df[df.status == status_f]
        if prio_f != "Tất cả": df = df[df.priority.str.contains(prio_f, na=False)]
        if user:
            t1, t2 = st.tabs(["📥 Việc TÔI ĐƯỢC GIAO", "📤 Việc TÔI ĐÃ GIAO"])
            with t1:
                my = df[df.assignees_json.str.contains(user, na=False, regex=False)]
                st.metric("Việc cần làm", len(my[my.status != 'Completed']))
                for _, t in my.iterrows():
                    icon = "🔴" if "P1" in str(t.priority) else "🟡" if "P2" in str(t.priority) else "🟢"
                    with st.expander(f"{icon} #{t.id} - {t.title} | {t.status} | {t.remind_at}"):
                        st.write(f"**Mô tả:** {t.description or '-'}")
                        st.write(f"**Người giao:** {t.created_by_name or '-'}")
                        cl = json.loads(t.checklist_json or '[]')
                        if cl:
                            for i, item in enumerate(cl):
                                cl[i]['done'] = st.checkbox(item.get('text',''), value=item.get('done',False), key=f"cl{t.id}_{i}")
                            if st.button("Lưu Checklist", key=f"scl{t.id}"):
                                get_conn().execute("UPDATE reminders SET checklist_json=? WHERE id=?", (json.dumps(cl), t.id))
                                st.rerun()
                        b1, b2, b3 = st.columns(3)
                        with b1:
                            if st.button("Bàn giao", key=f"ho{t.id}"):
                                send_teams_card(t.to_dict())
                                log = json.loads(t.handover_log or '[]')
                                log.append(f"[{now_str()}] {user} bàn giao")
                                conn = get_conn()
                                conn.execute("UPDATE reminders SET status='In Handover', handover_count=COALESCE(handover_count,0)+1, last_handover_at=?, handover_log=? WHERE id=?", (now_str(), json.dumps(log), t.id))
                                conn.commit(); conn.close(); st.rerun()
                        with b2:
                            if st.button("Complete", key=f"cp{t.id}"):
                                conn = get_conn()
                                if t.recurrence in ["Hàng ngày","Hàng tuần","Hàng tháng"]:
                                    dt = datetime.strptime(t.remind_at, "%Y-%m-%d %H:%M")
                                    delta = timedelta(days=1) if t.recurrence=="Hàng ngày" else timedelta(weeks=1) if t.recurrence=="Hàng tuần" else timedelta(days=30)
                                    next_r = (dt+delta).strftime("%Y-%m-%d %H:%M")
                                    for i in cl: i['done']=False
                                    conn.execute("UPDATE reminders SET remind_at=?, status='Pending', checklist_json=?, overdue_remind_count=0 WHERE id=?", (next_r, json.dumps(cl), t.id))
                                else:
                                    conn.execute("UPDATE reminders SET status='Completed', completed_at=? WHERE id=?", (now_str(), t.id))
                                conn.commit(); conn.close(); st.rerun()
                        with b3:
                            if st.button("Hoãn 30p", key=f"sn{t.id}"):
                                dt = datetime.strptime(t.remind_at, "%Y-%m-%d %H:%M")
                                get_conn().execute("UPDATE reminders SET remind_at=? WHERE id=?", ((dt+timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"), t.id))
                                st.rerun()
            with t2:
                created = df[df.created_by_name == user]
                st.dataframe(created[['id','title','status','remind_at','priority']] if not created.empty else None)
        else:
            st.dataframe(df[['id','title','status','remind_at','priority','created_by_name']])
    else:
        st.info("Chưa có công việc nào.")

elif menu == "➕ Tạo việc mới":
    st.title("➕ Tạo nhắc việc mới")
    contacts = pd.read_sql("SELECT name FROM contacts", get_conn())['name'].tolist()
    with st.form("new"):
        title = st.text_input("Tiêu đề *")
        desc = st.text_area("Mô tả")
        d, t = st.columns(2)
        with d: rd = st.date_input("Ngày", now_vn().date()); rt = st.time_input("Giờ")
        with t: rec = st.selectbox("Lặp", ["Không lặp","Hàng ngày","Hàng tuần","Hàng tháng"]); prio = st.selectbox("Ưu tiên", ["P1 - Khẩn cấp","P2 - Cao","P3 - Bình thường"])
        assigns = st.multiselect("Người nhận", contacts)
        clraw = st.text_area("Checklist (mỗi dòng 1 mục)")
        if st.form_submit_button("Lưu việc") and title:
            cl = [{"text":l.strip(),"done":False} for l in clraw.split("\n") if l.strip()]
            rat = datetime.combine(rd, rt).strftime("%Y-%m-%d %H:%M")
            conn = get_conn()
            conn.execute("INSERT INTO reminders (title,description,remind_at,recurrence,priority,assignees_json,created_by_name,checklist_json,created_at,handover_log) VALUES (?,?,?,?,?,?,?,?,?,?)", (title,desc,rat,rec,prio,json.dumps(assigns),st.session_state.current_user or "Admin",json.dumps(cl),now_str(),"[]"))
            conn.commit(); conn.close()
            st.success("Đã tạo!")

elif menu == "👥 Danh bạ & Webhook":
    st.title("👥 Danh bạ & Webhook")
    t1, t2 = st.tabs(["Danh bạ", "Webhook"])
    with t1:
        with st.form("addc"):
            n = st.text_input("Tên *"); e = st.text_input("Email"); r = st.text_input("Vai trò")
            if st.form_submit_button("Thêm") and n:
                try: get_conn().execute("INSERT INTO contacts (name,email,role) VALUES (?,?,?)", (n,e,r)); st.success("Đã thêm")
                except: st.warning("Tên đã có")
                st.rerun()
        st.dataframe(pd.read_sql("SELECT * FROM contacts", get_conn()))
    with t2:
        st.subheader("Webhook mặc định")
        wh = st.text_input("Webhook URL", value=get_setting("default_webhook",""))
        if st.button("Lưu Webhook"):
            set_setting("default_webhook", wh)
            st.session_state.default_webhook = wh
            st.success("Đã lưu vào database")

elif menu == "📜 Nhật ký bàn giao":
    st.title("📜 Nhật ký bàn giao")
    df = pd.read_sql("SELECT * FROM reminders WHERE handover_count > 0 OR status='In Handover'", get_conn())
    if not df.empty:
        for _, row in df.iterrows():
            with st.expander(f"#{row.id} - {row.title}"):
                st.write(row.status, row.handover_count, row.last_handover_at)
                for e in reversed(json.loads(row.handover_log or '[]')): st.write("- "+e)
    else: st.info("Chưa có bàn giao")

elif menu == "📊 Thống kê":
    st.title("📊 Thống kê")
    df = pd.read_sql("SELECT status, priority, COUNT(*) c FROM reminders GROUP BY status, priority", get_conn())
    if not df.empty: st.bar_chart(df.pivot_table(index='status', columns='priority', values='c', fill_value=0))

elif menu == "❓ Hướng dẫn":
    st.markdown("""### Hướng dẫn\n- Webhook được lưu vào database, không mất khi refresh\n- Chọn tên để xem việc được giao / đã giao\n- Bàn giao, Complete, Hoãn 30 phút\n""")

st.sidebar.caption(f"Giờ: {now_str()}")
