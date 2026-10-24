import os
import secrets
import uuid
from datetime import datetime

import requests
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

from database import db
from models import Participant, ParticipantPayment, Event
from utils.decorators import permissions_required

payments_bp = Blueprint('payments', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf', 'heic', 'heif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@payments_bp.route('/api/events/<int:event_id>/payments/send', methods=['POST'])
@permissions_required('events.payments')
def send_payment_requests(event_id):
    event = Event.query.get(event_id)
    if not event:
        return jsonify({'error': 'Evento no encontrado'}), 404

    data = request.get_json(silent=True) or {}
    participant_ids = data.get('participant_ids') or []

    query = Participant.query.filter_by(event_id=event_id)
    if participant_ids:
        query = query.filter(Participant.id.in_(participant_ids))
    participants = query.all()

    sent = 0
    for p in participants:
        if not p.email:
            continue
        existing = ParticipantPayment.query.filter_by(participant_id=p.id, event_id=event_id, status='pending').first()
        if existing:
            continue
        token = secrets.token_urlsafe(32).replace('-', '').replace('_', '')[:48]
        pay = ParticipantPayment(
            participant_id=p.id,
            event_id=event_id,
            token=token,
            amount=p.monetary_contribution or 0,
            status='pending',
            requested_at=datetime.utcnow()
        )
        db.session.add(pay)
        sent += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

    base_url = os.getenv('APP_BASE_URL', 'http://localhost:3000')
    # Enviar emails si hay servicio configurado
    try:
        from services.email_service import EmailNotifierService
        mailer = EmailNotifierService()
        if mailer.is_configured:
            for p in participants:
                pay = ParticipantPayment.query.filter_by(participant_id=p.id, event_id=event_id).order_by(ParticipantPayment.id.desc()).first()
                if not pay or not p.email:
                    continue
                link = f"{base_url}/pay/{pay.token}"
                html = f"""
                <html><body style="font-family:sans-serif;background:#0f172a;color:#e2e8f0;padding:20px">
                <div style="max-width:560px;margin:auto;background:#1e293b;border:1px solid #334155;border-radius:16px;padding:28px">
                <h1 style="color:#fff">VibePlanner - Pago de aporte</h1>
                <p>Hola {p.name},</p>
                <p>Para el evento <strong>{event.name}</strong> ({event.date}) se te solicita el aporte móvil por <strong>${pay.amount}</strong>.</p>
                <p>Subí la captura de pantalla de tu pago desde el siguiente enlace:</p>
                <p><a href="{link}" style="background:#6366f1;color:#fff;padding:10px 16px;border-radius:10px;text-decoration:none">Enviar comprobante de pago</a></p>
                <p style="font-size:12px;color:#94a3b8">Si ya pagaste, podés subir el comprobante ahora para que sea revisado.</p>
                </div></body></html>
                """
                try:
                    import yagmail
                    yag = yagmail.SMTP(mailer.user, mailer.password)
                    yag.send(to=p.email, subject=f"VibePlanner | Comprobante de pago - {event.name}", contents=html)
                except Exception:
                    pass
    except Exception:
        pass

    return jsonify({'message': f'Solicitudes generadas', 'sent': sent}), 200


@payments_bp.route('/api/events/<int:event_id>/payments', methods=['GET'])
@permissions_required('events.payments')
def list_payments(event_id):
    payments = ParticipantPayment.query.filter_by(event_id=event_id).order_by(ParticipantPayment.id.desc()).all()
    result = []
    for pay in payments:
        p = pay.participant
        result.append({
            'id': pay.id,
            'token': pay.token,
            'participant_id': p.id if p else None,
            'participant_name': p.name if p else None,
            'participant_email': p.email if p else None,
            'amount': float(pay.amount or 0),
            'status': pay.status,
            'screenshot_url': pay.screenshot_url,
            'requested_at': pay.requested_at.isoformat() if pay.requested_at else None,
            'uploaded_at': pay.uploaded_at.isoformat() if pay.uploaded_at else None,
            'reviewed_at': pay.reviewed_at.isoformat() if pay.reviewed_at else None,
            'notes': pay.notes
        })
    return jsonify(result), 200


@payments_bp.route('/api/events/<int:event_id>/payments/<int:payment_id>/review', methods=['POST'])
@permissions_required('events.payments')
def review_payment(event_id, payment_id):
    pay = ParticipantPayment.query.filter_by(id=payment_id, event_id=event_id).first()
    if not pay:
        return jsonify({'error': 'Pago no encontrado'}), 404
    data = request.get_json(silent=True) or {}
    action = data.get('action')  # approve | reject
    notes = data.get('notes')
    if action == 'approve':
        pay.status = 'approved'
        if pay.participant:
            pay.participant.contribution_status = 'Pagado'
    elif action == 'reject':
        pay.status = 'rejected'
    else:
        return jsonify({'error': 'Acción inválida'}), 400
    pay.reviewed_at = datetime.utcnow()
    pay.notes = notes
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    return jsonify({'message': 'Revisado'}), 200


@payments_bp.route('/api/public/payments/<token>', methods=['GET'])
def get_public_payment(token):
    pay = ParticipantPayment.query.filter_by(token=token).first()
    if not pay:
        return jsonify({'error': 'Enlace inválido o expirado'}), 404
    p = pay.participant
    ev = pay.event
    return jsonify({
        'token': pay.token,
        'status': pay.status,
        'amount': float(pay.amount or 0),
        'participant_name': p.name if p else None,
        'participant_email': p.email if p else None,
        'event': {'id': ev.id if ev else None, 'name': ev.name if ev else None, 'date': ev.date.isoformat() if ev and ev.date else None, 'time': ev.time.strftime('%H:%M') if ev and ev.time else None} if ev else None,
        'screenshot_url': pay.screenshot_url
    }), 200


@payments_bp.route('/api/public/payments/<token>', methods=['POST'])
def upload_public_payment(token):
    pay = ParticipantPayment.query.filter_by(token=token).first()
    if not pay:
        return jsonify({'error': 'Enlace inválido o expirado'}), 404
    if 'file' not in request.files:
        return jsonify({'error': 'Falta el archivo'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'Archivo vacío'}), 400
    if not allowed_file(f.filename):
        return jsonify({'error': 'Formato no permitido'}), 400

    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY')
    bucket = os.getenv('SUPABASE_BUCKET', 'payment-proofs')

    filename = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    file_bytes = f.read()
    content_type = f.content_type or 'application/octet-stream'

    public_url = None
    if supabase_url and supabase_key:
        try:
            upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{filename}"
            headers = {'Authorization': f'Bearer {supabase_key}', 'Content-Type': content_type}
            resp = requests.post(upload_url, headers=headers, data=file_bytes)
            if resp.ok:
                public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket}/{filename}"
        except Exception:
            public_url = None

    if not public_url:
        # Fallback local
        upload_dir = os.path.join(current_app.root_path, 'uploads', 'payments')
        os.makedirs(upload_dir, exist_ok=True)
        path = os.path.join(upload_dir, filename)
        with open(path, 'wb') as out:
            out.write(file_bytes)
        pay.screenshot_path = path
        pay.screenshot_url = f"/uploads/payments/{filename}"
    else:
        pay.screenshot_url = public_url
        pay.screenshot_path = filename

    pay.status = 'submitted'
    pay.uploaded_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    return jsonify({'message': 'Comprobante enviado correctamente', 'status': pay.status}), 200
from .payments import payments_bp
