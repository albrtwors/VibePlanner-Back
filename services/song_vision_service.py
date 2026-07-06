# services/song_vision_service.py
import os
from typing import List
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

# ==========================================
# ESQUEMAS DE EXTRACCIÓN (PYDANTIC)
# ==========================================
class AISongPartPartSchema(BaseModel):
    title: str = Field(description="Título de la sección musical (Ej: 'Estrofa', 'Coro'). Si no hay etiquetas claras, usa por defecto 'Estrofa'.")
    content: str = Field(description="Contenido procesado línea por línea con los acordes inyectados estrictamente en formato [Acorde].")

class AIChartExtractorSchema(BaseModel):
    song_name: str = Field(description="Título de la canción identificado o inferido de las primeras líneas.")
    parts: List[AISongPartPartSchema] = Field(description="Lista de bloques estructurados de la canción.")

# ==========================================
# MOTOR LOGÍSTICO DE ANÁLISIS DE IMÁGENES
# ==========================================
class SongVisionService:
    def __init__(self):
        self.llm = ChatGroq(
            api_key=os.getenv("GROQ_API_KEY"),
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.0
        ).with_structured_output(AIChartExtractorSchema)

    def extract_song_from_image(self, base64_image_data: str) -> dict:
        """
        Analiza la imagen utilizando un enfoque de indexación matricial y auditoría
        estricta de tokens para asegurar la inyección del 100% de los acordes detectados.
        """
        system_instructions = (
            "Eres el transcriptor armónico principal y especialista en análisis espacial bidimensional de VibePlanner.\n"
            "Tu tarea es procesar imágenes de cancioneros (digitales o manuscritos) y convertirlas en un JSON estructurado perfecto.\n\n"
            
            "¡REGLA DE ORO DE INYECCIÓN DE ACORDES!:\n"
            "Cualquier acorde detectado debe ser encapsulado estrictamente dentro de corchetes rígidos: '[Acorde]'.\n"
            "Está TOTALMENTE PROHIBIDO omitir acordes o dejarlos fuera del texto si están presentes en la imagen.\n\n"
            
            "🚨 AUDITORÍA OBLIGATORIA DE INVENTARIO (REGLA DE CERO OMISIONES):\n"
            "Antes de dar por terminada la transcripción de una línea de texto, debes contar cuántos acordes hay en la línea superior de la imagen.\n"
            "Debes asegurar que el 100% de esos acordes queden inyectados abajo. Si en la línea superior hay UN 'Bm' y UN 'G', en el JSON final DEBEN aparecer obligatoriamente un '[Bm]' y un '[G]' en sus posiciones correspondientes. ¡No ignores los acordes que están a mitad o al final de la línea!\n\n"
            
            "🚨 NORMALIZACIÓN DE CIFRADOS MANUSCRITOS (REGLA DE PARÉNTESIS):\n"
            "Si encuentras acordes escritos entre paréntesis como (Am), (F), (C), remueve los paréntesis y normalízalos siempre a corchetes rígidos [Am], [F], [C].\n\n"
            
            "ALGORITMO DE FUSIÓN POR COLUMNAS VERTICALES:\n"
            "Analiza las líneas de forma síncrona en pares (Renglón de Acordes + Renglón de Letra):\n"
            "1. Escanea horizontalmente de izquierda a derecha.\n"
            "2. Registra cada acorde y calcula su desplazamiento/sangría espacial exacta con respecto a la letra de abajo.\n"
            "3. Inyecta el '[Acorde]' inmediatamente a la izquierda del carácter o espacio exacto que tenga alineado verticalmente abajo.\n"
            "4. Si un acorde está muy a la derecha, calcula cuántos caracteres han pasado abajo e inyéctalo ahí. No lo dejes por fuera.\n\n"
            
            "EJEMPLO DE CORRECCIÓN DE DIVERGENCIAS (Estudio del último error de omisión):\n"
            "- Imagen Visual:\n"
            "     Bm              G\n"
            "  La única Razón de mi adoración\n"
            "  D            A\n"
            "  Eres tú mi Jesús\n\n"
            
            "- Análisis de Errores Anteriores:\n"
            "  * ERROR COMÚN: Omitir el 'G' por estar muy lejos a la derecha. (¡MAL!)\n"
            "  * ANÁLISIS CORRECTO:\n"
            "    - El 'Bm' está al inicio, justo encima de 'La'. -> '[Bm]La...'\n"
            "    - El 'G' está posicionado exactamente encima de la palabra 'mi'. -> '... de [G]mi adoración'\n"
            "    - El 'D' está al inicio de 'Eres'. -> '[D]Eres...'\n"
            "    - El 'A' está exactamente encima de 'Jesús'. -> '... tú mi [A]Jesús'\n\n"
            
            "- Resultado JSON esperado (Content):\n"
            "  '[Bm]La única Razón de [G]mi adoración\\n[D]Eres tú mi [A]Jesús\\n[Bm]El único motivo [G]para vivir\\n[D]Eres tú Mi [A]Señor'\n\n"
            
            "REGLAS DE SEGURIDAD E HIGIENE:\n"
            "- Respeta la ortografía y el uso de mayúsculas de la imagen (ej: si dice 'Razón' con R mayúscula y 'Mi' con M mayúscula, consérvalo tal cual).\n"
            "- Si no hay secciones explícitas (Intro, Coro), usa siempre 'Estrofa' por defecto.\n"
            "- Preserva estrictamente los saltos de línea (\\n) para mantener la simetría métrica de la canción."
        )

        message = HumanMessage(
            content=[
                {"type": "text", "text": system_instructions},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image_data}"},
                },
            ]
        )

        try:
            resultado_extractor = self.llm.invoke([message])
            return {
                "success": True,
                "song_name": resultado_extractor.song_name,
                "structure": {
                    "parts": [part.model_dump() for part in resultado_extractor.parts]
                }
            }
        except Exception as e:
            print(f"[ERROR MOTOR VISIÓN IA]: {str(e)}")
            return {
                "success": False,
                "error": "El motor de IA no logró estructurar armónicamente los acordes de la imagen."
            }