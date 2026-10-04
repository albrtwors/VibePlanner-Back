# services/song_moderation_service.py
"""
Reglas del ciclo de vida de una canción y de las sugerencias del equipo.

Va separado de routes/songs.py a propósito: las transiciones válidas, quién puede
ver qué y qué pasa cuando se aprueba una sugerencia son reglas de negocio, no
detalles de HTTP. Así se prueban sin montar la aplicación y no se duplican entre
la ruta de envío, la de revisión y la de sugerencias.

Flujo de una canción:

    borrador ─┐
              ├─> en_revision ─> aprobada ─> publicada
    rechazada ┘        │              │
                       └─> rechazada <─┘

Regla de visibilidad: solo ven una canción que no está publicada su autor y
quienes tengan el permiso 'songs.moderate'. Todo lo demás ve únicamente el
repertorio publicado.
"""

from datetime import datetime

from sqlalchemy import or_

from models import (
    SONG_STATUS_APROBADA,
    SONG_STATUS_BORRADOR,
    SONG_STATUS_EN_REVISION,
    SONG_STATUS_PUBLICADA,
    SONG_STATUS_RECHAZADA,
    SONG_SUGGESTION_APROBADA,
    SONG_SUGGESTION_PENDIENTE,
    SONG_SUGGESTION_RECHAZADA,
    Song,
    SongSuggestion,
)

# Quién puede pedir cada movimiento. El moderador también puede, para no quedar
# trabado si el autor se forgot de reenviar.
TRANSICIONES = {
    SONG_STATUS_BORRADOR: (SONG_STATUS_EN_REVISION,),
    SONG_STATUS_RECHAZADA: (SONG_STATUS_EN_REVISION,),
    SONG_STATUS_EN_REVISION: (SONG_STATUS_APROBADA, SONG_STATUS_RECHAZADA),
    SONG_STATUS_APROBADA: (SONG_STATUS_PUBLICADA, SONG_STATUS_EN_REVISION),
    SONG_STATUS_PUBLICADA: (SONG_STATUS_EN_REVISION,),
}

# Acciones que la cola de moderación puede pedir sobre una canción.
ACCIONES_REVISION = ('aprobar', 'rechazar', 'publicar')

# De qué estado se puede pedir cada acción. Publicar es el paso que sigue a
# aprobar, así que no sale de 'en_revision': si se pudiera publicar desde
# revisión, aprobar y publicar serían el mismo botón.
ORIGENES_POR_ACCION = {
    'aprobar': (SONG_STATUS_EN_REVISION,),
    'rechazar': (SONG_STATUS_EN_REVISION,),
    'publicar': (SONG_STATUS_APROBADA,),
}

# Motivo obligatorio: sin una explicación el autor no sabe qué corregir.
_NOTAS_OBLIGATORIAS = 5


def _fallo(mensaje, **extra):
    """Respuesta de error uniforme, igual que el resto de servicios del backend."""
    return {'success': False, 'error': mensaje, **extra}


def _ok(mensaje, song=None, **extra):
    return {'success': True, 'message': mensaje, 'song': song, **extra}


def transicion_valida(origen, destino):
    """Si 'destino' es un movimiento permitido desde 'origen'."""
    return destino in TRANSICIONES.get(origen, ())


# --------------------------------------------------------------------------
# Visibilidad
# --------------------------------------------------------------------------

def es_autor(song, user_id):
    """
    Si el usuario es dueño de la canción.

    Las canciones viejas (creadas antes de que existiera el dueño) no tienen
    user_id, así que no tienen autor: nadie las puede editar, pero sí publicar.
    """
    if user_id is None or song.user_id is None:
        return False
    return song.user_id == user_id


def puede_ver(song, user_id, permissions):
    """Autor, moderador, o cualquier persona si la canción está publicada."""
    if es_autor(song, user_id):
        return True
    if 'songs.moderate' in permissions:
        return True
    return song.status == SONG_STATUS_PUBLICADA


def puede_editar(song, user_id, permissions):
    """
    Editar la letra o la estructura: su autor o un moderador.

    Importa que el autor SÍ puede editar una canción publicada. Lo que no puede
    es publicarla solo: al guardar el cambio, la canción vuelve a 'en_revision'
    (ver reaperturar). Negarle la edición obligaría al autor a pedirle permiso a
    un moderador para corregir su propia letra.
    """
    return es_autor(song, user_id) or 'songs.moderate' in permissions


def filtro_visibilidad(query, user_id, permissions, column=Song.status):
    """
    Le agrega a una query las condiciones de visibilidad.

    Se usa en el listado: sin esto, songs.moderate no serviría de nada porque el
    catálogo público devolvería también lo que está en revisión.
    """
    if 'songs.moderate' in permissions:
        return query

    condiciones = [column == SONG_STATUS_PUBLICADA]
    # El autor siempre ve lo suyo, aunque esté en borrador o rechazada.
    if user_id is not None:
        condiciones.append(Song.user_id == user_id)

    return query.filter(or_(*condiciones))


# --------------------------------------------------------------------------
# Ciclo de vida de la canción
# --------------------------------------------------------------------------

def enviar_a_revision(song, user_id, permissions=()):
    """El autor (o un moderador) pide que le revisen la canción."""
    if not (es_autor(song, user_id) or 'songs.moderate' in permissions):
        return _fallo('Solo el autor de la canción puede enviarla a revisión.')

    if not transicion_valida(song.status, SONG_STATUS_EN_REVISION):
        return _fallo(
            f"No se puede enviar a revisión una canción que está '{song.status}'.",
            status_actual=song.status
        )

    song.status = SONG_STATUS_EN_REVISION
    song.submitted_at = datetime.utcnow()
    # Se limpia el motivo de la revisión anterior: si la resubmitió, el
    # moderador tiene que volver a mirarla y el motivo viejo ya no aplica.
    song.review_notes = None
    song.reviewed_at = None
    song.reviewed_by_id = None

    return _ok('Canción enviada a revisión.', song=song)


def revisar(song, reviewer_id, accion, notas=None):
    """
    Resuelve una canción en revisión: aprobar, rechazar o publicar.

    Publicar es un paso aparte de aprobar a propósito: deja en manos del
    moderador la decisión de qué entra al repertorio de los eventos.
    """
    if accion not in ACCIONES_REVISION:
        return _fallo(
            f"Acción desconocida '{accion}'. Usá una de: {', '.join(ACCIONES_REVISION)}."
        )

    notas = (notas or '').strip()

    if accion == 'aprobar':
        destino = SONG_STATUS_APROBADA
    elif accion == 'rechazar':
        destino = SONG_STATUS_RECHAZADA
    else:
        destino = SONG_STATUS_PUBLICADA

    if song.status not in ORIGENES_POR_ACCION[accion]:
        permitidos = ' o '.join(f"'{s}'" for s in ORIGENES_POR_ACCION[accion])
        return _fallo(
            f"No se puede '{accion}' una canción que está '{song.status}'. "
            f"Esa acción sale de {permitidos}.",
            status_actual=song.status
        )

    if not transicion_valida(song.status, destino):
        return _fallo(f"No se puede pasar de '{song.status}' a '{destino}'.")

    if accion == 'rechazar' and len(notas) < _NOTAS_OBLIGATORIAS:
        return _fallo('Contale al autor qué tiene que corregir para poder rechazarla.')

    song.status = destino
    song.reviewed_at = datetime.utcnow()
    song.reviewed_by_id = reviewer_id
    song.review_notes = notas or None

    mensajes = {
        'aprobar': 'Canción aprobada. Ya se puede publicar.',
        'rechazar': 'Canción rechazada.',
        'publicar': 'Canción publicada en el repertorio.',
    }
    return _ok(mensajes[accion], song=song)


def reaperturar(song, user_id, permissions=()):
    """
    Devuelve una canción publicada a revisión porque le cambiaron el contenido.

    Es lo que evita que una letra modificada se entierre en el catálogo sin que
    nadie la vuelva a mirar. La llama la ruta de edición después de guardar.
    """
    if not puede_editar(song, user_id, permissions):
        return _fallo('No tenés permiso para modificar esta canción.')

    if song.status != SONG_STATUS_PUBLICADA:
        # Solo se reabre lo que estaba publicado. Si estaba en revisión o
        # borrador, el estado que corresponde es el que ya tiene.
        return None

    song.status = SONG_STATUS_EN_REVISION
    song.submitted_at = datetime.utcnow()
    song.review_notes = None
    song.reviewed_at = None
    song.reviewed_by_id = None

    return _ok('La canción volvió a revisión porque cambió su contenido.', song=song)


# --------------------------------------------------------------------------
# Sugerencias del equipo
# --------------------------------------------------------------------------

def crear_sugerencia(song, user_id, permissions=(), suggested_structure=None,
                     suggested_key=None, notas=None):
    """
    Propone un cambio sobre una canción existente.

    Se puede sugerir solo la estructura, solo el tono, o ambos. Si no viene
    ninguna de las dos cosas no hay nada que aprobar, así que se rechaza el alta.
    """
    if 'songs.suggest' not in permissions:
        return _fallo('Tu rol no tiene permiso para sugerir cambios.')

    if not puede_ver(song, user_id, permissions):
        return _fallo('No podés sugerir cambios sobre esta canción.')

    if song.status == SONG_STATUS_BORRADOR:
        return _fallo('Esta canción todavía no se envió a revisión.')

    key = (suggested_key or '').strip() or None
    notas = (notas or '').strip() or None

    if suggested_structure is None and key is None:
        return _fallo('La sugerencia tiene que traer una estructura, un tono, o ambos.')

    sugerencia = SongSuggestion(
        song=song,
        user_id=user_id,
        suggested_structure=suggested_structure,
        suggested_key=key,
        notes=notas
    )

    return _ok('Sugerencia enviada a la cola de moderación.', suggestion=sugerencia)


def resolver_sugerencia(sugerencia, reviewer_id, accion, notas=None):
    """
    Aprueba o rechaza una sugerencia.

    Al aprobarla se aplica a la canción y esta vuelve a 'en_revision': la letra
    pública nunca cambia de golpe, tiene que pasar por la cola una vez más.
    """
    if accion not in ('aprobar', 'rechazar'):
        return _fallo(f"Acción desconocida '{accion}'. Usá 'aprobar' o 'rechazar'.")

    if sugerencia.status != SONG_SUGGESTION_PENDIENTE:
        return _fallo(
            f"Esta sugerencia ya se resolvió como '{sugerencia.status}'.",
            status_actual=sugerencia.status
        )

    notas = (notas or '').strip()

    if accion == 'rechazar' and len(notas) < _NOTAS_OBLIGATORIAS:
        return _fallo('Contale al autor por qué no se acepta el cambio.')

    sugerencia.status = (
        SONG_SUGGESTION_APROBADA if accion == 'aprobar' else SONG_SUGGESTION_RECHAZADA
    )
    sugerencia.resolved_at = datetime.utcnow()
    sugerencia.resolved_by_id = reviewer_id
    sugerencia.resolution_notes = notas or None

    song = sugerencia.song
    if accion == 'aprobar':
        if sugerencia.suggested_structure is not None:
            song.structure = sugerencia.suggested_structure
        if sugerencia.suggested_key:
            song.key = sugerencia.suggested_key

        # El contenido público cambió: vuelve a la cola en vez de publicarse solo.
        if song.status == SONG_STATUS_PUBLICADA:
            song.status = SONG_STATUS_EN_REVISION
            song.submitted_at = datetime.utcnow()
            song.review_notes = None
            song.reviewed_at = None
            song.reviewed_by_id = None

    return _ok(
        'Sugerencia aplicada a la canción.' if accion == 'aprobar' else 'Sugerencia rechazada.',
        song=song,
        suggestion=sugerencia
    )


def sugerencias_pendientes(song_id=None):
    """Sugerencias sin resolver, opcionalmente acotadas a una canción."""
    query = SongSuggestion.query.filter_by(status=SONG_SUGGESTION_PENDIENTE)
    if song_id is not None:
        query = query.filter_by(song_id=song_id)
    return query.order_by(SongSuggestion.created_at.asc()).all()


def cola_de_revision():
    """
    Canciones esperando decisión del moderador, con las sugerencias pendientes
    de cada una para poder resolverlas en la misma pantalla.
    """
    songs = (
        Song.query
        .filter_by(status=SONG_STATUS_EN_REVISION)
        .order_by(Song.submitted_at.asc())
        .all()
    )
    return songs


# --------------------------------------------------------------------------
# Serialización
# --------------------------------------------------------------------------

def serializar_cancion(song, user_id=None, permissions=(), detail=False):
    """
    Mapeo de Song para la API.

    Mantiene las claves que el frontend ya consumía (id, name, author, genre,
    structure) y agrega las del proceso de moderación. 'can_edit' y
    'can_review' se calculan en el servidor para que el frontend no tenga que
    duplicar la matriz de permisos.
    """
    pendientes = [s for s in (song.suggestions or []) if s.status == SONG_SUGGESTION_PENDIENTE]

    datos = {
        'id': song.id,
        'name': song.name,
        'author': song.author.name if song.author else None,
        'genre': song.genre.name if song.genre else None,
        'structure': song.structure,
        'key': song.key,
        'status': song.status,
        'is_public': song.is_public,
        'submitted_at': song.submitted_at.isoformat() if song.submitted_at else None,
        'review_notes': song.review_notes,
        'suggestions_count': len(pendientes),
        'can_edit': puede_editar(song, user_id, permissions),
        'can_review': 'songs.moderate' in permissions,
    }

    if detail:
        datos['reviewed_at'] = song.reviewed_at.isoformat() if song.reviewed_at else None
        datos['reviewed_by'] = song.reviewed_by.username if song.reviewed_by else None
        datos['suggestions'] = [
            {
                'id': s.id,
                'status': s.status,
                'notes': s.notes,
                'suggested_key': s.suggested_key,
                'user': s.user.username if s.user else None,
                'created_at': s.created_at.isoformat() if s.created_at else None,
                'resolution_notes': s.resolution_notes,
            }
            for s in sorted(song.suggestions or [], key=lambda s: s.created_at or datetime.min)
        ]

    return datos


def serializar_sugerencia(sugerencia):
    return {
        'id': sugerencia.id,
        'song_id': sugerencia.song_id,
        'song_name': sugerencia.song.name if sugerencia.song else None,
        'status': sugerencia.status,
        'notes': sugerencia.notes,
        'suggested_key': sugerencia.suggested_key,
        'suggested_structure': sugerencia.suggested_structure,
        'user': sugerencia.user.username if sugerencia.user else None,
        'created_at': sugerencia.created_at.isoformat() if sugerencia.created_at else None,
        'resolution_notes': sugerencia.resolution_notes,
        'resolved_by': sugerencia.resolved_by.username if sugerencia.resolved_by else None,
    }