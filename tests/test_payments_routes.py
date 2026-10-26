"""
Pruebas de integracion de las rutas de pagos de participantes.

    .venv/bin/python -m unittest discover -s tests -v

Se levanta la app con SQLite en memoria, se siembran los permisos de eventos
y se firman tokens JWT reales para cada rol. Se prueban los endpoints de
solicitud de pago, consulta publica del comprobante y revision de pagos.
"""

import sys
import unittest
from datetime import date, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

import models  # noqa: F401
from database import db
from models import Event, Participant, Permission, Role, RolePermission, User
from routes.payments import payments_bp
from utils.permissions import PERMISSION_CATALOG

EVENT_PERMISSIONS = [p for p in PERMISSION_CATALOG if p[0].startswith('events.')]


class PaymentsRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret',
            JWT_TOKEN_LOCATION=['headers'],
        )
        db.init_app(self.app)
        JWTManager(self.app)
        self.app.register_blueprint(payments_bp)

        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()

        db.create_all()
        self._sembrar_permisos()

        self.event = Event(
            name='Evento de Prueba',
            date=date(2026, 11, 20),
            time=time(19, 0),
            target_audience='General',
        )
        db.session.add(self.event)
        db.session.commit()

        self.org = self._usuario('org', 'apoyo_logistico', [
            'events.view', 'events.create', 'events.edit', 'events.export', 'events.payments',
        ])
        self.solo = self._usuario('solo', 'usuario', ['events.view'])

        self.participante = Participant(
            event_id=self.event.id,
            name='Juan Soto',
            email='juan@test.com',
            contact_type='email',
            contact_value='juan@test.com',
            monetary_contribution=15000,
            contribution_status='Pendiente',
        )
        db.session.add(self.participante)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    # -- helpers ----------------------------------------------------------

    def _sembrar_permisos(self):
        for key, label, module, description in EVENT_PERMISSIONS:
            db.session.add(Permission(key=key, label=label, module=module, description=description))
        db.session.commit()

    def _usuario(self, username, role, permisos):
        user = User(username=username, email=f'{username}@test.com',
                    password_hash='x', role=role, is_verified=True)
        db.session.add(user)
        db.session.flush()

        rol = Role.query.filter_by(name=role).first()
        if not rol:
            rol = Role(name=role, description=role)
            db.session.add(rol)
            db.session.flush()

        for key in permisos:
            permiso = Permission.query.filter_by(key=key).first()
            db.session.add(RolePermission(role_id=rol.id, permission_id=permiso.id))
        db.session.commit()
        return user

    def _token(self, user):
        return create_access_token(
            identity=str(user.id), additional_claims={'role': user.role}
        )

    def _auth(self, user):
        return {'Authorization': f'Bearer {self._token(user)}'}

    def _send(self, user, participant_ids=None):
        payload = {'participant_ids': participant_ids or [self.participante.id]}
        return self.client.post('/api/events/1/payments/send', json=payload, headers=self._auth(user))

    def _list(self, user):
        respuesta = self.client.get('/api/events/1/payments', headers=self._auth(user))
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.get_json()

    # -- pruebas ----------------------------------------------------------

    def test_enviar_solicitudes_requiere_permiso_de_evento(self):
        respuesta = self.client.post(
            '/api/events/1/payments/send',
            json={'participant_ids': [self.participante.id]},
            headers=self._auth(self.solo),
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_envio_de_solicitudes_crea_pagos_pendientes(self):
        respuesta = self._send(self.org)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()['sent'], 1)

        pagos = self._list(self.org)
        self.assertEqual(len(pagos), 1)
        self.assertEqual(pagos[0]['status'], 'pending')
        self.assertEqual(float(pagos[0]['amount']), 15000.0)
        self.assertIsNotNone(pagos[0]['token'])

    def test_enlace_publico_de_pago(self):
        self._send(self.org)
        pagos = self._list(self.org)
        token = pagos[0]['token']

        respuesta = self.client.get(f'/api/public/payments/{token}')
        self.assertEqual(respuesta.status_code, 200)
        data = respuesta.get_json()
        self.assertEqual(data['status'], 'pending')
        self.assertEqual(data['participant_name'], 'Juan Soto')
        self.assertEqual(float(data['amount']), 15000.0)

    def test_revision_de_pago_aprobar(self):
        self._send(self.org)
        pagos = self._list(self.org)
        payment_id = pagos[0]['id']

        respuesta = self.client.post(
            f'/api/events/1/payments/{payment_id}/review',
            json={'action': 'approve'},
            headers=self._auth(self.org),
        )
        self.assertEqual(respuesta.status_code, 200)

        pagos = self._list(self.org)
        self.assertEqual(pagos[0]['status'], 'approved')

    def test_revision_de_pago_rechazar_con_accion_invalida(self):
        self._send(self.org)
        pagos = self._list(self.org)
        payment_id = pagos[0]['id']

        respuesta = self.client.post(
            f'/api/events/1/payments/{payment_id}/review',
            json={'action': 'invalida'},
            headers=self._auth(self.org),
        )
        self.assertEqual(respuesta.status_code, 400)
