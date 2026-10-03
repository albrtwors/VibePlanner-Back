# routes/authors.py
from flask import Blueprint, jsonify, request
from database import db
from utils.decorators import permissions_required
from models import Author  # <-- Importamos el nuevo modelo simple

authors_bp = Blueprint('authors', __name__, url_prefix='/api/authors')

@authors_bp.route('/', methods=['GET'])
@permissions_required('songs.view')
def get_all_authors():
    """Obtener todos los autores para el select del frontend"""
    all_authors = Author.query.all()
    authors_list = [{"id": a.id, "name": a.name} for a in all_authors]
    return jsonify({"authors": authors_list}), 200


@authors_bp.route('/<int:author_id>', methods=['GET'])
@permissions_required('songs.view')
def get_author(author_id):
    """Obtener un autor específico"""
    author = Author.query.get_or_404(author_id)
    return jsonify({"id": author.id, "name": author.name}), 200


@authors_bp.route('/', methods=['POST'])
@permissions_required('songs.manage_catalog')
def create_author():
    """Crear un nuevo autor/artista"""
    data = request.get_json()
    name = data.get('name')
    
    if not name:
        return jsonify({"error": "El campo 'name' es obligatorio"}), 400
        
    # Evitar duplicados
    existing_author = Author.query.filter_by(name=name).first()
    if existing_author:
        return jsonify({"error": f"El autor '{name}' ya existe"}), 400

    new_author = Author(name=name)
    db.session.add(new_author)
    db.session.commit()
    
    return jsonify({
        "message": "¡Autor creado con éxito!", 
        "author": {"id": new_author.id, "name": new_author.name}
    }), 201