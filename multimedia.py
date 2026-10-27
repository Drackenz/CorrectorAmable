import os, io, wave, base64, json, httpx
import importlib
import ia
ia = importlib.reload(ia)
URL = ia.URL
obtener_clave = ia.obtener_clave


def pcm_a_wav(pcm, tasa=16000, canales=1):
    """Convierte audio PCM de 16 bits en WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(2)
        w.setframerate(tasa)
        w.writeframes(pcm)
    return buf.getvalue()


def ia_media(instruccion, datos, mime, texto="", como_json=False):
    """Envía texto junto a un archivo de imagen o audio a Gemini."""
    clave = obtener_clave()
    if not clave:
        raise RuntimeError("Falta GEMINI_API_KEY. En local usa un .env junto a ia.py; en el Space, guárdala en Settings > Secrets.")
    if not datos:
        raise RuntimeError("El archivo está vacío. Intenta grabar o seleccionar otro archivo.")
    if len(datos) > 15_000_000:
        raise RuntimeError("El archivo es muy grande (máximo 15 MB). Usa uno más corto.")
    pedido = instruccion + ("\n\nTexto: " + texto if texto else "")
    partes = [{"text": pedido},
              {"inline_data": {"mime_type": mime, "data": base64.b64encode(datos).decode("ascii")}}]
    cfg = {"responseMimeType": "application/json"} if como_json else {}
    try:
        r = httpx.post(URL, timeout=60, headers={"x-goog-api-key": clave},
                       json={"contents": [{"parts": partes}], "generationConfig": cfg})
    except httpx.TimeoutException as ex:
        raise RuntimeError("Gemini tardó demasiado en procesar el audio o la imagen. Intenta con una grabación más corta.") from ex
    except httpx.HTTPError as ex:
        raise RuntimeError("No pude conectar con Gemini para procesar el audio o la imagen. Revisa internet e inténtalo otra vez.") from ex
    if r.status_code in (401, 403):
        raise RuntimeError("Gemini rechazó la clave. Revisa la configuración de GEMINI_API_KEY.")
    if r.status_code == 400:
        raise RuntimeError(f"Gemini rechazó el archivo multimedia (error 400). Revisa el formato y el tamaño; el audio se envió como {mime}.")
    if r.status_code == 404:
        raise RuntimeError(f"Gemini no encontró el modelo configurado ({ia.MODELO}).")
    if r.status_code == 429:
        raise RuntimeError(ia._error_http(429, r))
    if r.status_code >= 500:
        raise RuntimeError(f"Gemini está temporalmente ocupado (error {r.status_code}). Inténtalo más tarde.")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini rechazó la solicitud multimedia (error {r.status_code}).")
    try:
        t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError) as ex:
        raise RuntimeError("Gemini recibió el archivo, pero no devolvió una transcripción o respuesta legible.") from ex
    if como_json:
        cerca = chr(96) * 3
        if t.startswith(cerca + "json"):
            t = t[len(cerca + "json"):]
        elif t.startswith(cerca):
            t = t[len(cerca):]
        if t.endswith(cerca):
            t = t[:-len(cerca)]
        try:
            return json.loads(t.strip())
        except json.JSONDecodeError as ex:
            raise RuntimeError("Gemini devolvió una respuesta con formato inválido.") from ex
    return t


def ia_audio(texto):
    """Genera un WAV de voz a partir de texto usando Gemini TTS."""
    clave = obtener_clave()
    if not clave:
        raise RuntimeError("Falta GEMINI_API_KEY. En local usa un .env junto a ia.py; en el Space, guárdala en Settings > Secrets.")
    if not texto or not texto.strip():
        raise RuntimeError("No hay texto para leer en voz alta.")
    url_tts = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent"
    try:
        r = httpx.post(
            url_tts,
            timeout=60,
            headers={"x-goog-api-key": clave},
            json={
                "contents": [{"role": "user", "parts": [{"text": texto.strip()}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}
                    },
                },
            },
        )
    except httpx.TimeoutException as ex:
        raise RuntimeError("Gemini tardó demasiado en generar la voz. Inténtalo otra vez.") from ex
    except httpx.HTTPError as ex:
        raise RuntimeError("No pude conectar con Gemini para generar la voz. Revisa internet.") from ex
    if r.status_code in (401, 403):
        raise RuntimeError("Gemini rechazó la clave al generar la voz. Revisa GEMINI_API_KEY.")
    if r.status_code == 429:
        raise RuntimeError(ia._error_http(429, r))
    if r.status_code >= 500:
        raise RuntimeError(f"El servicio de voz está temporalmente ocupado (error {r.status_code}).")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini no pudo generar la voz (error {r.status_code}).")
    try:
        audio = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        datos = base64.b64decode(audio)
        if not datos.startswith(b"RIFF"):
            raise RuntimeError("Gemini devolvió audio en un formato inesperado.")
        return datos
    except RuntimeError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as ex:
        raise RuntimeError("La voz no devolvió un audio WAV válido.") from ex
