# services/ai_song_service.py
import os
import time
import random
import requests
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, AliasChoices
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from dotenv import load_dotenv
load_dotenv()

URL_API_VERCEL = "https://song-api-wheat.vercel.app/songs"
URL_API_GENEROS = f"{URL_API_VERCEL}/genres"

# ==========================================
# 1. ESQUEMAS DE PYDANTIC (ESTRUCTURA DE SALIDA)
# ==========================================
class SongPart(BaseModel):
    title: Literal['intro', 'verso 1', 'verso 2', 'verso 3', 'verso 4', 'coro', 'puente', 'solo', 'outro'] = Field(
        description="El bloque o sección musical lírica de la estructura.",
        validation_alias=AliasChoices('title', 'name', 'section', 'type', 'part')
    )
    content: str = Field(description="Texto limpio de los versos de la sección. Usa saltos de línea '\\n'")

    @field_validator('title', mode='before')
    @classmethod
    def to_lowercase(cls, v: str) -> str:
        if isinstance(v, str):
            val = v.lower().strip().replace('verso', 'verso ')
            return " ".join(val.split())
        return v


class GeneratedSongSchema(BaseModel):
    name: str = Field(description="El título oficial de la canción o el más adecuado.")
    parts: List[SongPart] = Field(description="Lista de bloques secuenciales de la canción")


class SearchIntentionSchema(BaseModel):
    mode: Literal['BUSCAR', 'PROVEER'] = Field(
        description="BUSCAR si quiere rastrear una canción existente. PROVEER si dicta líricas directamente.",
        validation_alias=AliasChoices('mode', 'modo', 'intent', 'intencion')
    )
    title: str = Field(
        default="",
        description="Título de la canción SI el usuario lo menciona explícitamente (ej: 'In Too Deep'). Vacío si no."
    )
    author: str = Field(
        default="",
        description="Autor, artista o banda SI el usuario lo menciona (ej: 'de Genesis' -> 'Genesis'). Vacío si no."
    )
    genre: str = Field(
        default="",
        description=(
            "Género musical a usar en la búsqueda. Si el usuario menciona un género literal (ej: 'rock'), "
            "cópialo tal cual. Si en cambio pide algo por CARACTERÍSTICA que no es un género literal "
            "(ej: 'una para niños', 'algo para una boda', 'una canción triste', 'de adoración tranquila'), "
            "mapéalo al género MÁS parecido dentro de la lista de géneros que realmente existen en el "
            "catálogo (te la doy como contexto), usando el texto EXACTO tal como aparece en esa lista. "
            "Si ninguno se relaciona ni remotamente, deja este campo vacío."
        )
    )
    want_random: bool = Field(
        default=False,
        description=(
            "True si el pedido es genérico dentro de una categoría, sin título puntual (ej: 'dame una de rock', "
            "'alguna canción de adoración', 'cualquiera de Genesis'). False si pidió algo específico y puntual."
        )
    )
    cleaned_query: str = Field(
        default="",
        description="Si el modo es PROVEER, aquí van las líricas/texto tal cual las escribió, intactas."
    )


# ==========================================
# 2. CONEXIÓN CON LA API EXTERNA (BÚSQUEDA POR CAMPO, DETERMINÍSTICA)
# ==========================================
_genre_cache = {"genres": [], "fetched_at": 0.0}
_GENRE_CACHE_TTL_SECONDS = 300  # 5 minutos, para no pegarle a la API en cada mensaje


def _obtener_generos_disponibles() -> List[str]:
    """Trae los géneros reales del catálogo (con cache corto) para que la IA nunca invente uno que no existe."""
    now = time.time()
    if _genre_cache["genres"] and (now - _genre_cache["fetched_at"] < _GENRE_CACHE_TTL_SECONDS):
        return _genre_cache["genres"]
    try:
        response = requests.get(URL_API_GENEROS, timeout=6)
        if response.status_code == 200:
            generos = response.json()
            if isinstance(generos, list):
                _genre_cache["genres"] = generos
                _genre_cache["fetched_at"] = now
                return generos
    except Exception as e:
        print(f"[WARN] No se pudo refrescar el cache de géneros: {e}")
    return _genre_cache["genres"]  # lo último que tengamos, aunque esté vencido, mejor que nada


def _buscar_en_api(**campos) -> List[dict]:
    """
    Búsqueda por campo específico (name/author/genre/lyrics). Varios campos juntos
    se combinan como AND en el backend (ej: name + author = coincide con ambos).
    """
    params = {k: v for k, v in campos.items() if v}
    if not params:
        return []
    try:
        response = requests.get(URL_API_VERCEL, params=params, timeout=6)
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[WARN] Error de conexión con la API de Vercel: {e}")
    return []


def _elegir_mejor_resultado(resultados: List[dict], author_hint: str = "") -> Optional[dict]:
    """De una lista de resultados, prioriza el que matchee el autor pedido (si lo hay)."""
    if not resultados:
        return None
    if author_hint:
        objetivo = author_hint.strip().lower()
        coincidencias = [r for r in resultados if objetivo in (r.get("author") or "").strip().lower()]
        if coincidencias:
            return coincidencias[0]
    return resultados[0]


def _resolver_busqueda(analisis: SearchIntentionSchema) -> Optional[dict]:
    """
    Toda la lógica de QUÉ pedir y CUÁNDO elegir al azar vive acá, en código determinístico,
    no en la IA (así el comportamiento es siempre el mismo, no depende de que el modelo
    "se acuerde" de la regla en cada respuesta).
    """
    title = analisis.title.strip()
    author = analisis.author.strip()
    genre = analisis.genre.strip()

    # CASO 1: hay título y/o autor puntual -> búsqueda precisa, NUNCA al azar si hay título.
    if title or author:
        resultados = _buscar_en_api(name=title, author=author)
        if resultados:
            if title:
                return resultados[0]
            return random.choice(resultados) if analisis.want_random else resultados[0]

        # Plan B: el AND (título+autor) no dio nada -> relajamos y probamos por separado.
        if title:
            resultados_titulo = _buscar_en_api(name=title)
            mejor = _elegir_mejor_resultado(resultados_titulo, author_hint=author)
            if mejor:
                return mejor
        if author:
            resultados_autor = _buscar_en_api(author=author)
            if resultados_autor:
                return random.choice(resultados_autor) if analisis.want_random else resultados_autor[0]
        return None

    # CASO 2: solo género (literal o mapeado desde una característica como "para niños")
    # -> siempre se elige al azar entre los resultados, tal como pediste.
    if genre:
        resultados = _buscar_en_api(genre=genre)
        if not resultados:
            return None
        return random.choice(resultados)

    return None


# ==========================================
# 3. FUNCIÓN PRINCIPAL (PROCESAMIENTO E IA)
# ==========================================
def generar_cancion_ia(prompt_usuario: str) -> dict:
    llm = ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="openai/gpt-oss-120b",
        temperature=0.0
    )

    # ---------------------------------------------------------------------
    # PASO 1: Clasificación de Intención + Extracción Estructurada
    # ---------------------------------------------------------------------
    generos_disponibles = _obtener_generos_disponibles()
    generos_texto = ", ".join(generos_disponibles) if generos_disponibles else "(sin géneros registrados todavía)"

    structured_intencion = llm.with_structured_output(SearchIntentionSchema, method="json_mode")

    prompt_intencion = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres un clasificador y extractor de intención de búsqueda musical para VibePlanner.\n\n"
            "CRÍTICO: responde única y exclusivamente con el JSON del esquema provisto.\n\n"
            "1. mode='BUSCAR' si el usuario quiere encontrar una canción existente (por título, autor, género, "
            "o una característica como 'para niños', 'de adoración', 'triste', 'para una boda').\n"
            "   mode='PROVEER' si te está dictando o pegando la letra/estructura completa directamente.\n\n"
            "Si mode='BUSCAR', además extrae:\n"
            "- title: título EXACTO si lo menciona explícitamente. Vacío si no.\n"
            "- author: artista/banda si lo menciona. Vacío si no.\n"
            "- genre: si menciona un género musical literal, cópialo tal cual. Si pide algo por CARACTERÍSTICA "
            "que no es un género literal, mapéalo al género MÁS parecido de esta lista real del catálogo: "
            f"[{generos_texto}]. Usa el texto EXACTO tal como aparece ahí. Si nada se relaciona, deja vacío.\n"
            "- want_random: true si el pedido es genérico dentro de una categoría sin título puntual "
            "('dame una de rock', 'cualquiera de Genesis'). false si pidió algo específico.\n\n"
            "Si mode='PROVEER', copia el texto de la letra completo e intacto en cleaned_query."
        )),
        ("human", "{input}")
    ])

    print("[INFO] Analizando intención del usuario...")
    analisis_inicial: SearchIntentionSchema = (prompt_intencion | structured_intencion).invoke({
        "input": prompt_usuario
    })

    # ---------------------------------------------------------------------
    # PASO 2: Resolución del Origen de los Datos
    # ---------------------------------------------------------------------
    texto_origen = ""
    titulo_sugerido = ""
    autor_sugerido = "Desconocido"
    genero_sugerido = ""

    if analisis_inicial.mode == "BUSCAR":
        datos_api = _resolver_busqueda(analisis_inicial)

        if datos_api:
            titulo_sugerido = datos_api.get("name") or analisis_inicial.title or "Sin título"
            autor_sugerido = datos_api.get("author") or "Desconocido"
            genero_sugerido = datos_api.get("genre") or ""
            texto_origen = datos_api.get("lyrics") or ""
            print(f"[INFO] Éxito: Canción '{titulo_sugerido}' de '{autor_sugerido}' recuperada.")
        else:
            criterios = []
            if analisis_inicial.title:
                criterios.append(f"título '{analisis_inicial.title}'")
            if analisis_inicial.author:
                criterios.append(f"autor '{analisis_inicial.author}'")
            if analisis_inicial.genre:
                criterios.append(f"género '{analisis_inicial.genre}'")
            descripcion = " y ".join(criterios) if criterios else f"'{prompt_usuario}'"

            print("[WARN] No se encontraron resultados en la API de Vercel.")
            return {
                "bot_response": (
                    f"¡Hola! Busqué por {descripcion} en nuestro catálogo, pero no logré encontrar nada. "
                    f"¿Me podrías pasar la letra tú mismo para que la estructure?"
                ),
                "song_data": None
            }
    else:
        print("[INFO] Modo PROVEER detectado. Procesando líricas directas.")
        texto_origen = analisis_inicial.cleaned_query
        titulo_sugerido = "Estructura Proveída por Usuario"

    if not texto_origen or len(texto_origen.strip()) == 0:
        return {
            "bot_response": "¡Hola! No pude detectar ninguna letra o texto válido en tu mensaje. ¿Podrías volver a intentarlo?",
            "song_data": None
        }

    # ---------------------------------------------------------------------
    # PASO 3: Formateo Estricto de la Estructura Lírica con Pydantic
    # ---------------------------------------------------------------------
    parser = PydanticOutputParser(pydantic_object=GeneratedSongSchema)

    prompt_formateador = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres un formateador de datos JSON ultra-preciso especializado en música.\n"
            "Tu trabajo es tomar el texto de la letra provista, identificar las secciones y estructurar todo en el JSON requerido.\n\n"
            "{instrucciones_formato}"
        )),
        ("human", "Título de referencia: {titulo}\nTexto original de la letra:\n\n{texto_crudo}")
    ])

    print("[INFO] Estructurando líricas con IA...")
    try:
        chain_formateadora = prompt_formateador | llm | parser
        resultado_ia = chain_formateadora.invoke({
            "titulo": titulo_sugerido,
            "texto_crudo": texto_origen,
            "instrucciones_formato": parser.get_format_instructions()
        })

        if not resultado_ia or not resultado_ia.parts or len(resultado_ia.parts) == 0:
            print("[WARN] La IA no pudo identificar una estructura musical válida.")
            return {
                "bot_response": "¡Ups! Estuve analizando el texto que me pasaste, pero no logré identificar una estructura musical clara (como versos o coros). ¿Podrías revisar la letra y asegurarte de separar bien las estrofas?",
                "song_data": None
            }

    except Exception as e:
        print(f"[ERROR] Fallo en el parseo o invocación de la IA: {e}")
        return {
            "bot_response": "Lo siento, tuve un problema interno al procesar y formatear la estructura de la canción. ¿Podrías intentar enviarla de nuevo?",
            "song_data": None
        }

    # ---------------------------------------------------------------------
    # PASO 4: Formato Final Enriquecido con Metadata para el Frontend
    # ---------------------------------------------------------------------
    return {
        "bot_response": f"¡Listo! Encontré y procesé la estructura para **{resultado_ia.name}**. Aquí abajo la tienes organizada para tu setlist.",
        "song_data": {
            "name": resultado_ia.name,
            "author": autor_sugerido,
            "genre": genero_sugerido,
            "structure": {
                "parts": [{"title": part.title, "content": part.content} for part in resultado_ia.parts]
            }
        }
    }