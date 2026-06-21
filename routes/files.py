# routes/files.py
from flask import Blueprint, jsonify, request
from database import db
from models import File, FileSong, Song
from services.ai_file_service import procesar_asistente_file_ia
files_bp = Blueprint('files', __name__, url_prefix='/api/files')

# ==========================================
# 1. OBTENER TODOS LOS REPERTORIOS (GET)
# ==========================================
@files_bp.route('/', methods=['GET'])
def get_all_files():
    # Capturamos filtros básicos por si quieres buscar listas por nombre o temática
    name_query = request.args.get('name')
    tematica_query = request.args.get('tematica')

    query = db.session.query(File)

    if name_query:
        query = query.filter(File.name.ilike(f"%{name_query.strip()}%"))
    if tematica_query:
        query = query.filter(File.tematica.ilike(f"%{tematica_query.strip()}%"))

    all_files = query.all()

    return jsonify({
        "files": [
            {
                "id": f.id,
                "name": f.name,
                "tematica": f.tematica,
                "created_at": f.created_at.isoformat(),
                "songs_count": f.songs_association.count()  # Nos dice cuántas canciones tiene acumuladas
            }
            for f in all_files
        ]
    }), 200


# ==========================================
# 2. OBTENER UN REPERTORIO POR ID CON SU ORDEN (GET)
# ==========================================
@files_bp.route('/<int:file_id>', methods=['GET'])
def get_file(file_id):
    file_obj = File.query.get_or_404(file_id)
    
    # Traemos las canciones ordenadas usando la propiedad helper que definimos en el modelo
    # y mapeamos los datos completos incluyendo el orden numérico real en este cancionero
    ordered_songs_list = []
    for assoc in file_obj.songs_association.order_by(FileSong.position).all():
        ordered_songs_list.append({
            "id": assoc.song.id,
            "name": assoc.song.name,
            "author": assoc.song.author.name if assoc.song.author else None,
            "genre": assoc.song.genre.name if assoc.song.genre else None,
            "position": assoc.position  # Su número en la lista (1, 2, 3...)
        })

    return jsonify({
        "id": file_obj.id,
        "name": file_obj.name,
        "tematica": file_obj.tematica,
        "created_at": file_obj.created_at.isoformat(),
        "songs": ordered_songs_list
    }), 200


# ==========================================
# 3. CREAR UN NUEVO REPERTORIO (POST)
# ==========================================
@files_bp.route('/', methods=['POST'])
def create_file():
    data = request.get_json(force=True)
    
    file_name = data.get('name')
    tematica = data.get('tematica')
    songs_data = data.get('songs', [])  # Se espera una lista de objetos: [{"id": 4, "position": 1}, ...]

    if not file_name:
        return jsonify({"message": "El campo 'name' es obligatorio para el cancionero."}), 400

    try:
        # Creación del contenedor base del cancionero
        new_file = File(
            name=file_name.strip(),
            tematica=tematica.strip() if tematica else None
        )
        db.session.add(new_file)
        db.session.flush()  # Obtenemos el id del cancionero antes del commit final

        # Vinculación de canciones con su posición numérica explícita
        for song_item in songs_data:
            song_id = song_item.get('id')
            position = song_item.get('position', 1) # Si por error no viene la posición, asignamos 1 por defecto
            
            # Verificamos rápidamente que la canción exista para no romper la integridad referencial
            song_exists = db.session.query(Song.id).filter_by(id=song_id).first()
            if song_exists:
                association = FileSong(
                    file_id=new_file.id,
                    song_id=song_id,
                    position=position
                )
                db.session.add(association)

        db.session.commit()
        return jsonify({
            "message": "¡Repertorio/Cancionero creado con éxito!",
            "file_id": new_file.id
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Al crear cancionero: {e}")
        return jsonify({"message": "Ocurrió un error interno al procesar el cancionero."}), 500


# ==========================================
# 4. EDITAR UN REPERTORIO (PUT)
# ==========================================
@files_bp.route('/<int:file_id>', methods=['PUT'])
def update_file(file_id):
    file_obj = File.query.get_or_404(file_id)
    data = request.get_json(force=True)

    file_name = data.get('name')
    tematica = data.get('tematica')
    songs_data = data.get('songs', []) # Estructura esperada idéntica al POST

    if not file_name:
        return jsonify({"message": "El campo 'name' es obligatorio."}), 400

    try:
        # Actualizamos los metadatos principales
        file_obj.name = file_name.strip()
        file_obj.tematica = tematica.strip() if tematica else None

        # Limpiamos las canciones viejas asociadas a este cancionero para reescribir la lista
        # (El delete-orphan configurado en el modelo se encargará del resto)
        FileSong.query.filter_by(file_id=file_id).delete()

        # Insertamos el nuevo set ordenado de canciones
        for song_item in songs_data:
            song_id = song_item.get('id')
            position = song_item.get('position', 1)
            
            song_exists = db.session.query(Song.id).filter_by(id=song_id).first()
            if song_exists:
                association = FileSong(
                    file_id=file_id,
                    song_id=song_id,
                    position=position
                )
                db.session.add(association)

        db.session.commit()
        return jsonify({"message": "¡Cancionero actualizado con éxito!"}), 200

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Al actualizar cancionero: {e}")
        return jsonify({"message": "Ocurrió un error interno al actualizar el cancionero."}), 500


# ==========================================
# 5. ELIMINAR UN REPERTORIO (DELETE)
# ==========================================
@files_bp.route('/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    file_obj = File.query.get_or_404(file_id)
    try:
        db.session.delete(file_obj)
        db.session.commit()
        return jsonify({"message": f"Cancionero '{file_obj.name}' eliminado correctamente."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Al eliminar cancionero: {e}")
        return jsonify({"message": "Ocurrió un error interno al intentar eliminar el cancionero."}), 500
    
@files_bp.route('/chat', methods=['POST'])
def chat_asistente_files():
    data = request.get_json() or {}
    prompt_usuario = data.get("prompt", "").strip()

    if not prompt_usuario:
        return jsonify({
            "bot_response": "¡Hola, varón! Cuéntame qué canciones o géneros quieres buscar para tu setlist hoy.",
            "songs": []
        }), 200

    try:
        # Ejecutamos la lógica del servicio IA dentro del contexto
        resultado = procesar_asistente_file_ia(prompt_usuario)
        return jsonify(resultado), 200

    except Exception as e:
        print(f"[CRITICAL] Error en la ruta del asistente de cancioneros: {e}")
        return jsonify({
            "bot_response": "Disculpa, varón. Tuve un contratiempo interno procesando esa consulta musical.",
            "songs": []
        }), 500