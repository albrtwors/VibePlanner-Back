# routes/files.py
import os
import tempfile

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from database import db
from utils.decorators import permissions_required
from models import (
    SONG_STATUS_BORRADOR,
    Author,
    File,
    FileSong,
    Genre,
    Song,
)
from services.ai_file_service import procesar_asistente_file_ia
from services.file_ingest_service import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    extract_text_from_file,
    split_songs_from_text,
)
from services import transpose_service as transposer
files_bp = Blueprint('files', __name__, url_prefix='/api/files')

# ==========================================
# 1. OBTENER TODOS LOS REPERTORIOS (GET)
# ==========================================
@files_bp.route('/', methods=['GET'])
@permissions_required('files.view')
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
@permissions_required('files.view')
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
@permissions_required('files.create')
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
@permissions_required('files.edit')
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
@permissions_required('files.delete')
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


# ==========================================
# 6. ASISTENTE DE IA DEL CANCIONERO (POST)
# ==========================================
@files_bp.route('/chat', methods=['POST'])
@permissions_required('ai.use')
def chat_asistente_files():
    data = request.get_json() or {}
    prompt_usuario = data.get("prompt", "").strip()

    # El frontend nos manda el estado ACTUAL del cancionero que se está armando en pantalla
    # (aún no guardado en DB), para que la IA pueda agregar o quitar sobre esa base.
    # Formato esperado: [{"id": 4, "name": "...", "author": "...", "genre": "..."}, ...]
    current_songs = data.get("current_songs", [])
    if not isinstance(current_songs, list):
        current_songs = []

    if not prompt_usuario:
        return jsonify({
            "bot_response": "¡Hola, varón! Cuéntame qué canciones, artistas o géneros quieres agregar o quitar de tu setlist hoy.",
            "songs_to_add": [],
            "songs_to_remove": []
        }), 200

    try:
        # Ejecutamos la lógica del servicio IA dentro del contexto
        resultado = procesar_asistente_file_ia(prompt_usuario, current_songs)
        return jsonify(resultado), 200

    except Exception as e:
        print(f"[CRITICAL] Error en la ruta del asistente de cancioneros: {e}")
        return jsonify({
            "bot_response": "Disculpa, varón. Tuve un contratiempo interno procesando esa consulta musical.",
            "songs_to_add": [],
            "songs_to_remove": []
        }), 500

# ==========================================
# 7. INGESTA MASIVA: PARSEAR UN ARCHIVO (POST)
# ==========================================
# No escribe nada en la base: devuelve los bloques de canciones que detectamos
# para que el usuario los revise y recién ahí confirmar.
#
# Acepta dos formatos:
#   - multipart con el archivo (campo "file"): .txt / .pdf / .docx
#   - JSON {"text": "..."}: por si el navegador ya lo parseó (pdfjs / mammoth)
#
@files_bp.route('/ingest/parse', methods=['POST'])
@permissions_required('files.create')
def ingest_parse():
    filename = ""
    texto = None
    tmp_path = None

    try:
        if 'file' in request.files and request.files['file'].filename:
            uploaded = request.files['file']
            filename = uploaded.filename
            ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''

            if ext not in ALLOWED_EXTENSIONS:
                return jsonify({
                    "error": f"Formato no soportado ('{ext or '?'}'). Subí un archivo .txt, .pdf o .docx."
                }), 400

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
            tmp_path = tmp.name
            uploaded.save(tmp_path)

            if os.path.getsize(tmp_path) > MAX_UPLOAD_BYTES:
                return jsonify({
                    "error": f"El archivo supera el máximo de {int(MAX_UPLOAD_BYTES / 1024 / 1024)} MB."
                }), 400

            texto = extract_text_from_file(tmp_path, filename)
        else:
            data = request.get_json(silent=True) or {}
            texto = (data.get('text') or '').strip()

        if not texto:
            return jsonify({
                "error": "No encontré texto para analizar. Si el PDF es una imagen escaneada, "
                         "no se puede leer: primero pasalo a texto."
            }), 400

        canciones = split_songs_from_text(texto)

        return jsonify({
            "message": f"Detecté {len(canciones)} canción(es). Revisá los datos antes de guardar.",
            "filename": filename or None,
            "count": len(canciones),
            "songs": canciones,
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] En /files/ingest/parse: {e}")
        return jsonify({"error": "Ocurrió un error interno analizando el archivo."}), 500
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ==========================================
# 8. INGESTA MASIVA: CONFIRMAR Y GUARDAR (POST)
# ==========================================
# Recibe los bloques ya revisados por el usuario, crea las canciones y arma
# el cancionero con ese orden. Las canciones nacen en 'borrador' y con el
# usuario que las subió como autor de la carga.
#
@files_bp.route('/ingest/commit', methods=['POST'])
@permissions_required('files.create', 'songs.create')
def ingest_commit():
    data = request.get_json(silent=True) or {}
    canciones = data.get('songs') or []
    file_data = data.get('file') or {}

    if not isinstance(canciones, list) or not canciones:
        return jsonify({"error": "No llegó ninguna canción para guardar."}), 400

    nombre_file = (file_data.get('name') or '').strip()
    if not nombre_file:
        return jsonify({"error": "El cancionero necesita un nombre."}), 400

    try:
        caller_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        caller_id = None

    try:
        # Autor y género: reutilizamos los existentes si coinciden, si no creamos.
        genero_por_defecto = Genre.query.filter_by(name="Sin género").first()
        if not genero_por_defecto:
            genero_por_defecto = Genre(name="Sin género")
            db.session.add(genero_por_defecto)

        autor_por_defecto = Author.query.filter_by(name="Desconocido").first()
        if not autor_por_defecto:
            autor_por_defecto = Author(name="Desconocido")
            db.session.add(autor_por_defecto)

        db.session.flush()

        nuevas_ids = []

        for item in canciones:
            if not isinstance(item, dict):
                continue

            nombre = (item.get('name') or '').strip()
            if not nombre:
                continue

            autor = autor_por_defecto
            nombre_autor = (item.get('author') or '').strip()
            if nombre_autor:
                autor = Author.query.filter_by(name=nombre_autor).first()
                if not autor:
                    autor = Author(name=nombre_autor)
                    db.session.add(autor)
                    db.session.flush()

            genero = genero_por_defecto
            nombre_genero = (item.get('genre') or '').strip()
            if nombre_genero:
                genero = Genre.query.filter_by(name=nombre_genero).first()
                if not genero:
                    genero = Genre(name=nombre_genero)
                    db.session.add(genero)
                    db.session.flush()

            cancion = Song(
                name=nombre,
                genre_id=genero.id,
                author_id=autor.id,
                user_id=caller_id,
                status=SONG_STATUS_BORRADOR,
            )

            tono = transposer.normalize_key(item.get('key') or '')
            if tono:
                cancion.key = tono

            estructura = item.get('structure')
            if isinstance(estructura, dict) and estructura.get('parts'):
                cancion.structure = estructura
            else:
                # Si el front manda solo el texto plano, lo guardamos como una parte.
                crudo = (item.get('raw_text') or '').strip()
                cancion.structure = {
                    "parts": [{"title": "letra", "content": crudo}]
                } if crudo else None

            db.session.add(cancion)
            db.session.flush()
            nuevas_ids.append(cancion.id)

        if not nuevas_ids:
            db.session.rollback()
            return jsonify({"error": "Ninguna canción tenía título válido."}), 400

        nuevo_file = File(
            name=nombre_file,
            tematica=(file_data.get('tematica') or '').strip() or None,
            user_id=caller_id,
        )
        db.session.add(nuevo_file)
        db.session.flush()

        for posicion, song_id in enumerate(nuevas_ids, start=1):
            db.session.add(FileSong(
                file_id=nuevo_file.id,
                song_id=song_id,
                position=posicion,
            ))

        db.session.commit()

        return jsonify({
            "message": f"Importé {len(nuevas_ids)} canción(es) al cancionero '{nuevo_file.name}'.",
            "file_id": nuevo_file.id,
            "song_ids": nuevas_ids,
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] En /files/ingest/commit: {e}")
        return jsonify({"error": "Ocurrió un error interno guardando la importación."}), 500
