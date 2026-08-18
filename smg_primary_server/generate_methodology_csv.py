"""
Generate methodology.csv from PC client test results.
Separates uncontended P4 latency (odd messages) from contended (even messages)
to account for server-side lock contention from background EMS/status loops.
"""

import json
import csv
import os
import statistics

RESULTS_PATH = r"C:\Users\racas\Latex\Metodologia v3\case study SMG\Implementation\smg_primary_server\pc_client_results.json"
OUTPUT_PATH = r"C:\Users\racas\Latex\Metodologia v3\case study SMG\measurements\methodology.csv"

with open(RESULTS_PATH) as f:
    results = json.load(f)

p4_latencies = results.get("p4_latencies", [])
p4_count = results.get("p4_count", len(p4_latencies))

# Separate odd-indexed (uncontended) from even-indexed (contended) messages
# Odd indices (0,2,4...) = uncontended baseline ~2ms
# Even indices (1,3,5...) = contended by server background loops ~45ms
uncontended = [p4_latencies[i] for i in range(0, len(p4_latencies), 2)]
contended = [p4_latencies[i] for i in range(1, len(p4_latencies), 2)]

p4_total = sum(p4_latencies)
p4_avg = p4_total / p4_count if p4_count > 0 else 0
p4_min = min(p4_latencies) if p4_latencies else 0
p4_max = max(p4_latencies) if p4_latencies else 0
p4_stddev = statistics.stdev(p4_latencies) if len(p4_latencies) > 1 else 0

uncontended_avg = statistics.mean(uncontended) if uncontended else 0
uncontended_min = min(uncontended) if uncontended else 0
uncontended_max = max(uncontended) if uncontended else 0
uncontended_stddev = statistics.stdev(uncontended) if len(uncontended) > 1 else 0

contended_avg = statistics.mean(contended) if contended else 0
contended_min = min(contended) if contended else 0
contended_max = max(contended) if contended else 0

rows = [
    ["Metric", "Value", "Unit"],
    ["P2 (Root of Trust)", results["p2_duration_us"], "us"],
    ["P3 (Session Establishment)", results["p3_duration_us"], "us"],
    ["Handshake Total (P2+P3)", results["handshake_total_us"], "us"],
    ["P4 Transactions", p4_count, "count"],
    ["P4 Total Time", p4_total, "us"],
    ["P4 Average Latency (all)", round(p4_avg, 1), "us"],
    ["P4 Min Latency", p4_min, "us"],
    ["P4 Max Latency", p4_max, "us"],
    ["P4 Std Dev (all)", round(p4_stddev, 1), "us"],
    ["P4 Uncontended Avg (baseline)", round(uncontended_avg, 1), "us"],
    ["P4 Uncontended Min", uncontended_min, "us"],
    ["P4 Uncontended Max", uncontended_max, "us"],
    ["P4 Uncontended Std Dev", round(uncontended_stddev, 1), "us"],
    ["P4 Contended Avg (server lock)", round(contended_avg, 1), "us"],
    ["P4 Contended Min", contended_min, "us"],
    ["P4 Contended Max", contended_max, "us"],
    ["P5 (Logout)", results["p5_duration_us"], "us"],
    ["Errors", len(results.get("errors", [])), "count"],
]

os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

with open(OUTPUT_PATH, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerows(rows)

print(f"methodology.csv written to {OUTPUT_PATH}")
print()
for row in rows:
    print(f"  {row[0]:40s} {str(row[1]):>10s} {row[2]}")
