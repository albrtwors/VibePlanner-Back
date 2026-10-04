"""
Pruebas del servicio de moderación de canciones.

    .venv/bin/python -m unittest discover -s tests -v

Se levanta una app Flask propia contra SQLite en memoria, así que no tocan la
base de desarrollo ni necesitan migración previa.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask

import models  # noqa: F401  (registra las tablas en db.metadata)
from database import db
from models import (
    SONG_STATUS_APROBADA,
    SONG_STATUS_BORRADOR,
    SONG_STATUS_EN_REVISION,
    SONG_STATUS_PUBLICADA,
    SONG_STATUS_RECHAZADA,
    Author,
    Genre,
    Song,
    User,
)
from services import song_moderation_service as mod


class ModerationTestCase(unittest.TestCase):
    def setUp(self):
        # App propia en memoria: app.py no tiene fábrica de apps y la de
        # desarrollo apunta a la base real, así que no se puede reutilizar.
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        db.init_app(self.app)

        # Un único contexto para todo el test: si se cierra al terminar setUp,
        # los objetos quedan desligados de la sesión y después no se pueden leer.
        self.ctx = self.app.app_context()
        self.ctx.push()

        db.create_all()

        self.autor = self._usuario('autor')
        self.otro = self._usuario('otro')

        self.genero = Genre(name='Pop')
        db.session.add(self.genero)
        db.session.flush()
        self.autor_nombre = Author(name='Los Autores')
        db.session.add(self.autor_nombre)
        db.session.flush()

        self.autor_id = self.autor.id
        self.otro_id = self.otro.id

        self.cancion = self._crear('Amor de prueba', self.autor_id)
        self.ajeno = self._crear('Canción ajena', self.otro_id)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _usuario(self, nombre):
        user = User(
            username=nombre,
            email=f'{nombre}@test.com',
            password_hash='x',
            is_verified=True
        )
        db.session.add(user)
        db.session.flush()
        return user

    def _crear(self, nombre, user_id, status=SONG_STATUS_BORRADOR, key='Am'):
        song = Song(
            name=nombre,
            genre_id=self.genero.id,
            author_id=self.autor_nombre.id,
            user_id=user_id,
            status=status,
            key=key,
            structure={'parts': [{'title': 'verso', 'content': '[Am]Cuando [F]bajo'}]}
        )
        db.session.add(song)
        db.session.commit()
        return song

    def _publicar(self, song):
        mod.enviar_a_revision(song, self.autor_id)
        mod.revisar(song, self.otro_id, 'aprobar')
        mod.revisar(song, self.otro_id, 'publicar')

    # -- visibilidad ------------------------------------------------------

    def test_una_cancion_borrador_solo_la_ve_su_autor(self):
        self.assertTrue(mod.puede_ver(self.cancion, self.autor_id, set()))
        self.assertFalse(mod.puede_ver(self.cancion, self.otro_id, set()))
        self.assertTrue(mod.puede_ver(self.cancion, self.otro_id, {'songs.moderate'}))

    def test_una_cancion_publicada_la_ve_cualquiera(self):
        self.cancion.status = SONG_STATUS_PUBLICADA
        db.session.commit()
        self.assertTrue(mod.puede_ver(self.cancion, self.otro_id, set()))
        self.assertTrue(self.cancion.is_public)

    def test_el_listado_filtra_lo_no_publicado(self):
        visible_para_otro = mod.filtro_visibilidad(Song.query, self.otro_id, set()).all()
        self.assertNotIn(self.cancion.id, {s.id for s in visible_para_otro})

        visibles_para_autor = mod.filtro_visibilidad(Song.query, self.autor_id, set()).all()
        self.assertIn(self.cancion.id, {s.id for s in visibles_para_autor})

        todo_para_mod = mod.filtro_visibilidad(
            Song.query, self.otro_id, {'songs.moderate'}
        ).all()
        self.assertIn(self.cancion.id, {s.id for s in todo_para_mod})

    # -- ciclo de vida ----------------------------------------------------

    def test_el_autor_puede_enviar_su_borrador_a_revision(self):
        resultado = mod.enviar_a_revision(self.cancion, self.autor_id)
        self.assertTrue(resultado['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_EN_REVISION)
        self.assertIsNotNone(self.cancion.submitted_at)

    def test_un_ajeno_no_puede_enviar_a_revision(self):
        resultado = mod.enviar_a_revision(self.cancion, self.otro_id)
        self.assertFalse(resultado['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_BORRADOR)

    def test_no_se_reenvia_una_cancion_ya_en_revision(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)
        repetido = mod.enviar_a_revision(self.cancion, self.autor_id)
        self.assertFalse(repetido['success'])

    def test_rechazar_exige_motivo(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)

        sin_motivo = mod.revisar(self.cancion, self.otro_id, 'rechazar')
        self.assertFalse(sin_motivo['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_EN_REVISION)

        con_motivo = mod.revisar(
            self.cancion, self.otro_id, 'rechazar', 'El verso 2 está incompleto'
        )
        self.assertTrue(con_motivo['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_RECHAZADA)
        self.assertEqual(self.cancion.review_notes, 'El verso 2 está incompleto')
        self.assertEqual(self.cancion.reviewed_by_id, self.otro_id)

    def test_aprobar_y_publicar_son_pasos_distintos(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)

        aprobada = mod.revisar(self.cancion, self.otro_id, 'aprobar')
        self.assertTrue(aprobada['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_APROBADA)
        self.assertFalse(self.cancion.is_public)

        publicada = mod.revisar(self.cancion, self.otro_id, 'publicar')
        self.assertTrue(publicada['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_PUBLICADA)
        self.assertTrue(self.cancion.is_public)

    def test_no_se_publica_algo_que_no_esta_aprobado(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)
        # Está en revisión, no aprobada: publicar directo no es el paso que sigue.
        resultado = mod.revisar(self.cancion, self.otro_id, 'publicar')
        self.assertFalse(resultado['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_EN_REVISION)

    def test_al_reenviar_se_limpia_el_motivo_anterior(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)
        mod.revisar(self.cancion, self.otro_id, 'rechazar', 'Falta el puente')
        self.assertIsNotNone(self.cancion.review_notes)

        mod.enviar_a_revision(self.cancion, self.autor_id)
        self.assertIsNone(self.cancion.review_notes)
        self.assertIsNone(self.cancion.reviewed_by_id)

    def test_editar_una_publicada_la_reabre(self):
        self._publicar(self.cancion)
        self.assertEqual(self.cancion.status, SONG_STATUS_PUBLICADA)

        resultado = mod.reaperturar(self.cancion, self.autor_id)
        self.assertTrue(resultado['success'])
        self.assertEqual(self.cancion.status, SONG_STATUS_EN_REVISION)
        self.assertFalse(self.cancion.is_public)
        self.assertIsNone(self.cancion.reviewed_by_id)

    def test_reabrir_algo_no_publicado_no_cambia_su_estado(self):
        # Si nunca se publicó, 'reaperturar' no aplica y devuelve None para que
        # la ruta de edición no lo trate como error.
        self.assertIsNone(mod.reaperturar(self.cancion, self.autor_id))
        self.assertEqual(self.cancion.status, SONG_STATUS_BORRADOR)

    def test_permisos_de_edicion(self):
        self._publicar(self.cancion)
        self.assertTrue(mod.puede_editar(self.cancion, self.autor_id, set()))
        self.assertTrue(mod.puede_editar(self.cancion, self.otro_id, {'songs.moderate'}))
        self.assertFalse(mod.puede_editar(self.cancion, self.otro_id, set()))
        # El autor no puede publicar por su cuenta: su cambio vuelve a la cola.
        self.assertFalse(mod.revisar(self.cancion, self.autor_id, 'publicar')['success'])

    # -- sugerencias ------------------------------------------------------

    def test_una_sugerencia_necesita_algo_que_aprobar(self):
        self._publicar(self.cancion)
        resultado = mod.crear_sugerencia(
            self.cancion, self.otro_id, {'songs.suggest'}, notas='me gusta así'
        )
        self.assertFalse(resultado['success'])

    def test_no_se_sugiere_sobre_un_borrador(self):
        resultado = mod.crear_sugerencia(
            self.cancion, self.otro_id, {'songs.suggest'}, suggested_key='C'
        )
        self.assertFalse(resultado['success'])

    def test_sugerir_requiere_el_permiso(self):
        self._publicar(self.cancion)
        resultado = mod.crear_sugerencia(
            self.cancion, self.otro_id, set(), suggested_key='C'
        )
        self.assertFalse(resultado['success'])

    def test_no_se_puede_sugerir_sobre_lo_que_no_se_ve(self):
        # La canción está en revisión y es de otro: un tercero ni siquiera la ve,
        # así que tampoco puede proponerle cambios.
        mod.enviar_a_revision(self.cancion, self.autor_id)
        resultado = mod.crear_sugerencia(
            self.cancion, self.otro_id, {'songs.suggest'}, suggested_key='C'
        )
        self.assertFalse(resultado['success'])

    def _sugerir(self, **kwargs):
        # Se sugiere sobre lo publicado, que es lo que ve el resto del equipo.
        self._publicar(self.cancion)
        creada = mod.crear_sugerencia(self.cancion, self.otro_id, {'songs.suggest'}, **kwargs)
        self.assertTrue(creada['success'], creada.get('error'))
        sugerencia = creada['suggestion']
        db.session.add(sugerencia)
        db.session.commit()
        return sugerencia

    def test_aprobar_una_sugerencia_aplica_el_cambio_y_reabre_la_cancion(self):
        nueva = {'parts': [{'title': 'verso', 'content': '[C]Hola [G]mundo'}]}
        sugerencia = self._sugerir(
            suggested_structure=nueva, suggested_key='C',
            notas='Lo paso a Do para que sea más cantable'
        )

        resuelta = mod.resolver_sugerencia(sugerencia, self.autor_id, 'aprobar')
        self.assertTrue(resuelta['success'])
        self.assertEqual(self.cancion.structure, nueva)
        self.assertEqual(self.cancion.key, 'C')
        # El contenido público no se publica solo: vuelve a la cola.
        self.assertEqual(self.cancion.status, SONG_STATUS_EN_REVISION)
        self.assertEqual(sugerencia.status, 'aprobada')

    def test_una_sugerencia_rechazada_no_toca_la_cancion(self):
        estructura_original = self.cancion.structure
        sugerencia = self._sugerir(suggested_structure={'parts': []}, notas='prueba')

        mod.resolver_sugerencia(sugerencia, self.autor_id, 'rechazar', 'No va')
        self.assertEqual(self.cancion.structure, estructura_original)
        self.assertEqual(sugerencia.status, 'rechazada')

    def test_una_sugerencia_no_se_resuelve_dos_veces(self):
        sugerencia = self._sugerir(suggested_key='C')

        primera = mod.resolver_sugerencia(sugerencia, self.autor_id, 'aprobar')
        self.assertTrue(primera['success'])
        segunda = mod.resolver_sugerencia(sugerencia, self.autor_id, 'aprobar')
        self.assertFalse(segunda['success'])

    def test_la_cola_solo_muestra_lo_pendiente(self):
        mod.enviar_a_revision(self.cancion, self.autor_id)
        cola = mod.cola_de_revision()
        self.assertIn(self.cancion.id, {s.id for s in cola})
        self.assertNotIn(self.ajeno.id, {s.id for s in cola})
        self.assertEqual(mod.sugerencias_pendientes(), [])

    # -- serialización ----------------------------------------------------

    def test_la_serializacion_calcula_lo_que_puede_hacer_cada_uno(self):
        datos = mod.serializar_cancion(self.cancion, self.autor_id, set(), detail=True)
        self.assertTrue(datos['can_edit'])
        self.assertFalse(datos['can_review'])
        self.assertEqual(datos['status'], SONG_STATUS_BORRADOR)
        self.assertEqual(datos['key'], 'Am')
        # Las claves viejas del frontend siguen intactas.
        self.assertIn('name', datos)
        self.assertIn('structure', datos)

        como_mod = mod.serializar_cancion(self.cancion, self.otro_id, {'songs.moderate'})
        self.assertTrue(como_mod['can_edit'])
        self.assertTrue(como_mod['can_review'])

    def test_la_serializacion_cuenta_las_sugerencias_pendientes(self):
        sugerencia = self._sugerir(suggested_key='C')
        datos = mod.serializar_cancion(self.cancion, self.autor_id, set())
        self.assertEqual(datos['suggestions_count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
