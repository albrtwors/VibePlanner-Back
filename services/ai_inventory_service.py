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
        model="llama-3.3-70b-specdec",
        temperature=0.0
    )
    
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", (
            "Eres el extractor logístico automatizado de la bodega de VibePlanner.\n"
            "Tu misión es tomar las peticiones del usuario (que pueden venir en lenguaje coloquial, listas desordenadas o compras) "
            "y mapearlas a estructuras de datos rígidas para el inventario.\n\n"
            
            "Reglas de Oro de Clasificación:\n"
            "1. CATEGORÍAS permitidas: 'Audio', 'Iluminación', 'Video', 'Estructuras', 'Cables', 'Consumibles', 'Logística'.\n"
            "   - Cables XLR, PL, de corriente van en 'Cables'.\n"
            "   - Gaffer tape, conectores rápidos, tirrajes van en 'Consumibles' (e 'is_consumable' debe ser True).\n"
            "2. UNIDADES permitidas: 'uds', 'metros', 'packs', 'cajas', 'sets'. Mapea según corresponda.\n"
            "3. CANTIDADES: Extrae el número exacto. Si dice '3 cajas de...', total_stock = 3 y unit_of_measure = 'cajas'.\n\n"
            "Responde estrictamente en formato JSON que cumpla con la raíz 'items'."
        )),
        ("human", "{input}")
    ])
    
    try:
        structured_llm = llm.with_structured_output(InventoryBulkSchema, method="json_mode")
        analisis = (prompt_template | structured_llm).invoke({"input": prompt_usuario})
        
        # Convertimos el esquema Pydantic directamente a un diccionario de Python regular
        return {
            "success": True,
            "summary": analisis.summary_message,
            "items": [item.model_dump() for item in analisis.items]
        }
    except Exception as e:
        print(f"[ERROR] Extracción de inventario falló: {e}")
        return {
            "success": False,
            "error": "No se pudo formatear la lista de insumos de forma estructurada.",
            "items": []
        }