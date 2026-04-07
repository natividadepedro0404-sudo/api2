# IMPORTANTE: Monkey patch deve ser a PRIMEIRA coisa
import eventlet
eventlet.monkey_patch()

# Agora sim, importar os outros módulos
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
from datetime import datetime
import threading
import time
import json
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração do SocketIO com eventlet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Configuração dos webhooks
WEBHOOKS = {
    "NORMAL_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
    "SPECIAL_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
    "ULTRA_HIGH_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
}

DB_FILE = "servers.db"
MAX_BRAINROTS = 5  # Máximo de brainrots que serão mantidos

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sent_servers
                 (job_id TEXT PRIMARY KEY, timestamp DATETIME, players INTEGER, max_players INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS brainrot_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  job_id TEXT, 
                  brainrot_name TEXT, 
                  brainrot_value INTEGER, 
                  brainrot_value_str TEXT, 
                  timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

# Cache FIFO - apenas os 5 brainrots mais recentes
latest_brainrots = []

@app.route('/webhook-filter', methods=['POST'])
def webhook_filter():
    global latest_brainrots
    
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400
        
        job_id = data.get('job_id')
        new_brainrots = data.get('brainrots', [])
        players = data.get('players', 0)
        max_players = data.get('max_players', 0)
        
        print(f"\n📥 Recebendo dados do servidor: {job_id}")
        print(f"📊 Brainrots recebidos: {len(new_brainrots)}")
        
        # Verificar se o servidor já foi processado
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT job_id FROM sent_servers WHERE job_id = ?", (job_id,))
        
        if not c.fetchone():
            # Salvar servidor no banco
            c.execute("INSERT INTO sent_servers VALUES (?, ?, ?, ?)", 
                      (job_id, datetime.now(), players, max_players))
            conn.commit()
            print(f"✅ Servidor {job_id} salvo no banco")
        
        # Processar cada brainrot recebido
        for brainrot in new_brainrots:
            # Salvar no banco de dados
            c.execute("""INSERT INTO brainrot_history 
                        (job_id, brainrot_name, brainrot_value, brainrot_value_str, timestamp) 
                        VALUES (?, ?, ?, ?, ?)""",
                      (job_id, 
                       brainrot.get('name'), 
                       brainrot.get('value'), 
                       brainrot.get('valueStr'), 
                       datetime.now()))
            conn.commit()
            
            # Adicionar ao cache FIFO (no início da lista)
            brainrot_with_meta = {
                "name": brainrot.get('name'),
                "value": brainrot.get('value'),
                "valueStr": brainrot.get('valueStr'),
                "job_id": job_id,
                "players": players,
                "max_players": max_players,
                "timestamp": datetime.now().isoformat()
            }
            
            # Inserir no início da lista
            latest_brainrots.insert(0, brainrot_with_meta)
            
            # Manter apenas os MAX_BRAINROTS mais recentes
            if len(latest_brainrots) > MAX_BRAINROTS:
                removed = latest_brainrots.pop()
                print(f"🗑️ Removido brainrot antigo: {removed.get('name')}")
        
        conn.close()
        
        # Mostrar status atual do cache
        print(f"\n📋 CACHE ATUAL ({len(latest_brainrots)}/{MAX_BRAINROTS} brainrots):")
        for i, brainrot in enumerate(latest_brainrots):
            print(f"   {i+1}º - {brainrot.get('name')} - {brainrot.get('valueStr')}")
        
        # Enviar via WebSocket para clientes conectados
        socketio.emit('new_brainrots', {
            "brainrots": latest_brainrots,
            "total": len(latest_brainrots),
            "max": MAX_BRAINROTS,
            "timestamp": datetime.now().isoformat()
        })
        
        # Enviar para Discord se tiver brainrot bom
        if new_brainrots and new_brainrots[0].get('value', 0) >= 1000000:
            send_to_discord(data)
        
        return jsonify({
            "status": "sent", 
            "message": f"Data forwarded. Cache: {len(latest_brainrots)}/{MAX_BRAINROTS} brainrots"
        }), 200
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get-brainrots', methods=['GET'])
def get_brainrots():
    """Retorna os brainrots em ordem FIFO (mais recentes primeiro)"""
    global latest_brainrots
    
    limit = request.args.get('limit', default=MAX_BRAINROTS, type=int)
    limit = min(limit, MAX_BRAINROTS)
    
    return jsonify({
        "status": "success",
        "count": len(latest_brainrots),
        "max": MAX_BRAINROTS,
        "brainrots": latest_brainrots[:limit],
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/get-servers', methods=['GET'])
def get_servers():
    """Lista servidores recentes"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT job_id, timestamp, players, max_players FROM sent_servers ORDER BY timestamp DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    
    servers = []
    for row in rows:
        servers.append({
            "job_id": row[0],
            "timestamp": row[1],
            "players": row[2],
            "max_players": row[3]
        })
    
    return jsonify({"status": "success", "servers": servers}), 200

@app.route('/servers', methods=['GET'])
def servers():
    """Alias para /get-servers"""
    return get_servers()

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "cache_size": len(latest_brainrots),
        "max_brainrots": MAX_BRAINROTS
    }), 200

@app.route('/clear-cache', methods=['POST'])
def clear_cache():
    """Endpoint para limpar o cache"""
    global latest_brainrots
    latest_brainrots = []
    return jsonify({"status": "success", "message": "Cache cleared"}), 200

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Brainrot Scanner API - FIFO Cache",
        "max_brainrots": MAX_BRAINROTS,
        "current_cache_size": len(latest_brainrots),
        "endpoints": {
            "POST /webhook-filter": "Recebe brainrots do Roblox",
            "GET /get-brainrots": "Buscar brainrots (mais recentes primeiro)",
            "GET /get-servers": "Listar servidores",
            "GET /servers": "Alias para /get-servers",
            "GET /health": "Health check",
            "POST /clear-cache": "Limpar cache"
        }
    })

def send_to_discord(data):
    """Envia brainrots para o Discord"""
    brainrots = data.get('brainrots', [])
    if not brainrots:
        return
    
    highest = brainrots[0]
    
    if highest.get('value', 0) >= 100000000:
        webhook = WEBHOOKS.get("ULTRA_HIGH_WEBHOOK")
        color = 0xFF6B6B
        emoji = "💎"
    elif highest.get('value', 0) >= 10000000:
        webhook = WEBHOOKS.get("SPECIAL_WEBHOOK")
        color = 0xFFB347
        emoji = "🔥"
    else:
        webhook = WEBHOOKS.get("NORMAL_WEBHOOK")
        color = 0x4CAF50
        emoji = "⭐"
    
    if not webhook:
        return
    
    description = ""
    for i, b in enumerate(brainrots[:5], 1):
        description += f"**{i}º** - {b.get('name')}: **{b.get('valueStr')}**\n"
    
    embed = {
        "title": f"{emoji} {highest.get('name')}",
        "description": description,
        "color": color,
        "fields": [
            {"name": "👥 Jogadores", "value": f"{data.get('players', 0)}/{data.get('max_players', 0)}", "inline": True},
            {"name": "🎯 Total", "value": f"{len(brainrots)} brainrots", "inline": True}
        ],
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    
    try:
        requests.post(webhook, json={"embeds": [embed]}, timeout=10)
        print(f"✅ Enviado para Discord: {highest.get('name')}")
    except Exception as e:
        print(f"❌ Erro ao enviar Discord: {e}")

@socketio.on('connect')
def handle_connect():
    print(f"🔌 Cliente conectado: {request.sid}")
    emit('new_brainrots', {
        "brainrots": latest_brainrots,
        "total": len(latest_brainrots),
        "max": MAX_BRAINROTS,
        "timestamp": datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    print(f"🔌 Cliente desconectado: {request.sid}")

@socketio.on('get_latest')
def handle_get_latest():
    emit('new_brainrots', {
        "brainrots": latest_brainrots,
        "total": len(latest_brainrots),
        "max": MAX_BRAINROTS,
        "timestamp": datetime.now().isoformat()
    })

def cleanup_old_entries():
    """Limpa entradas antigas do banco de dados"""
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("DELETE FROM sent_servers WHERE timestamp < datetime('now', '-7 days')")
            c.execute("DELETE FROM brainrot_history WHERE timestamp < datetime('now', '-7 days')")
            deleted = conn.total_changes
            conn.commit()
            conn.close()
            if deleted > 0:
                print(f"🧹 Banco limpo: {deleted} entradas")
        except Exception as e:
            print(f"Erro ao limpar: {e}")
        time.sleep(86400)

if __name__ == '__main__':
    print("=" * 50)
    print("🚀 BRAINROT SCANNER - API SERVER (FIFO MODE)")
    print("=" * 50)
    print(f"📦 Máximo de brainrots no cache: {MAX_BRAINROTS}")
    print("🔄 Modo FIFO: Os mais antigos saem quando novos chegam")
    print("=" * 50)
    
    cleanup_thread = threading.Thread(target=cleanup_old_entries, daemon=True)
    cleanup_thread.start()
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
