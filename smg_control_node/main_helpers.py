"""Helper functions for SMG Control Node main loop."""

import time
import gc
import machine

# Track total reconnects across the session. After 20, reset to avoid PMKSA cycling.
_reconnect_total = 0
_reconnect_fail_streak = 0


def safe_shutdown(ems, sec, comm, led):
    """Controlled shutdown: stop PWM, logout, close comms."""
    print("[main] === CONTROLLED SHUTDOWN ===")
    ems.stop_pwm()
    print("[main] PWM disabled (SSRs off)")

    # Attempt P5 logout unconditionally — the server may have closed the connection
    # but the logout() method catches its own errors, so we always try.
    try:
        if comm._sock is not None:
            comm._sock.settimeout(3)
            sec.logout(comm._sock)
        else:
            print("[main] No socket — skipping P5 logout")
    except Exception:
        print("[main] P5 logout skipped — socket gone")
    comm.close()
    print("[main] Session closed")

    led.off()
    print("[main] SMG stopped — node remains powered, waiting for button press")


def average_readings(readings):
    """Compute average of multiple SensorReading objects."""
    n = len(readings)
    if n == 1:
        return readings[0]

    avg_v_bus  = sum(r.V_bus  for r in readings) / n
    avg_i_bus  = sum(r.I_bus  for r in readings) / n
    avg_p_bus  = sum(r.P_bus  for r in readings) / n
    avg_v_gen  = sum(r.V_gen  for r in readings) / n
    avg_i_gen  = sum(r.I_gen  for r in readings) / n
    avg_p_gen  = sum(r.P_gen  for r in readings) / n
    avg_i_node = sum(r.I_node for r in readings) / n
    avg_p_node = sum(r.P_node for r in readings) / n

    any_saturated = any(r.saturated for r in readings)
    from sensor_module import SensorReading
    return SensorReading(avg_v_bus, avg_i_bus, avg_p_bus,
                         avg_v_gen, avg_i_gen, avg_p_gen,
                         avg_i_node, avg_p_node,
                         readings[-1].timestamp_ms, any_saturated)


def reconnect(sec, comm):
    """Attempt reconnection with secure session re-establishment."""
    global _reconnect_total, _reconnect_fail_streak
    for attempt in range(3):
        print("[main] Reconnect attempt {}/3...".format(attempt + 1))

        sock = comm.reconnect()
        if sock is None:
            time.sleep(5 * (attempt + 1))
            continue

        if sec.handshake(sock):
            print("[main] Reconnected and session restored.")
            _reconnect_total += 1
            _reconnect_fail_streak = 0
            gc.collect()
            gc.collect()
            print("[main] GC after reconnect: {} bytes free".format(gc.mem_free()))

            if _reconnect_total >= 20:
                print("[main] Reconnect limit reached ({}), resetting to clear PMKSA".format(_reconnect_total))
                _reconnect_total = 0
                time.sleep(1)
                machine.reset()

            return sock

        comm.close()
        time.sleep(5 * (attempt + 1))

    # All 3 attempts failed — track consecutive failures and hard-reset if stuck.
    _reconnect_fail_streak += 1
    print("[main] Reconnect failed (streak={})".format(_reconnect_fail_streak))
    if _reconnect_fail_streak >= 2:
        print("[main] {} consecutive failures — hardware reset to clear WiFi state".format(
            _reconnect_fail_streak))
        time.sleep(1)
        machine.reset()

    return None


def continue_degraded(sensors, ems, metrics, loop_start, read_interval_ms):
    """Handle a single degraded cycle within the main loop."""
    reading = sensors.get_last_reading()
    if reading:
        duty = ems.infer(reading.V_bus)
        ems.update_pwm(duty)

    loop_end = time.ticks_us()
    metrics.record_loop(time.ticks_diff(loop_end, loop_start))

    sleep_ms = max(10, read_interval_ms - (time.ticks_diff(loop_end, loop_start) // 1000))
    time.sleep_ms(sleep_ms)
