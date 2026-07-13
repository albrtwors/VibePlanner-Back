# services/song_vision_service.py
import os
import re
from typing import List
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

# ==========================================
# ESQUEMAS DE EXTRACCIÓN (PYDANTIC)
# ==========================================
# CLAVE DEL DISEÑO: la IA NUNCA arma el string final con corchetes.
# Solo transcribe la letra "limpia" y dice, por cada acorde, en qué índice
# de carácter de esa letra debería insertarse. El empalme real (que es
# aritmética pura de offsets) lo hace Python de forma 100% determinística.
class ChordPlacementSchema(BaseModel):
    chord: str = Field(
        description="El acorde tal como aparece en la imagen, SIN corchetes ni paréntesis. Ej: 'Bm', 'G', 'C7', 'F#m7'."
    )
    char_index: int = Field(
        description=(
            "Índice de carácter (empezando en 0) del campo 'text' de ESTA MISMA línea, en la posición donde "
            "el acorde queda alineado verticalmente arriba en la imagen. El acorde se insertará automáticamente "
            "justo ANTES de ese carácter. Si el acorde está encima del inicio de una palabra, usa el índice donde "
            "empieza esa palabra. Si el acorde cae después de la última letra de la línea (acorde 'colgado' al "
            "final), usa la longitud total del texto de la línea."
        )
    )


class LyricLineSchema(BaseModel):
    text: str = Field(
        description=(
            "El texto de la línea de LETRA tal cual aparece en la imagen, respetando mayúsculas y ortografía. "
            "NUNCA incluyas acordes, corchetes ni paréntesis en este campo: va la letra pelada, nada más."
        )
    )
    chords: List[ChordPlacementSchema] = Field(
        default=[],
        description=(
            "TODOS los acordes que aparecen en el renglón de acordes justo ARRIBA de esta línea de letra. "
            "Si no hay acordes arriba de esta línea (ej: una línea de letra sin cambio de acorde), deja la lista vacía."
        )
    )


class AISongPartPartSchema(BaseModel):
    title: str = Field(
        description="Título de la sección musical (Ej: 'Estrofa', 'Coro', 'Puente'). Si no hay etiquetas claras en la imagen, usa por defecto 'Estrofa'."
    )
    lines: List[LyricLineSchema] = Field(
        description="Lista ordenada de las líneas de letra de esta sección, cada una con sus acordes y posiciones."
    )


class AIChartExtractorSchema(BaseModel):
    song_name: str = Field(description="Título de la canción identificado o inferido de las primeras líneas.")
    parts: List[AISongPartPartSchema] = Field(description="Lista de bloques estructurados de la canción.")


# ==========================================
# HELPERS DE MERGE DETERMINÍSTICO (SIN IA)
# ==========================================
def _clean_chord_token(raw: str) -> str:
    """Quita corchetes/paréntesis/espacios que la IA pudiera haber metido igual por las dudas."""
    return re.sub(r"[\[\]\(\)]", "", (raw or "")).strip()


def _inject_chords_into_line(text: str, chords: List[ChordPlacementSchema]) -> str:
    """
    Inserta cada acorde como '[Acorde]' en la posición indicada, de forma exacta y sin
    desalinear a los demás. Truco matemático: se insertan de DERECHA A IZQUIERDA
    (mayor índice primero), así cada inserción nunca corre los índices de los acordes
    que todavía faltan por insertar.
    Para acordes que comparten exactamente el mismo índice (caso raro: varios acordes
    "colgados" sin letra debajo), se procesan en orden inverso de aparición para que el
    resultado final respete el orden original de izquierda a derecha.
    """
    if not chords:
        return text

    length = len(text)
    enumerated = []
    for original_pos, c in enumerate(chords):
        chord_name = _clean_chord_token(c.chord)
        if not chord_name:
            continue
        idx = max(0, min(int(c.char_index), length))
        enumerated.append((original_pos, idx, chord_name))

    if not enumerated:
        return text

    # Orden: índice descendente primero; en empates, posición original descendente
    # (para que al insertar de atrás para adelante, el orden final quede correcto).
    enumerated.sort(key=lambda t: (-t[1], -t[0]))

    result = text
    for _original_pos, idx, chord_name in enumerated:
        result = result[:idx] + f"[{chord_name}]" + result[idx:]

    return result


def _build_part_content(part: AISongPartPartSchema) -> str:
    lines_with_chords = [_inject_chords_into_line(line.text, line.chords) for line in part.lines]
    return "\n".join(lines_with_chords)


# ==========================================
# MOTOR DE ANÁLISIS DE IMÁGENES
# ==========================================
class SongVisionService:
    def __init__(self):
        self.llm = ChatGroq(
            api_key=os.getenv("GROQ_API_KEY"),
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.0
        ).with_structured_output(AIChartExtractorSchema)

    def extract_song_from_image(self, base64_image_data: str, mime_type: str = "image/jpeg") -> dict:
        """
        Analiza la imagen de un cancionero/tablatura y devuelve la estructura ya transpuesta
        a corchetes [Acorde] insertados exactamente donde corresponde sobre la letra.

        La IA solo hace el trabajo de PERCEPCIÓN (leer letra + detectar posición de cada
        acorde); el EMPALME exacto lo hace código Python determinístico, evitando los
        errores de conteo/offset que ocurrían cuando se le pedía a la IA armar el string
        final con los corchetes ya insertados.
        """
        system_instructions = (
            "Eres el transcriptor armónico principal de VibePlanner, especialista en lectura de cancioneros "
            "(digitales o manuscritos) con acordes escritos arriba de la letra.\n\n"

            "TU TAREA (en dos partes, NO mezcles ambas):\n"
            "1. Transcribe cada línea de LETRA tal cual aparece en la imagen, en el campo 'text', SIN insertar "
            "ningún acorde, corchete ni paréntesis ahí. Respeta mayúsculas y ortografía exactas.\n"
            "2. Para cada acorde que veas en el renglón justo ARRIBA de esa línea de letra, reporta en 'chords' "
            "el nombre del acorde (sin corchetes ni paréntesis) y el 'char_index': la posición del carácter en "
            "'text' donde ese acorde queda alineado verticalmente arriba.\n\n"

            "🚨 AUDITORÍA OBLIGATORIA DE INVENTARIO (CERO OMISIONES):\n"
            "Antes de terminar cada línea, cuenta cuántos acordes hay en el renglón de acordes de arriba. La "
            "lista 'chords' de esa línea DEBE tener exactamente esa cantidad de elementos. Un acorde 'Bm' y un "
            "'G' arriba significa que DEBEN aparecer un objeto para 'Bm' y otro para 'G', sin importar si están "
            "al inicio, en medio o muy a la derecha de la línea.\n\n"

            "NORMALIZACIÓN DE CIFRADOS MANUSCRITOS:\n"
            "Si el acorde está escrito entre paréntesis, ej. '(Am)', repórtalo en 'chord' solo como 'Am' (sin "
            "los paréntesis). Nunca pongas corchetes ni paréntesis dentro del campo 'chord'.\n\n"

            "CÓMO CALCULAR char_index (piensa columna por columna):\n"
            "1. Mira el acorde en el renglón de arriba y su posición horizontal.\n"
            "2. Baja verticalmente hasta el renglón de letra e identifica qué carácter de 'text' queda justo "
            "debajo (o el más cercano si no calza exacto).\n"
            "3. char_index es la posición (contando desde 0) de ESE carácter dentro de 'text'.\n"
            "4. Si el acorde está exactamente sobre el inicio de una palabra, usa el índice donde empieza esa "
            "palabra. Si está muy a la derecha, más allá de la última letra, usa len(text).\n"
            "5. Si una línea NO tiene ningún acorde arriba, deja 'chords' como lista vacía — no inventes acordes.\n\n"

            "EJEMPLO COMPLETO:\n"
            "- Imagen visual:\n"
            "     Bm              G\n"
            "  La única Razón de mi adoración\n"
            "  D            A\n"
            "  Eres tú mi Jesús\n\n"
            "- Debe transcribirse como estas dos líneas:\n"
            "  Línea 1: text='La única Razón de mi adoración', "
            "chords=[{chord:'Bm', char_index:0}, {chord:'G', char_index:18}]\n"
            "  (índice 0 = inicio de 'La'; índice 18 = inicio de 'mi')\n"
            "  Línea 2: text='Eres tú mi Jesús', "
            "chords=[{chord:'D', char_index:0}, {chord:'A', char_index:11}]\n"
            "  (índice 0 = inicio de 'Eres'; índice 11 = inicio de 'Jesús')\n\n"

            "REGLAS DE SEGURIDAD E HIGIENE:\n"
            "- Si no hay secciones explícitas (Intro, Coro, etc.), usa 'Estrofa' como título por defecto.\n"
            "- Preserva el orden exacto de las líneas tal como aparecen en la imagen.\n"
            "- Si una línea es puramente instrumental (acordes sin letra debajo), igual crea el objeto de línea "
            "con text='' y reporta ahí los acordes con índices crecientes (0, 1, 2...) en el orden en que "
            "aparecen de izquierda a derecha."
        )

        message = HumanMessage(
            content=[
                {"type": "text", "text": system_instructions},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{base64_image_data}"},
                },
            ]
        )

        try:
            resultado_extractor: AIChartExtractorSchema = self.llm.invoke([message])

            parts_out = []
            for part in resultado_extractor.parts:
                parts_out.append({
                    "title": part.title,
                    "content": _build_part_content(part)
                })

            return {
                "success": True,
                "song_name": resultado_extractor.song_name,
                "structure": {
                    "parts": parts_out
                }
            }
        except Exception as e:
            print(f"[ERROR MOTOR VISIÓN IA]: {str(e)}")
            return {
                "success": False,
                "error": "El motor de IA no logró estructurar armónicamente los acordes de la imagen."
            }