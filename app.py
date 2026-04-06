from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
from datetime import datetime
import threading
import time
import json
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuração dos webhooks (SUBSTITUA PELOS SEUS)
WEBHOOKS = {
    "NORMAL_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
    "SPECIAL_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
    "ULTRA_HIGH_WEBHOOK": "https://discord.com/api/webhooks/1432382898123833477/tzktyvZAZ4T-y_CwEM6kqGCILxwZcEFVP9F8Gbepd1tAC8X6yjA0t1Lqurvs_P1d2RXX",
}

DB_FILE = "servers.db"
CACHE_FILE = "latest_brainrots.json"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sent_servers
                 (job_id TEXT PRIMARY KEY, timestamp DATETIME, players INTEGER, max_players INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS brainrot_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, brainrot_name TEXT, brainrot_value INTEGER, brainrot_value_str TEXT, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

# Cache dos últimos brainrots encontrados
latest_brainrots = {
    "job_id": None,
    "players": 0,
    "max_players": 0,
    "brainrots": [],
    "timestamp": None
}

@app.route('/webhook-filter', methods=['POST'])
def webhook_filter():
    global latest_brainrots
    
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400
        
        job_id = data.get('job_id')
        brainrots = data.get('brainrots', [])
        players = data.get('players', 0)
        max_players = data.get('max_players', 0)
        
        # Verificar duplicado
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT job_id FROM sent_servers WHERE job_id = ?", (job_id,))
        if not c.fetchone():
            # Salvar no banco
            c.execute("INSERT INTO sent_servers VALUES (?, ?, ?, ?)", 
                      (job_id, datetime.now(), players, max_players))
            
            # Salvar histórico de brainrots
            for brainrot in brainrots:
                c.execute("INSERT INTO brainrot_history (job_id, brainrot_name, brainrot_value, brainrot_value_str, timestamp) VALUES (?, ?, ?, ?, ?)",
                          (job_id, brainrot.get('name'), brainrot.get('value'), brainrot.get('valueStr'), datetime.now()))
            
            conn.commit()
            
            # Atualizar cache
            latest_brainrots = {
                "job_id": job_id,
                "players": players,
                "max_players": max_players,
                "brainrots": brainrots,
                "timestamp": datetime.now().isoformat()
            }
            
            # Salvar cache em arquivo
            with open(CACHE_FILE, 'w') as f:
                json.dump(latest_brainrots, f, default=str)
        
        conn.close()
        
        # Enviar via WebSocket para clientes conectados
        socketio.emit('new_brainrots', latest_brainrots)
        
        # Enviar para Discord se tiver brainrots bons
        if brainrots and brainrots[0].get('value', 0) >= 1000000:
            send_to_discord(data)
        
        return jsonify({"status": "sent", "message": "Data forwarded to clients"}), 200
        
    except Exception as e:
        print(f"Erro: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get-brainrots', methods=['GET'])
def get_brainrots():
    """Endpoint GET para o menu buscar os brainrots"""
    global latest_brainrots
    
    # Parâmetros opcionais
    limit = request.args.get('limit', default=50, type=int)
    min_value = request.args.get('min_value', default=0, type=int)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Buscar brainrots recentes do banco
    c.execute("""
        SELECT bh.brainrot_name, bh.brainrot_value, bh.brainrot_value_str, bh.timestamp, s.players, s.max_players, bh.job_id
        FROM brainrot_history bh
        JOIN sent_servers s ON bh.job_id = s.job_id
        WHERE bh.brainrot_value >= ?
        ORDER BY bh.timestamp DESC
        LIMIT ?
    """, (min_value, limit))
    
    rows = c.fetchall()
    conn.close()
    
    brainrots = []
    for row in rows:
        brainrots.append({
            "name": row[0],
            "value": row[1],
            "valueStr": row[2],
            "timestamp": row[3],
            "players": row[4],
            "max_players": row[5],
            "job_id": row[6]
        })
    
    return jsonify({
        "status": "success",
        "count": len(brainrots),
        "brainrots": brainrots,
        "latest": latest_brainrots
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

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

def send_to_discord(data):
    """Envia brainrots para o Discord"""
    import requests
    
    brainrots = data.get('brainrots', [])
    if not brainrots:
        return
    
    highest = brainrots[0]
    
    # Determinar categoria
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
    
    # Construir embed
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
    except Exception as e:
        print(f"Erro ao enviar Discord: {e}")

@socketio.on('connect')
def handle_connect():
    print(f"Cliente conectado: {request.sid}")
    # Enviar últimos brainrots para o cliente que conectou
    if latest_brainrots.get('brainrots'):
        emit('new_brainrots', latest_brainrots)

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Cliente desconectado: {request.sid}")

@socketio.on('get_latest')
def handle_get_latest():
    emit('new_brainrots', latest_brainrots)

if __name__ == '__main__':
    print("🚀 Servidor Python iniciado!")
    print("📡 WebSocket: ws://127.0.0.1:5000")
    print("🌐 HTTP: http://127.0.0.1:5000")
    print("📋 Endpoints:")
    print("   POST /webhook-filter - Receber brainrots do Roblox")
    print("   GET  /get-brainrots  - Buscar brainrots (para o menu)")
    print("   GET  /get-servers    - Listar servidores")
    print("   GET  /health         - Health check")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
