# Created by Martin Saulnier - AMJE Bordeaux Quality Manager
import os
import sqlite3
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = Flask(__name__)

TOKEN = os.environ["SLACK_BOT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
CHANNEL = "annonces"
DB_NAME = "amje.db"

studies = {}

# Database
def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''CREATE TABLE IF NOT EXISTS studies 
                    (channel_id TEXT PRIMARY KEY, ts TEXT, channel TEXT, 
                     name TEXT, creator TEXT, 
                     devis BOOL, devis_by TEXT, devis_date TEXT,
                     rm BOOL, rm_by TEXT, rm_date TEXT,
                     ce BOOL, ce_by TEXT, ce_date TEXT,
                     pvrf BOOL, pvrf_by TEXT, pvrf_date TEXT,
                     created_at TEXT)''')
    conn.commit()
    conn.close()
    print(f"✅ Database initialized")

def load_studies():
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute("SELECT * FROM studies").fetchall()
    for row in rows:
        studies[row[0]] = {
            "ts": row[1], "channel": row[2], "name": row[3], "creator": row[4],
            "check": {
                "devis": {"done": bool(row[5]), "by": row[6], "date": row[7]},
                "rm": {"done": bool(row[8]), "by": row[9], "date": row[10]},
                "ce": {"done": bool(row[11]), "by": row[12], "date": row[13]},
                "pvrf": {"done": bool(row[14]), "by": row[15], "date": row[16]}
            },
            "created_at": row[17]
        }
    conn.close()
    print(f"✅ Loaded {len(studies)} studies from database")

def save_study(channel_id):
    s = studies[channel_id]
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel_id, s["ts"], s["channel"], s["name"], s["creator"],
                 s["check"]["devis"]["done"], s["check"]["devis"]["by"], s["check"]["devis"]["date"],
                 s["check"]["rm"]["done"], s["check"]["rm"]["by"], s["check"]["rm"]["date"],
                 s["check"]["ce"]["done"], s["check"]["ce"]["by"], s["check"]["ce"]["date"],
                 s["check"]["pvrf"]["done"], s["check"]["pvrf"]["by"], s["check"]["pvrf"]["date"],
                 s.get("created_at", datetime.now().isoformat())))
    conn.commit()
    conn.close()

def delete_study_from_db(channel_id):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM studies WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()

# Utils
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_user_name(user_id):
    try:
        r = requests.get("https://slack.com/api/users.info", headers=HEADERS, 
                        params={"user": user_id}, timeout=5).json()
        return r["user"]["real_name"] if r.get("ok") else "Inconnu"
    except:
        return "Inconnu"

def format_message(name, creator, check):
    def format_doc(doc_name, doc_data):
        icon = "✅" if doc_data["done"] else "⬜"
        if doc_data["done"] and doc_data["by"]:
            date_str = datetime.fromisoformat(doc_data["date"]).strftime("%d/%m/%Y %H:%M") if doc_data["date"] else ""
            return f"{icon} {doc_name} - _validé par {doc_data['by']} le {date_str}_"
        return f"{icon} {doc_name}"
    
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
    
    return (f"*📚 Nouvelle étude : #{name}*\n👤 Par : {creator}\n\n*Documents :*\n"
            f"{format_doc('Devis', check['devis'])}\n"
            f"{format_doc('Récapitulatif de mission', check['rm'])}\n"
            f"{format_doc('Convention d étude', check['ce'])}\n"
            f"{format_doc('PVRF', check['pvrf'])}\n\n"
            "_Commandes :_ `!devis done` | `!rm done` | `!ce done` | `!pvrf done` | `!status` | `!delete`\n"
            f"📊 <{dashboard_url}|Voir le dashboard complet>")

def check_completion(channel_id):
    s = studies[channel_id]
    if all(doc["done"] for doc in s["check"].values()):
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": f"🎉 *Étude #{s['name']} terminée !*\n"
                                      f"Tous les documents sont validés. Bravo ! 👏"}, timeout=5)
            log(f"✅ Completion notification sent for #{s['name']}")
        except Exception as e:
            log(f"❌ Error sending completion: {e}")

# Création d'étude
def create_study(channel_id, channel_name, creator_id):
    creator = get_user_name(creator_id)
    check = {
        "devis": {"done": False, "by": None, "date": None},
        "rm": {"done": False, "by": None, "date": None},
        "ce": {"done": False, "by": None, "date": None},
        "pvrf": {"done": False, "by": None, "date": None}
    }
    msg = format_message(channel_name, creator, check)
    
    try:
        r = requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": CHANNEL, "text": msg}, timeout=5).json()
        
        if r.get("ok"):
            studies[channel_id] = {
                "ts": r["ts"], "channel": r["channel"], "name": channel_name, 
                "creator": creator, "check": check, "created_at": datetime.now().isoformat()
            }
            save_study(channel_id)
            log(f"✅ Study created: #{channel_name}")
    except Exception as e:
        log(f"❌ Error creating study: {e}")

# Mise à jour document
def update_doc(channel_id, doc_key, user_id):
    if channel_id not in studies or doc_key not in studies[channel_id]["check"]:
        return
    
    s = studies[channel_id]
    user_name = get_user_name(user_id)
    
    s["check"][doc_key]["done"] = True
    s["check"][doc_key]["by"] = user_name
    s["check"][doc_key]["date"] = datetime.now().isoformat()
    
    msg = format_message(s["name"], s["creator"], s["check"])
    
    try:
        requests.post("https://slack.com/api/chat.update", headers=HEADERS,
                     json={"channel": s["channel"], "ts": s["ts"], "text": msg}, timeout=5)
        save_study(channel_id)
        log(f"✅ Updated {doc_key} for #{s['name']} by {user_name}")
        check_completion(channel_id)
    except Exception as e:
        log(f"❌ Error updating doc: {e}")

def send_status(channel_id):
    if channel_id not in studies:
        return
    
    s = studies[channel_id]
    docs_done = sum(1 for doc in s["check"].values() if doc["done"])
    
    # Format simplifié pour le status
    def format_doc_status(doc_name, doc_data):
        icon = "✅" if doc_data["done"] else "⬜"
        if doc_data["done"] and doc_data["by"]:
            date_str = datetime.fromisoformat(doc_data["date"]).strftime("%d/%m/%Y %H:%M") if doc_data["date"] else ""
            return f"{icon} {doc_name} - _validé par {doc_data['by']} le {date_str}_"
        return f"{icon} {doc_name}"
    
    status_msg = (f"*📊 Statut de l'étude #{s['name']}*\n\n"
                  f"*Documents :*\n"
                  f"{format_doc_status('Devis', s['check']['devis'])}\n"
                  f"{format_doc_status('Récapitulatif de mission', s['check']['rm'])}\n"
                  f"{format_doc_status('Convention d étude', s['check']['ce'])}\n"
                  f"{format_doc_status('PVRF', s['check']['pvrf'])}\n\n"
                  f"📈 Progression : {docs_done}/4 documents validés ({int(docs_done/4*100)}%)")
    
    try:
        requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                     json={"channel": channel_id, "text": status_msg}, timeout=5)
        log(f"✅ Status sent for #{s['name']}")
    except Exception as e:
        log(f"❌ Error sending status: {e}")

def delete_study(channel_id, user_id):
    if channel_id not in studies:
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": "❌ Ce channel n'est pas enregistré comme une étude."}, timeout=5)
        except:
            pass
        return
    
    s = studies[channel_id]
    study_name = s["name"]
    user_name = get_user_name(user_id)
    
    # Supprimer le message d'annonce
    try:
        requests.post("https://slack.com/api/chat.delete", headers=HEADERS,
                     json={"channel": s["channel"], "ts": s["ts"]}, timeout=5)
    except Exception as e:
        log(f"⚠️ Could not delete announcement message: {e}")
    
    # Supprimer de la base de données et de la mémoire
    delete_study_from_db(channel_id)
    del studies[channel_id]
    
    # Archiver le channel Slack
    try:
        r = requests.post("https://slack.com/api/conversations.archive", headers=HEADERS,
                         json={"channel": channel_id}, timeout=5).json()
        
        if r.get("ok"):
            log(f"✅ Study deleted and channel archived: #{study_name} by {user_name}")
        else:
            log(f"⚠️ Study deleted but could not archive channel: {r.get('error')}")
            try:
                requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                             json={"channel": channel_id, 
                                   "text": f"✅ Étude supprimée par {user_name}. Le channel peut maintenant être supprimé manuellement."}, timeout=5)
            except:
                pass
    except Exception as e:
        log(f"❌ Error archiving channel: {e}")

# Routes
@app.route("/slack/events", methods=["POST"])
def events():
    data = request.json
    
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})
    
    event = data.get("event", {})
    
    if event.get("type") == "channel_created":
        ch = event["channel"]
        create_study(ch["id"], ch["name"], ch["creator"])
    
    elif event.get("type") == "message" and not event.get("bot_id"):
        txt = event.get("text", "").strip().lower()
        channel_id = event["channel"]
        user_id = event.get("user")
        
        cmds = {"!devis done": "devis", "!rm done": "rm", "!ce done": "ce", "!pvrf done": "pvrf"}
        
        if txt in cmds:
            update_doc(channel_id, cmds[txt], user_id)
        elif txt == "!status":
            send_status(channel_id)
        elif txt == "!delete":
            delete_study(channel_id, user_id)
    
    return "", 200

@app.route("/health", methods=["GET"])
def health():
    total = len(studies)
    completed = sum(1 for s in studies.values() if all(doc["done"] for doc in s["check"].values()))
    return jsonify({
        "status": "ok",
        "total_studies": total,
        "completed": completed,
        "in_progress": total - completed
    })

@app.route("/", methods=["GET"])
def dashboard():
    total = len(studies)
    completed = sum(1 for s in studies.values() if all(doc["done"] for doc in s["check"].values()))
    in_progress = total - completed
    
    # Préparer les données pour le template
    studies_data = []
    for cid, s in studies.items():
        docs_done = sum(1 for doc in s["check"].values() if doc["done"])
        progress_pct = (docs_done / 4) * 100
        
        studies_data.append({
            "name": s["name"],
            "creator": s["creator"],
            "progress_pct": progress_pct,
            "docs_done": docs_done,
            "check": s["check"]
        })
    
    return render_template("dashboard.html", 
                          total=total, 
                          completed=completed, 
                          in_progress=in_progress,
                          studies=studies_data)

if __name__ == "__main__":
    print("🤖 Starting AMJE Slack Bot...")
    print(f"📢 Announcement channel: #{CHANNEL}")
    init_db()
    load_studies()
    print("🌐 Server running on http://0.0.0.0:3000")
    print("📊 Dashboard available at http://localhost:3000")
    print("🏥 Health check at http://localhost:3000/health")
    app.run(host="0.0.0.0", port=3000)