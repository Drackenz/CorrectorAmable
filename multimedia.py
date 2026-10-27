import os, io, wave, base64, json, httpx
from ia import URL

def pcm_a_wav(pcm, tasa=16000, canales=1):
    """Convierte audio crudo PCM de 16 bits (lo que entrega la grabadora) en un archivo WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(2)
        w.setframerate(tasa)
        w.writeframes(pcm)
    return buf.getvalue()

def ia_media(instruccion, datos, mime, texto="", como_json=False):
    """Manda un archivo (imagen o audio) y una instrucción a Gemini."""
    clave = os.getenv("GEMINI_API_KEY", "")
    if not clave:
        raise RuntimeError("Falta GEMINI_API_KEY: defínela en tu terminal o en el secret del Space.")
    if len(datos) > 15_000_000:
        raise RuntimeError("El archivo es muy grande (máximo 15 MB). Usa uno más corto.")
    pedido = instruccion + ("\n\nTexto: " + texto if texto else "")
    partes = [{"text": pedido},
              {"inline_data": {"mime_type": mime, "data": base64.b64encode(datos).decode()}}]
    cfg = {"responseMimeType": "application/json"} if como_json else {}
    r = httpx.post(URL, timeout=60, headers={"x-goog-api-key": clave},
                   json={"contents": [{"parts": partes}], "generationConfig": cfg})
    if r.status_code != 200:
        raise RuntimeError(f"La IA respondió con error {r.status_code}. 400/403: clave o archivo no válido; 404: modelo; 429: espera un momento.")
    t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if como_json:
        t = t.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(t)
    return t
