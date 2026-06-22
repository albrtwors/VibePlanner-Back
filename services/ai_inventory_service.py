# services/inventory_ai_service.py
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# ESQUEMAS DE EXTRACCIÓN PARA INVENTARIO
# ==========================================

class AISingleItemSchema(BaseModel):
    name: str = Field(
        description="Nombre descriptivo y limpio del equipo o insumo. Ej: 'Consola Behringer X32', 'Cable de red CAT6 20m'."
    )
    category: str = Field(
        description="Debe ser estrictamente una de estas categorías: 'Audio', 'Iluminación', 'Video', 'Estructuras', 'Cables', 'Consumibles', 'Logística'. Si no encaja, usa 'Logística'."
    )
    total_stock: float = Field(
        default=1.0,
        description="Cantidad numérica de existencias iniciales especificadas por el usuario. Si no se indica, por defecto es 1.0."
    )
    unit_of_measure: str = Field(
        default="uds",
        description="Debe ser estrictamente una de estas unidades: 'uds', 'metros', 'packs', 'cajas', 'sets'."
    )
    is_consumable: bool = Field(
        default=False,
        description="True si es material gastable que se consume en los shows (ej: cinta gaffer, tirrajes, pilas). False para equipos fijos."
    )
    price_per_unit: float = Field(
        default=0.00,
        description="Precio o costo unitario del artículo en dólares USD si se menciona de forma explícita o implícita. Si no se indica, poner 0.00."
    )

class InventoryBulkSchema(BaseModel):
    items: List[AISingleItemSchema] = Field(
        description="Lista estructurada de todos los artículos detectados en la orden del usuario."
    )
    summary_message: Optional[str] = Field(
        default=None,
        description="Un saludo o breve resumen analítico de lo que procesó la IA."
    )


# ==========================================
# PROCESAMIENTO CON MODELO DE LENGUAJE
# ==========================================

def procesar_extraccion_inventario_ia(prompt_usuario: str) -> dict:
    llm = ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="openai/gpt-oss-120b", # Cambia al modelo que uses de Groq si es necesario (ej: llama3-70b-8192)
        temperature=0.0
    )
    
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres el extractor logístico automatizado de la bodega de VibePlanner.\n"
            "Tu misión es tomar las peticiones del usuario y mapearlas a un formato JSON estructurado rígido.\n\n"
            
            "REGLAS OBLIGATORIAS PARA CADA CAMPO DEL JSON:\n"
            "1. 'name': Nombre claro del artículo.\n"
            "2. 'category': Debe ser estrictamente uno de: 'Audio', 'Iluminación', 'Video', 'Estructuras', 'Cables', 'Consumibles', 'Logística'.\n"
            "3. 'total_stock': Cantidad numérica de elementos. Por defecto 1.0.\n"
            "4. 'unit_of_measure': Debe ser estrictamente una de estas strings: 'uds', 'metros', 'packs', 'cajas', 'sets'. Si el usuario dice 'unidades', mapealo a 'uds'.\n"
            "5. 'is_consumable': True si es material gastable (cintas, tirrajes, conectores rápidos, pilas), False para equipos duraderos.\n"
            "6. 'price_per_unit': Extrae el precio o costo unitario en dólares (USD). Si el usuario da un precio global para un lote, divídelo entre el stock para calcular el unitario. Si no se menciona precio, pon 0.00.\n\n"
            
            "Estructura del JSON de salida esperado:\n"
            "{{\n"
            "  \"items\": [\n"
            "    {{\n"
            "      \"name\": \"...\",\n"
            "      \"category\": \"...\",\n"
            "      \"total_stock\": 1.0,\n"
            "      \"unit_of_measure\": \"...\",\n"
            "      \"is_consumable\": false,\n"
            "      \"price_per_unit\": 0.00\n"
            "    }}\n"
            "  ],\n"
            "  \"summary_message\": \"Breve resumen en español de lo que procesaste\"\n"
            "}}\n\n"
            "Responde únicamente con el objeto JSON puro sin bloques de código markdown extra."
        )),
        ("human", "{input}")
    ])
    
    try:
        # CONSEJO: Si notas que json_mode sigue terco, quita el `method="json_mode"` 
        # para que LangChain use Function Calling nativo, que es mucho más preciso.
        structured_llm = llm.with_structured_output(InventoryBulkSchema, method="json_mode")
        analisis = (prompt_template | structured_llm).invoke({"input": prompt_usuario})
        
        return {
            "success": True,
            "summary": analisis.summary_message or "Procesamiento completado con éxito.",
            "items": [item.model_dump() for item in analisis.items]
        }
    except Exception as e:
        print(f"[ERROR] Extracción de inventario falló: {e}")
        return {
            "success": False,
            "error": "No se pudo formatear la lista de insumos de forma estructurada.",
            "items": []
        }