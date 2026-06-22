import os
from flask import Flask, jsonify
from database import db
import models 
from flask_cors import CORS
from routes import genres, songs, authors, files, inventory, events, chatbot

app = Flask(__name__)

# --- CAMBIO AQUÍ: Configuración para Supabase ---
# Intenta leer desde el entorno, si no existe, usa la URL de Supabase directa
SUPABASE_URL = os.getenv(
    'DATABASE_URL', 
    
)

# Garantiza que use postgresql:// requerido por SQLAlchemy moderno
if SUPABASE_URL.startswith("postgres://"):
    SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = SUPABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# -----------------------------------------------

db.init_app(app)

app.register_blueprint(songs.songs_bp)
app.register_blueprint(chatbot.chatbot_bp)
app.register_blueprint(inventory.inventory_bp)
app.register_blueprint(files.files_bp)
app.register_blueprint(genres.genres_bp)
app.register_blueprint(authors.authors_bp)
app.register_blueprint(events.event_bp)
CORS(app)

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

@app.route('/health', methods=['GET'])
def health():
    total_songs = models.Song.query.count()
    return jsonify({"status": "ok", "users_count": total_songs})

if __name__ == '__main__':
    app.run(debug=True)