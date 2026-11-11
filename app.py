import streamlit as st
import sqlite3
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os
from zoneinfo import ZoneInfo

# ==================== CONFIG ====================
st.set_page_config(
    page_title="Remind Pro Teams",
    page_icon="⏰",
    layout="wide",
    initial_sidebar_state="expanded"
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DB_PATH = "reminders.db"

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            remind_at TEXT,
            recurrence TEXT DEFAULT 'Không lặp',
            status TEXT DEFAULT 'Pending',
            priority TEXT DEFAULT 'P3',
            assignees_json TEXT DEFAULT '[]',
            created_by_name TEXT,
            checklist_json TEXT DEFAULT '[]',
            handover_count INTEGER DEFAULT 0,
            last_handover_at TEXT,
            completed_at TEXT,
            webhook_url TEXT,
            created_at TEXT,
            notes TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            role TEXT,
            team TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT,
            type TEXT DEFAULT 'channel'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_conn():
    return sqlite3.connect(DB_PATH)

def now_vn():
    return datetime.now(VN_TZ)

# ==================== SCHEDULER ====================
def check_and_fire_reminders():
    try:
        conn = get_conn()
        c = conn.cursor()
        now_str = now_vn().strftime("%Y-%m-%d %H:%M")
        c.execute("""
            SELECT * FROM reminders 
            WHERE status IN ('Pending', 'In Handover') 
            AND remind_at <= ? 
            AND remind_at != ''
        """, (now_str,))
        rows = c.fetchall()
        columns = [d[0] for d in c.description]
        for row in rows:
            task = dict(zip(columns, row))
            send_teams_card(task)
            rec = task.get('recurrence', 'Không lặp')
            if rec != 'Không lặp':
                dt = datetime.strptime(task['remind_at'], "%Y-%m-%d %H:%M")
                if rec == 'Hàng ngày':
                    next_dt = dt + timedelta(days=1)
                elif rec == 'Hàng tuần':
                    next_dt = dt + timedelta(weeks=1)
                else:
                    next_dt = dt + timedelta(days=30)
                c.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", 
                         (next_dt.strftime("%Y-%m-%d %H:%M"), task['id']))
            else:
                c.execute("UPDATE reminders SET status = 'Sent' WHERE id = ?", (task['id'],))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Scheduler error: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(check_and_fire_reminders, 'interval', seconds=60)
scheduler.start()

# ==================== TEAMS CARD ====================
def send_teams_card(task):
    webhook = task.get('webhook_url') or st.session_state.get('default_webhook')
    if not webhook:
        return
    assignees = json.loads(task.get('assignees_json') or '[]')
    checklist = json.loads(task.get('checklist_json') or '[]')
    
    cl_text = "\n".join([f"- [{'x' if i.get('done') else ' '}] {i.get('text','')}" for i in checklist]) if checklist else "Không có checklist"
    
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": "⏰ NHẮC VIỆC - Remind Pro", "weight": "Bolder", "color": "Accent", "size": "Medium"},
                    {"type": "TextBlock", "text": task.get('title', ''), "wrap": True, "size": "Large", "weight": "Bolder"},
                    {"type": "FactSet", "facts": [
                        {"title": "Thời gian:", "value": task.get('remind_at', '')},
                        {"title": "Ưu tiên:", "value": task.get('priority', 'P3')},
                        {"title": "Người giao:", "value": task.get('created_by_name', 'N/A')},
                        {"title": "Người nhận:", "value": ", ".join(assignees) if assignees else "Chưa gán"}
                    ]},
                    {"type": "TextBlock", "text": f"**Checklist:**\n{cl_text}", "wrap": True},
                    {"type": "TextBlock", "text": task.get('description') or '', "wrap": True, "isSubtle": True}
                ]
            }
        }]
    }
    try:
        requests.post(webhook, json=payload, timeout=10)
    except:
        pass

# ==================== UI ====================
st.sidebar.title("⏰ Remind Pro")
st.sidebar.caption("Hệ thống nhắc việc cho Microsoft Teams")

menu = st.sidebar.radio("Menu", [
    "📌 Board View",
    "➕ Tạo việc mới",
    "👥 Danh bạ & Webhook",
    "📊 Thống kê",
    "❓ Hướng dẫn"
])

if 'current_user' not in st.session_state:
    st.session_state.current_user = ""

# ==================== BOARD VIEW ====================
if menu == "📌 Board View":
    st.title("📌 Board View - Quản lý công việc")
    
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM reminders ORDER BY remind_at", conn)
    contacts = pd.read_sql("SELECT name FROM contacts", conn)
    conn.close()
    
    col1, col2 = st.columns([2, 1])
    with col1:
        user = st.selectbox("👤 Chọn tài khoản của bạn", [""] + contacts['name'].tolist() if not contacts.empty else [""], 
                           index=0, key="user_select")
        st.session_state.current_user = user
    with col2:
        status_filter = st.selectbox("Lọc trạng thái", ["Tất cả", "Pending", "In Handover", "Completed", "Quá hạn"])
    
    if not df.empty:
        if user:
            tab1, tab2 = st.tabs(["📥 Việc TÔI ĐƯỢC GIAO", "📤 Việc TÔI ĐÃ GIAO"])
            
            with tab1:
                my_tasks = df[df['assignees_json'].str.contains(user, na=False)]
                st.metric("Việc cần làm", len(my_tasks[my_tasks['status'] != 'Completed']))
                for _, t in my_tasks.iterrows():
                    with st.expander(f"#{t['id']} - {t['title']} | {t['status']} | {t['remind_at']}"):
                        st.write(f"**Mô tả:** {t['description']}")
                        st.write(f"**Ưu tiên:** {t['priority']}")
                        if st.button("✅ Complete", key=f"comp_{t['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE reminders SET status='Completed', completed_at=? WHERE id=?", 
                                       (now_vn().strftime("%Y-%m-%d %H:%M"), t['id']))
                            conn.commit()
                            conn.close()
                            st.rerun()
            
            with tab2:
                created = df[df['created_by_name'] == user]
                st.metric("Việc đã giao", len(created))
                st.dataframe(created[['id','title','status','remind_at','priority']])
        else:
            st.dataframe(df[['id','title','status','remind_at','priority','created_by_name']])
    else:
        st.info("Chưa có công việc nào. Hãy tạo việc mới!")

# ==================== TẠO VIỆC ====================
elif menu == "➕ Tạo việc mới":
    st.title("➕ Tạo nhắc việc mới")
    
    with st.form("new_task"):
        title = st.text_input("Tiêu đề công việc *")
        description = st.text_area("Mô tả chi tiết")
        col1, col2 = st.columns(2)
        with col1:
            remind_date = st.date_input("Ngày nhắc", value=now_vn().date())
            remind_time = st.time_input("Giờ nhắc", value=now_vn().time())
        with col2:
            recurrence = st.selectbox("Lặp lại", ["Không lặp", "Hàng ngày", "Hàng tuần", "Hàng tháng"])
            priority = st.selectbox("Mức ưu tiên", ["P1 - Khẩn cấp", "P2 - Cao", "P3 - Bình thường"])
        
        assignees = st.multiselect("Người nhận việc", 
                                   pd.read_sql("SELECT name FROM contacts", get_conn())['name'].tolist() 
                                   if not pd.read_sql("SELECT name FROM contacts", get_conn()).empty else [])
        checklist_raw = st.text_area("Checklist (mỗi dòng 1 mục)", placeholder="Kiểm tra bay\nGửi báo cáo\n...")
        
        submitted = st.form_submit_button("💾 Lưu việc")
        if submitted and title:
            checklist = [{"text": line.strip(), "done": False} for line in checklist_raw.split("\n") if line.strip()]
            remind_at = datetime.combine(remind_date, remind_time).strftime("%Y-%m-%d %H:%M")
            conn = get_conn()
            conn.execute("""
                INSERT INTO reminders (title, description, remind_at, recurrence, priority, 
                                     assignees_json, created_by_name, checklist_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, description, remind_at, recurrence, priority, 
                  json.dumps(assignees), st.session_state.current_user or "Admin",
                  json.dumps(checklist), now_vn().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
            conn.close()
            st.success("Đã tạo việc thành công!")

# ==================== DANH BẠ ====================
elif menu == "👥 Danh bạ & Webhook":
    st.title("👥 Danh bạ & Cấu hình Webhook")
    
    tab1, tab2 = st.tabs(["Danh bạ", "Webhook Teams"])
    
    with tab1:
        st.subheader("Thêm nhân sự")
        with st.form("add_contact"):
            name = st.text_input("Tên")
            email = st.text_input("Email")
            role = st.text_input("Vai trò")
            if st.form_submit_button("Thêm"):
                conn = get_conn()
                conn.execute("INSERT INTO contacts (name, email, role) VALUES (?,?,?)", (name, email, role))
                conn.commit()
                conn.close()
                st.success("Đã thêm")
                st.rerun()
        
        contacts = pd.read_sql("SELECT * FROM contacts", get_conn())
        st.dataframe(contacts)
    
    with tab2:
        st.subheader("Cấu hình Webhook")
        webhook = st.text_input("Webhook URL của Channel/Chat", 
                               value=st.session_state.get('default_webhook', ''))
        if st.button("Lưu Webhook"):
            st.session_state.default_webhook = webhook
            st.success("Đã lưu webhook mặc định")
        
        st.info("Lấy Webhook: Teams Channel → ... → Workflows → Post to a channel when a webhook request is received")

# ==================== THỐNG KÊ ====================
elif menu == "📊 Thống kê":
    st.title("📊 Thống kê")
    df = pd.read_sql("SELECT status, COUNT(*) as count FROM reminders GROUP BY status", get_conn())
    if not df.empty:
        st.bar_chart(df.set_index('status'))
    else:
        st.info("Chưa có dữ liệu")

# ==================== HƯỚNG DẪN ====================
elif menu == "❓ Hướng dẫn":
    st.title("❓ Hướng dẫn sử dụng")
    st.markdown("""
    ### Cách deploy vào Microsoft Teams
    1. Deploy app này lên Streamlit Community Cloud hoặc Hugging Face Spaces
    2. Lấy URL (ví dụ: `https://xxx.streamlit.app`)
    3. Sửa `manifest.json` thay YOUR-SPACE-NAME.streamlit.app bằng domain của bạn
    4. Nén 3 file: manifest.json, color.png, outline.png thành ZIP
    5. Teams → Apps → Manage your apps → Upload a custom app → chọn ZIP
    6. Add to team/channel để mọi người dùng được
    
    ### Tính năng chính
    - Tạo việc với checklist, ưu tiên, lặp lại
    - Board View phân biệt việc được giao / đã giao
    - Gửi Adaptive Card vào Teams qua Webhook
    - Scheduler tự động kiểm tra mỗi phút
    """)

st.sidebar.markdown("---")
st.sidebar.caption(f"Giờ hiện tại: {now_vn().strftime('%H:%M %d/%m/%Y')}")
