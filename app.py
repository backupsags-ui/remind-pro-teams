import streamlit as st
import sqlite3
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import time

# ==================== CONFIG ====================
st.set_page_config(
    page_title="Remind Pro Teams V8",
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
            notes TEXT,
            overdue_remind_count INTEGER DEFAULT 0,
            last_overdue_remind_at TEXT,
            handover_log TEXT DEFAULT '[]'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
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
    # Migrate existing tables if needed
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN overdue_remind_count INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN last_overdue_remind_at TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN handover_log TEXT DEFAULT '[]'")
    except:
        pass
    conn.commit()
    conn.close()

init_db()

def get_conn():
    return sqlite3.connect(DB_PATH)

def now_vn():
    return datetime.now(VN_TZ)

def now_str():
    return now_vn().strftime("%Y-%m-%d %H:%M")

# ==================== SCHEDULER ====================
def check_and_fire_reminders():
    try:
        conn = get_conn()
        c = conn.cursor()
        now_s = now_str()
        c.execute("""
            SELECT * FROM reminders 
            WHERE status IN ('Pending', 'In Handover') 
            AND remind_at <= ? 
            AND remind_at != ''
        """, (now_s,))
        rows = c.fetchall()
        columns = [d[0] for d in c.description]
        for row in rows:
            task = dict(zip(columns, row))
            is_overdue = True
            send_teams_card(task, is_overdue_alert=is_overdue)
            # Increment overdue count
            c.execute("""
                UPDATE reminders 
                SET overdue_remind_count = COALESCE(overdue_remind_count, 0) + 1,
                    last_overdue_remind_at = ?
                WHERE id = ?
            """, (now_s, task['id']))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Scheduler error: {e}")

if 'scheduler_started' not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_fire_reminders, 'interval', minutes=1)
    scheduler.start()
    st.session_state.scheduler_started = True

# ==================== TEAMS CARD ====================
def send_teams_card(task, is_overdue_alert=False):
    webhook = task.get('webhook_url') or st.session_state.get('default_webhook')
    if not webhook:
        return False
    
    assignees = json.loads(task.get('assignees_json') or '[]')
    checklist = json.loads(task.get('checklist_json') or '[]')
    priority = task.get('priority', 'P3')
    
    color = "Attention" if "P1" in priority or is_overdue_alert else "Accent"
    title_prefix = "🚨 QUÁ HẠN - " if is_overdue_alert else "⏰ "
    
    cl_text = "\n".join([
        f"- [{'✅' if i.get('done') else '⬜'}] {i.get('text','')}" 
        for i in checklist
    ]) if checklist else "Không có checklist"
    
    scope_display = "👤 **Người thực hiện:** " + (", ".join(assignees) if assignees else "Chưa gán")
    if task.get('created_by_name'):
        scope_display += f"\n\n👑 **Người giao việc / Giám sát:** {task.get('created_by_name')}"
    
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": f"{title_prefix}NHẮC VIỆC - Remind Pro", 
                     "weight": "Bolder", "color": color, "size": "Medium"},
                    {"type": "TextBlock", "text": task.get('title', ''), 
                     "wrap": True, "size": "Large", "weight": "Bolder"},
                    {"type": "FactSet", "facts": [
                        {"title": "Thời gian:", "value": task.get('remind_at', '')},
                        {"title": "Ưu tiên:", "value": priority},
                        {"title": "Trạng thái:", "value": task.get('status', 'Pending')},
                        {"title": "Số lần bàn giao:", "value": str(task.get('handover_count', 0))}
                    ]},
                    {"type": "TextBlock", "text": scope_display, "wrap": True},
                    {"type": "TextBlock", "text": f"**Checklist:**\n{cl_text}", "wrap": True},
                    {"type": "TextBlock", "text": task.get('description') or '', "wrap": True, "isSubtle": True}
                ]
            }
        }]
    }
    try:
        r = requests.post(webhook, json=payload, timeout=10)
        return r.status_code < 400
    except:
        return False

# ==================== UI ====================
st.sidebar.title("⏰ Remind Pro V8")
st.sidebar.caption("Hệ thống nhắc việc + Bàn giao ca")

menu = st.sidebar.radio("Menu", [
    "📌 Board View",
    "➕ Tạo việc mới",
    "👥 Danh bạ & Webhook",
    "📜 Nhật ký bàn giao",
    "📊 Thống kê",
    "❓ Hướng dẫn"
])

if 'current_user' not in st.session_state:
    st.session_state.current_user = ""

# ==================== BOARD VIEW ====================
if menu == "📌 Board View":
    st.title("📌 Board View - Không gian làm việc theo vai trò")
    
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM reminders ORDER BY remind_at ASC", conn)
    contacts_df = pd.read_sql("SELECT name FROM contacts ORDER BY name", conn)
    conn.close()
    
    contact_names = [""] + contacts_df['name'].tolist() if not contacts_df.empty else [""]
    
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        user = st.selectbox("👤 Chọn tài khoản của bạn", contact_names, key="user_select")
        st.session_state.current_user = user
    with col2:
        status_filter = st.selectbox("Lọc trạng thái", 
            ["Tất cả", "Pending", "In Handover", "Completed", "Quá hạn"])
    with col3:
        priority_filter = st.selectbox("Ưu tiên", ["Tất cả", "P1", "P2", "P3"])
    
    if not df.empty:
        # Apply filters
        if status_filter != "Tất cả":
            if status_filter == "Quá hạn":
                df = df[(df['remind_at'] <= now_str()) & (df['status'].isin(['Pending', 'In Handover']))]
            else:
                df = df[df['status'] == status_filter]
        if priority_filter != "Tất cả":
            df = df[df['priority'].str.contains(priority_filter, na=False)]
        
        if user:
            tab1, tab2 = st.tabs(["📥 Việc TÔI ĐƯỢC GIAO", "📤 Việc TÔI ĐÃ GIAO"])
            
            with tab1:
                my_tasks = df[df['assignees_json'].str.contains(user, na=False, regex=False)]
                overdue = my_tasks[(my_tasks['remind_at'] <= now_str()) & (my_tasks['status'] != 'Completed')]
                st.metric("Việc cần làm", len(my_tasks[my_tasks['status'] != 'Completed']), 
                         delta=f"{len(overdue)} quá hạn" if len(overdue) else None)
                
                for _, t in my_tasks.iterrows():
                    prio_icon = "🔴" if "P1" in str(t['priority']) else "🟡" if "P2" in str(t['priority']) else "🟢"
                    with st.expander(f"{prio_icon} #{t['id']} - {t['title']} | {t['status']} | {t['remind_at']}"):
                        st.write(f"**Mô tả:** {t['description'] or '-'}")
                        st.write(f"**Người giao:** {t['created_by_name'] or '-'}")
                        st.write(f"**Bàn giao:** {t['handover_count']} lần | Lần cuối: {t['last_handover_at'] or '-'}")
                        
                        # Checklist
                        cl = json.loads(t['checklist_json'] or '[]')
                        if cl:
                            st.write("**Checklist:**")
                            for i, item in enumerate(cl):
                                checked = st.checkbox(item.get('text',''), value=item.get('done', False), 
                                                     key=f"cl_{t['id']}_{i}")
                                cl[i]['done'] = checked
                            if st.button("💾 Lưu Checklist", key=f"savecl_{t['id']}"):
                                conn = get_conn()
                                conn.execute("UPDATE reminders SET checklist_json=? WHERE id=?", 
                                           (json.dumps(cl), t['id']))
                                conn.commit()
                                conn.close()
                                st.success("Đã lưu")
                                st.rerun()
                        
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            if st.button("💬 Bàn giao vào Task Chat", key=f"ho_{t['id']}"):
                                send_teams_card(t.to_dict(), is_overdue_alert=False)
                                log = json.loads(t.get('handover_log') or '[]')
                                log.append(f"[{now_str()}] {user} đã bàn giao")
                                conn = get_conn()
                                conn.execute("""
                                    UPDATE reminders 
                                    SET status='In Handover', 
                                        handover_count=COALESCE(handover_count,0)+1,
                                        last_handover_at=?,
                                        handover_log=?
                                    WHERE id=?
                                """, (now_str(), json.dumps(log), t['id']))
                                conn.commit()
                                conn.close()
                                st.toast("Đã bàn giao!")
                                st.rerun()
                        with c2:
                            if st.button("✅ Complete", key=f"comp_{t['id']}"):
                                rec = t['recurrence']
                                if rec in ["Hàng ngày", "Hàng tuần", "Hàng tháng"]:
                                    dt = datetime.strptime(t['remind_at'], "%Y-%m-%d %H:%M")
                                    if rec == "Hàng ngày":
                                        next_r = (dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
                                    elif rec == "Hàng tuần":
                                        next_r = (dt + timedelta(weeks=1)).strftime("%Y-%m-%d %H:%M")
                                    else:
                                        next_r = (dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
                                    # Reset checklist
                                    for item in cl:
                                        item['done'] = False
                                    conn = get_conn()
                                    conn.execute("""
                                        UPDATE reminders 
                                        SET remind_at=?, status='Pending', checklist_json=?,
                                            overdue_remind_count=0, last_overdue_remind_at=''
                                        WHERE id=?
                                    """, (next_r, json.dumps(cl), t['id']))
                                    conn.commit()
                                    conn.close()
                                    st.success(f"Đã hoàn thành lượt này! Kỳ tiếp theo: {next_r}")
                                else:
                                    conn = get_conn()
                                    conn.execute("""
                                        UPDATE reminders 
                                        SET status='Completed', completed_at=?, checklist_json=?
                                        WHERE id=?
                                    """, (now_str(), json.dumps(cl), t['id']))
                                    conn.commit()
                                    conn.close()
                                    st.success("Đã hoàn thành!")
                                st.rerun()
                        with c3:
                            if st.button("⏰ Hoãn 30 phút", key=f"snooze_{t['id']}"):
                                dt = datetime.strptime(t['remind_at'], "%Y-%m-%d %H:%M")
                                new_t = (dt + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
                                conn = get_conn()
                                conn.execute("UPDATE reminders SET remind_at=?, overdue_remind_count=0 WHERE id=?", 
                                           (new_t, t['id']))
                                conn.commit()
                                conn.close()
                                st.success(f"Đã hoãn đến {new_t}")
                                st.rerun()
            
            with tab2:
                created = df[df['created_by_name'] == user]
                st.metric("Việc đã giao", len(created))
                if not created.empty:
                    st.dataframe(created[['id','title','status','remind_at','priority','assignees_json']], 
                               use_container_width=True)
        else:
            st.info("Chọn tên của bạn ở trên để xem việc được giao / đã giao.")
            st.dataframe(df[['id','title','status','remind_at','priority','created_by_name']], 
                        use_container_width=True)
    else:
        st.info("Chưa có công việc nào. Hãy tạo việc mới!")

# ==================== TẠO VIỆC ====================
elif menu == "➕ Tạo việc mới":
    st.title("➕ Tạo nhắc việc mới")
    
    contacts_df = pd.read_sql("SELECT name FROM contacts", get_conn())
    contact_list = contacts_df['name'].tolist() if not contacts_df.empty else []
    
    with st.form("new_task"):
        title = st.text_input("Tiêu đề công việc *")
        description = st.text_area("Mô tả chi tiết / Ghi chú")
        col1, col2 = st.columns(2)
        with col1:
            remind_date = st.date_input("Ngày nhắc", value=now_vn().date())
            remind_time = st.time_input("Giờ nhắc")
        with col2:
            recurrence = st.selectbox("Lặp lại", ["Không lặp", "Hàng ngày", "Hàng tuần", "Hàng tháng"])
            priority = st.selectbox("Mức ưu tiên", ["P1 - Khẩn cấp", "P2 - Cao", "P3 - Bình thường"])
        
        assignees = st.multiselect("Người nhận việc", contact_list)
        checklist_raw = st.text_area("Checklist (mỗi dòng 1 mục)", 
                                    placeholder="Kiểm tra chuyến bay\nGửi báo cáo hãng\nCập nhật hệ thống")
        
        submitted = st.form_submit_button("💾 Lưu việc", type="primary")
        if submitted and title:
            checklist = [{"text": line.strip(), "done": False} 
                        for line in checklist_raw.split("\n") if line.strip()]
            remind_at = datetime.combine(remind_date, remind_time).strftime("%Y-%m-%d %H:%M")
            conn = get_conn()
            conn.execute("""
                INSERT INTO reminders (title, description, remind_at, recurrence, priority, 
                                     assignees_json, created_by_name, checklist_json, created_at, handover_log)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, description, remind_at, recurrence, priority, 
                  json.dumps(assignees), st.session_state.current_user or "Admin",
                  json.dumps(checklist), now_str(), "[]"))
            conn.commit()
            conn.close()
            st.success("Đã tạo việc thành công!")

# ==================== DANH BẠ ====================
elif menu == "👥 Danh bạ & Webhook":
    st.title("👥 Danh bạ & Cấu hình Webhook")
    
    tab1, tab2 = st.tabs(["Danh bạ nhân sự", "Webhook Teams"])
    
    with tab1:
        with st.form("add_contact"):
            c1, c2, c3 = st.columns(3)
            with c1:
                name = st.text_input("Tên *")
            with c2:
                email = st.text_input("Email")
            with c3:
                role = st.text_input("Vai trò")
            if st.form_submit_button("➕ Thêm nhân sự"):
                if name:
                    conn = get_conn()
                    try:
                        conn.execute("INSERT INTO contacts (name, email, role) VALUES (?,?,?)", 
                                   (name, email, role))
                        conn.commit()
                        st.success("Đã thêm")
                    except:
                        st.warning("Tên đã tồn tại")
                    conn.close()
                    st.rerun()
        
        contacts = pd.read_sql("SELECT * FROM contacts ORDER BY name", get_conn())
        st.dataframe(contacts, use_container_width=True)
    
    with tab2:
        st.subheader("Webhook mặc định")
        webhook = st.text_input("Webhook URL của Channel/Chat", 
                               value=st.session_state.get('default_webhook', ''))
        if st.button("💾 Lưu Webhook"):
            st.session_state.default_webhook = webhook
            st.success("Đã lưu webhook mặc định")
        
        st.info("""
        **Cách lấy Webhook:**
        1. Vào Channel Teams → dấu ... → **Workflows**
        2. Chọn mẫu **"Post to a channel when a webhook request is received"**
        3. Copy URL và dán vào đây
        """)

# ==================== NHẬT KÝ BÀN GIAO ====================
elif menu == "📜 Nhật ký bàn giao":
    st.title("📜 Nhật ký bàn giao ca")
    df = pd.read_sql("""
        SELECT id, title, status, handover_count, last_handover_at, handover_log, created_by_name
        FROM reminders 
        WHERE handover_count > 0 OR status = 'In Handover'
        ORDER BY last_handover_at DESC
    """, get_conn())
    if not df.empty:
        for _, row in df.iterrows():
            with st.expander(f"#{row['id']} - {row['title']} ({row['handover_count']} lần)"):
                st.write(f"**Trạng thái:** {row['status']}")
                st.write(f"**Lần cuối:** {row['last_handover_at'] or '-'}")
                log = json.loads(row['handover_log'] or '[]')
                if log:
                    st.write("**Lịch sử:**")
                    for entry in reversed(log):
                        st.write(f"- {entry}")
    else:
        st.info("Chưa có bàn giao nào.")

# ==================== THỐNG KÊ ====================
elif menu == "📊 Thống kê":
    st.title("📊 Thống kê")
    df = pd.read_sql("SELECT status, priority, COUNT(*) as count FROM reminders GROUP BY status, priority", get_conn())
    if not df.empty:
        st.bar_chart(df.pivot_table(index='status', columns='priority', values='count', fill_value=0))
    else:
        st.info("Chưa có dữ liệu")

# ==================== HƯỚNG DẪN ====================
elif menu == "❓ Hướng dẫn":
    st.title("❓ Hướng dẫn sử dụng")
    st.markdown("""
    ### Tính năng V8
    - **Role Workspace**: Chọn tên → xem việc được giao / việc đã giao
    - **Bàn giao ca**: Nút "Bàn giao vào Task Chat" + nhật ký
    - **Priority**: P1 (đỏ) / P2 (vàng) / P3 (xanh)
    - **Checklist**: Tick từng bước và lưu
    - **Recurrence**: Tự động nhảy kỳ tiếp theo khi Complete
    - **Snooze**: Hoãn 30 phút
    - **Overdue alert**: Scheduler gửi cảnh báo khi quá hạn
    
    ### Cài vào Teams
    Dùng file ZIP đã tạo trước đó (URL không đổi).
    """)

st.sidebar.markdown("---")
st.sidebar.caption(f"Giờ: {now_str()}")
