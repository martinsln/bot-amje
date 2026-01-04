# Created by Martin Saulnier - AMJE Bordeaux Quality Manager
import os
import sqlite3
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = Flask(__name__)

TOKEN = os.environ["SLACK_BOT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
CHANNEL = "annonces"
DB_NAME = "amje.db"

# User IDs des validateurs
RSE_USER_ID = os.environ.get("RSE_USER_ID")
RQ_USER_ID = os.environ.get("RQ_USER_ID")
PRESIDENT_USER_ID = os.environ.get("PRESIDENT_USER_ID")

studies = {}

# Database
def init_db():
    conn = sqlite3.connect(DB_NAME)
    
    # Table studies
    conn.execute('''CREATE TABLE IF NOT EXISTS studies 
                    (channel_id TEXT PRIMARY KEY, ts TEXT, channel TEXT, 
                     name TEXT, creator TEXT, created_at TEXT)''')
    
    # Table documents
    conn.execute('''CREATE TABLE IF NOT EXISTS documents
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     study_channel_id TEXT,
                     doc_name TEXT,
                     status TEXT,
                     submitted_by TEXT,
                     submitted_date TEXT,
                     rse_date TEXT,
                     rq_date TEXT,
                     president_date TEXT,
                     approved_date TEXT,
                     last_reminder_date TEXT,
                     FOREIGN KEY (study_channel_id) REFERENCES studies(channel_id))''')
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized")

def load_studies():
    conn = sqlite3.connect(DB_NAME)
    
    # Charger les études
    study_rows = conn.execute("SELECT * FROM studies").fetchall()
    for row in study_rows:
        channel_id = row[0]
        studies[channel_id] = {
            "ts": row[1],
            "channel": row[2],
            "name": row[3],
            "creator": row[4],
            "created_at": row[5],
            "docs": {}
        }
        
        # Charger les documents de cette étude
        doc_rows = conn.execute("SELECT * FROM documents WHERE study_channel_id = ?", (channel_id,)).fetchall()
        for doc_row in doc_rows:
            doc_name = doc_row[2]
            studies[channel_id]["docs"][doc_name] = {
                "status": doc_row[3],
                "submitted_by": doc_row[4],
                "submitted_date": doc_row[5],
                "rse_date": doc_row[6],
                "rq_date": doc_row[7],
                "president_date": doc_row[8],
                "approved_date": doc_row[9],
                "last_reminder_date": doc_row[10]
            }
    
    conn.close()
    print(f"✅ Loaded {len(studies)} studies from database")

def save_study(channel_id):
    s = studies[channel_id]
    conn = sqlite3.connect(DB_NAME)
    
    # Sauvegarder l'étude
    conn.execute("INSERT OR REPLACE INTO studies VALUES (?,?,?,?,?,?)",
                (channel_id, s["ts"], s["channel"], s["name"], s["creator"], s["created_at"]))
    
    # Sauvegarder les documents
    conn.execute("DELETE FROM documents WHERE study_channel_id = ?", (channel_id,))
    for doc_name, doc_data in s["docs"].items():
        conn.execute("INSERT INTO documents (study_channel_id, doc_name, status, submitted_by, submitted_date, rse_date, rq_date, president_date, approved_date, last_reminder_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (channel_id, doc_name, doc_data["status"], doc_data["submitted_by"], 
                     doc_data["submitted_date"], doc_data["rse_date"], doc_data["rq_date"],
                     doc_data["president_date"], doc_data["approved_date"], doc_data["last_reminder_date"]))
    
    conn.commit()
    conn.close()

def delete_study_from_db(channel_id):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM documents WHERE study_channel_id = ?", (channel_id,))
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

def get_doc_emoji(status):
    if status == "not_submitted":
        return "⏳"
    elif status in ["pending_rse", "pending_rq", "pending_president"]:
        return "👀"
    elif status == "approved":
        return "✅"
    return "⬜"

def get_doc_text(doc_name, status):
    emoji = get_doc_emoji(status)
    if status == "not_submitted":
        return f"{emoji} {doc_name} - Non soumis"
    elif status in ["pending_rse", "pending_rq", "pending_president"]:
        return f"{emoji} {doc_name} - En cours de validation"
    elif status == "approved":
        return f"{emoji} {doc_name} - Validé"
    return f"⬜ {doc_name}"

def format_message(name, creator, docs):
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
    
    doc_names = {"devis": "Devis", "rm": "Récapitulatif de mission", "ce": "Convention d étude", "pvrf": "PVRF"}
    
    docs_text = "\n".join([get_doc_text(doc_names[key], docs[key]["status"]) for key in ["devis", "rm", "ce", "pvrf"]])
    
    return (f"*📚 Nouvelle étude : #{name}*\n👤 Par : {creator}\n\n*Documents :*\n"
            f"{docs_text}\n\n"
            "_Commandes :_ `!devis submission` | `!rm submission` | `!ce submission` | `!pvrf submission` | `!status` | `!delete`\n"
            f"📊 <{dashboard_url}|Voir le dashboard complet>")

def get_next_validator(status):
    if status == "pending_rse":
        return RSE_USER_ID
    elif status == "pending_rq":
        return RQ_USER_ID
    elif status == "pending_president":
        return PRESIDENT_USER_ID
    return None

def get_validator_role(user_id):
    if user_id == RSE_USER_ID:
        return "rse"
    elif user_id == RQ_USER_ID:
        return "rq"
    elif user_id == PRESIDENT_USER_ID:
        return "president"
    return None

def get_next_status(current_status):
    transitions = {
        "not_submitted": "pending_rse",
        "pending_rse": "pending_rq",
        "pending_rq": "pending_president",
        "pending_president": "approved"
    }
    return transitions.get(current_status)

# Création d'étude
def create_study(channel_id, channel_name, creator_id):
    creator = get_user_name(creator_id)
    docs = {
        "devis": {"status": "not_submitted", "submitted_by": None, "submitted_date": None, "rse_date": None, "rq_date": None, "president_date": None, "approved_date": None, "last_reminder_date": None},
        "rm": {"status": "not_submitted", "submitted_by": None, "submitted_date": None, "rse_date": None, "rq_date": None, "president_date": None, "approved_date": None, "last_reminder_date": None},
        "ce": {"status": "not_submitted", "submitted_by": None, "submitted_date": None, "rse_date": None, "rq_date": None, "president_date": None, "approved_date": None, "last_reminder_date": None},
        "pvrf": {"status": "not_submitted", "submitted_by": None, "submitted_date": None, "rse_date": None, "rq_date": None, "president_date": None, "approved_date": None, "last_reminder_date": None}
    }
    msg = format_message(channel_name, creator, docs)
    
    try:
        r = requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": CHANNEL, "text": msg}, timeout=5).json()
        
        if r.get("ok"):
            studies[channel_id] = {
                "ts": r["ts"], "channel": r["channel"], "name": channel_name, 
                "creator": creator, "docs": docs, "created_at": datetime.now().isoformat()
            }
            save_study(channel_id)
            log(f"✅ Study created: #{channel_name}")
    except Exception as e:
        log(f"❌ Error creating study: {e}")

# Soumission de document
def submit_document(channel_id, doc_key, user_id):
    if channel_id not in studies:
        return
    
    s = studies[channel_id]
    doc = s["docs"][doc_key]
    
    if doc["status"] != "not_submitted":
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": f"❌ Le document {doc_key.upper()} a déjà été soumis."}, timeout=5)
        except:
            pass
        return
    
    user_name = get_user_name(user_id)
    doc["status"] = "pending_rse"
    doc["submitted_by"] = user_name
    doc["submitted_date"] = datetime.now().isoformat()
    
    # Mettre à jour le message dans #annonces
    msg = format_message(s["name"], s["creator"], s["docs"])
    try:
        requests.post("https://slack.com/api/chat.update", headers=HEADERS,
                     json={"channel": s["channel"], "ts": s["ts"], "text": msg}, timeout=5)
    except:
        pass
    
    save_study(channel_id)
    
    # Mentionner le RSE
    try:
        requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                     json={"channel": channel_id, 
                           "text": f"<@{RSE_USER_ID}> Le document {doc_key.upper()} attend ta validation !"}, timeout=5)
        log(f"✅ Document {doc_key} submitted for #{s['name']} by {user_name}")
    except Exception as e:
        log(f"❌ Error mentioning RSE: {e}")

# Validation de document
def validate_document(channel_id, doc_key, user_id):
    if channel_id not in studies:
        return
    
    s = studies[channel_id]
    doc = s["docs"][doc_key]
    
    # Vérifier que le document est soumis
    if doc["status"] == "not_submitted":
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": f"❌ Le document {doc_key.upper()} n'a pas encore été soumis. Utilisez `!{doc_key} submission` d'abord."}, timeout=5)
        except:
            pass
        return
    
    # Déterminer quel rôle doit valider selon le statut actuel
    if doc["status"] == "pending_rse":
        required_user_id = RSE_USER_ID
        role = "rse"
    elif doc["status"] == "pending_rq":
        required_user_id = RQ_USER_ID
        role = "rq"
    elif doc["status"] == "pending_president":
        required_user_id = PRESIDENT_USER_ID
        role = "president"
    else:
        return
    
    # Vérifier que c'est le bon validateur
    if user_id != required_user_id:
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": "❌ Ce n'est pas ton tour de valider ce document."}, timeout=5)
        except:
            pass
        return
    
    # Valider le document
    current_date = datetime.now().isoformat()
    if role == "rse":
        doc["rse_date"] = current_date
    elif role == "rq":
        doc["rq_date"] = current_date
    elif role == "president":
        doc["president_date"] = current_date
    
    # Passer au statut suivant
    next_status = get_next_status(doc["status"])
    doc["status"] = next_status
    
    if next_status == "approved":
        doc["approved_date"] = current_date
    
    # Mettre à jour le message dans #annonces
    msg = format_message(s["name"], s["creator"], s["docs"])
    try:
        requests.post("https://slack.com/api/chat.update", headers=HEADERS,
                     json={"channel": s["channel"], "ts": s["ts"], "text": msg}, timeout=5)
    except:
        pass
    
    save_study(channel_id)
    
    # Mentionner la prochaine personne
    if next_status == "approved":
        # Document validé, mentionner le CA
        ca_name = doc["submitted_by"]
        try:
            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                         json={"channel": channel_id, 
                               "text": f"Le document {doc_key.upper()} est validé, tu peux l'envoyer ! ✅"}, timeout=5)
            log(f"✅ Document {doc_key} approved for #{s['name']}")
        except Exception as e:
            log(f"❌ Error notifying CA: {e}")
    else:
        # Mentionner le prochain validateur
        next_validator_id = get_next_validator(next_status)
        if next_validator_id:
            try:
                requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                             json={"channel": channel_id, 
                                   "text": f"<@{next_validator_id}> Le document {doc_key.upper()} attend ta validation !"}, timeout=5)
                log(f"✅ Document {doc_key} validated by {role.upper()} for #{s['name']}")
            except Exception as e:
                log(f"❌ Error mentioning next validator: {e}")

# Status détaillé
def send_status(channel_id):
    if channel_id not in studies:
        return
    
    s = studies[channel_id]
    
    doc_names = {"devis": "Devis", "rm": "Récapitulatif de mission", "ce": "Convention d étude", "pvrf": "PVRF"}
    
    status_lines = []
    approved_count = 0
    
    for key in ["devis", "rm", "ce", "pvrf"]:
        doc = s["docs"][key]
        doc_name = doc_names[key]
        
        lines = [f"\n📄 *{doc_name}*"]
        
        if doc["status"] == "not_submitted":
            lines.append("  └ ⏳ Non soumis")
        else:
            if doc["submitted_by"]:
                date_str = datetime.fromisoformat(doc["submitted_date"]).strftime("%d/%m/%Y %H:%M")
                lines.append(f"  └ Soumis par {doc['submitted_by']} le {date_str}")
            
            if doc["rse_date"]:
                date_str = datetime.fromisoformat(doc["rse_date"]).strftime("%d/%m/%Y %H:%M")
                lines.append(f"  └ ✅ Validé par <@{RSE_USER_ID}> (RSE) le {date_str}")
            elif doc["status"] == "pending_rse":
                lines.append(f"  └ 👀 En attente de validation par <@{RSE_USER_ID}> (RSE)")
            
            if doc["rq_date"]:
                date_str = datetime.fromisoformat(doc["rq_date"]).strftime("%d/%m/%Y %H:%M")
                lines.append(f"  └ ✅ Validé par <@{RQ_USER_ID}> (RQ) le {date_str}")
            elif doc["status"] == "pending_rq":
                lines.append(f"  └ 🔍 En attente de validation par <@{RQ_USER_ID}> (RQ)")
            
            if doc["president_date"]:
                date_str = datetime.fromisoformat(doc["president_date"]).strftime("%d/%m/%Y %H:%M")
                lines.append(f"  └ ✅ Validé par <@{PRESIDENT_USER_ID}> (Président) le {date_str}")
            elif doc["status"] == "pending_president":
                lines.append(f"  └ 👑 En attente de validation par <@{PRESIDENT_USER_ID}> (Président)")
            
            if doc["status"] == "approved":
                lines.append("  └ 📤 *Validé et envoyable*")
                approved_count += 1
        
        status_lines.append("\n".join(lines))
    
    status_msg = (f"*📊 Statut de l'étude #{s['name']}*\n"
                  f"{''.join(status_lines)}\n\n"
                  f"📈 Progression globale : {approved_count}/4 documents validés ({int(approved_count/4*100)}%)")
    
    try:
        requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                     json={"channel": channel_id, "text": status_msg}, timeout=5)
        log(f"✅ Status sent for #{s['name']}")
    except Exception as e:
        log(f"❌ Error sending status: {e}")

# Rappels automatiques
def check_reminders():
    log("🔔 Checking reminders...")
    now = datetime.now()
    
    for channel_id, study in studies.items():
        for doc_key, doc in study["docs"].items():
            # Vérifier si le document est en attente de validation
            if doc["status"] in ["pending_rse", "pending_rq", "pending_president"]:
                # Vérifier si on doit envoyer un rappel
                should_remind = False
                
                if doc["last_reminder_date"] is None:
                    # Premier rappel : 24h après la soumission
                    if doc["submitted_date"]:
                        submitted = datetime.fromisoformat(doc["submitted_date"])
                        if now - submitted >= timedelta(hours=24):
                            should_remind = True
                else:
                    # Rappels suivants : tous les jours
                    last_reminder = datetime.fromisoformat(doc["last_reminder_date"])
                    if now - last_reminder >= timedelta(hours=24):
                        should_remind = True
                
                if should_remind:
                    validator_id = get_next_validator(doc["status"])
                    if validator_id:
                        try:
                            requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                                         json={"channel": channel_id, 
                                               "text": f"<@{validator_id}> Rappel de relecture !"}, timeout=5)
                            doc["last_reminder_date"] = now.isoformat()
                            save_study(channel_id)
                            log(f"✅ Reminder sent for {doc_key} in #{study['name']}")
                        except Exception as e:
                            log(f"❌ Error sending reminder: {e}")

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
        
        # Commandes de soumission
        submission_cmds = {
            "!devis submission": "devis",
            "!rm submission": "rm",
            "!ce submission": "ce",
            "!pvrf submission": "pvrf"
        }
        
        # Commandes de validation
        validation_cmds = {
            "!devis done": "devis",
            "!rm done": "rm",
            "!ce done": "ce",
            "!pvrf done": "pvrf"
        }
        
        if txt in submission_cmds:
            submit_document(channel_id, submission_cmds[txt], user_id)
        elif txt in validation_cmds:
            validate_document(channel_id, validation_cmds[txt], user_id)
        elif txt == "!status":
            send_status(channel_id)
        elif txt == "!delete":
            delete_study(channel_id, user_id)
    
    return "", 200

@app.route("/health", methods=["GET"])
def health():
    total = len(studies)
    completed = sum(1 for s in studies.values() if all(doc["status"] == "approved" for doc in s["docs"].values()))
    return jsonify({
        "status": "ok",
        "total_studies": total,
        "completed": completed,
        "in_progress": total - completed
    })

@app.route("/", methods=["GET"])
def dashboard():
    total = len(studies)
    completed = sum(1 for s in studies.values() if all(doc["status"] == "approved" for doc in s["docs"].values()))
    in_progress = total - completed
    
    # Préparer les données pour le template
    studies_data = []
    for cid, s in studies.items():
        approved_count = sum(1 for doc in s["docs"].values() if doc["status"] == "approved")
        progress_pct = (approved_count / 4) * 100
        
        studies_data.append({
            "name": s["name"],
            "creator": s["creator"],
            "progress_pct": progress_pct,
            "docs_done": approved_count,
            "check": s["docs"]
        })
    
    return render_template("dashboard.html", 
                          total=total, 
                          completed=completed, 
                          in_progress=in_progress,
                          studies=studies_data)

if __name__ == "__main__":
    print("🤖 Starting AMJE Slack Bot...")
    print(f"📢 Announcement channel: #{CHANNEL}")
    
    # Initialiser la base de données
    init_db()
    load_studies()
    
    # Démarrer le scheduler pour les rappels
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_reminders, 'cron', hour=9, minute=0)
    scheduler.start()
    print("⏰ Reminder scheduler started (runs daily at 9:00)")
    
    print("🌐 Server running on http://0.0.0.0:3000")
    print("📊 Dashboard available at http://localhost:3000")
    print("🏥 Health check at http://localhost:3000/health")
    
    try:
        app.run(host="0.0.0.0", port=3000)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("\n👋 Bot stopped")