# services/transpose_service.py
"""
Proceso de transposición automática de tonos.

Qué resuelve este módulo: dado el tono base de una canción (Song.key) y el tono
que pide el equipo musical, calcular la nueva estructura con TODOS los acordes
movidos la misma distancia, sin tocar un solo carácter de la letra.

Decisiones de diseño (importan para no romper letras):

1. No se transpone con una regex gigante sobre el texto: se tokeniza la letra
   y cada token se clasifica como "acorde" o "palabra". Así "Amor", "Bad",
   "Fade" o "Aé" nunca se tocan, porque no son acordes válidos.
2. El acorde se reconstruye desde sus partes (tónica + alteración + calidad +
   extensión + bajo fifth), nunca con un replace de texto. Por eso "Bm7"
   transpuesto a Am queda "Am7" y no "Bm7" mutado a algo raro.
3. Laarmonía se mantiene en la convención que ya usa la canción: si el acorde
   original llevaba bemol (Bb, Eb), el resultado también lleva bemol.
"""
import json
import re
from typing import Dict, List, Optional, Tuple

# ==========================================
# 1. TEORÍA DE LA ESCALA
# ==========================================

# Semitonos desde C para cada nota natural.
NOTE_SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}

SHARP_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
FLAT_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']

# El catálogo tiene que cubrir las 12 tónicas sin repetir ninguna por
# enharmonía. En la parte de los sostenidos manda la grafía con sostenidos
# (F#) porque es la que se usa en casi todo el cancionero de rock y pop, y en la
# de los bemoles la de bemoles (Db, Eb, Ab, Bb). Gb no entra: sería el mismo
# tono que F# y provocaría duplicados en el selector.
PREFERRED_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']

# Escala mayor y menor natural, en grados semitónicos desde la tónica.
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# Catálogo de tonos que consume el selector del frontend (12 mayores + 12 menores).
ALL_KEYS = [PREFERRED_NAMES[i] for i in range(12)] + [
    f"{PREFERRED_NAMES[i]}m" for i in range(12)
]


def _accidental_to_semitones(letter: str, accidental: str) -> int:
    """Semitonos absolutos de una tónica escrita como letra + alteración."""
    value = NOTE_SEMITONES[letter.upper()]
    if accidental == '#':
        value += 1
    elif accidental == 'b':
        value -= 1
    return value % 12


def spell_pitch(pitch_class: int, prefer_flats: bool = True) -> str:
    """
    Pasa una clase semitónica (0..11) al nombre que se va a mostrar.

    Por defecto usa bemoles (Db, Eb, Gb, Ab, Bb), que es como se escribe en
    guitarra y en los cancioneros. Se pasa prefer_flats=False solo cuando el
    material original ya usaba sostenidos, para no convertir un F# en un Gb.
    """
    names = FLAT_NAMES if prefer_flats else SHARP_NAMES
    return names[pitch_class % 12]


def parse_key(text: Optional[str]) -> Optional[Dict[str, object]]:
    """
    Parsea un tono escrito por el usuario ('am', 'C#m', 'Bb ', 'F#') y
    devuelve su clase semitónica y si es menor. None si no es un tono válido.
    """
    if not text:
        return None

    match = re.match(r"^\s*([A-Ga-g])\s*([#b]?)\s*(m|min|M|maj)?\s*$", str(text))
    if not match:
        return None

    letter, accidental, suffix = match.groups()
    is_minor = suffix in ('m', 'min')
    # 'M' y 'maj' son mayor explícito, pero el default de todos modos es mayor.
    return {
        'pitch_class': _accidental_to_semitones(letter, accidental),
        'mode': 'minor' if is_minor else 'major',
        'is_minor': is_minor,
    }


def normalize_key(text: Optional[str]) -> Optional[str]:
    """
    Deja el tono canonizado como lo muestra el sistema ('Am', 'F#m', 'Bb').
    Se usa al guardar Song.key para que transponer no dependa de cómo lo
    escribió el usuario en el formulario.

    Respeta la alteración que escribió el usuario: si pidió 'f#m' se guarda
    'F#m' y no 'Gbm'. Solo cuando no dijo nada (escribió 'C' o 'Am') se usa el
    nombre canónico del catálogo.
    """
    parsed = parse_key(text)
    if not parsed:
        return None

    pitch = int(parsed['pitch_class'])
    prefer_flats = _key_spelling_preference(str(text).strip())
    name = (
        PREFERRED_NAMES[pitch]
        if prefer_flats is None
        else spell_pitch(pitch, prefer_flats=prefer_flats)
    )

    return f"{name}m" if parsed['is_minor'] else name


def semitones_between(source_key: Optional[str], target_key: str) -> Optional[int]:
    """
    Distancia en semitonos para ir de un tono a otro. El sentido lo decide el
    tono destino, así que devuelve el valor con signo que hay que sumar a cada
    acorde (de Am a F son +8, de C a B son +11).

    Si el origen no se puede interpretar devuelve None en vez de 0: asumir que
    el material ya está en el tono pedido devolvería una canción sin tocar
    haciéndola pasar por transpuesta con éxito.
    """
    source = parse_key(source_key)
    target = parse_key(target_key)

    if not target or not source:
        return None

    return (int(target['pitch_class']) - int(source['pitch_class'])) % 12


# ==========================================
# 2. PARSER DE ACORDES
# ==========================================

# Cualquier palabra o acorde candidato: letras, dígitos, #'s, bemoles y la barra
# del acorde con bajo. El corchete de [Am] queda afuera y se conserva intacto.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9#b/+-]*")

# Grupo de corchetes: es la notación que ya usa ChordStructureViewer, y la
# única forma de marcar un acorde sin ambigüedad dentro de una frase.
_BRACKET_RE = re.compile(r"\[([^\]\n]*)\]")

# Lo que puede ir detrás de la tónica para que el token sea un acorde de verdad.
# Solo admite abreviaturas conocidas y dígitos, así que cualquier palabra que
# empieza con A-G pero no es acorde (Amor, Bad, Fade, Canción) se descarta.
_CHORD_TAIL_RE = re.compile(
    r"^(?:maj|min|m|M|dim|aug|sus|add|alt|no|omit)?"
    r"\d{0,2}"
    r"(?:[#b+-]\d{1,2}|(?:add|sus)\d{1,2})?$"
)

_ROOT_RE = re.compile(r"^([A-Ga-g])([#b]?)(.*)$")


def _parse_chord(token: str) -> Optional[Dict[str, object]]:
    """
    Descompone un acorde en {'root', 'quality', 'number', 'slash'} o devuelve
    None si el token no es un acorde. Acepta marcadores como Bbm7, F#sus4,
    Cadd9, D7/E y G/B.
    """
    if not token:
        return None

    slash_root = None
    slash_quality = ''
    body = token

    # acorde con bajo: nos deshacemos de la parte tras la barra
    if '/' in body:
        parts = body.split('/')
        if len(parts) != 2:
            return None
        body, slash_part = parts

        slash_match = _ROOT_RE.match(slash_part)
        if not slash_match:
            return None
        slash_letter, slash_accidental, slash_tail = slash_match.groups()
        if slash_tail and not _CHORD_TAIL_RE.match(slash_tail):
            return None
        slash_root = f"{slash_letter.upper()}{slash_accidental}"
        slash_quality = slash_tail

    match = _ROOT_RE.match(body)
    if not match:
        return None

    letter, accidental, tail = match.groups()

    # "Cm" sí es acorde, pero "Mor" no: el resto tiene que ser cola válida.
    if tail and not _CHORD_TAIL_RE.match(tail):
        return None

    quality, number = '', ''
    if tail:
        quality_match = re.match(r"^(maj|min|m|M|dim|aug|sus|add|alt|no|omit)", tail)
        if quality_match:
            quality = quality_match.group(1)
            number = tail[len(quality):]
        else:
            number = tail

    return {
        'root': f"{letter.upper()}{accidental}",
        'quality': quality,
        'number': number,
        'slash': slash_root,
        'slash_quality': slash_quality,
        'prefers_flats': accidental == 'b',
        # Un acorde "pelado" (solo la tónica, sin m, sin 7, sin sus) es el
        # ambiguo: se parece demasiado a una letra suelta como la "a" del
        # español o la "a" inglesa, así que exige un contexto más estricto.
        'has_suffix': bool(tail),
        # ...salvo que lleve alteración (Bb, F#) o nota de bajo (C/E), que ya
        # no se pueden confundir con ninguna palabra.
        'has_accidental': bool(accidental),
    }


def _is_marked_chord(chord: Dict[str, object]) -> bool:
    """¿El acorde es inconfundible aunque no venga entre corchetes?"""
    return bool(chord['has_suffix'] or chord['has_accidental'] or chord['slash'])



def transpose_chord(chord: Dict[str, object], semitones: int, prefer_flats: bool) -> str:
    """Reconstruye un acorde movido 'semitones' semitonos, sin tocar la letra."""
    flats = bool(chord['prefers_flats']) or prefer_flats

    root_match = _ROOT_RE.match(str(chord['root']))
    root_pc = _accidental_to_semitones(root_match.group(1), root_match.group(2))
    new_root = spell_pitch((root_pc + semitones) % 12, flats)

    result = f"{new_root}{chord['quality']}{chord['number']}"

    if chord['slash']:
        slash_match = _ROOT_RE.match(str(chord['slash']))
        slash_pc = _accidental_to_semitones(slash_match.group(1), slash_match.group(2))
        result += f"/{spell_pitch((slash_pc + semitones) % 12, flats)}{chord['slash_quality']}"

    return result


def _text_prefers_flats(text: str) -> bool:
    """
    Qué convención de grafía sigue el material, por mayoría de uso.

    Cuenta cuántas apariciones de sostenidos y bemoles hay en los acordes y gana
    la mayoritaria. Importa que sea por mayoría y no "si aparece uno": un único
    [G#dim] suelto no puede empujar a bemoles toda una canción escrita en C.
    """
    sharps = len(re.findall(r"\b[A-Ga-g]#", text or ""))
    flats = len(re.findall(r"\b[A-Ga-g]b", text or ""))
    return flats >= sharps


def _key_spelling_preference(key_name: Optional[str]) -> Optional[bool]:
    """
    Grafía que impone un tono escrito: False para sostenidos, True para bemoles.

    Devuelve None cuando el tono no trae alteración (C, G, Am, F...), porque en
    ese caso no manda sobre la convención que ya usa la letra.
    """
    if not key_name:
        return None
    if '#' in key_name:
        return False
    if 'b' in key_name:
        return True
    return None


def _is_chord_line(line: str) -> bool:
    """
    ¿Esta línea es una fila de acordes pelados (las de un chord sheet)?

    Tiene que ser SÍ o SÍ una lista de acordes: si hay una sola palabra de la
    letra en la línea, no la tocamos. Esa es la regla que evita que el artículo
    "a" del español o la "A" de "A thousand miles" se conviertan en un acorde.
    """
    tokens = _TOKEN_RE.findall(line)
    if not tokens:
        return False

    parsed = [_parse_chord(token) for token in tokens]
    if any(chord is None for chord in parsed):
        return False

    # Una fila real tiene varios acordes. Con uno solo, tiene que ser
    # inconfundible (Bb, F#sus4, C/E), nunca una tónica pelada que podría ser
    # una palabra suelta.
    if len(parsed) >= 2:
        return True

    return _is_marked_chord(parsed[0])


def _transpose_segment(segment: str, semitones: int, prefer_flats: bool,
                       counter: List[int]) -> str:
    """
    Transpone los acordes de un fragmento y deja el resto de caracteres igual.
    El llamador ya decidió que el fragmento es musical (corchetes o fila de
    acordes), así que acá todo acorde se mueve, incluso los pelados.
    """
    def _replace(match):
        parsed = _parse_chord(match.group(0))
        if not parsed:
            return match.group(0)

        replacement = transpose_chord(parsed, semitones, prefer_flats)
        # Reescribir "Am" como "Am" no es haber movido nada: solo cuenta si el
        # nombre del acorde cambia de verdad.
        if replacement != match.group(0):
            counter[0] += 1

        return replacement

    return _TOKEN_RE.sub(_replace, segment)


def _transpose_line(line: str, semitones: int, prefer_flats: bool,
                    counter: List[int]) -> str:
    """Aplica las dos notaciones que usa el proyecto: [Acorde] y fila de acordes."""
    def _bracket(match):
        return f"[{_transpose_segment(match.group(1), semitones, prefer_flats, counter)}]"

    result = _BRACKET_RE.sub(_bracket, line)

    # Si la línea ya traía notación con corchetes, solo contaban esos acordes.
    if _BRACKET_RE.search(result):
        return result

    if not _is_chord_line(result):
        return result

    return _transpose_segment(result, semitones, prefer_flats, counter)


def transpose_text(
    text: str,
    semitones: int,
    prefer_flats: Optional[bool] = None
) -> Tuple[str, int]:
    """
    Transpone todos los acordes de un texto y devuelve (texto nuevo, cuántos
    acordes se movieron). Si no hay ni un acorde, el texto vuelve idéntico.

    prefer_flats=None deja que se decida por la convención del propio texto;
    True o False forzados se respetan siempre (sirve para cuando quien llama ya
    sabe qué tono pidió el usuario).

    Solo cuenta como movido un acorde que realmente cambió de nombre, así que
    transponer al mismo tono devuelve 0 y no el número de acordes de la letra.
    """
    if not text:
        return text, 0

    flats = _text_prefers_flats(text) if prefer_flats is None else prefer_flats
    counter = [0]

    result = "\n".join(
        _transpose_line(line, semitones, flats, counter)
        for line in text.split("\n")
    )

    return result, counter[0]


# ==========================================
# 3. DETECCIÓN DEL TONO DE LA CANCIÓN
# ==========================================

def iter_chords(text: str) -> List[Dict[str, object]]:
    """
    Lista de acordes reales de un texto, aplicando exactamente las mismas reglas
    de contexto que la transposición: la notación [Acorde] marca todo lo que
    hay dentro, y fuera de ella solo cuentan las filas que son puramente
    acordes. Así la detección de tono nunca confunde una palabra con un acorde.
    """
    found: List[Dict[str, object]] = []

    for line in (text or "").split("\n"):
        brackets = list(_BRACKET_RE.finditer(line))

        # Con notación de corchetes solo importan los acordes entre corchetes.
        if brackets:
            for match in brackets:
                for token_match in _TOKEN_RE.finditer(match.group(1)):
                    chord = _parse_chord(token_match.group(0))
                    if chord:
                        found.append(chord)
            continue

        if not _is_chord_line(line):
            continue

        for token_match in _TOKEN_RE.finditer(line):
            chord = _parse_chord(token_match.group(0))
            if chord:
                found.append(chord)

    return found


def collect_chords(structure) -> List[Dict[str, object]]:
    """Extrae todos los acordes de la estructura, con su clase semitónica."""
    parsed = _as_structure(structure)

    chords = []
    for part in parsed.get('parts') or []:
        chords.extend(iter_chords(part.get('content') or ''))

    return chords


def _chord_third_pc(root_pc: int, quality: str) -> Optional[int]:
    """
    Clase semitónica de la tercera del acorde, o None si no se puede saber
    (suspendidas y similares). Es lo que separa un Am de un A: los dos tienen la
    misma raíz, así que sin la tercera la detección no puede distinguirlos.
    """
    if quality in ('sus', 'alt'):
        return None

    if quality in ('m', 'min', 'dim'):
        return (root_pc + 3) % 12

    # Mayor, aumentada, 'add9', dom7, 'no3'... todas llevan tercera mayor.
    return (root_pc + 4) % 12


def _has_seventh(chord: Dict[str, object]) -> bool:
    """
    Si el acorde trae una séptima explícita (7, m7, 7sus4, 7b5, maj7...).

    Importa para detectar el tono menor: un E7 sí delata un Am porque lleva el
    G# de la séptima elevada, pero un E pelado es simplemente el I de Do mayor y
    no debe contarse como dominante prestada.
    """
    return '7' in str(chord.get('number') or '')


def detect_key(structure) -> Optional[str]:
    """
    Deduce el tono de una estructura cuando Song.key viene vacío, puntuando los
    24 tonos posibles.

    Un acorde solo suma si TODOS sus grados conocidos están en la escala: por eso
    Am no "cabe" en F mayor (su tercera, Do, sí encaja, pero el Mi no), aunque la
    raíz A sí.

    La tónica pesa más que cualquier otro grado, y un poco menos si el acorde no
    tiene la calidad del tono (un menor en la tónica de un mayor es prestado). En
    empate se queda con el mayor, que es como se rotula casi todo el cancionero.

    OJO con el par relativo (Do mayor / La menor): comparten los mismos acordes,
    así que sin un acorde que los separe la respuesta es ambigua y gana el mayor
    por el desempate. Para separar Do mayor de La menor hace falta el Mi (o el
    E7, que además lleva la séptima elevada del menor).
    """
    chords = collect_chords(structure)
    if not chords:
        return None

    entries: List[Tuple[int, bool, Optional[int], bool]] = []
    for chord in chords:
        root_match = _ROOT_RE.match(str(chord['root']))
        root_pc = _accidental_to_semitones(root_match.group(1), root_match.group(2))
        quality = str(chord['quality'])
        entries.append((
            root_pc,
            quality in ('m', 'min'),
            _chord_third_pc(root_pc, quality),
            _has_seventh(chord)
        ))

        # La nota de bajo también tiene que pertenecer a la escala.
        if chord['slash']:
            slash_match = _ROOT_RE.match(str(chord['slash']))
            entries.append((
                _accidental_to_semitones(slash_match.group(1), slash_match.group(2)),
                False,
                None,
                False
            ))

    best_key, best_score, best_is_minor = None, -1, False

    for tonic in range(12):
        for scale, is_minor in ((MAJOR_SCALE, False), (MINOR_SCALE, True)):
            score = 0
            for root_pc, chord_is_minor, third_pc, has_seventh in entries:
                degree = (root_pc - tonic) % 12
                if degree not in scale:
                    continue

                if third_pc is not None:
                    third_degree = (third_pc - tonic) % 12
                    if third_degree in scale:
                        pass
                    elif is_minor and third_degree == 11 and has_seventh:
                        # Séptima elevada: el E7 de Am lleva el G# que no está en
                        # la escala natural, y es justamente lo que lo delata.
                        pass
                    else:
                        continue         # el acorde no pertenece a este tono

                if degree == 0:
                    # La tónica manda, y pesa un poco menos si el acorde no
                    # tiene la calidad del tono (prestado, no definitorio).
                    score += 3 if chord_is_minor == is_minor else 2
                elif degree == 7:
                    score += 2          # dominante
                else:
                    score += 1

            if score > best_score or (score == best_score and not is_minor and best_is_minor):
                best_score = score
                best_is_minor = is_minor
                best_key = f"{PREFERRED_NAMES[tonic]}m" if is_minor else PREFERRED_NAMES[tonic]

    return best_key if best_score > 0 else None


def _circle_position(pitch_class: int, prefer_flats: bool) -> Optional[int]:
    """
    Posición de una tónica en la rueda de quintas: 0 es Do, +1 es Sol, +2 es
    Re... y en negativo bajamos hacia Gb y Db. Ese número es exactamente la
    cantidad de sostenidos (si es positivo) o bemoles (si es negativo) que
    lleva la armadura de ese tono.
    """
    pitch_class %= 12

    # Primer intento: respetar la grafía pedida, para que F# y Db no se mezclen.
    for fifths in range(-7, 8):
        if (fifths * 7) % 12 == pitch_class and (fifths < 0) == prefer_flats:
            return fifths

    for fifths in range(-7, 8):
        if (fifths * 7) % 12 == pitch_class:
            return fifths

    return None


def signature_fifths(key: Optional[str]) -> Optional[int]:
    """
    Cantidad de alteraciones de la armadura de un tono (sostenidos positivos,
    bemoles negativos). Una menor comparte armadura con su mayor relativo, que
    está tres semitonos más arriba.
    """
    parsed = parse_key(key)
    if not parsed:
        return None

    pitch_class = int(parsed['pitch_class'])
    if parsed['is_minor']:
        pitch_class = (pitch_class + 3) % 12

    return _circle_position(pitch_class, prefer_flats=not re.search(r"#", str(key)))


def capo_suggestion(key: Optional[str]) -> Optional[int]:
    """
    Si el tono pedido tiene demasiadas alteraciones para tocarlo cómodo de
    posición, sugerimos un capo: capotear en la cejilla N es lo mismo que
    tocar N semitonos más abajo. Devuelve None cuando no hace falta.

    Ej: F#m tiene tres sostenidos, así que rinde más tocar Fa menor con capo en
    la primera cejilla que Epidem la forma de F#m directo.
    """
    fifths = signature_fifths(key)
    if fifths is None or abs(fifths) <= 2:
        return None

    return min(3, abs(fifths) - 2)



# ==========================================
# 4. API PÚBLICA DEL PROCESO
# ==========================================

def _as_structure(structure) -> Dict[str, object]:
    """
    Normaliza la estructura a dict. SQLite y algunos drivers devuelven la
    columna JSON como string, así que la desempaquetamos acá en vez de
    obligar a que cada llamador se acuerde.
    """
    if not structure:
        return {}
    if isinstance(structure, str):
        try:
            structure = json.loads(structure)
        except (ValueError, TypeError):
            return {}
    if not isinstance(structure, dict):
        return {}
    return structure


def transpose_structure(
    structure,
    target_key: str,
    source_key: Optional[str] = None
) -> Dict[str, object]:
    """
    Proceso completo: dada la estructura de una canción y el tono pedido,
    devuelve la estructura transpuesta con el mismo formato que la base.

    Respuesta:
        {
            "structure": {...},
            "source_key": "Am",
            "target_key": "C",
            "detected_key": "Am",
            "semitones": 3,
            "capo": None,
            "chords_changed": 14,
        }
    """
    parsed_structure = _as_structure(structure)

    # Si la canción no declara tono, lo deducimos de los acordes que tiene.
    effective_source = source_key or detect_key(parsed_structure)
    target = normalize_key(target_key)

    # El tono de origen se valida aparte: si no, el error acaba señalando al
    # destino y deja al usuario buscando un fallo que no tiene.
    if source_key and not parse_key(source_key):
        return {
            'success': False,
            'error': f"'{source_key}' no es un tono válido. Ejemplos válidos: Am, C, F#m, Bb.",
        }

    semitones = semitones_between(effective_source, target)
    if semitones is None:
        if not target:
            return {
                'success': False,
                'error': f"'{target_key}' no es un tono válido. Ejemplos válidos: Am, C, F#m, Bb.",
            }
        if not effective_source:
            return {
                'success': False,
                'error': (
                    "No se pudo deducir el tono de la canción. "
                    "Indicá el tono original o revisá los acordes."
                ),
            }

    if not parsed_structure:
        return {
            'success': False,
            'error': 'La canción no tiene una estructura con partes para transponer.',
        }

    # La grafía la manda el tono pedido: si piden F# no vamos a contestar Gb.
    # Si el tono no trae alteración, manda la convención que ya usa la letra.
    full_text = " ".join(
        (part.get('content') or '') for part in parsed_structure.get('parts') or []
    )
    prefer_flats = _key_spelling_preference(target)
    if prefer_flats is None:
        prefer_flats = _text_prefers_flats(full_text)

    new_parts, changed_total = [], 0
    for part in parsed_structure.get('parts') or []:
        new_content, changed = transpose_text(part.get('content') or '', semitones, prefer_flats)
        changed_total += changed
        new_part = dict(part)
        new_part['content'] = new_content
        new_parts.append(new_part)

    return {
        'success': True,
        'structure': {'parts': new_parts},
        'source_key': normalize_key(effective_source),
        'detected_key': detect_key(parsed_structure),
        'target_key': target,
        'semitones': semitones,
        'capo': capo_suggestion(target),
        'chords_changed': changed_total,
        'unchanged': semitones == 0,
    }
