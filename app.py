import os
from flask import Flask, jsonify
from database import db
import models 
from flask_cors import CORS
from routes import genres, songs, authors, files, inventory, events, chatbot
app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "database.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

app.register_blueprint(songs.songs_bp)
app.register_blueprint(chatbot.chatbot_bp)
app.register_blueprint(inventory.inventory_bp)
app.register_blueprint(files.files_bp)
app.register_blueprint(genres.genres_bp)
app.register_blueprint(authors.authors_bp)
app.register_blueprint(events.event_bp)
CORS(app)
# -> BUENA PRÁCTICA: Asegura la limpieza de conexiones
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

@app.route('/health', methods=['GET'])
def health():
    total_songs = models.Song.query.count()
    return jsonify({"status": "ok", "users_count": total_songs})

if __name__ == '__main__':
    app.run(debug=True)