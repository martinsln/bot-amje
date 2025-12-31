import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

TOKEN = os.environ["SLACK_BOT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
CHANNEL = "annonces"

# Stockage: {channel_id: {ts, channel, name, creator, checklist}}
studies = {}

# Utils
def get_user_name(user_id):
    r = requests.get("https://slack.com/api/users.info", headers=HEADERS, params={"user": user_id}).json()
    return r["user"]["real_name"] if r.get("ok") else "Inconnu"

def format_message(name, creator, check):  # check = checklist dict
    c = lambda x: "✅" if check[x] else "⬜"
    return (f"*📚 Nouvelle étude : #{name}*\n👤 Par : {creator}\n\n*Documents :*\n"
            f"{c('devis')} Devis\n{c('rm')} Récapitulatif de mission\n"
            f"{c('ce')} Convention d'étude\n{c('pvrf')} PVRF\n\n"
            "_Commandes :_ `!devis done` | `!rm done` | `!ce done` | `!pvrf done`")

# Création d'étude
def create_study(channel_id, channel_name, creator_id):
    creator = get_user_name(creator_id)
    check = {"devis": False, "rm": False, "ce": False, "pvrf": False}
    msg = format_message(channel_name, creator, check)
    
    r = requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                      json={"channel": CHANNEL, "text": msg}).json()
    
    if r.get("ok"):
        studies[channel_id] = {"ts": r["ts"], "channel": r["channel"], 
                               "name": channel_name, "creator": creator, "check": check}

# Mise à jour document
def update_doc(channel_id, doc_key):
    if channel_id not in studies or doc_key not in studies[channel_id]["check"]:
        return
    
    s = studies[channel_id]
    s["check"][doc_key] = True
    msg = format_message(s["name"], s["creator"], s["check"])
    
    requests.post("https://slack.com/api/chat.update", headers=HEADERS,
                  json={"channel": s["channel"], "ts": s["ts"], "text": msg})

# Routes
@app.route("/slack/events", methods=["POST"])
def events():
    data = request.json
    
    if "challenge" in data:  # validation webhook
        return jsonify({"challenge": data["challenge"]})
    
    event = data.get("event", {})
    
    if event.get("type") == "channel_created":  # nouveau channel
        ch = event["channel"]
        create_study(ch["id"], ch["name"], ch["creator"])
    
    elif event.get("type") == "message" and not event.get("bot_id"):  # commande
        txt = event.get("text", "").strip().lower()
        cmds = {"!devis done": "devis", "!rm done": "rm", "!ce done": "ce", "!pvrf done": "pvrf"}
        if txt in cmds:
            update_doc(event["channel"], cmds[txt])
    
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)