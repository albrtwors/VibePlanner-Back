# services/file_ingest_service.py
"""
Ingesta masiva de canciones desde un archivo (.txt / .pdf / .docx).

Este servicio SOLO extrae texto y propone un corte en canciones. No toca la
base de datos ni genera PDF: el front manda luego los datos ya revisados por
el usuario y el backend los persiste con los permisos normales.

Nota de despliegue: el parseo real del PDF/DOCX se hace en el FRONT
(pdfjs-dist + mammoth) para no sumar dependencias binarias al backend de
Vercel. Las librerías de Python quedan como respaldo opcional para .txt y para
el caso de que el navegador no pueda leer el archivo.
"""
import os
import re
from typing import Any, Dict, List

try:  # Opcionales: si faltan, el front ya mandó el texto parseado.
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

try:
    from docx import Document
except Exception:  # pragma: no cover
    Document = None

try:
    import chardet
except Exception:  # pragma: no cover
    chardet = None

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB
ALLOWED_EXTENSIONS = ("txt", "pdf", "docx")


def _detect_encoding(data: bytes) -> str:
    if chardet is None:
        return "utf-8"
    try:
        enc = (chardet.detect(data) or {}).get("encoding") or "utf-8"
        return enc if enc.lower() in ("ascii", "utf-8", "latin-1", "cp1252") else "utf-8"
    except Exception:
        return "utf-8"


def extract_text_from_file(file_path: str, original_filename: str = "") -> str:
    """Saca texto plano de un .txt, .pdf o .docx guardado en disco."""
    fname = original_filename or os.path.basename(file_path)
    ext = fname.lower().rsplit(".", 1)[-1] if "." in fname else ""

    if ext == "pdf":
        if PdfReader is None:
            raise ValueError("El servidor no puede leer PDFs; parsealo en el navegador.")
        parts = []
        for page in PdfReader(file_path).pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n\n".join(parts).strip()

    if ext == "docx":
        if Document is None:
            raise ValueError("El servidor no puede leer DOCX; parsealo en el navegador.")
        doc = Document(file_path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    parts.extend(p.text for p in cell.paragraphs)
        return "\n".join(parts).strip()

    with open(file_path, "rb") as fh:
        data = fh.read()
    return data.decode(_detect_encoding(data), errors="replace").strip()


# ---------------------------------------------------------------------------
# Corte del texto en canciones
# ---------------------------------------------------------------------------

# Línea que puede ser título: corta y con mayúscula inicial.
_TITULO_RE = re.compile(r"^[A-ZÁÉÍÓÚÑÜ0-9][A-Za-zÁÉÍÓÚÑÜáéíóúñ0-9\s'’\-\(\)\.\,!¡\?¿]{1,70}$")
# Línea que es solo acordes (para no tomarla por título).
_SOLO_ACORDES_RE = re.compile(
    r"^[\[\(\{]?\s*(?:[A-Ga-g][#b]?m?(?:maj\d*|sus\d*|add\d*|dim\d*)?|[0-9]{1,2})\s*[\]\)\}]?"
    r"(\s*[,/]\s*)*$"
)
# Separadores explícitos entre canciones.
_SEPARADOR_RE = re.compile(
    r"^[ \t]*(?:[-=_~]{3,}|\*{3,})[ \t]*$"
    r"|^[ \t]*(?:canci[oó]n|tema|track|blusa)[ \t]*n?[º°o\.]?[ \t]*\d+[ \t]*$"
    r"|^[ \t]*\d{1,2}[.)][ \t]?(?=[A-ZÁÉÍÓÚÑ0-9]|$)",
    re.IGNORECASE | re.MULTILINE,
)

# "## Cancion" / "# Cancion": el marcador va pegado al título, no en su propia línea.
_MARCADOR_TITULO_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*(?=\S)")


# "Tono: Am", "Key: Am", "Tonalidad: Am", "Autor: X", "Letra y música: X"
_META_RE = re.compile(
    r"^(?:tono|key|tonalidad|autor|artista|letra(?:\s+y\s*m[uú]sica)?|m[uú]sica)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)
_TONO_RE = re.compile(r"^(?:[A-G][#b]?m?(?:maj\d*|sus\d*|add\d*|dim\d*)?|m|min)\s*$", re.IGNORECASE)


def _metadatos(lineas):
    """Saca tono y autor de las primeras líneas de la canción, si están."""
    autor = None
    tono = None
    for l in lineas[:5]:
        m = _META_RE.match(l.strip())
        if not m:
            continue
        etiqueta = l.split(":", 1)[0].strip().lower()
        valor = m.group(1).strip()
        if not valor:
            continue

        es_tono = "tono" in etiqueta or "key" in etiqueta or "tonalidad" in etiqueta
        if es_tono:
            if tono is None and _TONO_RE.match(valor):
                tono = valor
        elif autor is None:
            autor = valor

    return autor, tono


def _es_titulo(linea: str) -> bool:
    """True si la línea tiene pinta de título de canción."""
    s = _MARCADOR_TITULO_RE.sub("", linea.strip())
    if not s or len(s) < 2:
        return False
    if _SOLO_ACORDES_RE.match(s):
        return False
    return bool(_TITULO_RE.match(s))


def split_songs_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """
    Divide el texto en bloques de canción.

    Estrategia, en orden de confianza:
      1. Separadores explícitos (---, ===, "Canción 2", "1.", ##).
      2. Salto doble seguido de una línea con pinta de título.
      3. Cualquier salto doble (último recurso, puede partir de más).
    """
    if not raw_text or not raw_text.strip():
        return []

    text = raw_text.replace("\f", "\n").replace("\r\n", "\n").replace("\r", "\n")

    bloques = _SEPARADOR_RE.split(text)
    if len(bloques) <= 1:
        lineas = text.split("\n")
        cortes: List[int] = []
        vacias = 0
        for i, l in enumerate(lineas):
            # Marcador explícito pegado al título ("## Mi canción"): corte seguro.
            if i > 0 and _MARCADOR_TITULO_RE.match(l) and _es_titulo(l):
                cortes.append(i)
                vacias = 0
                continue
            if not l.strip():
                vacias += 1
                continue
            # Salto doble y la línea siguiente parece título: cortamos antes.
            # Con un solo salto NO cortamos, porque así son las estrofas de una letra.
            if vacias >= 2 and _es_titulo(l):
                cortes.append(i)
            vacias = 0
        if cortes:
            bloques = []
            prev = 0
            for c in cortes:
                bloques.append("\n".join(lineas[prev:c]))
                prev = c
            bloques.append("\n".join(lineas[prev:]))
    resultado: List[Dict[str, Any]] = []
    for bloque in bloques:
        lineas = [l.rstrip() for l in bloque.split("\n")]
        while lineas and not lineas[0].strip():
            lineas.pop(0)
        while lineas and not lineas[-1].strip():
            lineas.pop()
        if not lineas:
            continue

        nombre = f"Canción {len(resultado) + 1}"
        cuerpo = lineas
        # "## Mi canción" -> el marcador se corre y el título queda limpio.
        primera = _MARCADOR_TITULO_RE.sub("", lineas[0]).strip()
        if _es_titulo(primera):
            nombre = primera
            cuerpo = lineas[1:]
        elif _es_titulo(lineas[0]):
            nombre = lineas[0].strip()
            cuerpo = lineas[1:]

        # Metadatos que suelen venir arriba: "Tono: Am", "Autor: X".
        autor, tono = _metadatos(lineas)
        # Esas líneas de metadato no forman parte de la letra.
        cuerpo_limpio = [l for l in cuerpo if not _META_RE.match(l.strip())]

        texto_cuerpo = "\n".join(cuerpo_limpio).strip() or "\n".join(cuerpo).strip()
        resultado.append({
            "name": nombre,
            "author": autor,
            "genre": None,
            "key": tono,
            "structure": {"parts": [{"title": "letra", "content": texto_cuerpo}]},
            "raw_text": "\n".join(lineas).strip(),
        })

    return resultado or [{
        "name": "Canción 1",
        "author": None,
        "genre": None,
        "key": None,
        "structure": {"parts": [{"title": "letra", "content": text.strip()}]},
        "raw_text": text.strip(),
    }]
