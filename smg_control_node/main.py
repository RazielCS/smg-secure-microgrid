"""SMG Control Node — Main Entry Point."""

import time
import gc
import machine
import config_manager


def main():
    print("=" * 50)
    print("  SMG Control Node Firmware v1.0")
    print("  MicroPython + SecureNode v3 + HKDF")
    print("=" * 50)

    config = config_manager.load()
    identity = config_manager.get_identity()

    if identity is None:
        print("[main] FATAL: No identity in NVS.")
        machine.reset()

    is_primary = identity.get("is_primary", b'\x00') == b'\x01'
    if is_primary:
        print("[main] PRIMARY node — this firmware is for SECONDARY nodes only.")
        machine.reset()

    bench_mode = config.get("bench_mode", False)
    if bench_mode:
        print("[main] BENCH MODE: sensor brown-out checks bypassed")

    # Activate WiFi driver (no connect yet — connection happens after imports+crypto
    # so that TCP connect and the first P2 sendall happen within milliseconds of
    # each other, avoiding link-idle issues that block sendall indefinitely).
    import network
    gc.collect()
    gc.collect()
    print("[main] Free heap before WiFi init: {} bytes".format(gc.mem_free()))

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep(1)

    ssid = config.get("wifi_ssid", "")
    password = config.get("wifi_pass", "")

    # Load ONLY lightweight modules before WiFi (comm, led, button).
    # Heavy modules (security, crypto, sensors, EMS) are deferred until after
    # WiFi is connected to avoid OOM on low-heap ESP32 variants.
    print("[main] Pre-WiFi init...")
    from comm_module import CommManager
    from led_semaphore import LedSemaphore
    gc.collect()

    comm = CommManager(config, wlan=wlan)
    led = LedSemaphore()
    btn_pin = machine.Pin(config_manager.PIN_BUTTON, machine.Pin.IN, machine.Pin.PULL_DOWN)

    if bench_mode:
        print("[main] BENCH MODE: skipping SW1, auto-starting")
        wdt = None
    else:
        wdt = machine.WDT(timeout=5000)
        print("[main] Hardware watchdog initialized (5s timeout)")
        print("[main] Ready. Press SW1 to start...")
        led.boot()
        while btn_pin.value() == 0:
            time.sleep_ms(50)
        time.sleep_ms(50)
        while btn_pin.value() == 1:
            time.sleep_ms(50)
        print("[main] SW1 pressed — starting")

    print("[main] Connecting to WiFi '{}'...".format(ssid))
    wlan.connect(ssid, password)

    wifi_start = time.time()
    while not wlan.isconnected():
        if time.time() - wifi_start > 30:
            print("[main] WiFi timed out — hard reset")
            machine.reset()
        time.sleep(0.2)

    print("[main] WiFi connected: {}".format(wlan.ifconfig()[0]))
    time.sleep(1)  # let lwIP populate ARP/routing table before TCP connect
    gc.collect()

    # NOW import heavy modules (security, crypto, etc.) — WiFi heap already allocated.
    print("[main] Loading crypto modules...")
    from secure_node import SecureNode
    from security_module import SecurityManager
    from metrics import MetricsCollector
    from main_helpers import safe_shutdown, average_readings, continue_degraded, reconnect
    gc.collect()
    print("[main] Free heap post-WiFi: {} bytes".format(gc.mem_free()))

    metrics = MetricsCollector()
    metrics.snapshot_heap("boot")

    # Precompute P2 crypto values.
    print("[main] Pre-computing P2...")
    sec = SecurityManager(config, identity)
    sec.precompute_p2()
    gc.collect()
    print("[main] Free heap pre-connect: {} bytes".format(gc.mem_free()))

    # Create TCP socket NOW (before hardware drivers eat system heap for ADC/PWM).
    sock = comm.connect_tcp()

    if not comm.wlan.isconnected():
        print("[main] WiFi dropped before handshake — hard reset")
        machine.reset()

    if sock is None or not sec.handshake(sock):
        if sock is not None:
            comm.close()
        print("[main] Initial handshake failed — attempting recovery...")
        sock = reconnect(sec, comm)

    if sock is None:
        print("[main] WiFi unrecoverable — hard reset")
        machine.reset()

    # Handshake complete — NOW initialize hardware drivers.
    # SensorModule and FuzzyEMS are imported here (after handshake) so their
    # C-level ADC/PWM initialization doesn't consume system heap before socket
    # creation (which would cause ENOMEM on some builds).
    from sensor_module import SensorModule
    from ems_module import FuzzyEMS
    print("[main] Initializing sensors...")
    sensors = SensorModule(config)
    metrics.snapshot_heap("sensors")

    print("[main] Initializing EMS...")
    ems = FuzzyEMS(config)
    ems.init_pwm(pins=config_manager.PIN_PWM_SSRS)
    metrics.snapshot_heap("ems")

    led.online()
    print("[main] Secure session established. Starting main loop.")
    metrics.snapshot_heap("session_established")

    metrics.set_v_nominal(config.get("ems", {}).get("v_bus_nominal", 12.0))

    read_interval_ms = int(config.get("read_interval_s", 0.333) * 1000)
    send_interval_ms = int(config.get("send_interval_s", 1.0) * 1000)

    bench_send_max = config.get("bench_send_max", 60) if bench_mode else 0

    def _load_rtc_bench():
        try:
            mem = machine.RTC().memory()
            if len(mem) >= 6 and mem[0] == 0xB3 and mem[1] == 0xC4:
                return (mem[2] << 24) | (mem[3] << 16) | (mem[4] << 8) | mem[5]
        except Exception:
            pass
        return 0

    def _save_rtc_bench(n):
        try:
            machine.RTC().memory(bytes([0xB3, 0xC4,
                                        (n >> 24) & 0xFF, (n >> 16) & 0xFF,
                                        (n >> 8) & 0xFF, n & 0xFF]))
        except Exception:
            pass

    def _clear_rtc_bench():
        try:
            machine.RTC().memory(b'\x00\x00\x00\x00\x00\x00')
        except Exception:
            pass

    bench_send_count = _load_rtc_bench() if bench_mode else 0
    if bench_mode and bench_send_count > 0:
        print("[main] BENCH RESUMED: continuing from send {}".format(bench_send_count))
    last_send_ms = time.ticks_ms()
    readings_since_send = []
    loop_counter = 0

    while True:
        loop_start = time.ticks_us()
        loop_counter += 1

        try:
            reading = sensors.read()
            sensor_stats = sensors.get_read_stats()
            metrics.record_sensor_read(sensor_stats.get('last_read_us', 0))
            metrics.record_v_bus(reading.V_bus)
            metrics.record_node_power(reading.P_node)
            metrics.maybe_snapshot_heap()

            if reading.V_bus < 9.0 and not bench_mode:
                print("[main] BROWN-OUT: V_bus={:.2f}V".format(reading.V_bus))
                ems.stop_pwm()
                led.error()
                while btn_pin.value() == 0:
                    time.sleep_ms(50)
                time.sleep_ms(50)
                while btn_pin.value() == 1:
                    time.sleep_ms(50)
                led.online()

            if wdt:
                wdt.feed()

            duty = ems.infer(reading.V_bus)
            ems.update_pwm(duty)
            ems_stats = ems.get_stats()
            metrics.record_ems_inference(ems_stats.get('last_inference_us', 0))

            readings_since_send.append(reading)

            now_ms = time.ticks_ms()
            if time.ticks_diff(now_ms, last_send_ms) >= send_interval_ms:
                if readings_since_send:
                    avg_reading = average_readings(readings_since_send)
                    payload = avg_reading.to_json_bytes()

                    # Proactive WiFi check: if WiFi dropped, reconnect before sending.
                    if not comm.wlan.isconnected():
                        print("[main] WiFi dropped — reconnecting before send...")
                        led.comms_fail()
                        sock = reconnect(sec, comm)
                        if sock is None:
                            readings_since_send = []
                            last_send_ms = now_ms
                            continue_degraded(sensors, ems, metrics,
                                              loop_start, read_interval_ms)
                            continue
                        led.online()

                    # Guard: TCP socket dead even though WiFi layer is up (e.g. after
                    # a failed reconnect attempt in a prior send interval).
                    if sock is None:
                        print("[main] Socket dead — re-establishing TCP+session...")
                        led.comms_fail()
                        sock = reconnect(sec, comm)
                        if sock is None:
                            readings_since_send = []
                            last_send_ms = now_ms
                            continue_degraded(sensors, ems, metrics,
                                              loop_start, read_interval_ms)
                            continue
                        led.online()

                    led.sending()
                    t0 = time.ticks_us()
                    success = sec.send_data(sock, payload)
                    t1 = time.ticks_us()
                    metrics.record_send(time.ticks_diff(t1, t0), success)
                    metrics.record_payload_size(len(payload))

                    if not success:
                        print("[main] Send failed, attempting reconnect...")
                        led.comms_fail()
                        sock = reconnect(sec, comm)
                        if sock is None:
                            readings_since_send = []
                            last_send_ms = now_ms
                            continue_degraded(sensors, ems, metrics,
                                              loop_start, read_interval_ms)
                            continue

                    led.online()
                    readings_since_send = []
                    last_send_ms = now_ms

                    setpoint = sec.receive_setpoint(sock)
                    if setpoint and 'p_demand' in setpoint:
                        ems.set_demand(setpoint['p_demand'])

                    if bench_mode:
                        bench_send_count += 1
                        _save_rtc_bench(bench_send_count)
                        print("[main] BENCH {}/{}".format(bench_send_count, bench_send_max))
                        if bench_send_count >= bench_send_max:
                            print("[main] BENCH MODE: {} sends completed — exporting".format(bench_send_count))
                            sec.print_metrics()
                            metrics.print_summary(
                                security_metrics=sec.get_metrics(),
                                sensor_stats=sensors.get_read_stats(),
                                ems_stats=ems.get_stats(),
                                comm_stats=comm.get_stats(),
                            )
                            metrics.print_methodology_table(security_metrics=sec.get_metrics())
                            safe_shutdown(ems, sec, comm, led)
                            metrics.export_methodology_csv('/methodology.csv',
                                                           security_metrics=sec.get_metrics())
                            _clear_rtc_bench()
                            return

                if btn_pin.value() == 1:
                    print("[main] SW1 pressed — stopping")
                    sec.print_metrics()
                    metrics.print_summary(
                        security_metrics=sec.get_metrics(),
                        sensor_stats=sensors.get_read_stats(),
                        ems_stats=ems.get_stats(),
                        comm_stats=comm.get_stats(),
                    )
                    metrics.print_methodology_table(security_metrics=sec.get_metrics())
                    safe_shutdown(ems, sec, comm, led)
                    metrics.export_methodology_csv('/methodology.csv',
                                                   security_metrics=sec.get_metrics())
                    break

            loop_end = time.ticks_us()
            loop_duration = time.ticks_diff(loop_end, loop_start)
            metrics.record_loop(loop_duration)

            sleep_ms = max(10, read_interval_ms - (loop_duration // 1000))
            time.sleep_ms(sleep_ms)

            if loop_counter % 10 == 0:
                gc.collect()
                free = gc.mem_free()
                if free < 20000:
                    print("[main] WARNING: Low heap: {} bytes".format(free))

        except KeyboardInterrupt:
            print("\n[main] Shutting down...")
            sec.print_metrics()
            metrics.print_summary(
                security_metrics=sec.get_metrics(),
                sensor_stats=sensors.get_read_stats(),
                ems_stats=ems.get_stats(),
                comm_stats=comm.get_stats(),
            )
            metrics.print_methodology_table(security_metrics=sec.get_metrics())
            safe_shutdown(ems, sec, comm, led)
            metrics.export_methodology_csv('/methodology.csv',
                                           security_metrics=sec.get_metrics())
            break

        except Exception as e:
            print("[main] Loop error: {}".format(e))
            led.error()
            time.sleep_ms(1000)
            led.online()


if __name__ == "__main__":
    main()
