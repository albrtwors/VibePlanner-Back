# routes/songs.py
from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt_identity
from database import db
from models import (
    SONG_STATUS_BORRADOR,
    SONG_STATUS_EN_REVISION,
    SONG_STATUS_PUBLICADA,
    SONG_STATUSES,
    Song,
    SongSuggestion,
    Genre,
    Author,
)
from services.song_vision_service import SongVisionService
from services.ai_song_service import generar_cancion_ia
from services import song_moderation_service as moderation
from services import transpose_service as transposer
songs_bp = Blueprint('songs', __name__, url_prefix='/api/songs')
from sqlalchemy import or_, func, cast, String
from utils.decorators import permissions_required

vision_service = SongVisionService()


def _caller():
    """
    Id y permisos de quien hace la petición.

    El id viene del JWT como texto (identity=str(user.id) en auth.py); los
    permisos ya los dejó resueltos permissions_required en g.
    """
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        user_id = None
    return user_id, set(getattr(g, 'current_permissions', set()))


@songs_bp.route('/upload-vision', methods=['POST'])
@permissions_required('songs.create', 'ai.use')
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
@permissions_required('songs.view')
def get_all_songs():
    name_query = request.args.get('name')
    genre_query = request.args.get('genre')
    author_query = request.args.get('author')
    search_query = request.args.get('search')  # Nueva barra de búsqueda global (incluye letras)
    key_query = request.args.get('key')
    status_query = request.args.get('status')

    caller_id, permissions = _caller()

    query = db.session.query(Song).join(Author, Song.author_id == Author.id)
    query = query.outerjoin(Genre, Song.genre_id == Genre.id)

    # Lo que no está publicado solo lo ve su autor y los moderadores.
    query = moderation.filtro_visibilidad(query, caller_id, permissions)

    # 1. Filtros específicos por columna
    if name_query:
        query = query.filter(Song.name.ilike(f"%{name_query.strip()}%"))
        
    if genre_query:
        query = query.filter(Genre.name.ilike(f"%{genre_query.strip()}%"))
        
    if author_query:
        query = query.filter(Author.name.ilike(f"%{author_query.strip()}%"))

    if key_query:
        query = query.filter(Song.key == transposer.normalize_key(key_query))

    if status_query:
        if status_query not in SONG_STATUSES:
            return jsonify({"error": f"Estado desconocido '{status_query}'."}), 400
        query = query.filter(Song.status == status_query)

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
            moderation.serializar_cancion(song, caller_id, permissions)
            for song in filtered_songs
        ]
    })

@songs_bp.route('/<int:song_id>', methods=['GET'])
@permissions_required('songs.view')
def get_song(song_id):
    caller_id, permissions = _caller()

    # Hacemos la consulta con los mismos joins pero filtrando estrictamente por el ID de la canción
    song = (
        db.session.query(Song)
        .join(Author, Song.author_id == Author.id)
        .outerjoin(Genre, Song.genre_id == Genre.id)
        .filter(Song.id == song_id)
        .first_or_404()
    )

    # Un borrador ajeno no se distingue de una canción que no existe.
    if not moderation.puede_ver(song, caller_id, permissions):
        return jsonify({"error": "No encontré esa canción."}), 404

    return jsonify(moderation.serializar_cancion(song, caller_id, permissions, detail=True))

@songs_bp.route('/', methods=['POST'])
@permissions_required('songs.create')
def create_song():
    data = request.get_json(force=True)
    caller_id, permissions = _caller()

    genre_name = data.get('genre')
    author_name = data.get('author')
    song_name = data.get('name')
    structure_data = data.get('structure')
    key_data = data.get('key')

    if not song_name or not author_name or not structure_data:
        return jsonify({"message": "Faltan campos obligatorios (name, author, structure)"}), 400

    key = transposer.normalize_key(key_data) if key_data else None
    if key_data and not key:
        return jsonify({"message": f"'{key_data}' no es un tono válido."}), 400

    # Se puede crear directamente en revisión para que el moderador la vea sin
    # que el autor tenga que entrar a buscarla y apretar "enviar".
    enviar_ya = bool(data.get('submit'))

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
            user_id=caller_id,
            key=key,
            status=SONG_STATUS_EN_REVISION if enviar_ya else SONG_STATUS_BORRADOR,
            structure=structure_data
        )
        if enviar_ya:
            from datetime import datetime
            new_song.submitted_at = datetime.utcnow()
        
        db.session.add(new_song)
        db.session.commit()
        
        return jsonify({
            "message": "¡Canción creada con éxito!", 
            "song_id": new_song.id,
            "song": moderation.serializar_cancion(new_song, caller_id, permissions, detail=True)
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error al guardar la canción: {e}")
        return jsonify({"message": "Ocurrió un error interno al procesar la canción."}), 500


@songs_bp.route('/<int:song_id>', methods=['PUT'])
@permissions_required('songs.edit')
def update_song(song_id):
    """
    Ruta para editar una canción existente.
    Sigue la misma lógica atómica para resolver las relaciones por strings simples.

    Si la canción ya estaba publicada, el cambio la devuelve a revisión: nadie
    modifica el repertorio publicado sin pasar otra vez por la cola.
    """
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    if not moderation.puede_editar(song, caller_id, permissions):
        return jsonify({"message": "No tenés permiso para editar esta canción."}), 403

    data = request.get_json(force=True)

    song_name = data.get('name')
    author_name = data.get('author')
    genre_name = data.get('genre')
    structure_data = data.get('structure')
    key_data = data.get('key')

    # Validación básica (manteniendo los requeridos idénticos al POST)
    if not song_name or not author_name or not structure_data:
        return jsonify({"message": "Faltan campos obligatorios (name, author, structure)"}), 400

    key = transposer.normalize_key(key_data) if key_data else None
    if key_data and not key:
        return jsonify({"message": f"'{key_data}' no es un tono válido."}), 400

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
        song.key = key

        # 4. Si estaba publicada, el cambio la devuelve a la cola.
        moderation.reaperturar(song, caller_id, permissions)

        db.session.commit()
        return jsonify({
            "message": "¡Canción actualizada con éxito!",
            "song": moderation.serializar_cancion(song, caller_id, permissions, detail=True)
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error al actualizar la canción: {e}")
        return jsonify({"message": "Ocurrió un error interno al actualizar la canción."}), 500


@songs_bp.route('/<int:song_id>', methods=['DELETE'])
@permissions_required('songs.delete')
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
@permissions_required('ai.use')
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


# =====================================================================
# Proceso de transposición
# =====================================================================

@songs_bp.route('/keys', methods=['GET'])
@permissions_required('songs.view')
def get_keys():
    """
    Catálogo de tonos para el selector del frontend, con la cejilla sugerida.

    Va antes de /<int:song_id>, y no colisiona porque ese usa el conversor int.
    """
    return jsonify({
        "keys": transposer.ALL_KEYS,
        "capo": {key: transposer.capo_suggestion(key) for key in transposer.ALL_KEYS}
    })


@songs_bp.route('/transpose/preview', methods=['POST'])
@permissions_required('songs.view')
def transpose_preview():
    """
    Transpone una estructura sin guardarla: es la vista previa del editor.

    Body: {structure, target_key, source_key?}. Si no viene source_key se deduce
    de los acordes de la estructura.
    """
    data = request.get_json(force=True) or {}
    structure = data.get('structure')
    target_key = data.get('target_key')
    source_key = data.get('source_key')

    if not structure or not target_key:
        return jsonify({"error": "Faltan 'structure' y 'target_key'."}), 400

    resultado = transposer.transpose_structure(structure, target_key, source_key=source_key)
    if not resultado['success']:
        return jsonify({"error": resultado['error']}), 400

    return jsonify(resultado), 200


@songs_bp.route('/<int:song_id>/transpose', methods=['POST'])
@permissions_required('songs.view')
def transpose_song(song_id):
    """
    Transpone una canción guardada.

    Body: {target_key, apply?}. Con apply=true y permiso de edición, el cambio
    queda guardado (y reabre la moderación si estaba publicada). Sin apply, solo
    devuelve la vista previa para que la use el editor.
    """
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    if not moderation.puede_ver(song, caller_id, permissions):
        return jsonify({"error": "No encontré esa canción."}), 404

    data = request.get_json(force=True) or {}
    target_key = data.get('target_key')
    if not target_key:
        return jsonify({"error": "Falta 'target_key'."}), 400

    resultado = transposer.transpose_structure(song.structure, target_key, source_key=song.key)
    if not resultado['success']:
        return jsonify({"error": resultado['error']}), 400

    if data.get('apply'):
        if not moderation.puede_editar(song, caller_id, permissions):
            return jsonify({"message": "No podés guardar cambios en esta canción."}), 403

        song.structure = resultado['structure']
        song.key = resultado['target_key']
        moderation.reaperturar(song, caller_id, permissions)
        db.session.commit()
        resultado['song'] = moderation.serializar_cancion(
            song, caller_id, permissions, detail=True
        )
        resultado['message'] = f"Canción transpuesta a {resultado['target_key']}."

    return jsonify(resultado), 200


# =====================================================================
# Envío a revisión, cola de moderación y sugerencias
# =====================================================================

@songs_bp.route('/<int:song_id>/submit', methods=['POST'])
@permissions_required('songs.create')
def submit_song(song_id):
    """El autor envía su canción a la cola de moderación."""
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    resultado = moderation.enviar_a_revision(song, caller_id, permissions)
    if not resultado['success']:
        return jsonify({"message": resultado['error']}), 403

    db.session.commit()
    return jsonify({
        "message": resultado['message'],
        "song": moderation.serializar_cancion(song, caller_id, permissions, detail=True)
    }), 200


@songs_bp.route('/<int:song_id>/review', methods=['POST'])
@permissions_required('songs.moderate')
def review_song(song_id):
    """
    El moderador resuelve una canción.

    Body: {action: 'aprobar'|'rechazar'|'publicar', notes?}. Rechazar exige
    motivo, para que el autor sepa qué corregir.
    """
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    data = request.get_json(force=True) or {}
    accion = data.get('action')

    resultado = moderation.revisar(song, caller_id, accion, data.get('notes'))
    if not resultado['success']:
        return jsonify({"message": resultado['error']}), 400

    db.session.commit()
    return jsonify({
        "message": resultado['message'],
        "song": moderation.serializar_cancion(song, caller_id, permissions, detail=True)
    }), 200


@songs_bp.route('/moderation/queue', methods=['GET'])
@permissions_required('songs.moderate')
def moderation_queue():
    """Canciones esperando decisión, con sus sugerencias pendientes."""
    caller_id, permissions = _caller()

    canciones = moderation.cola_de_revision()
    return jsonify({
        "songs": [
            moderation.serializar_cancion(song, caller_id, permissions, detail=True)
            for song in canciones
        ],
        "pending_suggestions": [
            moderation.serializar_sugerencia(s)
            for s in moderation.sugerencias_pendientes()
        ]
    })


@songs_bp.route('/<int:song_id>/suggestions', methods=['GET'])
@permissions_required('songs.view')
def list_suggestions(song_id):
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    if not moderation.puede_ver(song, caller_id, permissions):
        return jsonify({"error": "No encontré esa canción."}), 404

    return jsonify({
        "suggestions": [
            {
                'id': s.id,
                'status': s.status,
                'notes': s.notes,
                'suggested_key': s.suggested_key,
                'user': s.user.username if s.user else None,
                'created_at': s.created_at.isoformat() if s.created_at else None,
                'resolution_notes': s.resolution_notes,
                'resolved_by': s.resolved_by.username if s.resolved_by else None,
            }
            for s in sorted(song.suggestions or [], key=lambda s: s.created_at)
        ]
    })


@songs_bp.route('/<int:song_id>/suggestions', methods=['POST'])
@permissions_required('songs.suggest')
def create_suggestion(song_id):
    """
    Propone un arreglo sobre una canción.

    Body: {structure?, key?, notes?}. Al menos uno de structure o key.
    """
    song = Song.query.get_or_404(song_id)
    caller_id, permissions = _caller()

    data = request.get_json(force=True) or {}
    structure = data.get('structure')
    key_data = data.get('key')

    key = transposer.normalize_key(key_data) if key_data else None
    if key_data and not key:
        return jsonify({"message": f"'{key_data}' no es un tono válido."}), 400

    resultado = moderation.crear_sugerencia(
        song, caller_id, permissions,
        suggested_structure=structure,
        suggested_key=key,
        notas=data.get('notes')
    )
    if not resultado['success']:
        return jsonify({"message": resultado['error']}), 400

    db.session.add(resultado['suggestion'])
    db.session.commit()
    return jsonify({
        "message": resultado['message'],
        "suggestion": moderation.serializar_sugerencia(resultado['suggestion'])
    }), 201


@songs_bp.route('/suggestions/<int:suggestion_id>/resolve', methods=['PUT'])
@permissions_required('songs.moderate')
def resolve_suggestion(suggestion_id):
    """Aprueba o rechaza una sugerencia. Aprobar aplica el cambio y reabre la canción."""
    sugerencia = SongSuggestion.query.get_or_404(suggestion_id)
    caller_id, permissions = _caller()

    data = request.get_json(force=True) or {}
    accion = data.get('action')

    resultado = moderation.resolver_sugerencia(
        sugerencia, caller_id, accion, data.get('notes')
    )
    if not resultado['success']:
        return jsonify({"message": resultado['error']}), 400

    db.session.commit()
    return jsonify({
        "message": resultado['message'],
        "suggestion": moderation.serializar_sugerencia(sugerencia),
        "song": moderation.serializar_cancion(resultado['song'], caller_id, permissions, detail=True)
    }), 200