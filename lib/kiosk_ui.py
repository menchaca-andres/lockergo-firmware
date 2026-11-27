import os
import time
from machine import Pin, SPI


TIEMPO_RELE_MS = 5000
TIEMPO_ALERTA_MS = 15000
TIEMPO_ESPERA_APERTURA_MS = 15000
LARGO_PIN = 4

ETIQUETAS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "BORRAR", "0", "OK"]
TECLAS = [
    (etiqueta, 10 + (index % 3) * 100, 72 + (index // 3) * 42, 96, 38)
    for index, etiqueta in enumerate(ETIQUETAS)
]


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _ticks_add(a, delta):
    try:
        return time.ticks_add(a, delta)
    except AttributeError:
        return a + delta


class KioskUI:
    def __init__(self, settings):
        kiosk_cfg = settings.get("kiosk", {})
        self.enabled = bool(kiosk_cfg.get("enabled", False))
        self.relay_active_low = bool(kiosk_cfg.get("relayActiveLow", True))
        self.relay_pin_num = int(kiosk_cfg.get("relayPin", 22))
        self.buzzer_pin_num = int(kiosk_cfg.get("buzzerPin", 21))
        self.door_pin_num = int(kiosk_cfg.get("doorPin", 33))

        self.relay = None
        self.buzzer = None
        self.door_fallback_pin = None
        self.tft = None
        self.st7789 = None
        self.font_small = None
        self.font_large = None
        self.touch = None
        self.irq = None

        self.ty_left = int(kiosk_cfg.get("tyLeft", 304))
        self.ty_right = int(kiosk_cfg.get("tyRight", 11))
        self.tx_top = int(kiosk_cfg.get("txTop", 218))
        self.tx_bottom = int(kiosk_cfg.get("txBottom", 22))

        self.relay_until_ms = None
        self.buzzer_until_ms = None
        self.state = "IDLE"
        self.state_until_ms = None
        self.door_opened_at_ms = None
        self.door_alert_active = False
        self.next_alert_beep_ms = None
        self.pin_code = None
        self.entered_pin = ""
        self.attempts = 0
        self.touch_wait_release_until_ms = None

        if not self.enabled:
            return

        self._init_hardware(kiosk_cfg)

    def _init_hardware(self, kiosk_cfg):
        import gc

        self.relay = Pin(self.relay_pin_num, Pin.OUT)
        self._set_relay(False)
        self.buzzer = Pin(self.buzzer_pin_num, Pin.OUT)
        self.buzzer.value(0)
        self.door_fallback_pin = Pin(self.door_pin_num, Pin.IN, Pin.PULL_UP)

        try:
            gc.collect()
            import st7789py as st7789
            gc.collect()
            import vga1_8x16 as font_small
            gc.collect()
            try:
                if gc.mem_free() > 80000:
                    import vga1_bold_16x32 as font_large
                else:
                    font_large = font_small
            except Exception:
                font_large = font_small
            gc.collect()
            from xpt2046 import Touch
            gc.collect()
        except ImportError as error:
            print("Kiosk UI disabled; missing TFT/Touch driver:", error)
            self.enabled = False
            return

        self.st7789 = st7789
        self.font_small = font_small
        self.font_large = font_large

        try:
            spi_tft = SPI(
                2,
                baudrate=40000000,
                polarity=1,
                phase=0,
                sck=Pin(int(kiosk_cfg.get("tftSckPin", 18))),
                mosi=Pin(int(kiosk_cfg.get("tftMosiPin", 23))),
            )
            self.tft = st7789.ST7789(
                spi_tft,
                240,
                320,
                reset=Pin(int(kiosk_cfg.get("tftRstPin", 32)), Pin.OUT),
                cs=Pin(int(kiosk_cfg.get("tftCsPin", 5)), Pin.OUT),
                dc=Pin(int(kiosk_cfg.get("tftDcPin", 26)), Pin.OUT),
                rotation=int(kiosk_cfg.get("tftRotation", 3)),
            )
            gc.collect()

            spi_touch = SPI(
                1,
                baudrate=1000000,
                sck=Pin(int(kiosk_cfg.get("touchSckPin", 14))),
                mosi=Pin(int(kiosk_cfg.get("touchMosiPin", 13))),
                miso=Pin(int(kiosk_cfg.get("touchMisoPin", 34))),
            )
            self.touch = Touch(spi_touch, cs=Pin(int(kiosk_cfg.get("touchCsPin", 2))))
            self.irq = Pin(int(kiosk_cfg.get("touchIrqPin", 35)), Pin.IN)
            self._enter_idle()
        except Exception as error:
            print("Kiosk UI disabled; hardware init failed:", error)
            self.tft = None
            self.enabled = False

    def _set_relay(self, active):
        if self.relay is None:
            return
        if self.relay_active_low:
            self.relay.value(0 if active else 1)
        else:
            self.relay.value(1 if active else 0)

    def _open_relay(self, now_ms):
        self._set_relay(True)
        self.relay_until_ms = _ticks_add(now_ms, TIEMPO_RELE_MS)

    def _beep(self, now_ms, duration_ms=150):
        if self.buzzer is None:
            return
        self.buzzer.value(1)
        self.buzzer_until_ms = _ticks_add(now_ms, duration_ms)

    def _update_outputs(self, now_ms):
        if self.relay_until_ms is not None and _ticks_diff(now_ms, self.relay_until_ms) >= 0:
            self._set_relay(False)
            self.relay_until_ms = None
        if self.buzzer_until_ms is not None and _ticks_diff(now_ms, self.buzzer_until_ms) >= 0:
            if self.buzzer is not None:
                self.buzzer.value(0)
            self.buzzer_until_ms = None

    def _to_screen(self, tx, ty):
        sx = (self.ty_left - ty) * 320 // (self.ty_left - self.ty_right)
        sy = (self.tx_top - tx) * 240 // (self.tx_top - self.tx_bottom)
        return max(0, min(319, sx)), max(0, min(239, sy))

    def _read_touch(self, now_ms):
        if self.irq is None or self.touch is None:
            return None
        if self.touch_wait_release_until_ms is not None:
            if self.irq.value() == 0 and _ticks_diff(self.touch_wait_release_until_ms, now_ms) > 0:
                return None
            self.touch_wait_release_until_ms = None
            return None

        if self.irq.value() != 0:
            return None

        samples = []
        for _ in range(4):
            sample = self.touch.raw_touch()
            if sample is not None:
                samples.append(sample)
            time.sleep_ms(5)
        if len(samples) < 3:
            return None

        mx = sum(m[0] for m in samples) // len(samples)
        my = sum(m[1] for m in samples) // len(samples)
        dev = sum((m[0] - mx) ** 2 + (m[1] - my) ** 2 for m in samples) / len(samples)
        if dev > 900:
            return None

        self.touch_wait_release_until_ms = _ticks_add(now_ms, 300)
        tx, ty = self.touch.normalize(mx, my)
        return self._to_screen(tx, ty)

    def _bar(self, title, color, text_color, height):
        if len(title) * 16 <= 320:
            font, width, h = self.font_large, 16, 32
        else:
            font, width, h = self.font_small, 8, 16
        self.tft.fill_rect(0, 0, 320, height, color)
        x = (320 - len(title) * width) // 2
        y = (height - h) // 2
        self.tft.text(font, title, x, y, text_color, color)

    def _screen(self, title, line1, line2, color, text_color=None):
        if text_color is None:
            text_color = self.st7789.BLACK
        self.tft.fill(self.st7789.BLACK)
        self._bar(title, color, text_color, 60)
        self.tft.text(self.font_small, line1, 10, 100, self.st7789.WHITE, self.st7789.BLACK)
        self.tft.text(self.font_small, line2, 10, 130, self.st7789.WHITE, self.st7789.BLACK)

    def _button(self, x, y, w, h, text, color, text_color, font=None):
        if font is None:
            font = self.font_small
        width = 16 if font is self.font_large else 8
        height = 32 if font is self.font_large else 16
        self.tft.fill_rect(x, y, w, h, color)
        self.tft.text(
            font,
            text,
            x + (w - len(text) * width) // 2,
            y + (h - height) // 2,
            text_color,
            color,
        )

    def _draw_keypad(self):
        self.tft.fill(self.st7789.BLACK)
        self._bar("INGRESE SU PIN", self.st7789.YELLOW, self.st7789.BLACK, 40)
        for label, x, y, w, h in TECLAS:
            if label == "BORRAR":
                self._button(x, y, w, h, label, self.st7789.RED, self.st7789.WHITE)
            elif label == "OK":
                self._button(x, y, w, h, label, self.st7789.GREEN, self.st7789.BLACK)
            else:
                self._button(x, y, w, h, label, self.st7789.WHITE, self.st7789.BLACK)
        self._update_pin_input()

    def _update_pin_input(self):
        self.tft.fill_rect(0, 44, 320, 24, self.st7789.BLACK)
        self.tft.text(
            self.font_small,
            "PIN: " + "*" * len(self.entered_pin),
            10,
            48,
            self.st7789.WHITE,
            self.st7789.BLACK,
        )

    @staticmethod
    def _key_at(sx, sy):
        for label, x, y, w, h in TECLAS:
            if x <= sx < x + w and y <= sy < y + h:
                return label
        return None

    def _enter_idle(self):
        self.state = "IDLE"
        self.pin_code = None
        self.entered_pin = ""
        self.attempts = 0
        self._screen("LIBRE", "Casillero disponible", "Toque para depositar", self.st7789.GREEN)
        self._button(60, 170, 200, 50, "DEPOSITAR", self.st7789.GREEN, self.st7789.BLACK, self.font_large)

    def _enter_occupied(self):
        self.state = "OCCUPIED"
        self.entered_pin = ""
        self._draw_keypad()

    def _is_door_closed(self, door_closed):
        if door_closed is not None:
            return door_closed
        if self.door_fallback_pin is not None:
            return self.door_fallback_pin.value() == 0
        return True

    def tick(self, now_ms, door_closed=None):
        if not self.enabled or self.tft is None:
            return

        self._update_outputs(now_ms)
        closed = self._is_door_closed(door_closed)
        touch = self._read_touch(now_ms)

        if self.state == "IDLE":
            if touch and 60 <= touch[0] <= 260 and 170 <= touch[1] <= 220:
                self.pin_code = "".join(str(b % 10) for b in os.urandom(LARGO_PIN))
                print("PIN generado:", self.pin_code)
                self._screen("ABIERTO", "Deje su paquete", "y cierre la puerta", self.st7789.CYAN)
                self._open_relay(now_ms)
                self.state = "DEPOSIT_WAIT_OPEN"
                self.state_until_ms = _ticks_add(now_ms, TIEMPO_ESPERA_APERTURA_MS)
            return

        if self.state in ("DEPOSIT_WAIT_OPEN", "PICKUP_WAIT_OPEN"):
            if not closed:
                self.door_opened_at_ms = now_ms
                self.door_alert_active = False
                self.next_alert_beep_ms = now_ms
                self.state = "DEPOSIT_WAIT_CLOSE" if self.state == "DEPOSIT_WAIT_OPEN" else "PICKUP_WAIT_CLOSE"
                return
            if _ticks_diff(now_ms, self.state_until_ms) >= 0:
                if self.state == "DEPOSIT_WAIT_OPEN":
                    self._enter_idle()
                else:
                    self._enter_occupied()
            return

        if self.state in ("DEPOSIT_WAIT_CLOSE", "PICKUP_WAIT_CLOSE"):
            if closed:
                if self.state == "DEPOSIT_WAIT_CLOSE":
                    self._screen("CERRANDO", "Cerrando casillero...", "", self.st7789.MAGENTA)
                    self.state = "DEPOSIT_CLOSING"
                    self.state_until_ms = _ticks_add(now_ms, 1500)
                else:
                    self._enter_idle()
                return

            if _ticks_diff(now_ms, self.door_opened_at_ms) > TIEMPO_ALERTA_MS:
                if not self.door_alert_active:
                    self._screen(
                        "PUERTA ABIERTA",
                        "Cierre la puerta",
                        "por favor",
                        self.st7789.RED,
                        self.st7789.WHITE,
                    )
                    self.door_alert_active = True
                if _ticks_diff(now_ms, self.next_alert_beep_ms) >= 0:
                    self._beep(now_ms, 200)
                    self.next_alert_beep_ms = _ticks_add(now_ms, 500)
            return

        if self.state == "DEPOSIT_CLOSING":
            if _ticks_diff(now_ms, self.state_until_ms) >= 0:
                self._screen("SU PIN", "Anotelo, solo se muestra", "esta vez", self.st7789.YELLOW)
                text = " ".join(self.pin_code)
                self.tft.text(
                    self.font_large,
                    text,
                    (320 - len(text) * 16) // 2,
                    170,
                    self.st7789.WHITE,
                    self.st7789.BLACK,
                )
                self.state = "SHOW_PIN"
                self.state_until_ms = _ticks_add(now_ms, 10000)
            return

        if self.state == "SHOW_PIN":
            if _ticks_diff(now_ms, self.state_until_ms) >= 0:
                self._enter_occupied()
            return

        if self.state == "PIN_ERROR":
            if _ticks_diff(now_ms, self.state_until_ms) >= 0:
                if self.attempts >= 3:
                    self._screen("BLOQUEADO", "Espere 30 segundos", "", self.st7789.RED, self.st7789.WHITE)
                    self.state = "LOCKED_OUT"
                    self.state_until_ms = _ticks_add(now_ms, 30000)
                    self.attempts = 0
                else:
                    self._enter_occupied()
            return

        if self.state == "LOCKED_OUT":
            if _ticks_diff(now_ms, self.state_until_ms) >= 0:
                self._enter_occupied()
            return

        if self.state == "OCCUPIED" and touch:
            key = self._key_at(touch[0], touch[1])
            if key == "BORRAR":
                self.entered_pin = self.entered_pin[:-1]
                self._update_pin_input()
            elif key == "OK":
                if len(self.entered_pin) == LARGO_PIN:
                    if self.entered_pin == self.pin_code:
                        self._screen("ABIERTO", "Retire su paquete", "y cierre la puerta", self.st7789.CYAN)
                        self._open_relay(now_ms)
                        self.state = "PICKUP_WAIT_OPEN"
                        self.state_until_ms = _ticks_add(now_ms, TIEMPO_ESPERA_APERTURA_MS)
                    else:
                        self.attempts += 1
                        self.entered_pin = ""
                        self._screen("CODIGO INCORRECTO", "Intente de nuevo", "", self.st7789.RED, self.st7789.WHITE)
                        self._beep(now_ms, 400)
                        self.state = "PIN_ERROR"
                        self.state_until_ms = _ticks_add(now_ms, 1500)
            elif key is not None and len(self.entered_pin) < LARGO_PIN:
                self.entered_pin += key
                self._update_pin_input()

    def shutdown(self):
        self._set_relay(False)
        if self.buzzer is not None:
            self.buzzer.value(0)


def create_kiosk(settings):
    return KioskUI(settings)
