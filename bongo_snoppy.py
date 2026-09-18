#!/usr/bin/env python3
"""
Bongo Snoppy - OBS Overlay
Captura teclado/mouse via evdev (funciona en Wayland y X11).

Uso en OBS:
  1. Corre este script
  2. En OBS: Agregar fuente > Window Capture > selecciona "Bongo Snoppy"
  3. Agregar filtro Chroma Key con color #06FF00

Requiere estar en el grupo 'input':
  sudo usermod -aG input $USER   (luego cerrar sesión y volver a entrar)
"""

import json
import math
import os
import socket
import sys
import threading
import time

import pygame
import evdev
from evdev import ecodes

# === Rutas ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKIN_DIR = os.path.join(BASE_DIR, "skin")

# === Config ===
CHROMA_KEY  = (6, 255, 0)
PAW_LEFT_X  = 0
PAW_LEFT_Y  = 0
PAW_RIGHT_X = 0
PAW_RIGHT_Y = 0
FPS         = 60

# Rect del panel tactil dibujado en bg.png (x, y, w, h)
TOUCHPAD_RECT = (40, 210, 180, 95)
TOUCHPAD_INSET_RATIO = 0.30

# Hotspot visual del icono de mouse (centro del PNG de 99x72)
MOUSE_HOTSPOT_X = 49
MOUSE_HOTSPOT_Y = 36

# Offset fino para que la punta de la mano caiga exactamente
# sobre el punto visual deseado del icono del mouse.
HAND_TARGET_OFFSET_X = -15
HAND_TARGET_OFFSET_Y = -35

# Punto fijo de la mano (hombro/base) dentro del canvas completo.
# El tip es el punto de contacto visual con el trackpad.
HAND_ROOT_X = 295
HAND_ROOT_Y = 232
HAND_TIP_X = 111
HAND_TIP_Y = 232

# Dinamica elastica de la mano
HAND_DAMPING = 18.0
HAND_MAX_STRETCH = 0.45
HAND_MAX_SHRINK = 0.35
HAND_MIN_SCALE_Y = 0.70
HAND_VISIBLE_AFTER_MOVE = 0.14

# Polling de cursor global en Hyprland (si esta disponible)
CURSOR_POLL_INTERVAL = 1.0 / 120.0
MONITOR_REFRESH_INTERVAL = 2.0

# Teclas del lado IZQUIERDO del teclado
LEFT_KEYS = {
    ecodes.KEY_GRAVE, ecodes.KEY_1, ecodes.KEY_2, ecodes.KEY_3,
    ecodes.KEY_4,     ecodes.KEY_5,
    ecodes.KEY_TAB,   ecodes.KEY_Q, ecodes.KEY_W, ecodes.KEY_E,
    ecodes.KEY_R,     ecodes.KEY_T,
    ecodes.KEY_CAPSLOCK, ecodes.KEY_A, ecodes.KEY_S, ecodes.KEY_D,
    ecodes.KEY_F,     ecodes.KEY_G,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_Z, ecodes.KEY_X, ecodes.KEY_C,
    ecodes.KEY_V,     ecodes.KEY_B,
    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_LEFTMETA,
}

# === Estado global ===
_lock        = threading.Lock()
_left_down   = False
_right_down  = False
_mouse_left  = False
_mouse_right = False

_cursor_global_x = None
_cursor_global_y = None
_has_global_cursor = False

_virtual_cursor_x = 960.0
_virtual_cursor_y = 540.0
_monitor_geom = (0.0, 0.0, 1920.0, 1080.0)
_hand_visible_until = 0.0


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _hypr_socket_path():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not sig or not runtime_dir:
        return None
    return os.path.join(runtime_dir, "hypr", sig, ".socket.sock")


def _hypr_query_json(command):
    sock_path = _hypr_socket_path()
    if not sock_path or not os.path.exists(sock_path):
        return None

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(sock_path)
            sock.sendall(f"j/{command}".encode("utf-8"))
            chunks = []
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
            payload = b"".join(chunks).decode("utf-8", errors="ignore").strip()
            if not payload:
                return None
            return json.loads(payload)
    except Exception:
        return None


def _refresh_monitor_geometry():
    global _monitor_geom, _virtual_cursor_x, _virtual_cursor_y

    monitors = _hypr_query_json("monitors")
    if not isinstance(monitors, list) or not monitors:
        return False

    monitor = None
    for item in monitors:
        if item.get("focused"):
            monitor = item
            break
    if monitor is None:
        monitor = monitors[0]

    mx = float(monitor.get("x", 0.0))
    my = float(monitor.get("y", 0.0))
    mw = float(monitor.get("width", 1920.0))
    mh = float(monitor.get("height", 1080.0))
    if mw <= 1 or mh <= 1:
        return False

    with _lock:
        _monitor_geom = (mx, my, mw, mh)
        _virtual_cursor_x = _clamp(_virtual_cursor_x, mx, mx + mw - 1.0)
        _virtual_cursor_y = _clamp(_virtual_cursor_y, my, my + mh - 1.0)
    return True


def _hypr_cursor_listener():
    global _cursor_global_x, _cursor_global_y, _has_global_cursor, _hand_visible_until
    last_monitor_refresh = 0.0
    prev_cx = None
    prev_cy = None

    while True:
        now = time.monotonic()
        if now - last_monitor_refresh >= MONITOR_REFRESH_INTERVAL:
            _refresh_monitor_geometry()
            last_monitor_refresh = now

        cursor = _hypr_query_json("cursorpos")
        if isinstance(cursor, dict) and "x" in cursor and "y" in cursor:
            cx = float(cursor.get("x", 0.0))
            cy = float(cursor.get("y", 0.0))
            with _lock:
                _cursor_global_x = cx
                _cursor_global_y = cy
                _has_global_cursor = True
                _virtual_cursor_x = cx
                _virtual_cursor_y = cy
                if prev_cx is not None and prev_cy is not None:
                    moved = abs(cx - prev_cx) + abs(cy - prev_cy) > 0.01
                    if moved:
                        _hand_visible_until = time.monotonic() + HAND_VISIBLE_AFTER_MOVE
            prev_cx = cx
            prev_cy = cy

        time.sleep(CURSOR_POLL_INTERVAL)


def _update_virtual_from_rel(dx, dy):
    global _virtual_cursor_x, _virtual_cursor_y, _cursor_global_x, _cursor_global_y, _hand_visible_until
    with _lock:
        mx, my, mw, mh = _monitor_geom
        old_x = _virtual_cursor_x
        old_y = _virtual_cursor_y
        _virtual_cursor_x = _clamp(old_x + dx, mx, mx + mw - 1.0)
        _virtual_cursor_y = _clamp(old_y + dy, my, my + mh - 1.0)
        moved = (_virtual_cursor_x != old_x) or (_virtual_cursor_y != old_y)
        if moved:
            _hand_visible_until = time.monotonic() + HAND_VISIBLE_AFTER_MOVE
        if not _has_global_cursor:
            _cursor_global_x = _virtual_cursor_x
            _cursor_global_y = _virtual_cursor_y


def _update_virtual_from_abs(norm_x, norm_y):
    global _virtual_cursor_x, _virtual_cursor_y, _cursor_global_x, _cursor_global_y, _hand_visible_until
    with _lock:
        mx, my, mw, mh = _monitor_geom
        old_x = _virtual_cursor_x
        old_y = _virtual_cursor_y
        _virtual_cursor_x = mx + _clamp(norm_x, 0.0, 1.0) * (mw - 1.0)
        _virtual_cursor_y = my + _clamp(norm_y, 0.0, 1.0) * (mh - 1.0)
        moved = (_virtual_cursor_x != old_x) or (_virtual_cursor_y != old_y)
        if moved:
            _hand_visible_until = time.monotonic() + HAND_VISIBLE_AFTER_MOVE
        if not _has_global_cursor:
            _cursor_global_x = _virtual_cursor_x
            _cursor_global_y = _virtual_cursor_y


def _normalize_abs(device, code, value):
    try:
        info = device.absinfo(code)
    except Exception:
        return None

    if info is None:
        return None

    rng = float(info.max - info.min)
    if rng <= 0:
        return None

    return _clamp((float(value) - float(info.min)) / rng, 0.0, 1.0)


def _map_global_to_touchpad(global_x, global_y, monitor_geom):
    mx, my, mw, mh = monitor_geom
    pad_x, pad_y, pad_w, pad_h = TOUCHPAD_RECT

    inset_x = pad_w * TOUCHPAD_INSET_RATIO
    inset_y = pad_h * TOUCHPAD_INSET_RATIO
    inner_x = pad_x + inset_x
    inner_y = pad_y + inset_y
    inner_w = max(1.0, pad_w - inset_x * 2.0)
    inner_h = max(1.0, pad_h - inset_y * 2.0)

    if global_x is None or global_y is None or mw <= 1 or mh <= 1:
        norm_x = 0.5
        norm_y = 0.5
    else:
        norm_x = _clamp((global_x - mx) / (mw - 1.0), 0.0, 1.0)
        norm_y = _clamp((global_y - my) / (mh - 1.0), 0.0, 1.0)

    screen_x = inner_x + norm_x * inner_w
    screen_y = inner_y + norm_y * inner_h
    return pygame.Vector2(screen_x, screen_y)


def _blit_deformed_hand(surface, hand_image, pivot_xy, angle_deg, scale_x, scale_y):
    src_w, src_h = hand_image.get_size()
    new_w = max(1, int(src_w * scale_x))
    new_h = max(1, int(src_h * scale_y))

    scaled = pygame.transform.smoothscale(hand_image, (new_w, new_h))
    scaled_rect = scaled.get_rect(topleft=(0, 0))

    pivot = pygame.Vector2(pivot_xy)
    scaled_pivot = pygame.Vector2(pivot.x * scale_x, pivot.y * scale_y)
    pivot_to_center = pygame.Vector2(scaled_rect.center) - scaled_pivot
    rotated_offset = pivot_to_center.rotate(-angle_deg)

    rotated = pygame.transform.rotozoom(scaled, angle_deg, 1.0)
    rotated_center = pivot + rotated_offset
    rotated_rect = rotated.get_rect(center=(round(rotated_center.x), round(rotated_center.y)))
    surface.blit(rotated, rotated_rect.topleft)


def _find_devices():
    keyboards, pointers = [], []

    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])

            rel_axes = caps.get(ecodes.EV_REL, [])
            abs_axes = caps.get(ecodes.EV_ABS, [])

            has_keyboard_profile = ecodes.KEY_A in keys and ecodes.KEY_SPACE in keys
            has_rel_pointer = ecodes.REL_X in rel_axes and ecodes.REL_Y in rel_axes
            has_abs_pointer = (
                (ecodes.ABS_X in abs_axes and ecodes.ABS_Y in abs_axes)
                or (
                    ecodes.ABS_MT_POSITION_X in abs_axes
                    and ecodes.ABS_MT_POSITION_Y in abs_axes
                )
            )
            has_pointer_btn = any(
                btn in keys
                for btn in (
                    ecodes.BTN_LEFT,
                    ecodes.BTN_RIGHT,
                    ecodes.BTN_TOUCH,
                    ecodes.BTN_TOOL_FINGER,
                )
            )

            if has_keyboard_profile:
                keyboards.append(dev)
            if has_rel_pointer or has_abs_pointer or has_pointer_btn:
                pointers.append(dev)
        except Exception:
            pass

    return keyboards, pointers


def _keyboard_listener(device):
    global _left_down, _right_down
    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                pressed = event.value in (1, 2)
                with _lock:
                    if event.code in LEFT_KEYS:
                        _left_down = pressed
                    else:
                        _right_down = pressed
    except Exception:
        pass


def _mouse_listener(device):
    global _mouse_left, _mouse_right

    abs_norm_x = None
    abs_norm_y = None

    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                with _lock:
                    if event.code == ecodes.BTN_LEFT:
                        _mouse_left = bool(event.value)
                    elif event.code in (ecodes.BTN_RIGHT, ecodes.BTN_TOOL_DOUBLETAP):
                        _mouse_right = bool(event.value)

            elif event.type == ecodes.EV_REL:
                if event.code == ecodes.REL_X:
                    _update_virtual_from_rel(float(event.value), 0.0)
                elif event.code == ecodes.REL_Y:
                    _update_virtual_from_rel(0.0, float(event.value))

            elif event.type == ecodes.EV_ABS:
                if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                    abs_norm_x = _normalize_abs(device, event.code, event.value)
                elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                    abs_norm_y = _normalize_abs(device, event.code, event.value)

                if abs_norm_x is not None and abs_norm_y is not None:
                    _update_virtual_from_abs(abs_norm_x, abs_norm_y)
    except Exception:
        pass


def main():
    keyboards, pointers = _find_devices()

    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        threading.Thread(target=_hypr_cursor_listener, daemon=True).start()
        _refresh_monitor_geometry()

    if not keyboards and not pointers:
        print("ERROR: No se encontraron dispositivos.")
        print("  sudo usermod -aG input $USER  (luego cerrar sesión)")
        sys.exit(1)

    print(f"Teclados : {[d.name for d in keyboards]}")
    print(f"Pointers : {[d.name for d in pointers]}")

    for dev in keyboards:
        threading.Thread(target=_keyboard_listener, args=(dev,), daemon=True).start()
    for dev in pointers:
        threading.Thread(target=_mouse_listener, args=(dev,), daemon=True).start()

    # === Pygame ===
    pygame.init()
    pygame.display.set_caption("Bongo Snoppy")

    def load(name):
        return pygame.image.load(os.path.join(SKIN_DIR, name)).convert_alpha()

    screen = pygame.display.set_mode((612, 383), pygame.NOFRAME)

    img_bg         = load("bg.png")
    img_paw_down   = load("down.png")
    img_hand_mouse = load("handmouse.png")
    img_mouse = {
        (False, False): load("mouse.png"),
        (True,  False): load("mousel.png"),
        (False, True ): load("mouser.png"),
        (True,  True ): load("mouselr.png"),
    }

    W, H = img_bg.get_size()
    screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
    clock  = pygame.time.Clock()

    hand_root = pygame.Vector2(HAND_ROOT_X, HAND_ROOT_Y)
    hand_rest = pygame.Vector2(HAND_TIP_X - HAND_ROOT_X, HAND_TIP_Y - HAND_ROOT_Y)
    hand_rest_len = max(1.0, hand_rest.length())
    hand_rest_angle = math.degrees(math.atan2(-hand_rest.y, hand_rest.x))
    pad_x, pad_y, pad_w, pad_h = TOUCHPAD_RECT
    pad_inset_x = pad_w * TOUCHPAD_INSET_RATIO
    pad_inset_y = pad_h * TOUCHPAD_INSET_RATIO
    pad_inner_x = pad_x + pad_inset_x
    pad_inner_y = pad_y + pad_inset_y
    pad_inner_w = max(1.0, pad_w - pad_inset_x * 2.0)
    pad_inner_h = max(1.0, pad_h - pad_inset_y * 2.0)
    hand_vel = pygame.Vector2(0.0, 0.0)
    hand_prev_target = pygame.Vector2(
        pad_inner_x + pad_inner_w * 0.5,
        pad_inner_y + pad_inner_h * 0.5,
    )
    prev_time = time.perf_counter()

    print(f"Corriendo {W}x{H} — Chroma key #06FF00")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        now = time.perf_counter()
        dt = min(0.05, now - prev_time)
        prev_time = now

        with _lock:
            left  = _left_down
            right = _right_down
            ml    = _mouse_left
            mr    = _mouse_right
            gx    = _cursor_global_x
            gy    = _cursor_global_y
            monitor_geom = _monitor_geom
            hand_visible_until = _hand_visible_until

        cursor_on_pad = _map_global_to_touchpad(gx, gy, monitor_geom)

        screen.fill(CHROMA_KEY)
        screen.blit(img_bg, (0, 0))

        # Patas en el teclado
        if left or right:
            screen.blit(img_paw_down, (PAW_RIGHT_X, PAW_RIGHT_Y))

        # Mouse siguiendo cursor global mapeado al panel tactil
        mouse_sprite = img_mouse[(ml, mr)]
        mouse_w, mouse_h = mouse_sprite.get_size()
        draw_mouse_x = int(round(cursor_on_pad.x - MOUSE_HOTSPOT_X))
        draw_mouse_y = int(round(cursor_on_pad.y - MOUSE_HOTSPOT_Y))

        # Mantener el hotspot del mouse dentro del area interna reducida.
        min_draw_x = pad_inner_x - MOUSE_HOTSPOT_X
        max_draw_x = pad_inner_x + pad_inner_w - MOUSE_HOTSPOT_X
        min_draw_y = pad_inner_y - MOUSE_HOTSPOT_Y
        max_draw_y = pad_inner_y + pad_inner_h - MOUSE_HOTSPOT_Y

        draw_mouse_x = int(_clamp(draw_mouse_x, min_draw_x, max_draw_x))
        draw_mouse_y = int(_clamp(draw_mouse_y, min_draw_y, max_draw_y))
        screen.blit(mouse_sprite, (draw_mouse_x, draw_mouse_y))

        # Target real final del mouse (despues de clamp): la punta de la mano
        # debe perseguir este punto para quedar siempre encima.
        mouse_target = pygame.Vector2(
            draw_mouse_x + MOUSE_HOTSPOT_X + HAND_TARGET_OFFSET_X,
            draw_mouse_y + MOUSE_HOTSPOT_Y + HAND_TARGET_OFFSET_Y,
        )

        target_vel = (mouse_target - hand_prev_target) / max(1e-6, dt)
        vel_blend = 1.0 - math.exp(-HAND_DAMPING * dt)
        hand_vel += (target_vel - hand_vel) * vel_blend
        hand_prev_target = mouse_target

        # Mano siguiendo el mismo target que el mouse, con deformacion elastica
        show_hand = (ml or mr) or (now <= hand_visible_until)
        if show_hand:
            arm_vec = mouse_target - hand_root
            arm_dist = max(1.0, arm_vec.length())
            arm_angle = math.degrees(math.atan2(-arm_vec.y, arm_vec.x)) - hand_rest_angle

            # Escala X exacta para que el tip caiga sobre mouse_target.
            stretch = arm_dist / hand_rest_len
            stretch = _clamp(stretch, 1.0 - HAND_MAX_SHRINK, 1.0 + HAND_MAX_STRETCH)

            scale_x = stretch
            speed_squash = min(0.16, hand_vel.length() / 3200.0)
            stretch_squash = min(0.12, max(0.0, stretch - 1.0) * 0.6)
            scale_y = max(HAND_MIN_SCALE_Y, 1.0 - speed_squash - stretch_squash)

            _blit_deformed_hand(
                screen,
                img_hand_mouse,
                (HAND_ROOT_X, HAND_ROOT_Y),
                arm_angle,
                scale_x,
                scale_y,
            )

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
