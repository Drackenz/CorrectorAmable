import asyncio
import re
import time
import flet as ft
import flet_audio_recorder as far
import flet_audio as fta
from ia import ia
import importlib
import multimedia
multimedia = importlib.reload(multimedia)
pcm_a_wav = multimedia.pcm_a_wav
ia_media = multimedia.ia_media
ia_audio = multimedia.ia_audio

CIAN = ft.Colors.CYAN_300
MAX_CHARS = 500
MAX_USOS = 20
MAX_ARCHIVO = 20000

INSTR = (
    "Eres un corrector amable de español. Corrige ortografía, tildes, puntuación y gramática "
    "sin cambiar el significado ni el tono. Devuelve SOLO JSON válido con texto_corregido "
    "(string) y cambios (lista de strings cortos). Si no hay errores, cambios es []."
)


def vista(page: ft.Page):
    # Estado efímero de esta sesión
    estado = {
        "pcm": bytearray(), "grabando": False, "foto": None,
        "historial": [], "usos": 0, "likes": 0, "dislikes": 0,
        "resultado": "", "bloqueo_mic": False,
        "voz_texto": "", "voz_lista": False, "cooldown_hasta": 0.0,
    }

    campo = ft.TextField(
        label="Escribe, pega, dicta o sube tu texto",
        multiline=True, min_lines=3, max_lines=8, border_color=CIAN,
    )
    mensaje = ft.Text("", color=ft.Colors.AMBER_300)
    anillo = ft.ProgressRing(visible=False, width=22, height=22)
    resultado = ft.Column(spacing=10)
    historial_ui = ft.Column(spacing=4)
    historial_ui.visible = False
    estado_historial = ft.Text("Aún no hay correcciones en esta sesión.", size=12)
    cuenta_usos = ft.Text(f"Usos de IA: 0/{MAX_USOS}", size=12, color=ft.Colors.GREY_400)
    foto_estado = ft.Text("No hay foto seleccionada.", size=12, color=ft.Colors.GREY_400)
    aviso = ft.Text(
        "⚠️ La IA puede equivocarse: revisa el resultado. No ingreses teléfonos, DUI ni contraseñas.",
        size=12, color=ft.Colors.GREY_400,
    )
    tono = ft.Dropdown(
        label="Estilo de corrección", value="amable",
        options=[
            ft.dropdown.Option("amable", "Amable"),
            ft.dropdown.Option("formal", "Formal"),
            ft.dropdown.Option("explicativo", "Explicativo"),
        ],
        width=220,
    )
    titulo_app = ft.Text("✍️ Corrector amable", size=24, weight=ft.FontWeight.BOLD, color=CIAN)
    bienvenida = ft.Text(
        "Corrige ortografía y gramática conservando tu forma de expresarte.\n"
        "Puedes escribir, dictar o cargar un texto o una foto.\n"
        "No verifica hechos ni garantiza que toda sugerencia sea correcta.",
        size=13,
    )

    def tarjeta(titulo, contenido, icono="✨"):
        return ft.Card(content=ft.Container(
            padding=14,
            content=ft.Column([
                ft.Text(f"{icono} {titulo}", weight=ft.FontWeight.BOLD, color=CIAN),
                contenido,
            ], spacing=6),
        ))

    def actualizar_historial():
        ultimos = estado["historial"][-5:]
        historial_ui.controls = [
            ft.Text(f"{i}. Entrada: {item['entrada'][:90]}  →  {item['salida'][:110]}", size=12)
            for i, item in enumerate(ultimos, 1)
        ]
        estado_historial.value = f"Últimas {len(ultimos)} de 5 interacciones"

    def agregar_historial(entrada, salida):
        estado["historial"].append({"entrada": entrada, "salida": salida})
        estado["historial"] = estado["historial"][-5:]
        actualizar_historial()

    def contexto_historial():
        if not estado["historial"]:
            return ""
        lineas = ["Historial reciente (úsalo como contexto, sin confundirlo con la entrada actual):"]
        for item in estado["historial"][-5:]:
            lineas.append(f"Usuario: {item['entrada']}\nAsistente: {item['salida']}")
        return "\n".join(lineas)

    def validar_privacidad(texto):
        # Rechaza patrones comunes de DUI, teléfonos salvadoreños y contraseñas escritas explícitamente.
        patrones = [
            (r"(?<!\d)\d{8}-\d(?!\d)", "Parece que escribiste un DUI."),
            (r"(?<!\d)(?:\+?503[ -]?)?(?:[267]\d{3})[ -]?\d{4}(?!\d)", "Parece que escribiste un teléfono."),
            (r"(?i)\b(?:contraseña|contrasena|password|clave)\s*[:=]", "Parece que incluiste una contraseña o clave."),
        ]
        for patron, aviso_privacidad in patrones:
            if re.search(patron, texto):
                return aviso_privacidad + " Bórralo antes de enviarlo."
        return None

    def mensaje_error(ex):
        # Traduce errores técnicos a indicaciones entendibles.
        detalle = str(ex).lower()
        if "falta gemini_api_key" in detalle:
            return "No encuentro la clave. Crea un archivo .env junto a ia.py y define GEMINI_API_KEY dentro."
        if any(frase in detalle for frase in (
            "gemini rechazó el archivo multimedia", "gemini no encontró el modelo",
            "cuota temporal de gemini", "gemini está temporalmente ocupado",
            "no pude conectar con gemini", "gemini tardó demasiado",
            "no devolvió una transcripción", "respuesta con formato inválido",
        )):
            return str(ex)
        if "503" in detalle or "500" in detalle:
            return "Gemini está ocupado temporalmente. Espera unos segundos y vuelve a intentar."
        if "429" in detalle or "cuota" in detalle or "rate" in detalle:
            espera = re.search(r"unos (\d+) segundos", detalle)
            # Si Gemini no entrega Retry-After, evita una nueva ráfaga por 30 s.
            pausa = int(espera.group(1)) if espera else 30
            estado["cooldown_hasta"] = time.monotonic() + pausa
            return str(ex)
        if "403" in detalle or "api key" in detalle or "gemini_api_key" in detalle or "clave" in detalle:
            return "No se pudo autenticar el servicio. Pide revisar la configuración de la clave."
        if "400" in detalle or "404" in detalle or "modelo" in detalle or "json" in detalle or "respuesta" in detalle:
            return "La IA no devolvió una respuesta válida. Prueba con otro texto o inténtalo de nuevo."
        return "No se pudo conectar con el servicio. Revisa la conexión e inténtalo de nuevo."

    botones = []

    def ocupado(texto, si):
        mensaje.value = texto
        anillo.visible = si
        for boton in botones:
            if boton is not btn_mic:
                boton.disabled = si
        page.update()

    def reservar_uso():
        restante = max(0, int(estado["cooldown_hasta"] - time.monotonic() + 0.99))
        if restante:
            mensaje.value = f"Gemini pidió una pausa. Espera {restante} segundos antes de volver a intentar."
            page.update()
            return False
        if estado["usos"] >= MAX_USOS:
            mensaje.value = f"Alcanzaste el límite de {MAX_USOS} usos de IA en esta sesión."
            page.update()
            return False
        estado["usos"] += 1
        cuenta_usos.value = f"Usos de IA: {estado['usos']}/{MAX_USOS}"
        return True

    def validar_entrada(texto):
        limpio = (texto or "").strip()
        if not limpio:
            mensaje.value = "Escribe o carga un texto primero."
            return None
        if len(limpio) > MAX_CHARS:
            mensaje.value = f"El máximo es {MAX_CHARS} caracteres; tu entrada tiene {len(limpio)}."
            return None
        sensible = validar_privacidad(limpio)
        if sensible:
            mensaje.value = sensible
            return None
        return limpio

    def renderizar_resultado(datos, entrada):
        if not isinstance(datos, dict):
            raise ValueError("formato inválido")
        corregido = datos.get("texto_corregido")
        cambios = datos.get("cambios", [])
        if not isinstance(corregido, str) or not corregido.strip():
            raise ValueError("respuesta vacía")
        if not isinstance(cambios, list):
            cambios = []
        cambios = [str(c) for c in cambios[:30]]
        estilo = tono.value or "amable"
        salida = corregido.strip()
        if cambios:
            lista = ft.Column([ft.Text(f"• {c}", selectable=True) for c in cambios])
        else:
            lista = ft.Text("¡Tu texto ya estaba bien! 🎉")
        resultado.controls = [
            tarjeta("Texto corregido", ft.Text(salida, selectable=True, size=16), "✅"),
            tarjeta("Qué cambió", lista, "📝"),
            tarjeta("Cantidad de cambios", ft.Text(str(len(cambios)), size=24), "🔢"),
            ft.Row([btn_bien, btn_mal], spacing=8),
        ]
        estado["resultado"] = salida
        agregar_historial(entrada, salida)
        page.update()

    async def corregir_texto(texto, grabado=False):
        entrada = validar_entrada(texto)
        if entrada is None:
            ocupado(mensaje.value, False)
            return
        if not reservar_uso():
            return
        instruccion = INSTR + f" Estilo de explicación: {tono.value or 'amable'}."
        if tono.value == "explicativo":
            instruccion += " Explica brevemente cada cambio en la lista."
        elif tono.value == "formal":
            instruccion += " Usa un tono formal."
        texto_con_contexto = contexto_historial() + "\n\nEntrada actual:\n" + entrada
        ocupado("🤔 La IA está pensando…", True)
        try:
            datos = await asyncio.to_thread(ia, instruccion, texto_con_contexto, True)
            renderizar_resultado(datos, entrada)
            ocupado("✅ Corrección lista.", False)
        except Exception as ex:
            ocupado(mensaje_error(ex), False)

    # Voz: transcribe y envía la frase directamente a la corrección (R1).
    def al_recibir_audio(e):
        fragmento = getattr(e, "chunk", None)
        if isinstance(fragmento, (bytes, bytearray)):
            estado["pcm"].extend(fragmento)

    grabadora = far.AudioRecorder(on_stream=al_recibir_audio)
    page.services.append(grabadora)

    selector = ft.FilePicker()
    page.services.append(selector)

    async def seleccionar_imagen():
        archivos = await selector.pick_files(file_type=ft.FilePickerFileType.IMAGE, with_data=True)
        if not archivos or not archivos[0].bytes:
            return None
        return archivos[0]

    def mime_de(nombre):
        return "image/png" if nombre.lower().endswith(".png") else "image/jpeg"

    async def detener_grabacion():
        estado["grabando"] = False
        btn_mic.content = "🎤 Dictar y corregir"
        try:
            await grabadora.stop_recording()
            # El último fragmento puede llegar justo después del evento de parada.
            await asyncio.sleep(0.2)
        except Exception:
            ocupado("No pude detener la grabación. Vuelve a intentar.", False)
            return
        pcm = bytes(estado["pcm"])
        if len(pcm) < 3200:  # menos de 0.1 s a 16 kHz, mono, PCM de 16 bits
            ocupado("No llegó audio del micrófono. Revisa el permiso y el micrófono seleccionado.", False)
            return
        if not reservar_uso():
            return
        ocupado("📝 Transcribiendo…", True)
        try:
            wav = pcm_a_wav(pcm)
            if estado["foto"] is not None:
                pregunta = await asyncio.to_thread(
                    ia_media,
                    "Transcribe fielmente el audio en español. Devuelve solo la transcripción, sin comillas.",
                    wav, "audio/wav",
                )
                pregunta = pregunta.strip()
                if not pregunta:
                    ocupado("No pude entender la grabación. Intenta hablar más claro.", False)
                    return
                campo.value = pregunta
                await preguntar_sobre_foto(pregunta)
            else:
                # Una sola llamada multimodal transcribe y corrige para reducir solicitudes.
                instruccion_audio = (
                    INSTR + " Escucha el audio en español. Devuelve también la transcripción fiel "
                    "en texto_original, además de texto_corregido y cambios. No inventes palabras."
                )
                if tono.value == "formal":
                    instruccion_audio += " Usa un tono formal."
                elif tono.value == "explicativo":
                    instruccion_audio += " Explica brevemente cada cambio en la lista."
                datos = await asyncio.to_thread(
                    ia_media, instruccion_audio, wav, "audio/wav", contexto_historial(), True,
                )
                if not isinstance(datos, dict):
                    raise RuntimeError("La IA no devolvió una corrección de voz válida.")
                pregunta = datos.get("texto_original")
                if not isinstance(pregunta, str) or not pregunta.strip():
                    ocupado("No pude entender la grabación. Intenta hablar más claro.", False)
                    return
                pregunta = pregunta.strip()
                campo.value = pregunta
                sensible = validar_privacidad(pregunta)
                if sensible:
                    campo.value = ""
                    ocupado("El dictado podría contener datos sensibles. No lo enviaré; prueba escribiendo una versión sin esos datos.", False)
                    return
                if len(pregunta) > MAX_CHARS:
                    ocupado(f"El dictado tiene más de {MAX_CHARS} caracteres. Acórtalo antes de corregirlo.", False)
                    return
                renderizar_resultado(datos, pregunta)
                ocupado("✅ Corrección lista.", False)
        except Exception as ex:
            ocupado(mensaje_error(ex), False)

    async def auto_parar():
        await asyncio.sleep(30)
        if estado["grabando"]:
            await detener_grabacion()

    async def click_mic(e):
        if not estado["grabando"]:
            try:
                if not await grabadora.has_permission():
                    mensaje.value = "Necesito permiso del micrófono. Acepta el permiso y usa el enlace HTTPS."
                    page.update()
                    return
                estado["pcm"] = bytearray()
                inicio_correcto = await grabadora.start_recording(configuration=far.AudioRecorderConfiguration(
                    encoder=far.AudioEncoder.PCM16BITS, sample_rate=16000, channels=1,
                ))
                if not inicio_correcto:
                    ocupado("El navegador no inició el micrófono. Revisa el permiso y vuelve a intentar.", False)
                    return
                estado["grabando"] = True
                btn_mic.content = "⏹ Detener"
                mensaje.value = "🔴 Grabando… di la frase (máx. 30 s)."
                page.update()
                asyncio.create_task(auto_parar())
            except Exception:
                ocupado("No pude iniciar el micrófono. Revisa el permiso del sitio y usa localhost o HTTPS.", False)
        else:
            await detener_grabacion()

    async def click_foto(e):
        foto = await seleccionar_imagen()
        if foto is None:
            mensaje.value = "No se eligió una foto válida."
            page.update()
            return
        estado["foto"] = foto
        foto_estado.value = f"Foto seleccionada: {foto.name}"
        mensaje.value = "Foto lista. Puedes describirla, leer su texto o dictar una pregunta sobre ella."
        page.update()
        await describir_foto(foto)

    async def describir_foto(foto):
        if not reservar_uso():
            return
        ocupado("👀 Mirando la foto…", True)
        try:
            texto = await asyncio.to_thread(
                ia_media,
                "Describe brevemente el objeto o la escena visible. No identifiques personas.",
                foto.bytes, mime_de(foto.name), contexto_historial(),
            )
            respuesta = texto.strip()
            resultado.controls = [tarjeta("Lo que veo en la foto", ft.Text(respuesta, selectable=True), "📷")]
            estado["resultado"] = respuesta
            agregar_historial("Describe esta foto", respuesta)
            resultado.controls.append(ft.Row([btn_bien, btn_mal]))
            ocupado("✅ Descripción lista.", False)
        except Exception as ex:
            ocupado(mensaje_error(ex), False)

    async def preguntar_sobre_foto(pregunta):
        foto = estado["foto"]
        if foto is None:
            ocupado("Primero selecciona una foto para hacerle una pregunta.", False)
            return
        entrada = validar_entrada(pregunta)
        if entrada is None:
            ocupado(mensaje.value, False)
            return
        if not reservar_uso():
            return
        ocupado("🖼️🎤 Analizando la foto y tu pregunta…", True)
        try:
            respuesta = await asyncio.to_thread(
                ia_media,
                "Responde en español a la pregunta del usuario basándote en la imagen. "
                "Si no se puede saber, dilo claramente.",
                foto.bytes, mime_de(foto.name), contexto_historial() + "\nPregunta dictada: " + entrada,
            )
            datos = respuesta.strip()
            resultado.controls = [tarjeta("Respuesta sobre la foto", ft.Text(datos, selectable=True), "🖼️")]
            estado["resultado"] = datos
            agregar_historial("Pregunta sobre foto: " + entrada, datos)
            resultado.controls.append(ft.Row([btn_bien, btn_mal]))
            ocupado("✅ Respuesta lista.", False)
        except Exception as ex:
            ocupado(mensaje_error(ex), False)

    async def click_txt(e):
        archivos = await selector.pick_files(
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["txt"], with_data=True,
        )
        if not archivos:
            return
        try:
            texto = archivos[0].bytes.decode("utf-8")
        except (AttributeError, UnicodeDecodeError):
            mensaje.value = "No pude leer el archivo. Guárdalo como texto UTF-8 e inténtalo otra vez."
            page.update()
            return
        if len(texto) > MAX_ARCHIVO:
            mensaje.value = f"El archivo supera el límite de {MAX_ARCHIVO} caracteres."
            page.update()
            return
        campo.value = texto
        palabras = len(texto.split())
        mensaje.value = f"📄 Leí {palabras} palabras. Para corregirlo, el texto debe tener máximo {MAX_CHARS} caracteres."
        page.update()

    async def click_ocr(e):
        foto = await seleccionar_imagen()
        if foto is None:
            mensaje.value = "No se eligió una foto válida."
            page.update()
            return
        estado["foto"] = foto
        foto_estado.value = f"Foto seleccionada: {foto.name}"
        if not reservar_uso():
            return
        ocupado("🔎 Leyendo el texto de la foto…", True)
        try:
            texto = await asyncio.to_thread(
                ia_media,
                "Extrae el texto visible tal como está escrito. Devuelve solo el texto; "
                "si no hay texto responde exactamente SIN_TEXTO.",
                foto.bytes, mime_de(foto.name),
            )
            texto = texto.strip()
            if not texto or texto == "SIN_TEXTO":
                ocupado("No encontré texto en esa imagen.", False)
                return
            if len(texto) > MAX_CHARS:
                ocupado(f"Encontré texto, pero supera el máximo de {MAX_CHARS} caracteres para procesarlo.", False)
                return
            campo.value = texto
            ocupado("✅ Texto leído. Toca «Corregir».", False)
        except Exception as ex:
            ocupado(mensaje_error(ex), False)

    async def click_corregir(e):
        await corregir_texto(campo.value)

    async def click_copiar(e):
        if not estado["resultado"]:
            mensaje.value = "Primero genera un resultado para copiar."
            page.update()
            return
        await portapapeles.set(estado["resultado"])
        mensaje.value = "📋 Resultado copiado."
        page.update()

    async def click_compartir(e):
        if not estado["resultado"]:
            mensaje.value = "Primero genera un resultado para compartir."
            page.update()
            return
        try:
            await compartir.share_text(estado["resultado"], title="Corrector amable")
            mensaje.value = "Resultado listo para compartir."
        except Exception:
            mensaje.value = "No se pudo abrir la opción de compartir en este dispositivo."
        page.update()

    def votar(positivo):
        if positivo:
            estado["likes"] += 1
        else:
            estado["dislikes"] += 1
        panel_votos.value = f"👍 {estado['likes']}    👎 {estado['dislikes']}"
        mensaje.value = "Gracias por valorar la respuesta."
        page.update()

    async def click_bien(e):
        votar(True)

    async def click_mal(e):
        votar(False)

    async def click_hablar_resultado(e):
        if not estado["resultado"]:
            mensaje.value = "Primero genera un resultado para escucharlo."
            page.update()
            return
        # Algunos navegadores bloquean play después de esperar la respuesta de Gemini.
        # El toque adicional permite iniciar el audio como acción directa de la persona.
        if estado["voz_lista"] and estado["voz_texto"] == estado["resultado"]:
            try:
                await audio_salida.play()
                mensaje.value = "🔊 Reproduciendo el resultado."
            except Exception:
                mensaje.value = "La voz está lista. Pulsa «Reproducir voz» para iniciar el audio."
                btn_voz_resultado.content = "▶ Reproducir voz"
            page.update()
            return
        if not reservar_uso():
            return
        mensaje.value = "🔊 Generando la voz…"
        page.update()
        try:
            datos_audio = await asyncio.to_thread(ia_audio, estado["resultado"])
            if not datos_audio:
                raise RuntimeError("La voz no devolvió audio.")
            audio_salida.src = datos_audio
            audio_salida.update()
            estado["voz_texto"] = estado["resultado"]
            estado["voz_lista"] = True
            btn_voz_resultado.content = "▶ Reproducir voz"
            try:
                await audio_salida.play()
                mensaje.value = "🔊 Reproduciendo el resultado."
            except Exception:
                mensaje.value = "Voz generada. Pulsa «Reproducir voz» para iniciar el audio."
        except Exception as ex:
            mensaje.value = mensaje_error(ex)
        page.update()

    btn_mic = ft.Button(content="🎤 Dictar y corregir", on_click=click_mic)
    btn_foto = ft.Button("📷 Describir foto", on_click=click_foto)
    btn_txt = ft.Button("📄 Subir .txt", on_click=click_txt)
    btn_ocr = ft.Button("🔎 Leer texto de foto", on_click=click_ocr)
    btn_ok = ft.Button("Corregir ✨", on_click=click_corregir)
    btn_copiar = ft.Button("📋 Copiar", on_click=click_copiar)
    btn_compartir = ft.Button("↗ Compartir", on_click=click_compartir)
    btn_voz_resultado = ft.Button("🔊 Escuchar", on_click=click_hablar_resultado)
    btn_bien = ft.Button("👍", on_click=click_bien)
    btn_mal = ft.Button("👎", on_click=click_mal)
    panel_votos = ft.Text("👍 0    👎 0", size=12)
    btn_historial = ft.Button("Mostrar historial", on_click=lambda e: alternar_historial(e))
    def al_cambiar_estado_audio(e):
        if e.state == fta.AudioState.PLAYING:
            mensaje.value = "🔊 Reproduciendo el resultado."
            page.update()
        elif e.state == fta.AudioState.COMPLETED:
            mensaje.value = "✅ Terminó la lectura en voz alta."
            page.update()

    # Flet 1.0.5 no inicializa bien Audio cuando src=None. Se monta con un WAV
    # silencioso válido y luego se reemplaza por la voz generada.
    audio_salida = fta.Audio(
        src=pcm_a_wav(bytes(3200)), autoplay=False, volume=1,
        on_state_change=al_cambiar_estado_audio,
    )
    portapapeles = ft.Clipboard()
    compartir = ft.Share()
    page.services.extend([portapapeles, compartir, audio_salida])
    botones = [btn_mic, btn_foto, btn_txt, btn_ocr, btn_ok, btn_copiar, btn_compartir, btn_voz_resultado, btn_historial]

    def alternar_historial(e):
        historial_ui.visible = not historial_ui.visible
        btn_historial.text = "Ocultar historial" if historial_ui.visible else "Mostrar historial"
        page.update()

    return ft.Column([
        titulo_app, bienvenida, campo, tono,
        ft.Row([btn_mic, btn_foto, btn_txt, btn_ocr], wrap=True, spacing=6),
        foto_estado,
        ft.Row([btn_ok, anillo, cuenta_usos], spacing=10, wrap=True),
        ft.Row([btn_copiar, btn_compartir, btn_voz_resultado], wrap=True, spacing=6),
        ft.Row([btn_historial, panel_votos], spacing=10),
        estado_historial, historial_ui,
        mensaje, resultado, aviso,
    ], spacing=12)
