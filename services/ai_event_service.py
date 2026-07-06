import os
from typing import List, Literal, Optional
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
    name: str = Field(description="Nombre estricto de la actividad o canción. No mezclar con presupuestos.")
    time: str = Field(description="Horario de la actividad en formato HH:MM (24h).")
    type: Literal["song", "file", "generic"] = Field(description="Tipo de bloque.")

class ExtractedStaff(BaseModel):
    email: str = Field(description="Correo electrónico del operador técnico.")
    role: str = Field(description="Rol o cargo asignado.")

class ExtractedInventory(BaseModel):
    item_name: str = Field(description="Nombre del recurso de inventario o catering solicitado.")
    quantity: float = Field(default=1.0, description="Cantidad explícita dictada u obtenida basándose en los invitados.")
    price_reference: Optional[float] = Field(default=None, description="Precio unitario referencial si se menciona.")

class AssistantActionPayload(BaseModel):
    guests_count: Optional[int] = Field(default=None, description="Número de invitados NUEVO si el usuario lo dicta explícitamente. Dejar en None si el usuario habla de otra cosa.")
    itinerary_blocks: List[ExtractedItinerary] = Field(default=[])
    staff_members: List[ExtractedStaff] = Field(default=[])
    inventory_items: List[ExtractedInventory] = Field(default=[])


# ==========================================
# 2. SERVICIO CENTRAL DEL ASISTENTE
# ==========================================
class AssistantService:
    def __init__(self):
        self.llm = ChatGroq(
            temperature=0,
            model_name="openai/gpt-oss-120b", # Asegúrate de que use tu modelo asignado en Groq
            groq_api_key=os.getenv("GROQ_API_KEY")
        ).with_structured_output(AssistantActionPayload)
        
        # Modificado para evitar falsos positivos
        self.system_prompt = (
            "Eres el asistente logístico de VibePlanner.\n"
            "Tu único trabajo es extraer las entidades que el usuario te pide explícitamente en el mensaje.\n\n"
            "REGLAS CRÍTICAS DE EXTRACCIÓN:\n"
            "1. NO inventes bloques de itinerario, staff o inventario si el usuario no los menciona en su petición.\n"
            "2. Si el usuario pide agregar un bloque de itinerario o canción (ej: 'Pon Crimen a las 9pm'), extrae UNICAMENTE el bloque de itinerario. Deja la lista de inventario y 'guests_count' vacía.\n"
            "3. Solo extrae 'guests_count' si el usuario te dicta explícitamente cambiar o setear el aforo en su texto de forma directa (ej: 'Pon el aforo en 150 personas').\n"
            "4. Si el mensaje contiene una '(Nota del sistema: El usuario ya tiene un aforo real cargado en pantalla de X personas)', utiliza ese número de forma matemática ÚNICAMENTE si te piden calcular catering o comida proporcional a los asistentes (ej: 'calcula 2 refrescos por persona'). No alteres el 'guests_count' de salida a menos que te pidan cambiarlo."
        )
        
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "{user_input}")
        ])
        self.chain = self.prompt_template | self.llm

    def process_prompt(self, user_text: str) -> dict:
        if not user_text.strip():
            return {"extracted_data": {"itinerary": [], "staff": [], "inventory": [], "budget_projections": [], "guests_count": 0}, "message": "Texto vacío."}

        # Intentar extraer el valor numérico del aforo previo que inyectamos en la nota del sistema para mantener la persistencia si el LLM devuelve None
        backup_guests = 0
        if "El usuario ya tiene un aforo real cargado en pantalla de" in user_text:
            try:
                parts = user_text.split("El usuario ya tiene un aforo real cargado en pantalla de ")
                backup_guests = int(parts[1].split(" personas")[0].strip())
            except:
                pass

        extracted_data: AssistantActionPayload = self.chain.invoke({"user_input": user_text})
        
        # Mantenemos el aforo previo si el LLM no extrajo uno nuevo de manera explícita
        final_guests_count = extracted_data.guests_count if extracted_data.guests_count is not None else backup_guests

        response_payload = {
            "guests_count": final_guests_count,
            "itinerary": [],
            "staff": [],
            "inventory": [],
            "budget_projections": [],
            "total_estimated_logistic_cost": 0.00
        }
        
        total_budget = 0.0

        # --- Procesar Itinerario ---
        for block in extracted_data.itinerary_blocks:
            validated_name = block.name
            if block.type == "song":
                song_db = Song.query.filter(Song.name.ilike(f"%{block.name.strip()}%")).first()
                if song_db: validated_name = song_db.name
            response_payload["itinerary"].append({"time": block.time or "19:00", "type": block.type, "name": validated_name})

        # --- Procesar Staff ---
        for member in extracted_data.staff_members:
            response_payload["staff"].append({"email": member.email, "role": member.role})

        # --- Procesar Inventario y Costos ---
        for inv in extracted_data.inventory_items:
            item_db = InventoryItem.query.filter(InventoryItem.name.ilike(f"%{inv.item_name.strip()}%")).first()
            
            u_price = inv.price_reference or (float(item_db.price_per_unit) if item_db else 0.00)
            calculated_cost = inv.quantity * u_price
            total_budget += calculated_cost

            if item_db:
                response_payload["inventory"].append({
                    "item_id": item_db.id,
                    "name": item_db.name,
                    "quantity": inv.quantity,
                    "unit": item_db.unit_of_measure,
                    "category": item_db.category
                })
            
            response_payload["budget_projections"].append({
                "name": item_db.name if item_db else inv.item_name,
                "quantity": inv.quantity,
                "price_per_unit": u_price,
                "total_cost": calculated_cost,
                "in_stock": item_db is not None
            })

        response_payload["total_estimated_logistic_cost"] = total_budget

        # --- Feedback Dinámico Dinamizado por la Acción del usuario ---
        if extracted_data.itinerary_blocks and not extracted_data.inventory_items:
            feedback = f"¡Entendido, varón! He añadido los bloques solicitados al itinerario del evento."
        elif extracted_data.inventory_items:
            feedback = f"¡Analizado, varón! Con un aforo de {final_guests_count} personas, el costo proyectado para el inventario es de ${total_budget:.2f} USD."
        elif extracted_data.guests_count is not None:
            feedback = f"Aforo actualizado a {final_guests_count} personas con éxito."
        else:
            feedback = "Requerimiento procesado correctamente."

        return {
            "extracted_data": response_payload,
            "message": feedback
        }