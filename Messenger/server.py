import json, os, random, uvicorn, uuid, shutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from cryptography.fernet import Fernet
from datetime import datetime, timedelta

# --- KONFIGURATION ---
# Hinweis: In einer Produktionsumgebung sollte der KEY in einer Umgebungsvariable stehen
KEY = b'7P7-R0_u0j_pG2mQ6zX5W8_Xv7X3Zz9g_l6mN8S3xU0='
cipher = Fernet(KEY)
DB_FILE = "users.json"
UPLOAD_DIR = "uploads"

if not os.path.exists(UPLOAD_DIR): 
    os.makedirs(UPLOAD_DIR)

app = FastAPI()
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# --- GLOBALER STATUS (RAM) ---
pending_link_codes = {}  # { "CODE123": "username" }

# --- HILFSFUNKTIONEN ---

def load_db():
    if not os.path.exists(DB_FILE) or os.stat(DB_FILE).st_size == 0: 
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f: 
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f: 
        json.dump(data, f, indent=4)

# --- API ROUTES ---

@app.get("/")
async def get():
    with open("index.html", "r", encoding="utf-8") as f: 
        return HTMLResponse(content=f.read())

@app.post("/auth")
async def auth(data: dict):
    db = load_db()
    user, pw, token = data.get("username"), data.get("password"), data.get("token")
    
    if data.get("type") == "register":
        if user in db: 
            return {"status": "error", "msg": "User existiert bereits"}
        new_num = str(random.randint(100000, 999999))
        db[user] = {
            "pw": cipher.encrypt(pw.encode()).decode(), 
            "phone": new_num, 
            "tokens": [token], 
            "contacts": {}, 
            "history": {}, 
            "pic": None
        }
        save_db(db)
        return {"status": "success", "phone": new_num}
    
    else:
        u = db.get(user)
        if u and cipher.decrypt(u["pw"].encode()).decode() == pw:
            # Falls das Gerät (Token) nicht bekannt ist -> Link Code erforderlich
            if token not in u.get("tokens", []): 
                return {"status": "unauthorized"}
            
            # Bilder der Kontakte sammeln
            contact_pics = {v["phone"]: v.get("pic") for k, v in db.items() if v["phone"] in u.get("contacts", {})}
            return {
                "status": "success", 
                "phone": u["phone"], 
                "contacts": u["contacts"], 
                "history": u["history"], 
                "pic": u.get("pic"), 
                "contact_pics": contact_pics
            }
        return {"status": "error", "msg": "Login Daten falsch"}

# --- LINKING LOGIK (FÜR KEY-BUTTON) ---

@app.post("/generate_link_code")
async def generate_link_code(data: dict):
    username = data.get("username")
    # Generiere 6-stelligen Code
    code = str(uuid.uuid4().hex[:6]).upper()
    pending_link_codes[code] = username
    return {"status": "success", "code": code}

@app.post("/link_device")
async def link_device(data: dict):
    db = load_db()
    user, code, token = data.get("username"), data.get("code"), data.get("token")
    
    if code in pending_link_codes and pending_link_codes[code] == user:
        if user in db:
            if token not in db[user]["tokens"]:
                db[user]["tokens"].append(token)
                save_db(db)
            del pending_link_codes[code]
            return {"status": "success", "msg": "Gerät erfolgreich verknüpft!"}
    return {"status": "error", "msg": "Ungültiger oder abgelaufener Code"}

# --- NACHRICHTEN VERWALTUNG ---

@app.post("/save_msg")
async def save_msg(data: dict):
    db = load_db()
    s_num, t_num, msg = data.get("from"), data.get("to"), data.get("msg")
    m_id = data.get("id") or str(uuid.uuid4())[:8]
    
    for u in db.values():
        if u["phone"] == s_num or u["phone"] == t_num:
            partner = t_num if u["phone"] == s_num else s_num
            if partner not in u["history"]: u["history"][partner] = []
            u["history"][partner].append({
                "sender": s_num, 
                "text": msg, 
                "status": "delivered", 
                "id": m_id,
                "timestamp": datetime.now().strftime("%H:%M")
            })
    save_db(db)
    return {"status": "success"}

@app.post("/edit_msg")
async def edit_msg(data: dict):
    db = load_db()
    me, partner, msg_id, new_text = data.get("me"), data.get("partner"), data.get("id"), data.get("text")
    for u in db.values():
        if (u["phone"] == me or u["phone"] == partner) and partner in u["history"]:
            for m in u["history"][partner]:
                if str(m.get("id")) == str(msg_id):
                    m["text"] = new_text
                    m["edited"] = True
    save_db(db)
    return {"status": "success"}

@app.post("/delete_msg")
async def delete_msg(data: dict):
    db = load_db()
    me, partner, msg_id = data.get("me"), data.get("partner"), data.get("id")
    for u in db.values():
        if (u["phone"] == me or u["phone"] == partner) and partner in u["history"]:
            u["history"][partner] = [m for m in u["history"][partner] if str(m.get("id")) != str(msg_id)]
    save_db(db)
    return {"status": "success"}

@app.post("/set_read")
async def set_read(data: dict):
    db = load_db()
    me, partner = data.get("me"), data.get("partner")
    # Setze alle Nachrichten vom Partner auf 'read'
    if me in db: # Hier müsste man über den User-Key gehen
        pass # Logik analog zu save_msg zur Performance-Optimierung
    # Vereinfacht für dieses Setup:
    for u in db.values():
        if u["phone"] == me and partner in u["history"]:
            for m in u["history"][partner]:
                if m["sender"] == partner: m["status"] = "read"
    save_db(db)
    return {"status": "success"}

# --- MEDIA & PROFIL ---

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), sender: str = Form(...), target: str = Form(...)):
    ext = os.path.splitext(file.filename)[1]
    file_id = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, file_id)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success", "url": f"/files/{file_id}", "name": file.filename, "mime": file.content_type}

@app.post("/save_contact")
async def save_contact(data: dict):
    db = load_db()
    me, c_num, c_name = str(data.get("me")), str(data.get("num")), data.get("name")
    exists = any(u["phone"] == c_num for u in db.values())
    if not exists: return {"status": "error", "msg": "Nummer nicht gefunden"}
    for u in db.values():
        if u["phone"] == me:
            u["contacts"][c_num] = c_name
            save_db(db)
            return {"status": "success"}
    return {"status": "error"}

@app.get("/export_backup/{phone}")
async def export_backup(phone: str):
    db = load_db()
    for u in db.values():
        if u["phone"] == phone:
            return {"contacts": u["contacts"], "history": u["history"], "pic": u.get("pic")}
    return {"status": "error"}

# --- WEBSOCKET MANAGER ---

class ChatManager:
    def __init__(self): 
        self.active_users = {}
    
    async def connect(self, ws, phone):
        await ws.accept()
        self.active_users[str(phone)] = ws
    
    def disconnect(self, phone):
        if str(phone) in self.active_users: 
            del self.active_users[str(phone)]

manager = ChatManager()

@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await manager.connect(websocket, phone)
    try:
        while True:
            data = await websocket.receive_json()
            t = data.get("type")
            target = str(data.get("to"))

            # Spezial-Events direkt weiterleiten
            if t in ["edit_msg", "delete_msg"]:
                # Mapping auf Client-Events
                event_type = "edit_event" if t == "edit_msg" else "delete_event"
                if target in manager.active_users:
                    await manager.active_users[target].send_json({
                        "type": event_type,
                        "from": phone,
                        "id": data.get("id"),
                        "new_text": data.get("new_text") if t == "edit_msg" else None
                    })
                continue

            # Standard-Signalisierung (Chat, Call, Candidate, Answer, Read)
            if target in manager.active_users:
                # Wir hängen 'from' an, damit der Empfänger weiß, wer sendet
                data["from"] = phone 
                await manager.active_users[target].send_json(data)

    except WebSocketDisconnect:
        manager.disconnect(phone)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)