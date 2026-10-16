"""FraguMC-bot — panel www (Flask).

Start na hostingu:  pip install -r requirements.txt && python web/app.py
Zmienne: BOT_API_URL (np. http://fi15.bot-hosting.net:26269),
         API_KEY (klucz API bota), PANEL_PASSWORD (haslo do panelu),
         PORT (port www, domyslnie 5000).
"""
import json
import os
import urllib.request
import urllib.error

from flask import Flask, jsonify, render_template_string, request, session

BOT_API_URL = os.getenv("BOT_API_URL", "http://fi15.bot-hosting.net:26269").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
PORT = int(os.getenv("PORT", "5000"))

app = Flask(__name__)
app.secret_key = API_KEY or "zmien-mnie-lokalnie"


def bot_req(method, path, body=None, query=""):
    req = urllib.request.Request(
        BOT_API_URL + path + query,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return jsonify(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        try:
            return jsonify(json.loads(e.read().decode())), e.code
        except Exception:
            return jsonify({"ok": False, "error": f"HTTP {e.code}"}), e.code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def need_auth():
    return bool(PANEL_PASSWORD) and not session.get("auth")


@app.route("/")
def index():
    if need_auth():
        return render_template_string(LOGIN_HTML)
    return render_template_string(PANEL_HTML)


@app.route("/login", methods=["POST"])
def login():
    if request.form.get("password", "") == PANEL_PASSWORD:
        session["auth"] = True
        return '<meta http-equiv="refresh" content="0;url=/">OK'
    return "Złe hasło", 401


@app.get("/api/overview")
def api_overview():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("GET", "/overview")


@app.get("/api/channels")
def api_channels():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("GET", "/channels", query=f"?guild_id={request.args.get('guild_id', '')}")


@app.get("/api/mcstatus")
def api_mcstatus():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("GET", "/mcstatus")


@app.post("/api/server")
def api_server():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("POST", "/server", request.get_json(force=True, silent=True) or {})


@app.delete("/api/server")
def api_server_del():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("DELETE", "/server")


@app.post("/api/kary")
def api_kary():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("POST", "/kary", request.get_json(force=True, silent=True) or {})


@app.post("/api/propozycje")
def api_prop():
    if need_auth():
        return jsonify({"ok": False, "error": "auth"}), 401
    return bot_req("POST", "/propozycje", request.get_json(force=True, silent=True) or {})


LOGIN_HTML = """<!doctype html><html lang="pl"><head><meta charset="utf-8">
<title>FraguMC panel — logowanie</title></head><body>
<h2>FraguMC-bot — panel</h2>
<form method="post" action="/login">
<input type="password" name="password" placeholder="Hasło" autofocus>
<button>Zaloguj</button></form></body></html>"""

PANEL_HTML = """<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FraguMC-bot — panel</title>
<style>body{font-family:sans-serif;max-width:640px;margin:auto;padding:16px}
section{border:1px solid #ccc;border-radius:8px;padding:12px;margin:12px 0}
label{display:block;margin-top:8px}input,select{width:100%;padding:6px;margin-top:2px}
button{margin-top:10px;padding:8px 14px}#msg{color:green;white-space:pre-wrap}</style>
</head><body>
<h2>FraguMC-bot — panel</h2>
<div id="msg"></div>

<section><h3>Serwer</h3>
<label>Serwer Discord<select id="guild"></select></label></section>

<section><h3>Serwer Minecraft</h3>
<label>Adres<input id="mc_addr"></label>
<label>Nazwa<input id="mc_name"></label>
<button onclick="saveServer()">Zapisz</button>
<button onclick="delServer()">Usuń</button>
<button onclick="checkStatus()">Sprawdź status</button>
<pre id="mcstatus"></pre></section>

<section><h3>Kanał kar</h3>
<label>Kanał<select id="kary"></select></label>
<button onclick="saveKary()">Zapisz kanał kar</button></section>

<section><h3>Kanały propozycji</h3>
<div id="proplist"></div>
<label>Kanał<select id="propchan"></select></label>
<button onclick="propAdd()">Dodaj</button>
<button onclick="propDel()">Usuń</button></section>

<script>
let channels=[], propIds=[];
async function jget(u){const r=await fetch(u);return r.json();}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
function say(t){document.getElementById('msg').textContent=t;}
function gid(){return document.getElementById('guild').value;}
function cname(id){const c=channels.find(x=>String(x.id)===String(id));return c?'#'+c.name:id;}

async function init(){
  const ov=await jget('/api/overview');
  if(!ov.ok){say('Błąd: '+(ov.error||'bot offline?'));return;}
  document.getElementById('guild').innerHTML=ov.guilds.map(g=>`<option value="${g.id}">${g.name}</option>`).join('');
  if(ov.mc_server&&ov.mc_server.address){
    document.getElementById('mc_addr').value=ov.mc_server.address||'';
    document.getElementById('mc_name').value=ov.mc_server.name||'';
  }
  await loadGuild();
}
async function loadGuild(){
  const ch=await jget('/api/channels?guild_id='+gid());
  if(!ch.ok){say('Błąd kanałów: '+ch.error);return;}
  channels=ch.channels;
  const opts=channels.map(c=>`<option value="${c.id}">#${c.name}</option>`).join('');
  document.getElementById('kary').innerHTML=opts;
  document.getElementById('propchan').innerHTML=opts;
  const ov=await jget('/api/overview');
  const cfg=(ov.config||{})[gid()]||{};
  if(cfg.kary_channel)document.getElementById('kary').value=String(cfg.kary_channel);
  propIds=(cfg.propozycje||[]).map(String);
  document.getElementById('proplist').innerHTML=propIds.length?propIds.map(cname).join(', '):'(brak)';
}
async function saveServer(){
  const r=await jpost('/api/server',{address:document.getElementById('mc_addr').value,name:document.getElementById('mc_name').value});
  say(r.ok?`Zapisano: ${r.name} (${r.address}, ${r.type})`:'Błąd: '+r.error);
}
async function delServer(){
  if(!confirm('Usunąć serwer?'))return;
  const r=await fetch('/api/server',{method:'DELETE'}).then(x=>x.json());
  say(r.ok?'Usunięto':'Błąd: '+r.error);
}
async function checkStatus(){
  const r=await jget('/api/mcstatus');
  document.getElementById('mcstatus').textContent=JSON.stringify(r,null,1);
}
async function saveKary(){
  const r=await jpost('/api/kary',{guild_id:gid(),channel_id:document.getElementById('kary').value});
  say(r.ok?'Zapisano kanał kar':'Błąd: '+r.error);
}
async function propAdd(){
  const r=await jpost('/api/propozycje',{guild_id:gid(),channel_id:document.getElementById('propchan').value,action:'add'});
  if(r.ok)loadGuild();else say('Błąd: '+r.error);
}
async function propDel(){
  const r=await jpost('/api/propozycje',{guild_id:gid(),channel_id:document.getElementById('propchan').value,action:'remove'});
  if(r.ok)loadGuild();else say('Błąd: '+r.error);
}
document.getElementById('guild').addEventListener('change',loadGuild);
init();
</script></body></html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
