# routes/genres.py
from flask import Blueprint, jsonify, request
from database import db
from utils.decorators import permissions_required
from models import Genre

# Creamos el Blueprint para géneros con su respectivo prefijo
genres_bp = Blueprint('genres', __name__, url_prefix='/api/genres')

@genres_bp.route('/', methods=['GET'])
@permissions_required('songs.view')
def get_all_genres():
    """Obtener todos los géneros musicales"""
    all_genres = Genre.query.all()
    
    # Mapeamos la lista de objetos SQLAlchemy a un formato JSON válido
    genres_list = [{"id": g.id, "name": g.name} for g in all_genres]
    
    return jsonify({"genres": genres_list}), 200


@genres_bp.route('/<int:genre_id>', methods=['GET'])
@permissions_required('songs.view')
def get_genre(genre_id):
    """Obtener un género específico por su ID"""
    genre = Genre.query.get_or_404(genre_id)
    
    return jsonify({
        "id": genre.id,
        "name": genre.name
    }), 200


@genres_bp.route('/', methods=['POST'])
@permissions_required('songs.manage_catalog')
def create_genre():
    """Crear un nuevo género (ej. Rock, Pop, Indie)"""
    data = request.get_json()
    
    # Validar que nos envíen el nombre
    name = data.get('name')
    if not name:
        return jsonify({"error": "El campo 'name' es obligatorio"}), 400
        
    # Validar si ya existe el género para evitar errores de duplicado (unique=True)
    existing_genre = Genre.query.filter_by(name=name).first()
    if existing_genre:
        return jsonify({"error": f"El género '{name}' ya existe"}), 400

    # Crear y guardar el nuevo género
    new_genre = Genre(name=name)
    db.session.add(new_genre)
    db.session.commit()
    
    return jsonify({
        "message": "¡Género creado con éxito!", 
        "genre": {"id": new_genre.id, "name": new_genre.name}
    }), 201