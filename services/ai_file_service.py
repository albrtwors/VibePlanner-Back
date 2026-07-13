# services/ai_file_service.py
import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from sqlalchemy import or_, func, cast, String

from models import Song, Genre, Author
from database import db

load_dotenv()

DEFAULT_LIMIT = 5
MAX_LIMIT = 15

# ==========================================
# ESQUEMAS DE EXTRACCIÓN DE CONTENIDO MIXTO
# ==========================================

class SubRequestSchema(BaseModel):
    action: str = Field(
        default="ADD",
        description=(
            "Qué se debe hacer con este criterio sobre el cancionero ACTUAL. Debe ser 'ADD' o 'REMOVE':\n"
            "- 'ADD': el usuario quiere agregar/incluir/buscar canciones para sumarlas al cancionero. Es el valor por defecto "
            "si el usuario no aclara lo contrario.\n"
            "- 'REMOVE': el usuario quiere quitar/eliminar/sacar canciones que YA están en el cancionero actual. "
            "Ej: 'quita Creep de Radiohead', 'saca las de reggaeton', 'elimina la de Michael Jackson'."
        )
    )
    type: str = Field(
        description=(
            "El tipo de criterio extraído. Debe ser estrictamente uno de los siguientes:\n"
            "- 'SONG_AND_AUTHOR': Si se especifica una canción junto a su autor. Ej: 'Creep de Radiohead'.\n"
            "- 'SPECIFIC_SONG': Si solo se menciona el título de una canción. Ej: 'Tristessa'.\n"
            "- 'ARTIST_ONLY': Si se pide un autor/artista genérico o varias de él. Ej: '2 de Michael Jackson'.\n"
            "- 'KEYWORD': Si se buscan temas por palabras clave en el título o en el cuerpo de la letra. Ej: 'que diga amor'.\n"
            "- 'GENRE': Si se solicita un género musical o algo mapeable a un género (ej: 'algo para niños', 'una de adoración'). Ej: 'algo de pop'."
        )
    )
    value: str = Field(
        description=(
            "El valor limpio extraído de la petición (ej: 'Michael Jackson', 'Creep', 'amor', 'pop'). "
            "Si el tipo es 'GENRE', sigue las reglas de mapeo de género indicadas en el system prompt."
        )
    )
    meta: Optional[str] = Field(
        default=None,
        description="Si el tipo es 'SONG_AND_AUTHOR', guarda aquí el nombre del artista (ej: 'Radiohead')."
    )
    quantity: Optional[int] = Field(
        default=None,
        description=(
            "Cantidad EXACTA solicitada por el usuario para este criterio, solo aplica a ARTIST_ONLY, KEYWORD y GENRE "
            "(ej: 'dame 2 de Michael Jackson' -> 2, 'una canción de adoración' -> 1). "
            "Deja este campo vacío/null si el usuario no especificó ningún número."
        )
    )

    @field_validator("action", mode="before")
    @classmethod
    def _normalizar_action(cls, v):
        if not v:
            return "ADD"
        v = str(v).strip().upper()
        return v if v in ("ADD", "REMOVE") else "ADD"

    @field_validator("quantity", mode="before")
    @classmethod
    def _coercionar_quantity(cls, v):
        if v in (None, "", "null", "None"):
            return None
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None


class FileIntentionSchema(BaseModel):
    requests: List[SubRequestSchema] = Field(
        description="Lista obligatoria que contiene todas las sub-peticiones identificadas en el mensaje (tanto de ADD como de REMOVE)."
    )
    suggested_title: Optional[str] = Field(
        default=None,
        description="Un título profesional sugerido para el setlist o cancionero. Solo tiene sentido si se están agregando canciones nuevas o si el cancionero se está armando desde cero."
    )


# ==========================================
# HELPERS
# ==========================================

def _normalizar_texto(texto: str) -> str:
    """minúsculas + sin tildes, para comparar de forma tolerante."""
    return (
        (texto or "")
        .lower()
        .strip()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )


def _serializar_song(song: Song) -> Dict[str, Any]:
    return {
        "id": song.id,
        "name": song.name,
        "author": song.author.name if song.author else "Desconocido",
        "genre": song.genre.name if song.genre else None,
    }


def _obtener_generos_disponibles() -> List[str]:
    """Géneros reales ya cargados en la biblioteca, para que la IA nunca invente uno que no existe."""
    try:
        return [g.name for g in db.session.query(Genre).all()]
    except Exception as e:
        print(f"[WARN] No se pudieron obtener los géneros disponibles: {e}")
        return []


def _resolver_limite(req: SubRequestSchema) -> int:
    if req.quantity:
        return min(req.quantity, MAX_LIMIT)
    return DEFAULT_LIMIT


# ==========================================
# MOTOR DE BÚSQUEDA: AGREGAR (ADD)
# ==========================================

def _buscar_para_agregar(sub_requests: List[SubRequestSchema], ids_ya_en_cancionero: set) -> tuple[List[dict], List[str]]:
    encontradas: List[dict] = []
    omitidas: List[str] = []
    ids_agregados = set(ids_ya_en_cancionero)

    for req in sub_requests:
        if req.action != "ADD":
            continue

        val_limpio = req.value.strip()
        if not val_limpio:
            continue

        query = Song.query.join(Author).outerjoin(Genre)
        resultados_sub_peticion = []
        limite = _resolver_limite(req)

        if req.type == "SONG_AND_AUTHOR" and req.meta:
            meta_limpio = req.meta.strip()
            song = query.filter(
                Song.name.ilike(f"%{val_limpio}%"),
                Author.name.ilike(f"%{meta_limpio}%")
            ).first()
            if song: resultados_sub_peticion.append(song)
            else: omitidas.append(f"{val_limpio} (de {meta_limpio})")

        elif req.type == "SPECIFIC_SONG":
            song = query.filter(Song.name.ilike(f"%{val_limpio}%")).first()
            if song: resultados_sub_peticion.append(song)
            else: omitidas.append(val_limpio)

        elif req.type == "ARTIST_ONLY":
            songs = query.filter(Author.name.ilike(f"%{val_limpio}%")).limit(limite).all()
            if songs: resultados_sub_peticion.extend(songs)
            else: omitidas.append(f"Temas de {val_limpio}")

        elif req.type == "KEYWORD":
            # Escaneo completo: Título, Autor y conversión explícita del JSON de la letra a String plano
            songs = query.filter(
                or_(
                    Song.name.ilike(f"%{val_limpio}%"),
                    Author.name.ilike(f"%{val_limpio}%"),
                    cast(Song.structure, String).ilike(f"%{val_limpio}%")
                )
            ).limit(limite).all()
            if songs: resultados_sub_peticion.extend(songs)
            else: omitidas.append(f"Palabra/Letra: '{val_limpio}'")

        elif req.type == "GENRE":
            val_normalizado = _normalizar_texto(val_limpio)
            songs = query.filter(
                or_(
                    func.lower(Genre.name).ilike(f"%{val_limpio.lower()}%"),
                    func.lower(Genre.name).ilike(f"%{val_normalizado}%")
                )
            ).limit(limite).all()
            if songs: resultados_sub_peticion.extend(songs)
            else: omitidas.append(f"Género: {val_limpio}")

        # Consolidar resultados previniendo duplicados (tanto entre sub-peticiones como con lo que ya está en el cancionero)
        for s in resultados_sub_peticion:
            if s.id not in ids_agregados:
                ids_agregados.add(s.id)
                encontradas.append(_serializar_song(s))

    return encontradas, omitidas


# ==========================================
# MOTOR DE BÚSQUEDA: QUITAR (REMOVE)
# ==========================================

def _buscar_para_quitar(sub_requests: List[SubRequestSchema], current_songs: List[dict]) -> tuple[List[dict], List[str]]:
    """
    A diferencia del ADD, esto NO consulta la base de datos: opera únicamente sobre
    la lista de canciones que el frontend nos dice que ya están en el cancionero actual.
    """
    a_quitar: List[dict] = []
    ids_quitados = set()
    no_encontradas: List[str] = []

    for req in sub_requests:
        if req.action != "REMOVE":
            continue

        val = _normalizar_texto(req.value.strip())
        meta = _normalizar_texto((req.meta or "").strip())
        if not val:
            continue

        matches = []

        if req.type in ("SONG_AND_AUTHOR", "SPECIFIC_SONG", "KEYWORD"):
            for s in current_songs:
                nombre = _normalizar_texto(s.get("name") or "")
                autor = _normalizar_texto(s.get("author") or "")
                if val in nombre and (not meta or meta in autor):
                    matches.append(s)

        elif req.type == "ARTIST_ONLY":
            for s in current_songs:
                if val in _normalizar_texto(s.get("author") or ""):
                    matches.append(s)

        elif req.type == "GENRE":
            for s in current_songs:
                if val in _normalizar_texto(s.get("genre") or ""):
                    matches.append(s)

        # Si pidió cantidad específica para quitar (poco común, pero por si acaso) respetamos el límite
        if req.quantity:
            matches = matches[: req.quantity]

        if matches:
            for s in matches:
                song_id = s.get("id")
                if song_id not in ids_quitados:
                    ids_quitados.add(song_id)
                    a_quitar.append(s)
        else:
            no_encontradas.append(req.value.strip())

    return a_quitar, no_encontradas


# ==========================================
# PROCESAMIENTO PRINCIPAL DEL ASISTENTE
# ==========================================

def procesar_asistente_file_ia(prompt_usuario: str, current_songs: Optional[List[dict]] = None) -> dict:
    current_songs = current_songs or []

    llm = ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="openai/gpt-oss-120b",
        temperature=0.0
    )

    generos_disponibles = _obtener_generos_disponibles()
    generos_texto = ", ".join(generos_disponibles) if generos_disponibles else "(sin géneros registrados todavía)"

    if current_songs:
        lineas_actuales = "\n".join(
            f"- \"{s.get('name')}\" de {s.get('author') or 'Desconocido'} (género: {s.get('genre') or 'N/A'})"
            for s in current_songs
        )
        resumen_actual = f"El cancionero actual YA contiene estas canciones:\n{lineas_actuales}"
    else:
        resumen_actual = "El cancionero actual está vacío por ahora."

    prompt_intencion = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres el analizador de intenciones de VibePlanner, un asistente que arma y EDITA cancioneros/setlists.\n"
            "Tu tarea MANDATORIA es devolver un único objeto JSON estructurado que contenga la propiedad raíz 'requests'.\n"
            "Jamás respondas con una lista directa de elementos ni omitas la raíz 'requests'.\n\n"

            "Formato de respuesta obligatorio esperado:\n"
            "{{\n"
            "  \"requests\": [\n"
            "    {{\"action\": \"ADD\", \"type\": \"ARTIST_ONLY\", \"value\": \"Michael Jackson\", \"meta\": null, \"quantity\": 2}},\n"
            "    {{\"action\": \"REMOVE\", \"type\": \"SONG_AND_AUTHOR\", \"value\": \"Creep\", \"meta\": \"Radiohead\", \"quantity\": null}}\n"
            "  ],\n"
            "  \"suggested_title\": \"Mix de Michael Jackson\"\n"
            "}}\n\n"

            "Reglas para clasificar 'action' dentro de cada elemento de 'requests':\n"
            "- 'ADD' (por defecto): el usuario quiere agregar, incluir, buscar o sumar canciones al cancionero.\n"
            "- 'REMOVE': el usuario quiere quitar, sacar, eliminar o borrar canciones que YA están en el cancionero actual "
            "(ej: 'quita la de Radiohead', 'saca las de reggaeton', 'elimina Tristessa'). Un mismo mensaje puede tener "
            "sub-peticiones de ambos tipos a la vez (ej: 'saca las de rock y agrega 2 de Coldplay').\n\n"

            "Reglas para clasificar 'type' dentro de cada elemento de 'requests':\n"
            "1. 'SONG_AND_AUTHOR': Si asocia tema + artista (ej: 'Creep de Radiohead'). 'value' = canción, 'meta' = artista.\n"
            "2. 'SPECIFIC_SONG': Si solo menciona una canción específica (ej: 'añade Tristessa').\n"
            "3. 'ARTIST_ONLY': Si pide canciones de un artista en general (ej: 'busca canciones de Michael Jackson', '2 de Coldplay').\n"
            "4. 'KEYWORD': Si desea buscar por palabras clave presentes tanto en el título como en el contenido de la letra.\n"
            "5. 'GENRE': Si pide un género musical, o algo mapeable a un género por características.\n\n"

            "Reglas especiales para 'GENRE':\n"
            f"El catálogo REAL de géneros ya existentes en la biblioteca es: [{generos_texto}].\n"
            "- Si el usuario menciona un género que coincide (o se parece) a uno de esa lista, usa el texto EXACTO tal como "
            "aparece en el catálogo.\n"
            "- Si el usuario pide algo por CARACTERÍSTICA que no es un género literal (ej: 'una para niños', 'algo para una "
            "boda', 'una canción triste', 'de adoración tranquila', 'algo animado'), mapéalo al género MÁS parecido dentro "
            "de esa misma lista real.\n"
            "- Si nada del catálogo se relaciona ni remotamente, deja 'value' tal cual lo dijo el usuario.\n\n"

            "Reglas para 'quantity': solo aplica a ARTIST_ONLY, KEYWORD y GENRE. Si el usuario da un número explícito "
            "('2 de...', 'tres canciones de...', 'una de...') colócalo aquí. Si no da número, deja null (se usará un valor "
            "por defecto razonable).\n\n"

            "Contexto del cancionero actual (úsalo SOLO para saber qué existe cuando el usuario pida quitar algo; "
            "no repitas esta lista en tu respuesta):\n"
            "{resumen}\n"
        )),
        ("human", "{input}")
    ])

    try:
        structured_llm = llm.with_structured_output(FileIntentionSchema, method="json_mode")
        analisis = (prompt_intencion | structured_llm).invoke({
            "input": prompt_usuario,
            "resumen": resumen_actual
        })
    except Exception as e:
        print(f"[ERROR] Clasificación híbrida falló: {e}")
        return {
            "bot_response": "Ocurrió un inconveniente analizando la estructura de la solicitud. Por favor, intente reformularla.",
            "songs_to_add": [],
            "songs_to_remove": []
        }

    ids_ya_en_cancionero = {s.get("id") for s in current_songs if s.get("id") is not None}

    canciones_a_agregar, omitidas_agregar = _buscar_para_agregar(analisis.requests, ids_ya_en_cancionero)
    canciones_a_quitar, no_encontradas_quitar = _buscar_para_quitar(analisis.requests, current_songs)

    partes_mensaje = []

    if canciones_a_agregar:
        partes_mensaje.append(
            f"Encontré {len(canciones_a_agregar)} canción(es) para agregar según lo solicitado."
        )
    if omitidas_agregar:
        lista_omitidas = ", ".join(f"'{o}'" for o in omitidas_agregar)
        partes_mensaje.append(f"No pude localizar en la biblioteca: {lista_omitidas}.")

    if canciones_a_quitar:
        partes_mensaje.append(
            f"Marqué {len(canciones_a_quitar)} canción(es) del cancionero actual para quitar."
        )
    if no_encontradas_quitar:
        lista_no_encontradas = ", ".join(f"'{o}'" for o in no_encontradas_quitar)
        partes_mensaje.append(f"No encontré en el cancionero actual para quitar: {lista_no_encontradas}.")

    if not partes_mensaje:
        partes_mensaje.append(
            "No logré identificar ninguna acción concreta (agregar o quitar canciones) en tu mensaje. "
            "¿Podrías reformularlo? Por ejemplo: '2 de Coldplay y algo de adoración' o 'quita la de Radiohead'."
        )

    mensaje_bot = " ".join(partes_mensaje)

    return {
        "bot_response": mensaje_bot,
        "suggested_title": analisis.suggested_title,
        "songs_to_add": canciones_a_agregar,
        "songs_to_remove": canciones_a_quitar
    }