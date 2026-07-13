import os
from flask import Flask, jsonify
from database import db
import models 
from flask_cors import CORS
from routes import genres, songs, authors, files, inventory, events, chatbot, auth
from models import TokenBlocklist
from flask_jwt_extended import JWTManager
from datetime import timedelta

app = Flask(__name__)

# --- CONFIGURACIÓN DE BASE DE DATOS (SUPABASE) ---
SUPABASE_URL = os.getenv('DATABASE_URL', '')

if SUPABASE_URL.startswith("postgres://"):
    SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = SUPABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- CONFIGURACIÓN DE SEGURIDAD (JWT) ---
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "tu_clave_secreta_super_pro_para_firmar_tokens")

# 1. Ajustamos el tiempo de vida interno del JWT a 12 horas (Igual que la cookie)
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)

# 2. Desactivamos la verificación de doble header CSRF nativa de Flask para evitar conflictos con el proxy de Next.js
app.config["JWT_COOKIE_CSRF_PROTECT"] = False 

# =====================================================================
# SOLUCIÓN AL 401: Habilitar el escaneo del token dentro de las cookies
# =====================================================================
# Le indicamos a Flask-JWT-Extended que busque el token tanto en headers como en cookies
app.config["JWT_TOKEN_LOCATION"] = ["headers", "cookies"]

# Definimos el nombre exacto de la cookie que configuramos en auth.py
app.config["JWT_COOKIE_NAME"] = "vibe_token"
app.config["JWT_ACCESS_COOKIE_NAME"] = "vibe_token"
# =====================================================================

jwt = JWTManager(app)

# Callback indispensable para verificar la validez del token contra la base de datos (Logout check)
@jwt.token_in_blocklist_loader
def check_if_token_revoked(jwt_header, jwt_payload):
    jti = jwt_payload["jti"]
    token = TokenBlocklist.query.filter_by(jti=jti).first()
    return token is not None

# --- CONFIGURACIÓN DE CORS ---
CORS(app, origins=["http://localhost:3000", "https://vibe-planner-front.vercel.app"], supports_credentials=True)

# --- INICIALIZACIÓN DE MÓDULOS ---
db.init_app(app)

app.register_blueprint(auth.auth_bp)
app.register_blueprint(songs.songs_bp)
app.register_blueprint(chatbot.chatbot_bp)
app.register_blueprint(inventory.inventory_bp)
app.register_blueprint(files.files_bp)
app.register_blueprint(genres.genres_bp)
app.register_blueprint(authors.authors_bp)
app.register_blueprint(events.event_bp)

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

@app.route('/health', methods=['GET'])
def health():
    total_songs = models.Song.query.count()
    return jsonify({"status": "ok", "songs_count": total_songs})

if __name__ == '__main__':
    app.run(debug=True)