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
    
    if not user_input.strip():
        return jsonify({"error": "No enviaste ningún mensaje, varón."}), 400
        
    try:
        # Pasamos el user_input tal y como el usuario lo escribió para forzar literalidad.
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

    nuevo_evento = Event(
        name=data['name'],
        date=fecha_obj,
        time=hora_obj,
        target_audience=data.get('target_audience', 'General'),
        guests_count=data.get('guests_count', 0),
        estimated_logistic_budget=data.get('estimated_logistic_budget', 0.00),
        itinerary=data.get('itinerary', [])
    )

    staff_list = data.get('staff', [])
    emails_a_notificar = []
    for member in staff_list:
        if 'email' in member and 'role' in member:
            email_limpio = member['email'].strip()
            nuevo_evento.staff.append(EventStaff(email=email_limpio, role=member['role']))
            emails_a_notificar.append(email_limpio)

    inventory_requests = data.get('inventory', [])
    items_hidratados_para_mail = [] 
    
    for req in inventory_requests:
        item_id = req.get('item_id')
        qty_requested = req.get('quantity_used', req.get('quantity', 1.0)) # Soporta ambas keys por si acaso
        
        if not item_id:
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
                "unit": inv.item.unit_of_measure,
                "category": inv.item.category
            } for inv in e.inventory_assignments]
        })
    return jsonify(payload), 200


# ==========================================
# GET - DETALLE DE UN EVENTO ESPECÍFICO (EL QUE FALTABA)
# ==========================================
@event_bp.route('/api/events/<int:event_id>', methods=['GET'])
def get_event_detail(event_id):
    e = Event.query.get(event_id)
    if not e:
        return jsonify({"error": "El evento solicitado no existe, varón."}), 404
        
    payload = {
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
            "unit": inv.item.unit_of_measure,
            "category": inv.item.category
        } for inv in e.inventory_assignments]
    }
    return jsonify(payload), 200


# ==========================================
# PUT - ACTUALIZAR EVENTO EXISITENTE
# ==========================================
@event_bp.route('/api/events/<int:event_id>', methods=['PUT'])
def update_event(event_id):
    e = Event.query.get(event_id)
    if not e:
        return jsonify({"error": "El evento a editar no existe."}), 404
        
    data = request.get_json() or {}
    
    try:
        if 'date' in data:
            e.date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        if 'time' in data:
            e.time = datetime.strptime(data['time'], '%H:%M').time()
    except ValueError:
        return jsonify({"error": "Formatos de tiempo/fecha erróneos."}), 400

    if 'name' in data: e.name = data['name']
    if 'target_audience' in data: e.target_audience = data['target_audience']
    if 'guests_count' in data: e.guests_count = data['guests_count']
    if 'estimated_logistic_budget' in data: e.estimated_logistic_budget = data['estimated_logistic_budget']
    if 'itinerary' in data: e.itinerary = data['itinerary']

    # Re-mapear personal
    if 'staff' in data:
        EventStaff.query.filter_by(event_id=e.id).delete()
        for member in data['staff']:
            if 'email' in member and 'role' in member:
                e.staff.append(EventStaff(email=member['email'].strip(), role=member['role']))

    # Re-mapear inventario físico con validación
    if 'inventory' in data:
        EventInventory.query.filter_by(event_id=e.id).delete()
        for req in data['inventory']:
            item_id = req.get('item_id')
            qty_requested = req.get('quantity_used', req.get('quantity', 1.0))
            if item_id:
                item = InventoryItem.query.get(item_id)
                if item and qty_requested <= item.total_stock:
                    e.inventory_assignments.append(EventInventory(item_id=item_id, quantity_used=qty_requested))

    try:
        db.session.commit()
        return jsonify({"message": "Evento actualizado correctamente, varón."}), 200
    except Exception as err:
        db.session.rollback()
        return jsonify({"error": f"Error al actualizar la base de datos: {str(err)}"}), 500