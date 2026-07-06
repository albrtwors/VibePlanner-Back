# routes/routes_event.py
import threading
from flask import Blueprint, request, jsonify
from datetime import datetime
from database import db
from models import Event, EventStaff, EventInventory, InventoryItem
from services.ai_event_service import AssistantService
from services.email_service import EmailNotifierService

event_bp = Blueprint('event_bp', __name__)
assistant_service = AssistantService()
notifier = EmailNotifierService()

# ==========================================
# POST - CHAT ASISTENTE VIRTUAL (EXTRACCIÓN + PRESUPUESTO)
# ==========================================
@event_bp.route('/api/assistant/chat', methods=['POST'])
def assistant_chat():
    data = request.get_json() or {}
    user_input = data.get('message', '')
    
    # CORREGIDO: Emparejado con 'current_guests_count' que viene del Front
    frontend_guests_count = data.get('current_guests_count') 
    
    if not user_input.strip():
        return jsonify({"error": "No enviaste ningún mensaje, varón."}), 400
        
    try:
        # Concatenamos la nota del sistema de forma clara
        if frontend_guests_count is not None:
            user_input += f" (Nota del sistema para el LLM: El usuario ya tiene un aforo real cargado en pantalla de {frontend_guests_count} personas. Usa este número si necesitas calcular cantidades de catering o insumos)."

        result = assistant_service.process_prompt(user_input)
        
        return jsonify({
            "message": result["message"],
            "extracted_data": result["extracted_data"]
        }), 200
        
    except Exception as e:
        print(f"Error crítico en el asistente de eventos: {str(e)}")
        return jsonify({"error": "Fallo interno al procesar el dictado por IA."}), 500

# ==========================================
# POST - CREAR EVENTO CON VALIDACIÓN DE STOCK REAL
# ==========================================
@event_bp.route('/api/events', methods=['POST'])
def create_event():
    data = request.get_json() or {}
    
    required = ['name', 'date', 'time']
    if not all(k in data for k in required):
        return jsonify({"error": "Faltan campos mandatorios (name, date, time), varón."}), 400
        
    try:
        fecha_obj = datetime.strptime(data['date'], '%Y-%m-%d').date()
        hora_obj = datetime.strptime(data['time'], '%H:%M').time()
    except ValueError:
        return jsonify({"error": "Formatos inválidos. Use YYYY-MM-DD para date y HH:MM para time."}), 400

    # 1. Instanciar la cabecera del Evento incluyendo datos históricos informativos
    nuevo_evento = Event(
        name=data['name'],
        date=fecha_obj,
        time=hora_obj,
        target_audience=data.get('target_audience', 'General'),
        guests_count=data.get('guests_count', 0),
        estimated_logistic_budget=data.get('estimated_logistic_budget', 0.00),
        itinerary=data.get('itinerary', [])
    )

    # 2. Procesar Staff
    staff_list = data.get('staff', [])
    emails_a_notificar = []
    for member in staff_list:
        if 'email' in member and 'role' in member:
            email_limpio = member['email'].strip()
            nuevo_evento.staff.append(EventStaff(email=email_limpio, role=member['role']))
            emails_a_notificar.append(email_limpio)

    # 3. Procesar e Inyectar inventario FISCO real disponible
    inventory_requests = data.get('inventory', [])
    items_hidratados_para_mail = [] 
    
    for req in inventory_requests:
        item_id = req.get('item_id')
        qty_requested = req.get('quantity', 1.0)
        
        if not item_id: # Ignoramos proyecciones puras de la IA que el usuario no consolidó
            continue
            
        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({"error": f"El artículo con ID {item_id} no existe en la DB."}), 404
            
        if qty_requested > item.total_stock:
            return jsonify({
                "error": f"Falta de stock para '{item.name}'. Pediste {qty_requested} {item.unit_of_measure} pero solo hay {item.total_stock} disponibles."
            }), 400
            
        nuevo_evento.inventory_assignments.append(EventInventory(item_id=item_id, quantity_used=qty_requested))

        items_hidratados_para_mail.append({
            "name": item.name,
            "quantity": qty_requested,
            "unit": item.unit_of_measure
        })

    try:
        db.session.add(nuevo_evento)
        db.session.commit() 

        # Correo en segundo plano
        lista_final_correos = list(set(emails_a_notificar))
        if lista_final_correos:
            payload_mail = {
                "name": nuevo_evento.name,
                "date": data['date'], 
                "time": data['time'],
                "target_audience": nuevo_evento.target_audience,
                "itinerary": data.get('itinerary', []),
                "inventory": items_hidratados_para_mail
            }
            threading.Thread(target=notifier.send_production_sheet, args=(lista_final_correos, payload_mail)).start()

        return jsonify({
            "message": "¡Evento agendado con éxito, varón!", 
            "event_id": nuevo_evento.id
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Fallo al guardar en base de datos: {str(e)}"}), 500


# ==========================================
# GET - LISTAR EVENTOS (INCLUYE INVITADOS)
# ==========================================
@event_bp.route('/api/events', methods=['GET'])
def get_events():
    query = Event.query
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date:
        query = query.filter(Event.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        query = query.filter(Event.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        
    events = query.order_by(Event.date.asc()).all()
    
    payload = []
    for e in events:
        payload.append({
            "id": e.id,
            "name": e.name,
            "date": e.date.isoformat(),
            "time": e.time.strftime('%H:%M:%S'),
            "target_audience": e.target_audience,
            "guests_count": e.guests_count,
            "estimated_logistic_budget": float(e.estimated_logistic_budget or 0),
            "itinerary": e.itinerary,
            "staff": [{"email": s.email, "role": s.role} for s in e.staff],
            "inventory": [{
                "item_id": inv.item_id,
                "name": inv.item.name,
                "quantity_used": float(inv.quantity_used),
                "unit": inv.item.unit_of_measure
            } for inv in e.inventory_assignments]
        })
    return jsonify(payload), 200