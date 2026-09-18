# Bongo Snoppy

Overlay de escritorio en Python para OBS con Snoopy animado que reacciona a teclado y mouse/touchpad en tiempo real.

## Caracteristicas

- Ventana `pygame` sin borde para capturar en OBS.
- Fondo chroma key (`#06FF00`) para usar filtro Chroma Key.
- Reaccion de teclado con `down.png` para cualquier tecla.
- Tracking de cursor global en Wayland/Hyprland.
- Lectura de dispositivos `evdev`:
  - `REL_X/REL_Y` para mouse.
  - `ABS_X/ABS_Y` y `ABS_MT_POSITION_X/Y` para touchpad.
- Mapeo de cursor global al area del trackpad dibujado en `bg.png`.
- `mouse.png` (y variantes L/R) siguiendo el target mapeado.
- `handmouse.png` con origen fijo y deformacion elastica, siguiendo el mismo target del mouse.

## Requisitos

- Python 3.10+
- Linux con acceso a `evdev`
- Pertenecer al grupo `input`:

```bash
sudo usermod -aG input $USER
```

Despues de ejecutar eso, cierra sesion y vuelve a entrar.

## Instalacion

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Ejecutar

```bash
source venv/bin/activate
python bongo_snoppy.py
```

## Uso en OBS

1. Agrega una fuente **Window Capture**.
2. Selecciona la ventana **Bongo Snoppy**.
3. Agrega filtro **Chroma Key** con color `#06FF00`.

## Assets

Los sprites se cargan desde `skin/`:

- `bg.png`
- `down.png`
- `handmouse.png`
- `mouse.png`
- `mousel.png`
- `mouser.png`
- `mouselr.png`

## Ajustes rapidos

En `bongo_snoppy.py` puedes calibrar facilmente:

- `TOUCHPAD_RECT` y `TOUCHPAD_INSET_RATIO`
- `MOUSE_HOTSPOT_X`, `MOUSE_HOTSPOT_Y`
- `HAND_ROOT_X`, `HAND_ROOT_Y`
- `HAND_TIP_X`, `HAND_TIP_Y`
- `HAND_TARGET_OFFSET_X`, `HAND_TARGET_OFFSET_Y`
- `HAND_DAMPING`, `HAND_MAX_STRETCH`, `HAND_MAX_SHRINK`, `HAND_MIN_SCALE_Y`
