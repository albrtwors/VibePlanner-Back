# services/chatbot_service.py
import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

VIBEPLANNER_DOCUMENTATION = """
---
MANUAL DE OPERACIONES Y RUTAS (NEXT.JS):

## 1. Gestión de Canciones (Las canciones solo contienen la letra, el genero el autor y el nombre)
- Ver catálogo / Repertorio general todas las canciones: [/songs](/songs)
- Crear canción: [/songs/create](/songs/create)
- Editar canción: [/songs/[id]/edit](/songs/songs) (Buscar la canción y hacer clic desde [/songs](/songs))
- Eliminar canción: Icono de eliminar (✕) directo en la tabla de [/songs](/songs).

## 2. Cancioneros Inteligentes (Compilados de listas de canciones, NO admiten acordes)
- Ver cancioneros guardados: [/file](/file)
- Crear nuevo cancionero: [/file](/file)

## 3. Planificación de Eventos y Staff
- Ver agenda de eventos: [/events](/events)
- Crear evento desde cero: [/events/create](/events/create)

## 4. Control de Almacén e Inventario (Con candado de Stock atómico)
- Ver inventario general: [/inventory](/inventory)
- Registrar nuevo artículo: [/inventory/create](/inventory/create)
- Editar insumo: [/inventory/edit/[id]](/inventory)
---
"""

class VibePlannerChatbot:
    def __init__(self):
        # Corregido a un ID de modelo válido y soportado oficialmente en el catálogo de Groq
        self.llm = ChatGroq(
            model="openai/gpt-oss-120b",
            temperature=0.2, 
            groq_api_key=os.getenv("GROQ_API_KEY")
        )
        
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", VIBEPLANNER_DOCUMENTATION),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{user_message}")
        ])
        
        self.chain = self.prompt | self.llm | StrOutputParser()

    def ask(self, message: str, canciones_db: list, inventario_db: list, history: list = None) -> str:
        """
        Procesa la consulta inyectando dinámicamente el estado actual de los modelos de la DB.
        """
        try:
            chat_history = history if history is not None else []
            
            # Formateamos strings simples para que el modelo los interprete con facilidad
            str_canciones = ", ".join([c.name for c in canciones_db]) if canciones_db else "Ninguna canción registrada."
            str_inventario = ", ".join([f"{i.name} ({i.total_stock} {i.unit_of_measure})" for i in inventario_db]) if inventario_db else "No hay artículos en bodega."

            response = self.chain.invoke({
                "user_message": message,
                "chat_history": chat_history,
                "lista_canciones": str_canciones,
                "lista_inventario": str_inventario
            })
            return response
        except Exception as e:
            print(f"Error en LangChain Groq Service: {str(e)}")
            return "Ocurrió un error interno al procesar la solicitud con el servicio de IA."