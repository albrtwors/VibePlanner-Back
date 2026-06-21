# services/assistant_service.py
import os
from typing import List, Literal
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from models import Song, InventoryItem
from database import db

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# 1. ESQUEMAS DE EXTRACCIÓN (PYDANTIC)
# ==========================================

class ExtractedItinerary(BaseModel):
    name: str = Field(description="Nombre de la actividad, canción o setlist mencionado por el usuario.")
    time: str = Field(description="Horario de la actividad en formato HH:MM (24h). Si no se especifica, inferir según el contexto o dejar vacío.")
    type: Literal["song", "file", "generic"] = Field(
        description="Tipo de bloque. 'song' si se habla de canción/tema/música, 'file' si es cancionero/setlist/archivo, 'generic' si es protocolo/actividad/brinca brinca/almuerzo."
    )

class ExtractedStaff(BaseModel):
    email: str = Field(description="Correo electrónico del operador técnico o encargado.")
    role: str = Field(description="Rol o cargo asignado (ej: Iluminador, Sonidista, Animador).")

class ExtractedInventory(BaseModel):
    item_name: str = Field(description="Nombre o descripción del recurso de inventario/bodega que solicita.")
    quantity: float = Field(default=1.0, description="Cantidad del recurso solicitado.")

class AssistantActionPayload(BaseModel):
    itinerary_blocks: List[ExtractedItinerary] = Field(default=[], description="Lista de actividades o temas musicales extraídos.")
    staff_members: List[ExtractedStaff] = Field(default=[], description="Lista de encargados técnicos extraídos.")
    inventory_items: List[ExtractedInventory] = Field(default=[], description="Lista de recursos de bodega solicitados.")

# ==========================================
# 2. SERVICIO CENTRAL DEL ASISTENTE (GROQ)
# ==========================================

class AssistantService:
    def __init__(self):
        self.llm = ChatGroq(
            temperature=0,
            model_name="openai/gpt-oss-120b", 
            groq_api_key=os.getenv("GROQ_API_KEY")
        ).with_structured_output(AssistantActionPayload)
        
        self.system_prompt = (
            "Eres el asistente de producción virtual de VibePlanner. Tu trabajo es analizar la petición verbal "
            "del usuario y extraer de forma estructurada los bloques de itinerario, el staff de logística y los "
            "recursos de inventario.\n\n"
            "Reglas críticas:\n"
            "1. Clasifica el tipo de itinerario rigurosamente: si es una canción, usa 'song'; si es un setlist o cancionero, usa 'file'; si es otra cosa, 'generic'.\n"
            "2. Si el usuario no menciona un horario para una actividad, intenta estimarlo coherentemente o déjalo vacío.\n"
            "3. Extrae correos electrónicos y roles de forma limpia."
        )
        
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "{user_input}")
        ])
        
        self.chain = self.prompt_template | self.llm

    def process_prompt(self, user_text: str) -> dict:
        """
        Procesa el texto del usuario, extrae las entidades usando Groq y realiza las validaciones cruzadas con la DB.
        Retorna la data extraída junto con un mensaje de feedback dinámico por si hay faltantes.
        """
        if not user_text.strip():
            return {"itinerary": [], "staff": [], "inventory": [], "feedback_message": "No detecté ninguna instrucción, varón."}

        extracted_data: AssistantActionPayload = self.chain.invoke({"user_input": user_text})
        
        response_payload = {
            "itinerary": [],
            "staff": [],
            "inventory": []
        }
        
        # Lista local para capturar los ítems de inventario que no se consiguieron
        missing_inventory = []

        # --- REGLA 1: Procesar y Validar Itinerario ---
        for block in extracted_data.itinerary_blocks:
            validated_name = block.name
            
            if block.type == "song":
                song_db = Song.query.filter(Song.name.ilike(f"%{block.name.strip()}%")).first()
                if song_db:
                    validated_name = song_db.name
                    
            response_payload["itinerary"].append({
                "time": block.time or "19:00",
                "type": block.type,
                "name": validated_name
            })

        # --- REGLA 2: Procesar Staff ---
        for member in extracted_data.staff_members:
            response_payload["staff"].append({
                "email": member.email,
                "role": member.role
            })

        # --- REGLA 3: Procesar e Inyectar Inventario ---
        for inv in extracted_data.inventory_items:
            item_db = InventoryItem.query.filter(InventoryItem.name.ilike(f"%{inv.item_name.strip()}%")).first()
            if item_db:
                response_payload["inventory"].append({
                    "item_id": item_db.id,
                    "name": item_db.name,
                    "quantity_used": inv.quantity,
                    "unit": item_db.unit_of_measure,
                    "category": item_db.category
                })
            else:
                # Si no se encuentra, lo registramos para armar la notificación
                missing_inventory.append(inv.item_name)
                print(f"Advertencia del bot: El recurso '{inv.item_name}' no existe en la bodega.")

        # --- CONSTRUCCIÓN DEL MENSAJE DE RESPUESTA DINÁMICO ---
        if missing_inventory:
            items_str = ", ".join([f"'{item}'" for item in missing_inventory])
            feedback = f"Entendido, varón. Procesé los requerimientos válidos, pero ⚠️ NO encontré en bodega los siguientes artículos: {items_str}. Verifica que estén cargados en el inventario global."
        else:
            feedback = "¡Listo, varón! Requerimientos interpretados y volcados al formulario correctamente."

        # Adjuntamos el mensaje dinámico al diccionario de retorno
        return {
            "extracted_data": response_payload,
            "message": feedback
        }