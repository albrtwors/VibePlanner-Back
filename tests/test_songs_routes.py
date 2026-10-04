"""
Pruebas de integración de las rutas de canciones.

    .venv/bin/python -m unittest discover -s tests -v

Se levanta la app con SQLite en memoria, se siembran solo los permisos de
canciones y se firman tokens JWT reales para cada rol. Así se comprueba lo que
ve el frontend: códigos HTTP, permisos y forma de las respuestas.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

import models  # noqa: F401
from database import db
from models import (
    SONG_STATUS_APROBADA,
    SONG_STATUS_BORRADOR,
    SONG_STATUS_EN_REVISION,
    SONG_STATUS_PUBLICADA,
    Author,
    Genre,
    Permission,
    Role,
    RolePermission,
    Song,
    User,
)
from routes.songs import songs_bp
from utils.permissions import PERMISSION_CATALOG

SONG_PERMISSIONS = [p for p in PERMISSION_CATALOG if p[0].startswith('songs.')]


class SongsRoutesTestCase(unittest.TestCase):
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
        self.app.register_blueprint(songs_bp)

        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()

        db.create_all()
        self._sembrar_permisos()

        self.genero = Genre(name='Pop')
        db.session.add(self.genero)
        self.autor_nombre = Author(name='Los Autores')
        db.session.add(self.autor_nombre)
        db.session.commit()

        self.autor = self._usuario('autor', 'autor', ['songs.view', 'songs.create',
                                                      'songs.edit', 'songs.suggest'])
        self.moder = self._usuario('moder', 'admin', ['songs.view', 'songs.moderate',
                                                      'songs.edit'])
        self.lector = self._usuario('lector', 'usuario', ['songs.view', 'songs.suggest'])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    # -- helpers ----------------------------------------------------------

    def _sembrar_permisos(self):
        for key, label, module, description in SONG_PERMISSIONS:
            db.session.add(Permission(key=key, label=label, module=module,
                                      description=description))
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

    def _crear_cancion(self, user=None, **extra):
        payload = {
            'name': 'Canción de prueba',
            'author': 'Los Autores',
            'genre': 'Pop',
            'key': 'am',
            'structure': {'parts': [{'title': 'verso', 'content': '[Am]Cuando [F]bajo'}]},
        }
        payload.update(extra)
        respuesta = self.client.post('/api/songs/', json=payload,
                                     headers=self._auth(user or self.autor))
        return respuesta

    def _publicar(self, song_id):
        self.client.post(f'/api/songs/{song_id}/submit', headers=self._auth(self.autor))
        self.client.post(f'/api/songs/{song_id}/review', json={'action': 'aprobar'},
                         headers=self._auth(self.moder))
        self.client.post(f'/api/songs/{song_id}/review', json={'action': 'publicar'},
                         headers=self._auth(self.moder))

    # -- pruebas ----------------------------------------------------------

    def test_crear_cancion_la_deja_en_borrador_con_tono_normalizado(self):
        respuesta = self._crear_cancion()
        self.assertEqual(respuesta.status_code, 201)
        cancion = respuesta.get_json()['song']
        self.assertEqual(cancion['status'], SONG_STATUS_BORRADOR)
        self.assertEqual(cancion['key'], 'Am')
        self.assertFalse(cancion['is_public'])
        self.assertTrue(cancion['can_edit'])

    def test_crear_con_tono_invalido_da_400(self):
        respuesta = self._crear_cancion(key='Z9')
        self.assertEqual(respuesta.status_code, 400)

    def test_flujo_de_moderacion_completo(self):
        song_id = self._crear_cancion().get_json()['song_id']

        enviada = self.client.post(f'/api/songs/{song_id}/submit',
                                   headers=self._auth(self.autor))
        self.assertEqual(enviada.status_code, 200)
        self.assertEqual(enviada.get_json()['song']['status'], SONG_STATUS_EN_REVISION)

        cola = self.client.get('/api/songs/moderation/queue', headers=self._auth(self.moder))
        self.assertEqual(cola.status_code, 200)
        self.assertIn(song_id, {s['id'] for s in cola.get_json()['songs']})

        aprobada = self.client.post(f'/api/songs/{song_id}/review',
                                    json={'action': 'aprobar'},
                                    headers=self._auth(self.moder))
        self.assertEqual(aprobada.get_json()['song']['status'], SONG_STATUS_APROBADA)

        publicada = self.client.post(f'/api/songs/{song_id}/review',
                                     json={'action': 'publicar'},
                                     headers=self._auth(self.moder))
        self.assertEqual(publicada.get_json()['song']['status'], SONG_STATUS_PUBLICADA)

    def test_rechazar_sin_motivo_da_400(self):
        song_id = self._crear_cancion().get_json()['song_id']
        self.client.post(f'/api/songs/{song_id}/submit', headers=self._auth(self.autor))

        respuesta = self.client.post(f'/api/songs/{song_id}/review',
                                     json={'action': 'rechazar'},
                                     headers=self._auth(self.moder))
        self.assertEqual(respuesta.status_code, 400)

    def test_un_borrador_ajeno_da_404(self):
        song_id = self._crear_cancion().get_json()['song_id']
        respuesta = self.client.get(f'/api/songs/{song_id}', headers=self._auth(self.lector))
        self.assertEqual(respuesta.status_code, 404)

    def test_el_listado_oculta_lo_no_publicado(self):
        song_id = self._crear_cancion().get_json()['song_id']

        como_lector = self.client.get('/api/songs/', headers=self._auth(self.lector))
        self.assertNotIn(song_id, {s['id'] for s in como_lector.get_json()['songs']})

        self._publicar(song_id)

        como_lector = self.client.get('/api/songs/', headers=self._auth(self.lector))
        self.assertIn(song_id, {s['id'] for s in como_lector.get_json()['songs']})

        como_mod = self.client.get('/api/songs/', headers=self._auth(self.moder))
        self.assertIn(song_id, {s['id'] for s in como_mod.get_json()['songs']})

    def test_sin_permiso_de_moderar_da_403(self):
        song_id = self._crear_cancion().get_json()['song_id']
        respuesta = self.client.get('/api/songs/moderation/queue',
                                    headers=self._auth(self.lector))
        self.assertEqual(respuesta.status_code, 403)

    def test_una_sugerencia_aprobada_reabre_la_cancion(self):
        song_id = self._crear_cancion().get_json()['song_id']
        self._publicar(song_id)

        creada = self.client.post(
            f'/api/songs/{song_id}/suggestions',
            json={'key': 'C', 'structure': {'parts': [{'title': 'verso', 'content': '[C]Hola'}]},
                  'notes': 'Lo subo a Do'},
            headers=self._auth(self.lector)
        )
        self.assertEqual(creada.status_code, 201)
        suggestion_id = creada.get_json()['suggestion']['id']

        resuelta = self.client.put(
            f'/api/songs/suggestions/{suggestion_id}/resolve',
            json={'action': 'aprobar'}, headers=self._auth(self.moder)
        )
        self.assertEqual(resuelta.status_code, 200)
        self.assertEqual(resuelta.get_json()['song']['key'], 'C')
        self.assertEqual(resuelta.get_json()['song']['status'], SONG_STATUS_EN_REVISION)

    def test_el_catalogo_de_tonos_trae_cejilla(self):
        respuesta = self.client.get('/api/songs/keys', headers=self._auth(self.lector))
        cuerpo = respuesta.get_json()
        self.assertEqual(len(cuerpo['keys']), 24)
        self.assertEqual(cuerpo['capo']['F#m'], 1)

    def test_vista_previa_de_transposicion(self):
        respuesta = self.client.post(
            '/api/songs/transpose/preview',
            json={
                'structure': {'parts': [{'title': 'verso', 'content': '[Am]Cuando [F]bajo'}]},
                'source_key': 'Am',
                'target_key': 'F#m',
            },
            headers=self._auth(self.lector)
        )
        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.get_json()
        self.assertEqual(cuerpo['semitones'], 9)
        self.assertEqual(cuerpo['target_key'], 'F#m')

    def test_transponer_y_guardar_reabre_la_publicada(self):
        song_id = self._crear_cancion().get_json()['song_id']
        self._publicar(song_id)

        respuesta = self.client.post(
            f'/api/songs/{song_id}/transpose',
            json={'target_key': 'C', 'apply': True},
            headers=self._auth(self.autor)
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()['song']['key'], 'C')
        self.assertEqual(respuesta.get_json()['song']['status'], SONG_STATUS_EN_REVISION)


if __name__ == '__main__':
    unittest.main(verbosity=2)