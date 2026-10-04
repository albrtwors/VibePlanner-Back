"""
Pruebas del servicio de transposición.

Se ejecutan con la librería estándar, sin instalar nada:

    .venv/bin/python -m unittest discover -s tests -v

La transposición se valida contra una implementación de referencia independiente
(clases semitónicas), porque ahí no puede haber ni un error de una semitona. La
detección de tono es un heurístico, así que solo se comprueban los casos donde
hay un acorde decisivo, y el comportamiento en los pares relativos (Do mayor /
La menor) se fija como mayor por diseño.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import transpose_service as ts

LETTERS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def pitch_class(name):
    """'Bb' -> 10, 'F#' -> 6, 'C' -> 0. Referencia independiente del servicio."""
    letter, accidental = name[0], name[1] if len(name) > 1 and name[1] in '#b' else ''
    return (LETTERS[letter] + (1 if accidental == '#' else -1 if accidental == 'b' else 0)) % 12


def root_of(chord):
    match = re.match(r'^[A-G][#b]?', chord)
    return match.group(0)


def body_of(chord):
    """El acorde sin la tónica ni la nota de bajo: 'm7', 'sus4', '' ..."""
    return chord[len(root_of(chord)):].split('/')[0]


def bass_of(chord):
    return chord.split('/')[1] if '/' in chord else None


def structure(content):
    return {'parts': [{'title': 'seccion', 'content': content}]}


def chords_of(text):
    return re.findall(r'\[([^\]]+)\]', text)


class TransposeMathTest(unittest.TestCase):
    """El intervalo tiene que ser exacto en los 24 tonos, sin excepciones."""

    CONTENT = ('[Am]Cuando [F]bajo al [C]cielo\n'
               '[Dm]miro lo que [Bb]hay [F/A]aquí')

    def test_todas_las_raices_y_extensiones_se_mueven_el_intervalo_exacto(self):
        for key in ts.ALL_KEYS:
            with self.subTest(tono=key):
                result = ts.transpose_structure(structure(self.CONTENT), key, source_key='Am')
                moved = chords_of(result['structure']['parts'][0]['content'])
                semitones = result['semitones']
                self.assertEqual(result['success'], True)
                self.assertEqual(result['target_key'], ts.normalize_key(key))
                self.assertEqual(len(moved), len(chords_of(self.CONTENT)))
                for original, nuevo in zip(chords_of(self.CONTENT), moved):
                    self.assertEqual(
                        pitch_class(root_of(nuevo)),
                        (pitch_class(root_of(original)) + semitones) % 12
                    )
                    self.assertEqual(body_of(nuevo), body_of(original))
                    if bass_of(original):
                        self.assertEqual(
                            pitch_class(bass_of(nuevo)),
                            (pitch_class(bass_of(original)) + semitones) % 12
                        )
                    else:
                        self.assertIsNone(bass_of(nuevo))

    def test_la_letra_no_se_toca_nunca(self):
        sin_acordes = re.sub(r'\[[^\]]+\]', '', self.CONTENT)
        for key in ts.ALL_KEYS:
            with self.subTest(tono=key):
                result = ts.transpose_structure(structure(self.CONTENT), key, source_key='Am')
                texto = result['structure']['parts'][0]['content']
                self.assertEqual(re.sub(r'\[[^\]]+\]', '', texto), sin_acordes)

    def test_casos_conocidos(self):
        casos = [
            ('[F#]Hola [C#]mundo', 'F#', 'G', '[G]Hola [D]mundo'),
            ('[C]Hoy [F]no [G]dormí', 'C', 'F#', '[F#]Hoy [B]no [C#]dormí'),
            ('C/E', 'C', 'F', 'F/A'),
        ]
        for contenido, origen, destino, esperado in casos:
            with self.subTest(caso=contenido):
                result = ts.transpose_structure(structure(contenido), destino, source_key=origen)
                self.assertEqual(result['structure']['parts'][0]['content'], esperado)

    def test_las_tonaleidades_menor_y_aumentada_son_validas(self):
        self.assertEqual(ts.parse_key('F#m')['is_minor'], True)
        self.assertEqual(ts.parse_key('Bb')['is_minor'], False)
        self.assertIsNone(ts.parse_key('H'))
        self.assertEqual(ts.normalize_key('f#m'), 'F#m')
        self.assertEqual(ts.normalize_key('bb'), 'Bb')
        self.assertIsNone(ts.normalize_key('Zm'))

    def test_los_intervalos_entre_tonos(self):
        self.assertEqual(ts.semitones_between('Am', 'Bb'), 1)
        self.assertEqual(ts.semitones_between('Am', 'Ab'), 11)
        self.assertEqual(ts.semitones_between('Am', 'G'), 10)
        self.assertEqual(ts.semitones_between('Am', 'F'), 8)
        self.assertEqual(ts.semitones_between('C', 'B'), 11)
        self.assertEqual(ts.semitones_between('Am', 'Am'), 0)

    def test_sin_tono_de_origen_no_inventa_un_intervalo(self):
        # Devolver 0 haría pasar por transpuesta una canción que nunca se movió.
        self.assertIsNone(ts.semitones_between(None, 'C'))
        self.assertIsNone(ts.semitones_between('Zm', 'C'))
        self.assertIsNone(ts.semitones_between('Am', 'Zm'))

    def test_normalize_key_respeta_la_grafia_del_usuario(self):
        self.assertEqual(ts.normalize_key('f#m'), 'F#m')
        self.assertEqual(ts.normalize_key('bb'), 'Bb')
        self.assertEqual(ts.normalize_key('gb'), 'Gb')
        self.assertEqual(ts.normalize_key('  am '), 'Am')
        self.assertEqual(ts.normalize_key('C'), 'C')

    def test_el_catalogo_no_tiene_enharmonicos_duplicados(self):
        clases = {ts.parse_key(key)['pitch_class'] for key in ts.ALL_KEYS}
        self.assertEqual(len(ts.ALL_KEYS), 24)
        self.assertEqual(len({ts.normalize_key(key) for key in ts.ALL_KEYS}), 24)
        # F# es la grafía habitual, así que es la que va en el catálogo.
        self.assertIn('F#', ts.ALL_KEYS)
        self.assertIn('F#m', ts.ALL_KEYS)
        self.assertEqual(len(clases), 12)

    def test_transponer_al_mismo_tono_no_cambia_nada(self):
        result = ts.transpose_structure(structure(self.CONTENT), 'Am', source_key='Am')
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['chords_changed'], 0)

    def test_errores_de_uso(self):
        self.assertFalse(ts.transpose_structure(None, 'C')['success'])
        self.assertFalse(ts.transpose_structure({'parts': []}, 'Z9')['success'])
        self.assertFalse(ts.transpose_structure(structure('[C]x'), 'C', source_key='Z9')['success'])

    def test_cuenta_los_acordes_movidos(self):
        result = ts.transpose_structure(structure(self.CONTENT), 'C', source_key='Am')
        self.assertEqual(result['chords_changed'], len(chords_of(self.CONTENT)))


class FalsePositiveTest(unittest.TestCase):
    """
    Lo que no es un acorde no se puede mover. La 'a' del español y la 'a' inglesa
    son el caso histórico de este parser, así que hay pruebas de ambas.
    """

    PROSA = ('Amor_bad Fade Canción Añil Dear dime Be Age Dime Fame Came\n'
             'Estaba Adding salt a la mañana\n'
             'a la vez y al otro día\n'
             'A thousand miles away\n'
             'Ella es Add de mi vida')

    def test_la_prosa_se_queda_igual(self):
        texto, cambiados = ts.transpose_text(self.PROSA, 3)
        self.assertEqual(texto, self.PROSA)
        self.assertEqual(cambiados, 0)

    def test_una_letra_suelta_no_es_un_acorde(self):
        self.assertEqual(ts.transpose_text('a', 3), ('a', 0))
        self.assertEqual(ts.transpose_text('A', 3), ('A', 0))

    def test_los_marcadores_de_seccion_no_son_acordes(self):
        texto, cambiados = ts.transpose_text('[CORO]\n[VERSO 1]\n[PUENTE]', 3)
        self.assertEqual(texto, '[CORO]\n[VERSO 1]\n[PUENTE]')
        self.assertEqual(cambiados, 0)

    def test_detectar_tono_ignora_la_prosa(self):
        self.assertIsNone(ts.detect_key(structure('Amor de mi vida')))
        self.assertIsNone(ts.detect_key(structure('[CORO] [VERSO 1]')))
        self.assertIsNone(ts.detect_key(None))


class ChordNotationTest(unittest.TestCase):
    def test_fila_de_acordes_sin_corchetes(self):
        texto, cambiados = ts.transpose_text('Am        F        C\nBb7sus4', 3, True)
        self.assertEqual(texto, 'Cm        Ab        Eb\nDb7sus4')
        self.assertEqual(cambiados, 4)

    def test_los_acordes_pelados_dentro_de_corchetes_si_cuentan(self):
        self.assertEqual(len(ts.collect_chords(structure('[C] [F] [G]'))), 3)

    def test_la_grafia_sigue_al_tono_destino(self):
        # A F♭ no se escribe "G#": se escribe lo que el destino pide.
        resultado = ts.transpose_structure(structure('[F#]Hola'), 'Gb', source_key='F#')
        self.assertEqual(resultado['structure']['parts'][0]['content'], '[Gb]Hola')

    def test_los_bemoles_no_se_convierten_en_sostenidos(self):
        resultado = ts.transpose_structure(structure('[Bb]Hola'), 'C', source_key='Bb')
        self.assertEqual(resultado['structure']['parts'][0]['content'], '[C]Hola')


class StructureTest(unittest.TestCase):
    CANCION = {
        'parts': [
            {'title': 'verso 1', 'content': '[Am]Cuando [F]bajo al [C]cielo\n[Dm]miro lo que [Bb]hay'},
            {'title': 'coro', 'content': '[F]Moraleja [C]Bb7sus4 [Am7]E7'},
        ]
    }

    def test_conserva_los_titulos_y_el_orden_de_las_secciones(self):
        resultado = ts.transpose_structure(self.CANCION, 'C', source_key='Am')
        partes = resultado['structure']['parts']
        self.assertEqual([p['title'] for p in partes], ['verso 1', 'coro'])
        self.assertIn('Moraleja', partes[1]['content'])
        self.assertIn('verso 1', partes[0]['title'])

    def test_no_toca_el_titulo_que_no_es_un_acorde(self):
        resultado = ts.transpose_structure(self.CANCION, 'C', source_key='Am')
        self.assertEqual(resultado['structure']['parts'][1]['title'], 'coro')

    def test_acepta_un_json_en_cadena(self):
        import json
        resultado = ts.transpose_structure(json.dumps(self.CANCION), 'C', source_key='Am')
        self.assertTrue(resultado['success'])


class DetectKeyTest(unittest.TestCase):
    def detect(self, acordes):
        return ts.detect_key(structure(' '.join(f'[{c}]' for c in acordes)))

    def test_cuando_hay_un_acorde_decisivo(self):
        # El Mi (con su G#) separa Do mayor de La menor; el F# separa Sol de Mi.
        casos = [
            (['C', 'F', 'G', 'Am', 'E'], 'C'),
            (['Am', 'F', 'C', 'Dm', 'E7'], 'Am'),
            (['G', 'C', 'D', 'Em', 'F#m'], 'G'),
            (['Em', 'Am', 'B7', 'D'], 'Em'),
            (['Eb', 'Ab', 'Bb', 'Cm', 'Ebm'], 'Eb'),
            (['D', 'A', 'Bm', 'G', 'F#m'], 'D'),
            (['F#m', 'Bm', 'C#m', 'E'], 'F#m'),
        ]
        for acordes, esperado in casos:
            with self.subTest(acordes=acordes):
                self.assertEqual(self.detect(acordes), esperado)

    def test_los_pares_relativos_gana_el_mayor(self):
        # Do mayor y La menor comparten los mismos acordes: es imposible
        # decidirlo, así que por diseño se devuelve el mayor.
        self.assertEqual(self.detect(['C', 'F', 'G', 'Am']), 'C')
        self.assertEqual(self.detect(['G', 'C', 'D', 'Em']), 'G')

    def test_detecta_el_tonico_de_un_falso_amigo(self):
        # Un acorde menor en la tónica pesa menos que un acorde mayor bien
        # colocado, así que estos casos se resuelven hacia Do mayor.
        for acordes, esperado in [
            (['Am', 'F', 'C', 'G'], 'C'),
            (['Am', 'F', 'C', 'Dm', 'G'], 'C'),
        ]:
            with self.subTest(acordes=acordes):
                self.assertEqual(self.detect(acordes), esperado)


class CapoTest(unittest.TestCase):
    def test_sugiere_cejilla_para_los_tonos_con_mucha_alteracion(self):
        casos = [
            ('F#m', 1), ('C#m', 2), ('Gb', 3), ('Bbm', 3), ('Eb', 1),
            ('C', None), ('D', None), ('F', None), ('Am', None), ('Abm', 3),
        ]
        for tono, esperado in casos:
            with self.subTest(tono=tono):
                self.assertEqual(ts.capo_suggestion(tono), esperado)

    def test_firma_de_tonos(self):
        self.assertEqual(ts.signature_fifths('C'), 0)
        self.assertEqual(ts.signature_fifths('G'), 1)
        self.assertEqual(ts.signature_fifths('F'), -1)
        self.assertEqual(ts.signature_fifths('Eb'), -3)
        self.assertIsNone(ts.signature_fifths('Zm'))


if __name__ == '__main__':
    unittest.main(verbosity=2)