"""
Dual-node bench test: runs main.py on NODE_01 (COM11) and NODE_02 (COM6)
simultaneously, captures serial output from both, and retrieves
methodology.csv from each node when their bench cycle completes.

Prerequisites:
  - RPi server running on 10.42.0.1:5000 (SMG_Primary AP active)
  - Both nodes provisioned and files deployed
  - node_registry.json uploaded to RPi

Usage:
    python dual_bench_test.py

Output files (in measurements/):
  node01_bench_serial.log   raw serial output from NODE_01
  node02_bench_serial.log   raw serial output from NODE_02
  node01_methodology.csv    metrics from NODE_01
  node02_methodology.csv    metrics from NODE_02
"""

import subprocess
import threading
import time
import os
import sys

# ── Configuration ─────────────────────────────────────────────────────────────
NODES = [
    {"label": "NODE_01", "port": "COM16", "log": "node01_bench_serial.log",
     "csv": "node01_methodology.csv"},
    {"label": "NODE_02", "port": "COM12",  "log": "node02_bench_serial.log",
     "csv": "node02_methodology.csv"},
]

OUTPUT_DIR = r"C:\Users\racas\Latex\Metodologia v3\case study SMG\measurements"
FW_DIR = r"C:\Users\racas\Latex\Metodologia v3\case study SMG\Implementation\smg_control_node"

BENCH_TIMEOUT_S = 900   # 15 min max (400 sends at ~1.5 s/send = ~600 s)


# ── Per-node worker ───────────────────────────────────────────────────────────
def run_node(node, results):
    label = node["label"]
    port = node["port"]
    log_path = os.path.join(OUTPUT_DIR, node["log"])

    print("[{}] Hard-resetting...".format(label))
    subprocess.run(
        ["python", "-m", "mpremote", "connect", port, "reset"],
        capture_output=True, timeout=10
    )
    time.sleep(3)

    print("[{}] Starting main.py on {}".format(label, port))

    cmd = ["python", "-m", "mpremote", "connect", port, "run", "main.py"]

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=FW_DIR,
        )

        start = time.time()
        completed = False

        while proc.poll() is None:
            elapsed = time.time() - start

            if elapsed > BENCH_TIMEOUT_S:
                print("[{}] TIMEOUT after {}s — terminating".format(label, BENCH_TIMEOUT_S))
                proc.terminate()
                results[label] = "TIMEOUT"
                return

            time.sleep(4)

            try:
                with open(log_path) as f:
                    content = f.read()
            except Exception:
                continue

            if "sends completed" in content:
                print("[{}] All sends completed!".format(label))
                completed = True
                break
            elif "WiFi failed" in content or "FATAL" in content:
                print("[{}] ERROR detected — check log".format(label))
                results[label] = "ERROR"
                proc.terminate()
                return

            # Print latest bench progress line
            for line in reversed(content.strip().split("\n")):
                if "BENCH" in line:
                    print("[{}] {}".format(label, line.strip()))
                    break

        proc.wait(timeout=15)

    if completed:
        print("[{}] Retrieving methodology.csv...".format(label))
        csv_path = os.path.join(OUTPUT_DIR, node["csv"])
        cmd_cp = [
            "python", "-m", "mpremote", "connect", port,
            "cp", ":/methodology.csv", csv_path,
        ]
        r = subprocess.run(cmd_cp, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and os.path.exists(csv_path):
            print("[{}] methodology.csv saved to {}".format(label, csv_path))
            results[label] = "OK"
        else:
            print("[{}] WARNING: could not retrieve methodology.csv".format(label))
            print("[{}] stderr: {}".format(label, r.stderr[:200]))
            results[label] = "NO_CSV"
    else:
        results[label] = "INCOMPLETE"


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SMG Dual-Node Bench Test")
    print("  NODE_01: COM16   NODE_02: COM12")
    print("  400 sends per node  |  timeout: {}s".format(BENCH_TIMEOUT_S))
    print("=" * 60)
    print()
    print("  PRE-FLIGHT CHECKLIST:")
    print("  [ ] RPi AP 'SMG_Primary' is active")
    print("  [ ] server.py running on RPi (10.42.0.1:5000)")
    print("  [ ] node_registry.json uploaded to RPi with both nodes")
    print("  [ ] Both COM6 and COM11 visible in Device Manager")
    print("  [ ] Physical DC bus connected and MPPT/sensors wired")
    print()

    if "-y" in sys.argv or "--yes" in sys.argv:
        print("  All checks passed? Start test? (y/n): y  [auto-confirmed via --yes]")
    else:
        confirm = input("  All checks passed? Start test? (y/n): ")
        if confirm.strip().lower() != "y":
            print("  Aborted.")
            return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = {}
    threads = []

    print()
    print("  Launching both nodes simultaneously...")
    print()

    for node in NODES:
        t = threading.Thread(
            target=run_node,
            args=(node, results),
            daemon=True,
        )
        threads.append(t)

    # Start both threads within milliseconds of each other
    for t in threads:
        t.start()
        time.sleep(0.5)

    for t in threads:
        t.join(timeout=BENCH_TIMEOUT_S + 60)

    print()
    print("=" * 60)
    print("  DUAL BENCH TEST COMPLETE")
    print("=" * 60)
    for label, status in results.items():
        print("  {}: {}".format(label, status))
    print()

    all_ok = all(s in ("OK", "NO_CSV") for s in results.values())
    if all_ok:
        print("  Both nodes completed. Check measurements/ for CSV files.")
        print()
        print("  Next: read V_bus mean/std and node_power from each CSV,")
        print("  then update \\ph{} placeholders in Article/main.tex:")
        print("    Line 844: V_bus mean ± std")
        print("    Line 845: V_bus tracking error (|V_bus - 12.0| mean)")
        print("    Line 846: Node power (W)")
    else:
        print("  One or more nodes did not complete cleanly. Check serial logs:")
        for node in NODES:
            print("    {}".format(os.path.join(OUTPUT_DIR, node["log"])))


if __name__ == "__main__":
    main()
