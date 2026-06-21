# routes/routes_chatbot.py
from flask import Blueprint, request, jsonify
from services.chatbot_service import VibePlannerChatbot
from models import Song, InventoryItem  # Importa tus modelos reales de VibePlanner

chatbot_bp = Blueprint('chatbot_bp', __name__)
vibe_bot = VibePlannerChatbot()

@chatbot_bp.route('/api/chatbot', methods=['POST'])
def chat_with_assistant():
    data = request.get_json() or {}
    user_message = data.get('message')

    if not user_message or not user_message.strip():
        return jsonify({"error": "El mensaje no puede estar vacío."}), 400

    history = data.get('history', [])

    try:
        # Traer data en tiempo real de la DB para dársela al bot
        canciones = Song.query.all()
        inventario = InventoryItem.query.all()

        # Ejecutar consulta en Groq
        bot_response = vibe_bot.ask(
            message=user_message, 
            canciones_db=canciones, 
            inventario_db=inventario, 
            history=history
        )

        return jsonify({"response": bot_response}), 200
    except Exception as e:
        return jsonify({"error": f"Fallo al sincronizar contexto: {str(e)}"}), 500