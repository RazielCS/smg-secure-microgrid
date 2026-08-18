"""
LED semaphore for SMG control node status signaling.

3 discrete LEDs (not RGB): Red=GPIO23, Yellow=GPIO22, Green=GPIO21.
Each driven through a 220Ω series resistor (R1, R3, R2 respectively).
Hardware pull-down on base resistors ensures LEDs are OFF on power-up.

States:
  OFF       - All off (node idle / waiting for button press)
  BOOT      - Yellow blink (initializing)
  WIFI      - Yellow solid (connecting to WiFi)
  HANDSHAKE - Yellow fast blink (RoT protocol)
  ONLINE    - Green solid (operational, sending data)
  SEND      - Green blink (data transmission)
  ERROR     - Red solid (sensor/EMS failure)
  COMMS_FAIL- Red blink (TCP disconnected, retrying)
"""

import time
import machine


class LedSemaphore:
    """Controls status LEDs (Red/Yellow/Green) for node state indication."""

    def __init__(self, pin_red=23, pin_yellow=22, pin_green=21):
        self.led_r = machine.Pin(pin_red,    machine.Pin.OUT)
        self.led_y = machine.Pin(pin_yellow, machine.Pin.OUT)
        self.led_g = machine.Pin(pin_green,  machine.Pin.OUT)
        self._state = "OFF"
        self.off()

    def _set(self, r, y, g):
        self.led_r.value(r)
        self.led_y.value(y)
        self.led_g.value(g)

    def off(self):
        self._state = "OFF"
        self._set(0, 0, 0)

    def boot(self):
        self._state = "BOOT"
        self._blink(0, 1, 0, 200, 200)

    def wifi_connecting(self):
        self._state = "WIFI"
        self._set(0, 1, 0)

    def handshake(self):
        self._state = "HANDSHAKE"
        self._blink(0, 1, 0, 100, 100)

    def online(self):
        self._state = "ONLINE"
        self._set(0, 0, 1)

    def sending(self):
        self._state = "SEND"
        self._blink(0, 0, 1, 50, 150)

    def error(self):
        self._state = "ERROR"
        self._set(1, 0, 0)

    def comms_fail(self):
        self._state = "COMMS_FAIL"
        self._blink(1, 0, 0, 300, 300)

    def _blink(self, r, y, g, on_ms, off_ms):
        self._set(r, y, g)
        time.sleep_ms(on_ms)
        self._set(0, 0, 0)
        time.sleep_ms(off_ms)

    def pulse(self, r, y, g, duration_ms=100):
        """Single pulse for event indication."""
        self._set(r, y, g)
        time.sleep_ms(duration_ms)
        self._set(0, 0, 0)

    def get_state(self):
        return self._state

    def stop(self):
        self.off()
