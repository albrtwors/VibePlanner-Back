"""
Pruebas del módulo de cancioneros: ingesta masiva (.txt/.pdf/.docx) y
generación/compilación del PDF.

    .venv/bin/python -m unittest discover -s tests -v

Se levanta la app con SQLite en memoria y se siembran los permisos de
canciones y cancioneros. Se firman tokens JWT reales para probar los 403.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

import models  # noqa: F401
from database import db
from models import (
    SONG_STATUS_BORRADOR,
    File,
    FileSong,
    Permission,
    Role,
    RolePermission,
    Song,
    User,
)
from routes.files import files_bp
from services.file_ingest_service import (
    extract_text_from_file,
    split_songs_from_text,
)
from utils.permissions import PERMISSION_CATALOG

FILE_PERMISSIONS = [
    p for p in PERMISSION_CATALOG
    if p[0].startswith('files.') or p[0].startswith('songs.')
]


class SplitSongsTestCase(unittest.TestCase):
    """Corte del texto en canciones (no toca la base)."""

    def test_texto_vacio_devuelve_lista_vacia(self):
        self.assertEqual(split_songs_from_text(''), [])
        self.assertEqual(split_songs_from_text('   \n  '), [])

    def test_cancion_unica_no_se_parte_en_sus_estrofas(self):
        texto = (
            'Amor Eterno\n'
            'Tono: Am\n'
            '\n'
            '[Em]Hoy necesito hablarte\n'
            'De lo que hay en mi\n'
            '\n'
            'A Veces\n'
            '\n'
            '[Dm]A veces pienso en ti'
        )
        resultado = split_songs_from_text(texto)

        # Un solo salto doble es estrofa, no frontera de canción.
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]['name'], 'Amor Eterno')
        self.assertIn('A Veces', resultado[0]['raw_text'])

    def test_detecta_titulo_autor_y_tono(self):
        texto = 'Amor Eterno\nTono: Am\nAutor: Los Hermanos\n\n[Em]letra'
        cancion = split_songs_from_text(texto)[0]

        self.assertEqual(cancion['name'], 'Amor Eterno')
        self.assertEqual(cancion['key'], 'Am')
        self.assertEqual(cancion['author'], 'Los Hermanos')
        # La línea de metadato no debe quedar metida en la letra.
        self.assertNotIn('Tono:', cancion['structure']['parts'][0]['content'])

    def test_separadores_explicitos(self):
        casos = {
            'guiones': 'Cancion A\nl1\n---\nCancion B\nl2',
            'numerado': '1. Cancion A\nl1\n2. Cancion B\nl2',
            'hash': '## Cancion A\nl1\n## Cancion B\nl2',
            'asteriscos': '***\nCancion A\nl1\n***\nCancion B\nl2',
        }
        for nombre, texto in casos.items():
            with self.subTest(separador=nombre):
                resultado = split_songs_from_text(texto)
                self.assertEqual(len(resultado), 2)
                self.assertEqual([c['name'] for c in resultado],
                                 ['Cancion A', 'Cancion B'])

    def test_doble_salto_con_titulo_marca_frontera(self):
        texto = 'Cancion A\nl1\n\n\nCancion B\nl2'
        resultado = split_songs_from_text(texto)

        self.assertEqual(len(resultado), 2)
        self.assertEqual([c['name'] for c in resultado],
                         ['Cancion A', 'Cancion B'])

    def test_linea_de_acordes_no_toma_el_nombre_de_titulo(self):
        resultado = split_songs_from_text('[Am]\nlinea suelta\nletra real')
        self.assertNotEqual(resultado[0]['name'], 'Am')


class ExtractTextTestCase(unittest.TestCase):
    """Lectura del archivo crudo que llega por multipart."""

    def test_lee_txt(self):
        import tempfile

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w',
                                          encoding='utf-8')
        try:
            tmp.write('Titulo\nletra con acentos: á é í ó ú ñ')
            tmp.close()
            texto = extract_text_from_file(tmp.name, 'canciones.txt')
            self.assertIn('Titulo', texto)
            self.assertIn('á é í ó ú ñ', texto)
        finally:
            import os
            os.unlink(tmp.name)

    def test_lee_docx(self):
        import os
        import tempfile

        try:
            from docx import Document
        except ImportError:
            self.skipTest('python-docx no instalado')

        doc = Document()
        doc.add_paragraph('Cancion del Docx')
        doc.add_paragraph('[Am]primera linea')

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
        tmp.close()
        try:
            doc.save(tmp.name)
            texto = extract_text_from_file(tmp.name, 'canciones.docx')
            self.assertIn('Cancion del Docx', texto)
            self.assertIn('[Am]primera linea', texto)
        finally:
            os.unlink(tmp.name)


class FilesIngestRoutesTestCase(unittest.TestCase):
    """Las rutas /api/files/ingest/parse y /api/files/ingest/commit."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret',
            JWT_TOKEN_LOCATION=['headers'],
            MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        )
        db.init_app(self.app)
        JWTManager(self.app)
        self.app.register_blueprint(files_bp)

        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()

        db.create_all()
        self._sembrar_permisos()

        self.organizador = self._usuario('org', 'musico',
                                         ['files.view', 'files.create',
                                          'files.edit', 'songs.view',
                                          'songs.create'])
        self.espectador = self._usuario('esp', 'usuario',
                                        ['files.view', 'songs.view'])
        # Rol aparte: los permisos se resuelven por rol, no por usuario.
        self.canciones_sin_alta = self._usuario('sin_alta', 'coordinador',
                                                ['files.view', 'files.create'])

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    # -- helpers ----------------------------------------------------------

    def _sembrar_permisos(self):
        for key, label, module, description in FILE_PERMISSIONS:
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
            self.assertIsNotNone(permiso, f'permiso inexistente: {key}')
            ya_esta = RolePermission.query.filter_by(
                role_id=rol.id, permission_id=permiso.id
            ).first()
            if not ya_esta:
                db.session.add(RolePermission(role_id=rol.id,
                                              permission_id=permiso.id))
        db.session.commit()
        return user

    def _auth(self, user):
        token = create_access_token(identity=str(user.id),
                                    additional_claims={'role': user.role})
        return {'Authorization': f'Bearer {token}'}

    # -- pruebas ----------------------------------------------------------

    def test_parse_por_texto_devuelve_bloques_sin_tocar_la_base(self):
        respuesta = self.client.post(
            '/api/files/ingest/parse',
            json={'text': 'Cancion A\n[Am]l1\n\n\nCancion B\n[Dm]l2'},
            headers=self._auth(self.organizador),
        )

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.get_json()
        self.assertEqual(cuerpo['count'], 2)
        self.assertEqual([s['name'] for s in cuerpo['songs']],
                         ['Cancion A', 'Cancion B'])
        # Nada se guardó todavía: esto es solo una propuesta.
        self.assertEqual(Song.query.count(), 0)
        self.assertEqual(File.query.count(), 0)

    def test_parse_por_archivo_txt(self):
        data = {
            'file': (io.BytesIO('Mi Cancion\nTono: G\n[Am]l1'.encode('utf-8')),
                     'letras.txt')
        }
        respuesta = self.client.post('/api/files/ingest/parse', data=data,
                                     headers=self._auth(self.organizador),
                                     content_type='multipart/form-data')

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.get_json()
        self.assertEqual(cuerpo['filename'], 'letras.txt')
        self.assertEqual(cuerpo['songs'][0]['key'], 'G')

    def test_parse_rechaza_extension_no_soportada(self):
        data = {'file': (io.BytesIO(b'x'), 'canciones.xlsx')}
        respuesta = self.client.post('/api/files/ingest/parse', data=data,
                                     headers=self._auth(self.organizador),
                                     content_type='multipart/form-data')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Formato no soportado', respuesta.get_json()['error'])

    def test_parse_sin_texto_da_400(self):
        respuesta = self.client.post('/api/files/ingest/parse', json={'text': '   '},
                                     headers=self._auth(self.organizador))
        self.assertEqual(respuesta.status_code, 400)

    def test_parse_requiere_permiso_para_crear_cancioneros(self):
        respuesta = self.client.post('/api/files/ingest/parse',
                                     json={'text': 'Cancion\nletra'},
                                     headers=self._auth(self.espectador))
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('files.create', respuesta.get_json()['missing_permissions'])

    def test_commit_crea_canciones_en_borrador_y_cancionero_ordenado(self):
        songs = [
            {
                'name': 'Primera',
                'author': 'Los Autores',
                'genre': 'Pop',
                'key': 'am',
                'structure': {'parts': [{'title': 'verso', 'content': '[Am]uno'}]},
            },
            {
                'name': 'Segunda',
                'key': 'G',
                'raw_text': '[G]dos',
            },
        ]
        respuesta = self.client.post(
            '/api/files/ingest/commit',
            json={'songs': songs, 'file': {'name': 'Set del sábado',
                                           'tematica': 'Rock en español'}},
            headers=self._auth(self.organizador),
        )

        self.assertEqual(respuesta.status_code, 201)
        cuerpo = respuesta.get_json()
        self.assertEqual(len(cuerpo['song_ids']), 2)

        cancionero = File.query.get(cuerpo['file_id'])
        self.assertEqual(cancionero.name, 'Set del sábado')
        self.assertEqual(cancionero.tematica, 'Rock en español')

        orden = [a.song_id for a in
                 FileSong.query.filter_by(file_id=cancionero.id)
                 .order_by(FileSong.position).all()]
        self.assertEqual(orden, cuerpo['song_ids'])

        # Nacen como borrador y con el tono normalizado.
        primera = Song.query.get(cuerpo['song_ids'][0])
        self.assertEqual(primera.status, SONG_STATUS_BORRADOR)
        self.assertEqual(primera.key, 'Am')
        self.assertEqual(primera.author.name, 'Los Autores')
        self.assertEqual(primera.user_id, self.organizador.id)

        segunda = Song.query.get(cuerpo['song_ids'][1])
        self.assertEqual(segunda.key, 'G')
        # Sin autor informado caemos en "Desconocido" y el genre en "Sin género".
        self.assertEqual(segunda.author.name, 'Desconocido')
        self.assertEqual(segunda.genre.name, 'Sin género')
        # Si vino solo raw_text, se arma la estructura de una parte.
        self.assertEqual(segunda.structure['parts'][0]['content'], '[G]dos')

    def test_commit_ignora_canciones_sin_titulo(self):
        respuesta = self.client.post(
            '/api/files/ingest/commit',
            json={'songs': [{'name': '  '}, {'name': 'Buena'}],
                  'file': {'name': 'Mixto'}},
            headers=self._auth(self.organizador),
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(len(respuesta.get_json()['song_ids']), 1)

    def test_commit_sin_canciones_da_400(self):
        respuesta = self.client.post(
            '/api/files/ingest/commit',
            json={'songs': [], 'file': {'name': 'Vacío'}},
            headers=self._auth(self.organizador),
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_commit_sin_nombre_de_cancionero_da_400(self):
        respuesta = self.client.post(
            '/api/files/ingest/commit',
            json={'songs': [{'name': 'X'}], 'file': {}},
            headers=self._auth(self.organizador),
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_commit_exige_permiso_para_crear_canciones(self):
        """Con files.create pero sin songs.create no puede dar de alta canciones."""
        respuesta = self.client.post(
            '/api/files/ingest/commit',
            json={'songs': [{'name': 'X'}], 'file': {'name': 'Set'}},
            headers=self._auth(self.canciones_sin_alta),
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('songs.create', respuesta.get_json()['missing_permissions'])
        self.assertEqual(Song.query.count(), 0)
        self.assertEqual(File.query.count(), 0)


if __name__ == '__main__':
    unittest.main()