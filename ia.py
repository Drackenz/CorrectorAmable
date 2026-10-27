import json
import os
import re
from pathlib import Path

import httpx

# Modelo de Gemini usado por texto, imágenes y audio entrante.
MODELO = "gemini-3.6-flash"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent"
ARCHIVO_ENV = Path(__file__).with_name(".env")


def obtener_clave():
    """Busca la clave de Gemini en el entorno y luego en .env, sin mostrarla."""
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        clave = os.getenv(variable, "").strip()
        if clave:
            return clave.strip('"').strip("'")

    if ARCHIVO_ENV.is_file():
        try:
            for linea in ARCHIVO_ENV.read_text(encoding="utf-8-sig").splitlines():
                linea = linea.strip()
                if not linea or linea.startswith("#"):
                    continue
                if linea.lower().startswith("export "):
                    linea = linea[7:].strip()
                nombre, separador, valor = linea.partition("=")
                if separador and nombre.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    valor = valor.strip().strip('"').strip("'")
                    if valor and not valor.lower().startswith(("pega_", "tu_", "your_")):
                        return valor
        except OSError as ex:
            raise RuntimeError("No pude leer el archivo .env local.") from ex

    return ""


def _error_http(status, respuesta=None):
    if status in (401, 403):
        return "Gemini rechazó la clave. Verifica que sea una clave válida y que permita usar Gemini API."
    if status == 400:
        return "Gemini rechazó la solicitud. Revisa el texto, el archivo y la configuración del modelo."
    if status == 404:
        return f"No se encontró el modelo {MODELO}. Revisa el nombre del modelo en ia.py."
    if status == 429:
        detalle = ""
        if respuesta is not None:
            try:
                detalle = respuesta.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                pass
        espera = re.search(r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)s", detalle, re.IGNORECASE)
        if espera:
            segundos = max(1, int(float(espera.group(1)) + 0.99))
            return f"Gemini pide esperar unos {segundos} segundos por límite de solicitudes. Luego vuelve a intentar."
        detalle_lower = detalle.lower()
        if "per_day" in detalle_lower or "per day" in detalle_lower or "daily" in detalle_lower:
            return "Se agotó la cuota diaria de Gemini para esta cuenta. Revisa Usage & Billing en Google AI Studio y espera el reinicio de cuota."
        return "Gemini alcanzó un límite de solicitudes o cuota (429). Revisa Usage & Billing en Google AI Studio; si es límite por minuto, espera antes de reintentar."
    return f"Gemini no pudo completar la solicitud (error {status})."


def _pedir_gemini(instruccion, texto, como_json=False):
    clave = obtener_clave()
    if not clave:
        raise RuntimeError(
            "Falta GEMINI_API_KEY (o GOOGLE_API_KEY). En tu computadora crea un archivo .env "
            "junto a ia.py; en Hugging Face configúrala en Settings > Secrets."
        )

    configuracion = {"responseMimeType": "application/json"} if como_json else {}
    try:
        respuesta = httpx.post(
            URL,
            timeout=60,
            headers={"x-goog-api-key": clave},
            json={
                "contents": [{"parts": [{"text": instruccion + "\n\nTexto: " + texto}]}],
                "generationConfig": configuracion,
            },
        )
    except httpx.TimeoutException as ex:
        raise RuntimeError("Gemini tardó demasiado en responder. Inténtalo de nuevo.") from ex
    except httpx.HTTPError as ex:
        raise RuntimeError("No pude conectar con Gemini. Revisa tu conexión a internet.") from ex

    if respuesta.status_code != 200:
        raise RuntimeError(_error_http(respuesta.status_code, respuesta))

    try:
        contenido = respuesta.json()["candidates"][0]["content"]["parts"]
        resultado = next(parte["text"] for parte in contenido if "text" in parte).strip()
    except (ValueError, KeyError, IndexError, TypeError, StopIteration) as ex:
        raise RuntimeError("Gemini devolvió una respuesta vacía o con un formato inesperado.") from ex

    if como_json:
        cerca = chr(96) * 3
        if resultado.startswith(cerca + "json"):
            resultado = resultado[len(cerca + "json"):]
        elif resultado.startswith(cerca):
            resultado = resultado[len(cerca):]
        if resultado.endswith(cerca):
            resultado = resultado[:-len(cerca)]
        try:
            return json.loads(resultado.strip())
        except json.JSONDecodeError as ex:
            raise RuntimeError("Gemini no devolvió un JSON válido. Inténtalo otra vez.") from ex
    return resultado


def ia(instruccion, texto, como_json=False):
    """Envía texto a Gemini. Devuelve texto o un diccionario JSON."""
    return _pedir_gemini(instruccion, texto, como_json)
