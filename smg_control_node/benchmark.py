"""
SMG Node Offline Benchmark — Pre-deployment timing characterisation.

Runs on the ESP32 without WiFi or SecureNode, characterising the two
real-time subsystems (sensor acquisition and fuzzy EMS) in isolation.
Outputs both a console report and /benchmark.csv on the ESP32 flash.

Usage (from MicroPython REPL):
    exec(open('benchmark.py').read())

Do NOT run while main.py is active — main.py initialises a 5 s WDT
that would time out during Phase 3 (stability loop).

Phases:
  1. Sensor read timing:   N=100 reads, full 16-sample oversampling
  2. EMS inference timing: N=100 inferences, synthetic 10-14 V sweep
  3. Heap stability:       N=100 combined sensor+EMS loops, heap sampled
     every 10 iterations to detect memory leaks

Output columns in /benchmark.csv:
  metric, mean, std, min, max, unit
"""

import time
import gc
import math

import config_manager
from sensor_module import SensorModule
from ems_module import FuzzyEMS

N = 100


def _welford(data):
    """Return (mean, std, min_val, max_val) for a list of numbers."""
    n = len(data)
    if n == 0:
        return 0.0, 0.0, 0, 0
    mean, M2 = 0.0, 0.0
    for i, x in enumerate(data, 1):
        delta = x - mean
        mean += delta / i
        M2 += delta * (x - mean)
    std = math.sqrt(M2 / (n - 1)) if n > 1 else 0.0
    return mean, std, min(data), max(data)


def run():
    config = config_manager.load()

    gc.collect()
    heap_start = gc.mem_free()

    print("=" * 55)
    print("  SMG NODE OFFLINE BENCHMARK")
    print("  N = {}  iterations per phase".format(N))
    print("  Heap at entry: {} bytes".format(heap_start))
    print("=" * 55)

    # ── Phase 1: Sensor read timing ──────────────────────────────────────────
    print("\n[Phase 1] Sensor read timing ({} reads)...".format(N))
    sensors = SensorModule(config)
    gc.collect()
    heap_after_sensors = gc.mem_free()

    sensor_times = []
    for _ in range(N):
        t0 = time.ticks_us()
        sensors.read()
        t1 = time.ticks_us()
        sensor_times.append(time.ticks_diff(t1, t0))
        time.sleep_ms(5)    # avoid thermal/ADC settling issues between back-to-back reads

    s_mean, s_std, s_min, s_max = _welford(sensor_times)
    print("  avg/std/min/max: {:.1f} / {:.1f} / {} / {} us".format(
        s_mean, s_std, s_min, s_max))

    # ── Phase 2: EMS inference timing ────────────────────────────────────────
    print("\n[Phase 2] EMS inference timing ({} inferences)...".format(N))
    ems = FuzzyEMS(config)
    gc.collect()
    heap_after_ems = gc.mem_free()

    # Synthetic voltage sweep across the full operating range (10 V to 14 V)
    # to exercise all 9 fuzzy rules and expose worst-case inference time.
    step = 4.0 / max(N - 1, 1)
    ems_times = []
    duty_values = []
    for i in range(N):
        v = 10.0 + i * step
        t0 = time.ticks_us()
        duty = ems.infer(v)
        t1 = time.ticks_us()
        ems_times.append(time.ticks_diff(t1, t0))
        duty_values.append(duty)

    e_mean, e_std, e_min, e_max = _welford(ems_times)
    print("  avg/std/min/max: {:.1f} / {:.1f} / {} / {} us".format(
        e_mean, e_std, e_min, e_max))
    print("  Duty range: {} - {} (raw 0-1023)".format(min(duty_values), max(duty_values)))

    # ── Phase 3: Heap stability ───────────────────────────────────────────────
    print("\n[Phase 3] Heap stability ({} sensor+EMS loops)...".format(N))
    heap_samples = []   # (loop_index, free_bytes)
    for i in range(N):
        reading = sensors.read()
        ems.infer(reading.V_bus)
        if i % 10 == 0:
            gc.collect()
            heap_samples.append((i, gc.mem_free()))
        time.sleep_ms(10)

    gc.collect()
    heap_final = gc.mem_free()
    heap_drift = heap_samples[-1][1] - heap_samples[0][1] if len(heap_samples) >= 2 else 0
    leak = heap_drift < -500   # >500 bytes lost over 100 loops is a probable leak

    print("  Heap first sample : {} bytes (loop {})".format(
        heap_samples[0][1], heap_samples[0][0]) if heap_samples else "  (no samples)")
    print("  Heap last sample  : {} bytes (loop {})".format(
        heap_samples[-1][1], heap_samples[-1][0]) if heap_samples else "")
    print("  Heap drift        : {} bytes over {} loops".format(heap_drift, N))
    print("  Leak suspected    : {}".format("YES — {:.0f} bytes lost".format(-heap_drift)
                                             if leak else "no"))

    # ── Results summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  BENCHMARK RESULTS")
    print("=" * 55)
    print("  Heap: start={} after_sensors={} after_ems={} final={}".format(
        heap_start, heap_after_sensors, heap_after_ems, heap_final))
    print("  Sensor: {:.1f} ± {:.1f} us  (min={} max={})".format(
        s_mean, s_std, s_min, s_max))
    print("  EMS:    {:.1f} ± {:.1f} us  (min={} max={})".format(
        e_mean, e_std, e_min, e_max))
    print("  Heap drift: {} bytes / {} loops".format(heap_drift, N))
    print("=" * 55)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    try:
        with open('/benchmark.csv', 'w') as f:
            f.write("metric,mean,std,min,max,unit\n")
            f.write("sensor_read,{:.1f},{:.1f},{},{},us\n".format(
                s_mean, s_std, s_min, s_max))
            f.write("ems_inference,{:.1f},{:.1f},{},{},us\n".format(
                e_mean, e_std, e_min, e_max))
            f.write("heap_start,{},,,, bytes\n".format(heap_start))
            f.write("heap_after_sensors,{},,,, bytes\n".format(heap_after_sensors))
            f.write("heap_after_ems,{},,,, bytes\n".format(heap_after_ems))
            f.write("heap_final,{},,,, bytes\n".format(heap_final))
            f.write("heap_drift_N{},{},,,, bytes\n".format(N, heap_drift))
        print("[benchmark] Saved to /benchmark.csv")
    except Exception as e:
        print("[benchmark] Could not save CSV: {}".format(e))

    return {
        'sensor_avg_us': s_mean,
        'sensor_std_us': s_std,
        'sensor_min_us': s_min,
        'sensor_max_us': s_max,
        'ems_avg_us': e_mean,
        'ems_std_us': e_std,
        'ems_min_us': e_min,
        'ems_max_us': e_max,
        'heap_start': heap_start,
        'heap_after_sensors': heap_after_sensors,
        'heap_after_ems': heap_after_ems,
        'heap_final': heap_final,
        'heap_drift': heap_drift,
    }


run()
