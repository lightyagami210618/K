import telebot
from telebot import types
import sqlite3
import requests
import uuid
import secrets
import json
import urllib3

# HTTPS (IP) ချိတ်ဆက်သည့်အခါ တက်လာမည့် SSL Warning စာသားများကို ဖျောက်ထားခြင်း
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========================================================
# ၁။ CONFIGURATION (လူကြီးမင်း၏ အချက်အလက်များ ပြင်ဆင်ရန်နေရာ)
# ========================================================
BOT_TOKEN = ""  # BotFather မှရလာသော Token ထည့်ရန်
BOT_USERNAME = ""            # @ မပါဘဲ Bot ၏ Username ထည့်ရန် (Referral လင့်ခ်အတွက်)

# ADMIN များ၏ Telegram ID များကို ဤနေရာတွင် ထည့်ပါ
ADMIN_IDS = [] # 👈 မိမိ၏ ID အစစ် ပြောင်းထည့်ရန်

# Hysteria2 အတွက် ပုံသေ OBFS (UDP Mask) Password သတ်မှတ်ရန်နေရာ
UDP_MASK_PASSWORD = "4km1y03efq4ony16"

bot = telebot.TeleBot(BOT_TOKEN)

# Key Generation အတွက် User များ၏ ယာယီအခြေအနေကို မှတ်ရန် Dictionary
user_steps = {} 

# ========================================================
# ၂။ DATABASE SETUP (SQLite ဒေတာဘေ့စ် တည်ဆောက်ခြင်း)
# ========================================================
def init_db():
    conn = sqlite3.connect("vpn_bot.db")
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'user',
            credits REAL DEFAULT 0.0,
            referred_by INTEGER,
            is_registered INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            ip TEXT,
            domain TEXT,
            panel_url TEXT,
            username TEXT,
            password TEXT,
            protocol TEXT,
            inbound_id INTEGER,
            total_bandwidth REAL DEFAULT 1024.0,
            used_bandwidth REAL DEFAULT 0.0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vouchers (
            code TEXT PRIMARY KEY,
            amount REAL,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            server_id INTEGER,
            email TEXT,
            total_gb REAL
        )
    ''')
    
    conn.commit()
    conn.close()

# ========================================================
# ၃။ 3X-UI PANEL API HELPER FUNCTIONS
# ========================================================
def login_3xui(panel_url, username, password):
    session = requests.Session()
    try:
        login_url = f"{panel_url.rstrip('/')}/login"
        response = session.post(login_url, data={"username": username, "password": password}, timeout=5, verify=False)
        
        if response.status_code != 200 or not response.json().get("success"):
            login_url = f"{panel_url.rstrip('/')}/xui/login"
            response = session.post(login_url, data={"username": username, "password": password}, timeout=5, verify=False)

        if response.status_code == 200 and response.json().get("success"):
            return session
    except Exception as e:
        print(f"Login Error to {panel_url}: {e}")
    return None

def add_client_to_3xui(server_info, client_id_or_pass, email, gb_limit):
    session = login_3xui(server_info['panel_url'], server_info['username'], server_info['password'])
    if not session:
        return False
        
    total_bytes = int(gb_limit * 1073741824)
    
    if server_info['protocol'] == 'vless':
        client_settings = {"id": client_id_or_pass, "flow": "", "encryption": "none", "level": 0, "alterId": 0}
    else:
        client_settings = {
            "auth": client_id_or_pass,
            "id": str(uuid.uuid4()),
            "level": 0
        }

    client_data = {
        "id": int(server_info['inbound_id']),
        "settings": json.dumps({
            "clients": [{
                **client_settings,
                "email": email,
                "limitIp": 0,
                "totalGB": total_bytes,
                "expiryTime": 0,
                "enable": True,
                "tgId": str(email.split("_")[1])
            }]
        })
    }
    
    add_url = f"{server_info['panel_url'].rstrip('/')}/panel/api/inbounds/addClient"
    
    try:
        res = session.post(add_url, json=client_data, timeout=10, verify=False)
        if res.status_code == 200 and res.json().get("success"):
            return True
        return False
    except Exception as e:
        return False

# ========================================================
# ၄။ KEYBOARD NAVIGATION (Inline Buttons UI)
# ========================================================
def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_info = types.InlineKeyboardButton("👤 User Info", callback_data="main_info")
    btn_reg = types.InlineKeyboardButton("📝 Register", callback_data="main_register")
    btn_ref = types.InlineKeyboardButton("🔗 Refer", callback_data="main_refer")
    btn_gen = types.InlineKeyboardButton("🔑 Generate Key", callback_data="main_genkey")
    btn_mykeys = types.InlineKeyboardButton("📊 My Keys", callback_data="main_mykeys")
    btn_status = types.InlineKeyboardButton("🖥️ Server Status", callback_data="main_status")
    markup.add(btn_info, btn_reg, btn_ref, btn_gen, btn_mykeys, btn_status)
    return markup

# နောက်ကို ပြန်သွားမည့် Back Button ကို သီးသန့်ရေးဆွဲထားခြင်း
def get_back_button():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu"))
    return markup

# ========================================================
# ၅။ BOT HANDLERS (လုပ်ဆောင်ချက် အဆင့်ဆင့်)
# ========================================================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    tg_id = message.from_user.id
    username = message.from_user.username or f"User_{tg_id}"
    
    conn = sqlite3.connect("vpn_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_id,))
    user = cursor.fetchone()
    
    referred_by = None
    if len(message.text.split()) > 1 and not user:
        ref_payload = message.text.split()[1]
        if ref_payload.startswith("REF_"):
            try:
                referred_by = int(ref_payload.split("_")[1])
                if referred_by == tg_id:
                    referred_by = None
            except:
                referred_by = None

    if not user:
        cursor.execute("INSERT INTO users (telegram_id, username, referred_by) VALUES (?, ?, ?)", 
                       (tg_id, username, referred_by))
        conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, "✨ Premium VPN Bot မှ ကြိုဆိုပါတယ်ဗျာ။\nအောက်ပါ ခလုတ်များကို အသုံးပြုနိုင်ပါတယ်-", reply_markup=get_main_menu())

@bot.message_handler(commands=['get'])
def redeem_voucher_cmd(message):
    try:
        code = message.text.split()[1]
        tg_id = message.from_user.id
        
        conn = sqlite3.connect("vpn_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT amount, is_used FROM vouchers WHERE code = ?", (code,))
        row = cursor.fetchone()
        
        if row:
            if row[1] == 1:
                bot.reply_to(message, "⚠️ ဤ Voucher ကို အသုံးပြုပြီးသား ဖြစ်နေပါသည်ဗျာ။")
            else:
                amount = row[0]
                cursor.execute("UPDATE vouchers SET is_used = 1, used_by = ? WHERE code = ?", (tg_id, code))
                cursor.execute("UPDATE users SET credits = credits + ? WHERE telegram_id = ?", (amount, tg_id))
                conn.commit()
                bot.reply_to(message, f"🎉 Credit {amount} received! သင့်အကောင့်ထဲသို့ အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ။")
        else:
            bot.reply_to(message, "❌ Voucher code မှားယွင်းနေပါသည်။ (သို့) မရှိပါ။")
        conn.close()
    except IndexError:
        bot.reply_to(message, "⚠️ ပုံစံမမှန်ပါ။ ဥပမာ- `/get VOU-1A2B3C4D`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    tg_id = call.from_user.id
    chat_id = call.message.chat.id
    msg_id = call.message.message_id # မူလစာသားကို edit လုပ်ရန် message ID မှတ်ထားခြင်း
    
    conn = sqlite3.connect("vpn_bot.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_id,))
    user = cursor.fetchone()
    
    # --- Main Menu သို့ ပြန်သွားမည့်စနစ် ---
    if call.data == "main_menu":
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, 
                              text="✨ Premium VPN Bot မှ ကြိုဆိုပါတယ်ဗျာ။\nအောက်ပါ ခလုတ်များကို အသုံးပြုနိုင်ပါတယ်-", 
                              reply_markup=get_main_menu())
                              
    elif call.data == "main_info":
        msg = f"👤 *User Profile Info*\n\n"
        msg += f"🆔 Telegram ID: `{user['telegram_id']}`\n"
        msg += f"🎖️ Role Status: *{user['role'].upper()}*\n"
        msg += f"💰 Credit လက်ကျန်: *{user['credits']} Credits*"
        # အသစ်မပို့တော့ဘဲ edit_message_text ဖြင့် မူလ Box ကိုသာ ပြောင်းလိုက်ခြင်း
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg, parse_mode="Markdown", reply_markup=get_back_button())
        
    elif call.data == "main_register":
        if user['is_registered'] == 1:
            bot.answer_callback_query(call.id, "လူကြီးမင်းက Register လုပ်ပြီးသား ဖြစ်ပါတယ်ဗျာ။", show_alert=True)
        else:
            cursor.execute("UPDATE users SET credits = credits + 1, is_registered = 1 WHERE telegram_id = ?", (tg_id,))
            if user['referred_by']:
                cursor.execute("UPDATE users SET credits = credits + 10 WHERE telegram_id = ?", (user['referred_by'],))
                try:
                    # Referred User ဆီသို့ Alert လှမ်းပို့ခြင်း (ဒါကတော့ Chat ID မတူလို့ send_message ပဲ သုံးရပါမည်)
                    bot.send_message(user['referred_by'], f"🎉 လူကြီးမင်း ဖိတ်ခေါ်ထားသူတစ်ဦး အောင်မြင်စွာ Register လုပ်သွားသဖြင့် Reward *+10 Credits* ရရှိပါပြီဗျာ။", parse_mode="Markdown")
                except:
                    pass
            conn.commit()
            bot.answer_callback_query(call.id, "အကောင့်ဖွင့်ခြင်း အောင်မြင်ပြီး Bonus +1 Credit ရရှိပါပြီ။", show_alert=True)
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="📝 Register အောင်မြင်သွားပါပြီဗျာ။\n\n🎁 Bonus: +1 Credit", reply_markup=get_back_button())
            
    elif call.data == "main_refer":
        ref_link = f"https://t.me/{BOT_USERNAME}?start=REF_{tg_id}"
        msg = f"🔗 *လူကြီးမင်း၏ သီးသန့် Referral Link*\n\n"
        msg += f"`{ref_link}`\n\n"
        msg += f"💡 ဤလင့်ခ်ကို ကူးယူ၍ မိတ်ဆွေများကို ဖိတ်ခေါ်ပါ။ ထိုသူမှဝင်ရောက် Register လုပ်လျှင် လူကြီးမင်းထံ *+10 Credits* အလိုအလျောက် ရောက်ရှိပါမည်။"
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg, parse_mode="Markdown", reply_markup=get_back_button())
        
    elif call.data == "main_status":
        cursor.execute("SELECT * FROM servers")
        servers = cursor.fetchall()
        
        if not servers:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ လက်ရှိတွင် ထည့်သွင်းထားသော ဆာဗာမရှိသေးပါဗျာ။", reply_markup=get_back_button())
        else:
            msg = "🖥️ *Server Live Status & Bandwidth Pool*\n\n"
            for srv in servers:
                rem_bw = srv['total_bandwidth'] - srv['used_bandwidth']
                status_str = "🟢 Online"
                try:
                    requests.get(srv['panel_url'], timeout=2, verify=False)
                except:
                    status_str = "🔴 Offline"
                    
                msg += f"▪️ Server Name: {srv['name']} ({srv['protocol'].upper()})\n"
                msg += f"   Status: {status_str}\n"
                msg += f"   စုစုပေါင်း: {srv['total_bandwidth']} GB\n"
                msg += f"   အသုံးပြုပြီး: {srv['used_bandwidth']:.2f} GB\n"
                msg += f"   လက်ကျန်: {rem_bw:.2f} GB\n\n"
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg, parse_mode="HTML", reply_markup=get_back_button())

    elif call.data == "main_mykeys":
        cursor.execute("""
            SELECT uk.email, uk.total_gb, s.name, s.panel_url, s.username, s.password 
            FROM user_keys uk 
            JOIN servers s ON uk.server_id = s.id 
            WHERE uk.telegram_id = ?
        """, (tg_id,))
        user_keys = cursor.fetchall()
        
        if not user_keys:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ လူကြီးမင်းတွင် ဝယ်ယူထားသော Key မရှိသေးပါဗျာ။", reply_markup=get_back_button())
            return
            
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⏳ ခေတ္တစောင့်ပါ... ဆာဗာမှ Data အသုံးပြုမှု အခြေအနေကို စစ်ဆေးနေပါသည်။")
        
        msg = "📊 *လူကြီးမင်း၏ VPN Keys အသုံးပြုမှုများ*\n\n"
        for k in user_keys:
            email, total_gb, srv_name, panel, p_user, p_pass = k['email'], k['total_gb'], k['name'], k['panel_url'], k['username'], k['password']
            session = login_3xui(panel, p_user, p_pass)
            
            used_gb_str = "စစ်ဆေး၍မရပါ"
            rem_gb_str = "စစ်ဆေး၍မရပါ"
            
            if session:
                try:
                    traf_url = f"{panel.rstrip('/')}/panel/api/inbounds/getClientTraffics/{email}"
                    res = session.get(traf_url, timeout=5, verify=False)
                    if res.status_code == 200 and res.json().get("success"):
                        obj = res.json().get("obj", {})
                        up = obj.get("up", 0)
                        down = obj.get("down", 0)
                        used_bytes = up + down
                        used_gb = used_bytes / 1073741824
                        rem_gb = total_gb - used_gb
                        
                        used_gb_str = f"{used_gb:.2f} GB"
                        rem_gb_str = f"{rem_gb:.2f} GB"
                except Exception as e:
                    pass
            
            msg += f"🔹 *Server:* `{srv_name}`\n"
            msg += f"📧 *Client ID:* `{email}`\n"
            msg += f"📦 *Total:* `{total_gb} GB`\n"
            msg += f"📈 *Used:* `{used_gb_str}`\n"
            msg += f"📉 *Remaining:* `{rem_gb_str}`\n"
            msg += "--------------------------\n"
            
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg, parse_mode="Markdown", reply_markup=get_back_button())

    elif call.data == "main_genkey":
        if user['credits'] < 1:
            bot.answer_callback_query(call.id, "⚠️ ကီးထုတ်ရန် အနည်းဆုံး ၁ ခရက်ဒစ် ရှိရပါမည်။", show_alert=True)
            return
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Vless", callback_data="proto_vless"),
                   types.InlineKeyboardButton("Hysteria2", callback_data="proto_hysteria2"))
        markup.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚙️ *အဆင့် (၁)* - အသုံးပြုလိုသည့် VPN Protocol ကို ရွေးချယ်ပေးပါ-", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("proto_"):
        selected_proto = call.data.split("_")[1]
        user_steps[tg_id] = {"protocol": selected_proto}
        cursor.execute("SELECT * FROM servers WHERE protocol = ?", (selected_proto,))
        servers = cursor.fetchall()
        
        if not servers:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"⚠️ လက်ရှိတွင် {selected_proto.upper()} အတွက် ဆာဗာများ အဆင်မသင့်ဖြစ်သေးပါဗျာ။", reply_markup=get_back_button())
            return
            
        markup = types.InlineKeyboardMarkup(row_width=2)
        for srv in servers:
            markup.add(types.InlineKeyboardButton(f"{srv['name']}", callback_data=f"srv_{srv['id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back to Protocols", callback_data="main_genkey"))
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="🌍 *အဆင့် (၂)* - အသုံးပြုလိုသည့် ဆာဗာတည်နေရာကို ရွေးချယ်ပေးပါ-", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("srv_"):
        srv_id = int(call.data.split("_")[1])
        user_steps[tg_id]["server_id"] = srv_id
        markup = types.InlineKeyboardMarkup(row_width=2)
        gbs = [1, 10, 50, 100, 200]
        buttons = [types.InlineKeyboardButton(f"{g} GB", callback_data=f"gb_{g}") for g in gbs]
        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("🔙 Cancel / Back to Main Menu", callback_data="main_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="📊 *အဆင့် (၃)* - ဝယ်ယူအသုံးပြုမည့် Data GB ပမာဏကို ရွေးချယ်ပေးပါ-\n_(1 GB လျှင် 1 Credit နှုတ်ယူပါမည်)_", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("gb_"):
        selected_gb = int(call.data.split("_")[1])
        steps = user_steps.get(tg_id)
        
        if not steps:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ လုပ်ငန်းစဉ် အချိန်လွန်သွားပါပြီ။ အစက ပြန်လုပ်ပေးပါ။", reply_markup=get_back_button())
            return
            
        if user['credits'] < selected_gb:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"⚠️ ခရက်ဒစ်မလုံလောက်ပါ။ ရွေးချယ်ထားသော GB: {selected_gb} GB, လူကြီးမင်းလက်ကျန်: {user['credits']} Credits", reply_markup=get_back_button())
            return
            
        cursor.execute("SELECT * FROM servers WHERE id = ?", (steps['server_id'],))
        server_info = cursor.fetchone()
        
        rem_server_bw = server_info['total_bandwidth'] - server_info['used_bandwidth']
        if rem_server_bw < selected_gb:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ တောင်းပန်ပါတယ်ဗျာ။ ဤဆာဗာတွင် Bandwidth Pool လက်ကျန် မလုံလောက်တော့သဖြင့် GB လျှော့၍ ထုတ်ပေးပါရန်။", reply_markup=get_back_button())
            return
            
        email_id = f"TG_{tg_id}_{secrets.token_hex(3)}"
        
        if server_info['protocol'] == 'vless':
            generated_uuid = str(uuid.uuid4())
            api_success = add_client_to_3xui(server_info, generated_uuid, email_id, selected_gb)
            final_key = f"vless://{generated_uuid}@{server_info['domain']}:443?type=ws&encryption=none&security=tls&path=%2Fassets&host={server_info['domain']}&sni={server_info['domain']}&fp=chrome&alpn=http%2F1.1#{selected_gb}GB_{server_info['name']}"
        else:
            generated_pass = secrets.token_urlsafe(10)
            api_success = add_client_to_3xui(server_info, generated_pass, email_id, selected_gb)
            final_key = f"hysteria2://{generated_pass}@{server_info['ip']}:443?security=tls&obfs=salamander&obfs-password={UDP_MASK_PASSWORD}&insecure=1&sni=www.mpt.com.mm#{selected_gb}GB_{server_info['name']}"
            
        if api_success:
            cursor.execute("UPDATE users SET credits = credits - ? WHERE telegram_id = ?", (selected_gb, tg_id))
            cursor.execute("UPDATE servers SET used_bandwidth = used_bandwidth + ? WHERE id = ?", (selected_gb, steps['server_id']))
            cursor.execute("INSERT INTO user_keys (telegram_id, server_id, email, total_gb) VALUES (?, ?, ?, ?)", (tg_id, steps['server_id'], email_id, selected_gb))
            conn.commit()
            
            success_msg = f"🎉 *VPN Key အောင်မြင်စွာ ထုတ်ယူပြီးပါပြီ။*\n\n"
            success_msg += f"📊 ပမာဏ: `{selected_gb} GB`\n"
            success_msg += f"🌍 ဆာဗာ: `{server_info['name']}`\n"
            success_msg += f"💰 နှုတ်ယူခရက်ဒစ်: `{selected_gb} Credits`\n\n"
            success_msg += f"👇 _အောက်ပါစာသားကို ကလစ်တစ်ချက်နှိပ်ပြီး ကော်ပီကူးယူသုံးစွဲနိုင်ပါပြီ_ -\n\n"
            success_msg += f"`{final_key}`"
            
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=success_msg, parse_mode="Markdown", reply_markup=get_back_button())
        else:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ ဆာဗာ API နှင့် ချိတ်ဆက်ရာတွင် ချို့ယွင်းချက်ရှိသဖြင့် ခရက်ဒစ် နှုတ်မယူလိုက်ပါ။ ခေတ္တစောင့်ပြီး ပြန်ကြိုးစားပါ။", reply_markup=get_back_button())
            
        if tg_id in user_steps:
            del user_steps[tg_id]

    conn.close()

# ========================================================
# ၆။ ADMIN EXTRAS (Admin Commands)
# ========================================================

@bot.message_handler(commands=['addserver'])
def add_server_cmd(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ တောင်းပန်ပါတယ်။ ဤ Command ကို Admin များသာ အသုံးပြုနိုင်ပါသည်ဗျာ။")
        return

    try:
        args = message.text.split()[1:]
        name, ip, domain, panel_url, user, password, protocol, inbound_id = args
        
        conn = sqlite3.connect("vpn_bot.db")
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO servers (name, ip, domain, panel_url, username, password, protocol, inbound_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, ip, domain, panel_url, user, password, protocol, int(inbound_id)))
        conn.commit()
        conn.close()
        bot.reply_to(message, "✅ ဆာဗာအသစ်ကို ဒေတာဘေ့စ်ထဲသို့ အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီဗျာ။")
    except Exception as e:
        bot.reply_to(message, "⚠️ ပုံစံမမှန်ပါ။ ဥပမာ-\n`/addserver 🇸🇬_SG 127.0.0.1 sg.domain.com http://127.0.0.1:2053 admin adminpass vless 1`")
        
@bot.message_handler(commands=['listservers'])
def list_servers_cmd(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ တောင်းပန်ပါတယ်။ ဤ Command ကို Admin များသာ အသုံးပြုနိုင်ပါသည်ဗျာ။")
        return
        
    conn = sqlite3.connect("vpn_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, protocol, ip FROM servers ORDER BY protocol, id")
    servers = cursor.fetchall()
    conn.close()
    
    if not servers:
        bot.reply_to(message, "⚠️ လက်ရှိတွင် ထည့်သွင်းထားသော ဆာဗာ မရှိသေးပါဗျာ။")
        return
        
    msg = "🖥️ *လက်ရှိ Database ထဲမှ ဆာဗာစာရင်းများ*\n\n"
    
    current_protocol = ""
    for srv in servers:
        if srv[2] != current_protocol:
            current_protocol = srv[2]
            msg += f"🗂️ *{current_protocol.upper()} SERVERS*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"
            
        msg += f"🆔 ID: `{srv[0]}` | 🌍 Name: `{srv[1]}`\n"
        msg += f"   IP: `{srv[3]}`\n\n"
        
    msg += "💡 ဖျက်လိုပါက `/removeserver ID` ဟု ရိုက်ထည့်ပါ။\n(ဥပမာ- Vless JP ဆာဗာ၏ ID က 3 ဆိုလျှင် `/removeserver 3` ဟု ရိုက်ထည့်ပါ)"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['gen'])
def generate_voucher_cmd(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ တောင်းပန်ပါတယ်။ ဤ Command ကို Admin များသာ အသုံးပြုနိုင်ပါသည်ဗျာ။")
        return

    try:
        args = message.text.split()[1:]
        count = int(args[0])
        amount = float(args[1])
        
        conn = sqlite3.connect("vpn_bot.db")
        cursor = conn.cursor()
        codes = []
        
        for _ in range(count):
            code = "VOU-" + secrets.token_hex(4).upper()
            cursor.execute("INSERT INTO vouchers (code, amount) VALUES (?, ?)", (code, amount))
            codes.append(code)
            
        conn.commit()
        conn.close()
        
        msg = f"✅ *Voucher ({count}) ခု ထုတ်လုပ်မှု အောင်မြင်ပါသည်။* (1 ခုလျှင် {amount} Credits)\n\n"
        msg += "\n".join([f"`{c}`" for c in codes])
        bot.reply_to(message, msg, parse_mode="Markdown")
        
    except (IndexError, ValueError):
        bot.reply_to(message, "⚠️ ပုံစံမမှန်ပါ။ ဥပမာ- `/gen 5 10` (Voucher ၅ ခု၊ တစ်ခုလျှင် 10 credits)", parse_mode="Markdown")

if __name__ == '__main__':
    init_db()
    print("🚀 Premium VPN Telegram Bot is running successfully...")
    bot.infinity_polling()
