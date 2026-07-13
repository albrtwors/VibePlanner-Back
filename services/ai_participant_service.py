import os
from typing import List, Literal

from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate


# ==========================================
# 1. ESQUEMAS -> deben calzar EXACTO con el shape de "ParticipantBlock" del frontend
# ==========================================
class CollabItemSchema(BaseModel):
    item: str = Field(default="", description="Descripción del insumo/recurso.")
    quantity: int = Field(default=1, description="Cantidad del ítem.")


class GroupMemberSchema(BaseModel):
    name: str = Field(description="Nombre completo del integrante.")
    email: str = Field(default="", description="Email opcional del integrante.")
    monetaryContribution: float = Field(default=0.0, description="Aporte monetario propio del integrante.")
    logisticsToBring: List[CollabItemSchema] = Field(default=[], description="Insumos propios del integrante.")


class ParticipantBlockSchema(BaseModel):
    type: Literal['individual', 'group'] = Field(description="'individual' o 'group'.")
    displayName: str = Field(description="Nombre de la persona (individual) o del colectivo/mesa (group).")
    contactEmail: str = Field(default="", description="Email de contacto (solo tiene sentido para 'individual').")
    monetaryContribution: float = Field(default=0.0, description="Aporte monetario del bloque completo.")
    logisticsToBring: List[CollabItemSchema] = Field(default=[], description="Insumos generales del bloque.")
    members: List[GroupMemberSchema] = Field(default=[], description="Integrantes, solo si type='group'.")


class AssistantResponseSchema(BaseModel):
    message: str = Field(description="Respuesta amigable en español resumiendo el cambio, jerga 'varón'.")
    blocks: List[ParticipantBlockSchema] = Field(
        description="La lista COMPLETA y actualizada de bloques, con la instrucción del usuario ya aplicada."
    )


# ==========================================
# ESQUEMA PARA EL MAPEO DE ENCABEZADOS DE CSV
# ==========================================
# Solo se usa cuando la detección automática por sinónimos (en el frontend) no
# logra reconocer las columnas. Ojo: acá SOLO le mandamos a la IA los NOMBRES
# de las columnas del CSV, nunca los datos de las personas (nombres, emails,
# montos reales) — así nos protegemos de exponer datos personales a la IA
# quedándonos solo con lo mínimo necesario para resolver el mapeo.
class CsvHeaderMappingSchema(BaseModel):
    nombre: str = Field(default="", description="Header EXACTO (tal como viene en la lista) que corresponde al nombre del participante. Vacío si ninguno corresponde.")
    email: str = Field(default="", description="Header EXACTO que corresponde al email. Vacío si ninguno.")
    grupo: str = Field(default="", description="Header EXACTO que indica a qué grupo/mesa/colectivo pertenece. Vacío si ninguno.")
    aporte: str = Field(default="", description="Header EXACTO del aporte monetario INDIVIDUAL. Vacío si ninguno.")
    aporte_grupal: str = Field(default="", description="Header EXACTO del fondo/aporte del GRUPO completo (no del individuo). Vacío si ninguno.")
    insumos: str = Field(default="", description="Header EXACTO de insumos/logística que trae la persona. Vacío si ninguno.")


# ==========================================
# 2. SERVICIO DE IA (SOLO TRANSFORMA JSON, CERO DB)
# ==========================================
class ParticipantService:
    def __init__(self):
        self.llm = ChatGroq(
            api_key=os.getenv("GROQ_API_KEY"),
            model="openai/gpt-oss-120b",
            temperature=0.1,
        )
        self.structured_llm = self.llm.with_structured_output(AssistantResponseSchema, method="json_mode")
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", (
                "Eres el motor de IA de VibePlanner. Administras el FORMULARIO (todavía no guardado) de "
                "participantes de un evento. Te paso el estado ACTUAL del formulario como una lista de 'blocks' "
                "en JSON, y una instrucción en lenguaje natural. Tu trabajo es devolver la lista COMPLETA de "
                "blocks ya actualizada, aplicando lo que pidió el usuario. NO estás tocando ninguna base de datos, "
                "solo estás editando este JSON.\n\n"
                "ESTRUCTURA DE CADA BLOCK:\n"
                "- type: 'individual' (una sola persona) o 'group' (colectivo/mesa con integrantes en 'members').\n"
                "- displayName: nombre de la persona o del grupo.\n"
                "- contactEmail, monetaryContribution, logisticsToBring: datos del bloque.\n"
                "- members: solo si type='group', cada uno con name, email, monetaryContribution, logisticsToBring.\n\n"
                "REGLAS PARA APLICAR INSTRUCCIONES:\n"
                "1. Agregar una persona individual -> agrega un nuevo block type='individual'. Si en el mismo "
                "mensaje te dan su aporte monetario y/o insumos, cárgalos directo en ese nuevo block (o en el "
                "member, si además la meten a un grupo).\n"
                "2. Agregar una persona a un grupo existente -> busca el block type='group' cuyo displayName "
                "coincida (sin importar mayúsculas/tildes) y agrégala a su lista 'members'. Si el grupo no existe, "
                "créalo con ese integrante adentro.\n"
                "3. Quitar una persona -> búscala tanto entre los blocks individuales como dentro de 'members' de "
                "todos los grupos, y elimínala de donde esté.\n"
                "4. Quitar a alguien de su grupo (pero sin borrarla del evento) -> sácala de 'members' y agrégala "
                "como un nuevo block type='individual' con sus mismos datos (conserva su monetaryContribution y "
                "logisticsToBring tal como los tenía).\n"
                "5. Quitar un grupo completo -> elimina ese block. Sus integrantes se convierten cada uno en un "
                "block individual (NO se pierden, y conservan su aporte e insumos), salvo que el usuario pida "
                "explícitamente borrarlos también.\n"
                "6. APORTE MONETARIO (monetaryContribution) -> si el usuario menciona un monto para una persona "
                "o un grupo ('Juan aporta 50000', 'el fondo de Mesa 3 es de 200 mil', 'súbele 20 lucas a María'), "
                "actualiza el campo monetaryContribution del block o member correspondiente al valor final que "
                "corresponda (si te dan un monto absoluto, reemplázalo; si te piden sumar/restar una cifra, calcula "
                "el nuevo total a partir del valor actual que ves en el contexto).\n"
                "7. INSUMOS/ITEMS (logisticsToBring) -> si el usuario menciona que alguien o un grupo trae/lleva/"
                "aporta un insumo con o sin cantidad ('Juan trae 2 parlantes', 'agrégale hielo a Mesa 3', "
                "'la familia Pérez lleva 3 sillas y una mesa'), agrégalo a la lista logisticsToBring del block o "
                "member correspondiente. Si ya existe un ítem con el mismo nombre (comparando sin mayúsculas/"
                "tildes), actualiza su 'quantity' en vez de duplicarlo. Si el usuario no da cantidad, usa 1. Si el "
                "insumo menciona una unidad o medida (ej. '5kg de hielo', '2 cajas de agua'), inclúyela como parte "
                "del texto de 'item' (ej. item='hielo (5kg)') ya que el formulario no tiene un campo de unidad "
                "separado.\n"
                "8. Cualquier block o integrante que NO esté relacionado con la instrucción se devuelve EXACTAMENTE "
                "igual a como vino (no inventes ni cambies datos que no te pidieron cambiar).\n"
                "9. Si la instrucción menciona a alguien que no existe en el contexto actual y la acción es "
                "'quitar', no inventes cambios: deja los blocks igual y explica en 'message' que no lo encontraste.\n"
                "10. Responde siempre en 'message' con jerga amigable de 'varón', resumiendo qué hiciste (incluyendo "
                "montos e insumos si fue lo que cambió).\n"
                "11. CONSULTAS (sin modificar nada) -> si la instrucción es una pregunta o pedido de información "
                "('¿quiénes están en la Mesa 3?', 'cuánto lleva aportado el grupo Familia Pérez', 'qué insumos "
                "trae Juan', 'cuántos individuales tengo sin grupo') NO modifiques ningún block: devuelve la "
                "lista de blocks EXACTAMENTE igual a como vino, y responde la pregunta con los datos concretos "
                "(nombres, montos, insumos) en el campo 'message'.\n\n"
                "ESTADO ACTUAL DEL FORMULARIO (JSON):\n{current_blocks}"
            )),
            ("human", "Instrucción del usuario: {input}")
        ])

    def process_prompt(self, user_input: str, current_blocks: list) -> dict:
        """Recibe el estado actual del formulario + la instrucción, y devuelve el JSON actualizado."""
        try:
            chain = self.prompt_template | self.structured_llm
            res: AssistantResponseSchema = chain.invoke({
                "input": user_input,
                "current_blocks": current_blocks or []
            })
            return {
                "message": res.message,
                "blocks": [b.model_dump() for b in res.blocks]
            }
        except Exception as e:
            print(f"[ERROR IA - process_prompt]: {e}")
            # Ante un error, no tocamos nada: devolvemos el formulario tal cual estaba.
            return {
                "message": "Tuve un mal viaje con esa instrucción, varón. ¿Me la repites?",
                "blocks": current_blocks or []
            }

    def map_csv_headers(self, headers: list) -> dict:
        """
        Recibe SOLO los nombres de columna de un CSV (nunca los datos de las filas) y
        devuelve a qué campo canónico corresponde cada uno. El armado real de los
        bloques a partir de las filas sigue haciéndose de forma determinística en
        el frontend, usando este mapeo.
        """
        default_vacio = {
            "nombre": "", "email": "", "grupo": "",
            "aporte": "", "aporte_grupal": "", "insumos": ""
        }
        if not headers:
            return default_vacio

        try:
            structured_mapeo = self.llm.with_structured_output(CsvHeaderMappingSchema, method="json_mode")
            prompt_mapeo = ChatPromptTemplate.from_messages([
                ("system", (
                    "Vas a recibir una lista de encabezados (headers) de un CSV con participantes de un "
                    "evento. Tu único trabajo es decir qué header corresponde a cada uno de estos campos:\n"
                    "- nombre: nombre de la persona\n"
                    "- email: correo electrónico\n"
                    "- grupo: a qué mesa/colectivo/familia pertenece\n"
                    "- aporte: aporte monetario INDIVIDUAL de esa persona\n"
                    "- aporte_grupal: fondo del GRUPO completo (no del individuo)\n"
                    "- insumos: insumos, ítems o logística que trae la persona\n\n"
                    "Devuelve el TEXTO EXACTO del header tal como aparece en la lista, sin modificarlo ni "
                    "corregir ortografía. Si ningún header corresponde a un campo, deja ese campo como "
                    "string vacío. No inventes headers que no estén en la lista provista."
                )),
                ("human", "Headers disponibles: {headers}")
            ])
            resultado: CsvHeaderMappingSchema = (prompt_mapeo | structured_mapeo).invoke({"headers": headers})
            return resultado.model_dump()
        except Exception as e:
            print(f"[ERROR IA - map_csv_headers]: {e}")
            return default_vacio