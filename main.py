import os, inspect, importlib, pkgutil, flet as ft
import apps

def main(page: ft.Page):
    page.title = "AppLab IA"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO
    cuerpo = ft.Container()

    def abrir(nombre):
        def _abrir(e):
            modulo = importlib.reload(importlib.import_module(f"apps.{nombre}"))
            # Si la app pide "page" (para usar micrófono o selector de archivos), se la pasamos.
            pide_page = len(inspect.signature(modulo.vista).parameters) > 0
            cuerpo.content = modulo.vista(page) if pide_page else modulo.vista()
            page.update()
        return _abrir

    nombres = sorted(m.name for m in pkgutil.iter_modules(apps.__path__))
    page.add(ft.Text("AppLab IA ⚡", size=26, weight=ft.FontWeight.BOLD),
             ft.Row([ft.Button(n, on_click=abrir(n)) for n in nombres], wrap=True, spacing=6),
             ft.Divider(), cuerpo)

if __name__ == "__main__":
    ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0",
           port=int(os.getenv("PORT", 7860)))
