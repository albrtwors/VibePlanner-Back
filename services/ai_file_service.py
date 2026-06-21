# services/ai_file_service.py
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from sqlalchemy import or_, func, cast, String

from models import Song, Genre, Author
from database import db 

load_dotenv()

# ==========================================
# ESQUEMAS DE EXTRACCIÓN DE CONTENIDO MIXTO
# ==========================================

class SubRequestSchema(BaseModel):
    type: str = Field(
        description=(
            "El tipo de criterio extraído. Debe ser estrictamente uno de los siguientes:\n"
            "- 'SONG_AND_AUTHOR': Si se especifica una canción junto a su autor. Ej: 'Creep de Radiohead'.\n"
            "- 'SPECIFIC_SONG': Si solo se menciona el título de una canción. Ej: 'Tristessa'.\n"
            "- 'ARTIST_ONLY': Si se pide un autor/artista genérico o varias de él. Ej: '2 de Michael Jackson'.\n"
            "- 'KEYWORD': Si se buscan temas por palabras clave en el título o en el cuerpo de la letra. Ej: 'que diga amor'.\n"
            "- 'GENRE': Si se solicita un género musical. Ej: 'algo de pop'."
        )
    )
    value: str = Field(
        description="El valor limpio extraído de la petición (ej: 'Michael Jackson', 'Creep', 'amor', 'pop')."
    )
    meta: Optional[str] = Field(
        default=None,
        description="Si el tipo es 'SONG_AND_AUTHOR', guarda aquí el nombre del artista (ej: 'Radiohead')."
    )


class FileIntentionSchema(BaseModel):
    requests: List[SubRequestSchema] = Field(
        description="Lista obligatoria que contiene todas las sub-peticiones identificadas en el mensaje."
    )
    suggested_title: Optional[str] = Field(
        default=None,
        description="Un título profesional sugerido para el setlist o cancionero."
    )


# ==========================================
# MOTOR DE BÚSQUEDA INTEGRADO Y DE ALTO NIVEL
# ==========================================

def ejecutar_busqueda_combinada(sub_requests: List[SubRequestSchema]) -> tuple[List[dict], List[str]]:
    encontradas = []
    omitidas = []
    ids_agregados = set()

    for req in sub_requests:
        val_limpio = req.value.strip()
        if not val_limpio:
            continue

        query = Song.query.join(Author).outerjoin(Genre)
        resultados_sub_peticion = []

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
            songs = query.filter(Author.name.ilike(f"%{val_limpio}%")).limit(5).all()
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
            ).limit(5).all()
            if songs: resultados_sub_peticion.extend(songs)
            else: omitidas.append(f"Palabra/Letra: '{val_limpio}'")

        elif req.type == "GENRE":
            val_normalizado = val_limpio.lower().replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
            songs = query.filter(
                or_(
                    func.lower(Genre.name).ilike(f"%{val_limpio}%"),
                    func.lower(Genre.name).ilike(f"%{val_normalizado}%")
                )
            ).limit(5).all()
            if songs: resultados_sub_peticion.extend(songs)
            else: omitidas.append(f"Género: {val_limpio}")

        # Consolidar resultados previniendo duplicados concurrentes
        for s in resultados_sub_peticion:
            if s.id not in ids_agregados:
                ids_agregados.add(s.id)
                encontradas.append({
                    "id": s.id,
                    "name": s.name,
                    "author": s.author.name if s.author else "Desconocido",
                    "genre": s.genre.name if s.genre else None
                })

    return encontradas, omitidas


# ==========================================
# PROCESAMIENTO PRINCIPAL DEL ASISTENTE
# ==========================================

def procesar_asistente_file_ia(prompt_usuario: str) -> dict:
    # Corrección del modelo a la versión estable de producción en Groq
    llm = ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="openai/gpt-oss-120b",
        temperature=0.0
    )
    
    prompt_intencion = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres el analizador de intenciones de VibePlanner.\n"
            "Tu tarea MANDATORIA es devolver un único objeto JSON estructurado que contenga la propiedad raíz 'requests'.\n"
            "Jamás respondas con una lista directa de elementos ni omitas la raíz 'requests'.\n\n"
            
            "Formato de respuesta obligatorio esperado:\n"
            "{{\n"
            "  \"requests\": [\n"
            "    {{\"type\": \"ARTIST_ONLY\", \"value\": \"Michael Jackson\", \"meta\": null}}\n"
            "  ],\n"
            "  \"suggested_title\": \"Mix de Michael Jackson\"\n"
            "}}\n\n"
            
            "Reglas para clasificar las propiedades dentro de 'requests':\n"
            "1. 'SONG_AND_AUTHOR': Si asocia tema + artista (ej: 'Creep de Radiohead'). 'value' = canción, 'meta' = artista.\n"
            "2. 'SPECIFIC_SONG': Si solo menciona una canción específica (ej: 'añade Tristessa').\n"
            "3. 'ARTIST_ONLY': Si pide canciones de un artista en general (ej: 'busca canciones de Michael Jackson').\n"
            "4. 'KEYWORD': Si desea buscar por palabras clave presentes tanto en el título como en el contenido de la letra.\n"
            "5. 'GENRE': Si pide un género (ej: 'algo de pop')."
        )),
        ("human", "{input}")
    ])
    
    try:
        structured_llm = llm.with_structured_output(FileIntentionSchema, method="json_mode")
        analisis = (prompt_intencion | structured_llm).invoke({"input": prompt_usuario})
    except Exception as e:
        print(f"[ERROR] Clasificación híbrida falló: {e}")
        return {
            "bot_response": "Ocurrió un inconveniente analizando la estructura de la solicitud. Por favor, intente reformularla.",
            "songs": []
        }

    canciones_encontradas, omitidas = ejecutar_busqueda_combinada(analisis.requests)
    
    if not canciones_encontradas:
        mensaje_bot = "No se encontraron canciones que coincidan con los criterios especificados en la biblioteca actual."
    else:
        mensaje_bot = f"Se han recopilado con éxito {len(canciones_encontradas)} canciones basadas en los diferentes criterios de su solicitud."
        if omitidas:
            lista_omitidas = ", ".join([f"'{o}'" for o in omitidas])
            mensaje_bot += f" No obstante, no se pudieron localizar los siguientes elementos en la base de datos: {lista_omitidas}."

    return {
        "bot_response": mensaje_bot,
        "suggested_title": analisis.suggested_title,
        "songs": canciones_encontradas
    }