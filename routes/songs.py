# routes/songs.py
from flask import Blueprint, jsonify, request
from database import db
from models import Song, Genre, Author
from services.song_vision_service import SongVisionService
from services.ai_song_service import generar_cancion_ia
songs_bp = Blueprint('songs', __name__, url_prefix='/api/songs')
from sqlalchemy import or_, func, cast, String


vision_service = SongVisionService()

@songs_bp.route('/upload-vision', methods=['POST'])
def upload_song_vision_ia():
    data = request.get_json() or {}
    image_base64 = data.get('image_base64') # String Base64 sin prefijos (data:image/jpeg;base64,)
    
    if not image_base64:
        return jsonify({"error": "No se recibió el flujo de datos en Base64 de la captura, varón."}), 400
        
    # Invocamos el transcriptor de visión en caliente
    analisis_ia = vision_service.extract_song_from_image(image_base64)
    
    if not analisis_ia["success"]:
        return jsonify({"error": analisis_ia["error"]}), 422
        
    # Devolvemos la estructura preformateada lista para acoplarse al Front
    return jsonify({
        "message": "Imagen armonizada y procesada por el transcriptor virtual con éxito.",
        "detected_name": analisis_ia["song_name"],
        "structure": analisis_ia["structure"]
    }), 200
@songs_bp.route('/', methods=['GET'])
def get_all_songs():
    name_query = request.args.get('name')
    genre_query = request.args.get('genre')
    author_query = request.args.get('author')
    search_query = request.args.get('search')  # Nueva barra de búsqueda global (incluye letras)

    query = db.session.query(Song).join(Author, Song.author_id == Author.id)
    query = query.outerjoin(Genre, Song.genre_id == Genre.id)

    # 1. Filtros específicos por columna
    if name_query:
        query = query.filter(Song.name.ilike(f"%{name_query.strip()}%"))
        
    if genre_query:
        query = query.filter(Genre.name.ilike(f"%{genre_query.strip()}%"))
        
    if author_query:
        query = query.filter(Author.name.ilike(f"%{author_query.strip()}%"))

    # 2. Búsqueda Global / Inteligente (Barrido general incluyendo el JSON de estructura)
    if search_query:
        search_term = f"%{search_query.strip()}%"
        query = query.filter(
            or_(
                Song.name.ilike(search_term),
                Author.name.ilike(search_term),
                Genre.name.ilike(search_term),
                # Casteamos el JSON a String para buscar texto dentro de cualquier parte de la estructura
                cast(Song.structure, String).ilike(search_term)
            )
        )

    filtered_songs = query.all()
    
    return jsonify({
        "songs": [
            {
                "id": song.id,
                "name": song.name,
                "author": song.author.name if song.author else None,
                "genre": song.genre.name if song.genre else None,
                "structure": song.structure
            }
            for song in filtered_songs
        ]
    })

@songs_bp.route('/<int:song_id>', methods=['GET'])
def get_song(song_id):
    # Hacemos la consulta con los mismos joins pero filtrando estrictamente por el ID de la canción
    song = (
        db.session.query(Song)
        .join(Author, Song.author_id == Author.id)
        .outerjoin(Genre, Song.genre_id == Genre.id)
        .filter(Song.id == song_id)
        .first_or_404()
    )
    
    # Devolvemos exactamente el mismo mapeo que el GET general
    return jsonify({
        "id": song.id,
        "name": song.name,
        "author": song.author.name if song.author else None,
        "genre": song.genre.name if song.genre else None,
        "structure": song.structure
    })

@songs_bp.route('/', methods=['POST'])
def create_song():
    data = request.get_json(force=True)
    
    genre_name = data.get('genre')
    author_name = data.get('author')
    song_name = data.get('name')
    structure_data = data.get('structure')

    if not song_name or not author_name or not structure_data:
        return jsonify({"message": "Faltan campos obligatorios (name, author, structure)"}), 400

    try:
        genre_id = None
        if genre_name:
            genre_name_clean = genre_name.strip()
            genre = db.session.query(Genre).filter_by(name=genre_name_clean).first()
            if not genre:
                genre = Genre(name=genre_name_clean)
                db.session.add(genre)
                db.session.flush()
            genre_id = genre.id

        author_name_clean = author_name.strip()
        author = db.session.query(Author).filter_by(name=author_name_clean).first()
        if not author:
            author = Author(name=author_name_clean)
            db.session.add(author)
            db.session.flush()
        author_id = author.id

        new_song = Song(
            name=song_name.strip(),
            genre_id=genre_id,
            author_id=author_id,
            structure=structure_data
        )
        
        db.session.add(new_song)
        db.session.commit()
        
        return jsonify({
            "message": "¡Canción creada con éxito!", 
            "song_id": new_song.id
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error al guardar la canción: {e}")
        return jsonify({"message": "Ocurrió un error interno al procesar la canción."}), 500


@songs_bp.route('/<int:song_id>', methods=['PUT'])
def update_song(song_id):
    """
    Ruta para editar una canción existente.
    Sigue la misma lógica atómica para resolver las relaciones por strings simples.
    """
    song = Song.query.get_or_404(song_id)
    data = request.get_json(force=True)

    song_name = data.get('name')
    author_name = data.get('author')
    genre_name = data.get('genre')
    structure_data = data.get('structure')

    # Validación básica (manteniendo los requeridos idénticos al POST)
    if not song_name or not author_name or not structure_data:
        return jsonify({"message": "Faltan campos obligatorios (name, author, structure)"}), 400

    try:
        # 1. Actualizar o resolver Género
        if genre_name:
            genre_name_clean = genre_name.strip()
            genre = db.session.query(Genre).filter_by(name=genre_name_clean).first()
            if not genre:
                genre = Genre(name=genre_name_clean)
                db.session.add(genre)
                db.session.flush()
            song.genre_id = genre.id
        else:
            song.genre_id = None

        # 2. Actualizar o resolver Autor
        author_name_clean = author_name.strip()
        author = db.session.query(Author).filter_by(name=author_name_clean).first()
        if not author:
            author = Author(name=author_name_clean)
            db.session.add(author)
            db.session.flush()
        song.author_id = author.id

        # 3. Actualizar datos base de la canción
        song.name = song_name.strip()
        song.structure = structure_data

        db.session.commit()
        return jsonify({"message": "¡Canción actualizada con éxito!"}), 200

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error al actualizar la canción: {e}")
        return jsonify({"message": "Ocurrió un error interno al actualizar la canción."}), 500


@songs_bp.route('/<int:song_id>', methods=['DELETE'])
def delete_song(song_id):
    """
    Ruta para eliminar una canción por su ID.
    """
    song = Song.query.get_or_404(song_id)
    try:
        db.session.delete(song)
        db.session.commit()
        return jsonify({"message": f"Canción '{song.name}' eliminada correctamente."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error al eliminar la canción: {e}")
        return jsonify({"message": "Ocurrió un error interno al intentar eliminar la canción."}), 500


@songs_bp.route('/generate-ia', methods=['POST'])
def generate_song_structure():
    data = request.get_json(force=True)
    user_prompt = data.get('prompt')
    
    if not user_prompt:
        return jsonify({
            "bot_response": "Hubo un pequeño error: no recibí ningún texto para procesar. ¡Escríbeme algo!",
            "song_data": None
        }), 400
        
    try:
        resultado_ia = generar_cancion_ia(user_prompt)
        return jsonify(resultado_ia), 200
        
    except Exception as e:
        print(f"[ERROR] Error crítico en la ruta /generate-ia: {e}")
        return jsonify({
            "bot_response": "¡Upps! Ocurrió un error inesperado procesando tu solicitud con la IA. Por favor, intenta de nuevo en unos momentos.",
            "song_data": None
        }), 500