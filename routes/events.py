import threading
import uuid
from flask import Blueprint, request, jsonify
from datetime import datetime
from database import db
from services.ai_participant_service import ParticipantService
from services.ai_event_service import AssistantService
from services.email_service import EmailNotifierService
from models import Event, EventStaff, EventInventory, InventoryItem, ParticipantGroup, Participant

event_bp = Blueprint('event_bp', __name__)
assistant_service = AssistantService()
notifier = EmailNotifierService()
participant_service = ParticipantService()

# ==========================================
# POST - CHAT ASISTENTE VIRTUAL (creación/edición de eventos por dictado)
# ==========================================
@event_bp.route('/api/assistant/chat', methods=['POST'])
def assistant_chat():
    data = request.get_json() or {}
    user_input = data.get('message', '')

    if not user_input.strip():
        return jsonify({"error": "No enviaste ningún mensaje, varón."}), 400

    try:
        result = assistant_service.process_prompt(user_input)
        return jsonify({
            "message": result["message"],
            "extracted_data": result["extracted_data"]
        }), 200
    except Exception as e:
        print(f"Error crítico en el asistente de eventos: {str(e)}")
        return jsonify({"error": "Fallo interno al procesar el dictado por IA."}), 500


# ==========================================
# POST - CREAR EVENTO CON VALIDACIÓN DE STOCK
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
        qty_requested = req.get('quantity_used', req.get('quantity', 1.0))

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
# GET - LISTAR EVENTOS
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
            "staff": [{"email": s.email, "role": s.role} for s in e.staff]
        })
    return jsonify(payload), 200


# ==========================================
# GET - DETALLE DE UN EVENTO (INCLUYE GRUPOS Y PARTICIPANTES)
# ==========================================
@event_bp.route('/api/events/<int:event_id>', methods=['GET'])
def get_event_detail(event_id):
    e = Event.query.get(event_id)
    if not e:
        return jsonify({"error": "El evento solicitado no existe, varón."}), 404

    grupos_asociados = ParticipantGroup.query.filter_by(event_id=e.id).all()
    individuos_asociados = Participant.query.filter_by(event_id=e.id).all()

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
        } for inv in e.inventory_assignments],
        "groups": [{
            "id": g.id,
            "name": g.name,
            "logistics_to_bring": g.logistics_to_bring,
            "monetary_contribution": float(g.monetary_contribution),
            "contribution_status": g.contribution_status
        } for g in grupos_asociados],
        "participants": [{
            "id": p.id,
            "group_id": p.group_id,
            "name": p.name,
            "email": p.email,
            "logistics_to_bring": p.logistics_to_bring,
            "monetary_contribution": float(p.monetary_contribution),
            "contribution_status": p.contribution_status
        } for p in individuos_asociados]
    }
    return jsonify(payload), 200


# ==========================================
# PUT - ACTUALIZAR EVENTO EXISTENTE
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

    if 'staff' in data:
        EventStaff.query.filter_by(event_id=e.id).delete()
        for member in data['staff']:
            if 'email' in member and 'role' in member:
                e.staff.append(EventStaff(email=member['email'].strip(), role=member['role']))

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


# ==========================================
# INSERCIÓN EN LOTE (MANUAL O PARSEO DESDE CSV DEL FRONTEND)
# ==========================================
@event_bp.route('/api/events/<int:event_id>/participants/bulk', methods=['POST'])
def add_participants_bulk(event_id):
    e = Event.query.get(event_id)
    if not e: return jsonify({"error": "Evento no encontrado."}), 404

    data = request.get_json() or {}
    participants_list = data.get('participants', [])
    group_name = data.get('group_name', None)

    target_group_id = None
    if group_name:
        grupo = ParticipantGroup.query.filter_by(event_id=event_id, name=group_name).first()
        if not grupo:
            grupo = ParticipantGroup(
                event_id=event_id,
                name=group_name,
                logistics_to_bring=[],
                monetary_contribution=data.get('group_monetary_contribution', 0.00)
            )
            db.session.add(grupo)
            db.session.commit()
        target_group_id = grupo.id

    for p in participants_list:
        if not p.get('name'): continue

        raw_logistics = p.get('logistics', [])
        formatted_logistics = []
        for item in raw_logistics:
            formatted_logistics.append({
                "id": str(uuid.uuid4())[:8],
                "item": item.get('item', 'Insumo Indefinido'),
                "quantity": item.get('quantity', 1),
                "entregado": False
            })

        nuevo_p = Participant(
            event_id=event_id,
            group_id=target_group_id,
            name=p['name'],
            email=p.get('email'),
            logistics_to_bring=formatted_logistics,
            monetary_contribution=p.get('monetary_contribution', 0.00),
            contribution_status="Pendiente"
        )
        db.session.add(nuevo_p)

    try:
        db.session.commit()
        return jsonify({"message": f"Procesados {len(participants_list)} registros con éxito."}), 201
    except Exception as err:
        db.session.rollback()
        return jsonify({"error": str(err)}), 500


# ==========================================
# CHECK-IN DE CONTROL FÍSICO DE LOS APORTES DEL GRUPO
# ==========================================
@event_bp.route('/api/events/<int:event_id>/groups/<int:group_id>/check-item', methods=['PUT'])
def check_group_item(event_id, group_id):
    grupo = ParticipantGroup.query.filter_by(id=group_id, event_id=event_id).first()
    if not grupo:
        return jsonify({"error": "El grupo especificado no existe en este evento."}), 404

    data = request.get_json() or {}
    item_id = data.get('item_id')
    is_delivered = data.get('entregado', True)

    updated_list = list(grupo.logistics_to_bring)
    found = False

    for resource in updated_list:
        if resource['id'] == item_id:
            resource['entregado'] = is_delivered
            found = True
            break

    if not found:
        return jsonify({"error": "El ítem de logística no pertenece al grupo."}), 404

    grupo.logistics_to_bring = updated_list
    db.session.commit()

    return jsonify({
        "message": "Validación de insumo de entrada registrada.",
        "logistics": grupo.logistics_to_bring
    }), 200


# ==========================================
# ADICIÓN UNIFICADA POR LOTE (JSON DEL FORMULARIO FRONTEND)
# ==========================================
@event_bp.route('/api/events/<int:event_id>/participants/json-sync', methods=['POST'])
def sync_json_participants(event_id):
    e = Event.query.get(event_id)
    if not e:
        return jsonify({"error": "El evento solicitado no existe, varón."}), 404

    data = request.get_json() or {}
    blocks_list = data.get('participants', [])

    try:
        # ---------------------------------------------------------------------
        # REESCRITURA COMPLETA DE ASISTENCIAS: Evita la duplicación al guardar
        # ---------------------------------------------------------------------
        Participant.query.filter_by(event_id=event_id).delete()
        ParticipantGroup.query.filter_by(event_id=event_id).delete()
        db.session.commit()  # Asegura la limpieza física antes de re-inyectar

        for block in blocks_list:
            b_type = block.get('type')  # "individual" o "group"
            display_name = block.get('displayName', '').strip()
            email = block.get('contactEmail', '').strip()
            money = float(block.get('monetaryContribution', 0.00))
            raw_logistics = block.get('logisticsToBring', [])

            def format_logistics(log_list):
                formatted = []
                for item in log_list:
                    if item.get('item', '').strip():
                        formatted.append({
                            "id": str(uuid.uuid4())[:8],
                            "item": item.get('item'),
                            "quantity": int(item.get('quantity', 1)),
                            "entregado": False
                        })
                return formatted

            block_logistics = format_logistics(raw_logistics)

            if b_type == "group":
                nuevo_grupo = ParticipantGroup(
                    event_id=event_id,
                    name=display_name,
                    logistics_to_bring=block_logistics,
                    monetary_contribution=money,
                    contribution_status="Pagado" if money > 0 else "Pendiente"
                )
                db.session.add(nuevo_grupo)
                db.session.commit()

                sub_members = block.get('members', [])
                for m in sub_members:
                    if not m.get('name', '').strip():
                        continue
                    m_money = float(m.get('monetaryContribution', 0.00))
                    m_logistics = format_logistics(m.get('logisticsToBring', []))

                    nuevo_p = Participant(
                        event_id=event_id,
                        group_id=nuevo_grupo.id,
                        name=m.get('name').strip(),
                        email=m.get('email', '').strip() or None,
                        logistics_to_bring=m_logistics,
                        monetary_contribution=m_money,
                        contribution_status="Pagado" if m_money > 0 else "Pendiente"
                    )
                    db.session.add(nuevo_p)

            else:
                nuevo_p = Participant(
                    event_id=event_id,
                    group_id=None,
                    name=display_name,
                    email=email if email else None,
                    logistics_to_bring=block_logistics,
                    monetary_contribution=money,
                    contribution_status="Pagado" if money > 0 else "Pendiente"
                )
                db.session.add(nuevo_p)

        db.session.commit()
        return jsonify({"message": "¡Lote de asistencia consolidado y sincronizado de manera impecable!"}), 201

    except Exception as err:
        db.session.rollback()
        print(f"Error crítico en sync-json backend: {str(err)}")
        return jsonify({"error": f"Fallo en la consolidación del lote JSON: {str(err)}"}), 500


# ==========================================
# SINCRONIZACIÓN IA DE PARTICIPANTES (CHATBOT)
# Ojo: esto NO toca la base de datos. Solo transforma el JSON del
# formulario que todavía no fue guardado. La persistencia real sigue
# pasando únicamente por /participants/json-sync cuando el usuario
# confirma el formulario.
# ==========================================
@event_bp.route('/api/events/<int:event_id>/assistant/sync-participants', methods=['POST'])
def sync_ai_participants(event_id):
    data = request.get_json() or {}
    user_input = data.get('message', '')
    current_blocks = data.get('current_context', [])

    if not user_input.strip():
        return jsonify({"error": "No mandaste ninguna instrucción, varón."}), 400

    ai_result = participant_service.process_prompt(user_input, current_blocks=current_blocks)

    return jsonify({
        "message": ai_result["message"],
        "blocks": ai_result["blocks"]
    }), 200


# ==========================================
# MAPEO DE ENCABEZADOS DE CSV POR IA (solo fallback)
# Se usa únicamente cuando la detección automática por sinónimos en el
# frontend no logra reconocer las columnas del CSV. Recibe solo los NOMBRES
# de columna (nunca datos de personas) y devuelve a qué campo corresponde
# cada uno; el armado de los bloques sigue siendo determinístico en el front.
# ==========================================
@event_bp.route('/api/assistant/map-csv-headers', methods=['POST'])
def map_csv_headers():
    data = request.get_json() or {}
    headers = data.get('headers', [])

    if not headers or not isinstance(headers, list):
        return jsonify({"error": "No se recibieron encabezados válidos para mapear."}), 400

    mapping = participant_service.map_csv_headers(headers)

    return jsonify({"mapping": mapping}), 200