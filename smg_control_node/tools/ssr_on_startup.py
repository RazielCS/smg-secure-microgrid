"""
Force all 3 SSR PWM channels to full duty (100%) immediately at startup —
no button/SW1 required. For boards where SW1 is not functional.

Continuously prints live sensor readings for calibration/measurement
cross-check against a multimeter. Does NOT connect WiFi or touch the RTC
bench counter.

Run with:
    python -m mpremote connect COM12 run tools/ssr_on_startup.py
    python -m mpremote connect COM16 run tools/ssr_on_startup.py

To stop: interrupt the host process, then hard-reset the board
(mpremote connect COMx reset) to guarantee the PWM outputs are cleared.
"""

import machine
import time
import config_manager
from sensor_module import SensorModule

config = config_manager.load()
sensors = SensorModule(config)

pwms = []
for pin in config_manager.PIN_PWM_SSRS:
    pwm = machine.PWM(machine.Pin(pin))
    pwm.freq(1000)
    pwm.duty(0)
    pwms.append(pwm)

print("=" * 50)
print("  SSR Startup Conduction Test (no button required)")
print("=" * 50)

for pwm in pwms:
    pwm.duty(1023)
print("[OK] SSRs ON (100% duty, all 3 channels: Bus/Solar/Gen).")
print("Take your measurements now.")
print()

try:
    while True:
        r = sensors.read()
        print("V_bus={:.3f}V  I_bus={:.3f}A  P_bus={:.2f}W  |  "
              "V_gen={:.3f}V  I_gen={:.3f}A  P_gen={:.2f}W  |  "
              "I_node={:.3f}A  P_node={:.2f}W".format(
                  r.V_bus, r.I_bus, r.P_bus,
                  r.V_gen, r.I_gen, r.P_gen,
                  r.I_node, r.P_node))
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    for pwm in pwms:
        pwm.duty(0)
    print("[OK] SSRs OFF. Exiting.")
