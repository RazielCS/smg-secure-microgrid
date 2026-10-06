#!/usr/bin/env python3
"""
Adds/updates one device entry in puf_node_registry.json (server-side registry
consumed by rot_challenge_service.RotSession._handshake).

shs_a_ref = SHA-256(PUF_A) is the device's enrollment-time verifier. It is NOT
generatable on the host: it depends on that specific chip's physical SRAM PUF
response (rot_identity_derive_shs_a() in the firmware), so this tool only
*records* a value already captured from a real, enrolled ESP32 -- it does not
produce one itself. Until a hardware enrollment + read-out step exists (see
task report), run this manually with a placeholder / test value for protocol
development, or leave the registry empty.

Usage:
    python register_puf_node.py --id-a <32-hex> --shs-a-ref <64-hex> --label SMG_NODE_01
    python register_puf_node.py --list
    python register_puf_node.py --remove <32-hex>
"""
import argparse
import json
import os

DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "puf_node_registry.json")


def load(path):
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save(path, registry):
    with open(path, "w") as f:
        json.dump(registry, f, indent=2, sort_keys=True)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--id-a", help="Device ID (32 hex chars / 16 bytes)")
    ap.add_argument("--shs-a-ref", help="SHA-256(PUF_A) captured at enrollment (64 hex chars / 32 bytes)")
    ap.add_argument("--label", default=None, help="Human-readable node label")
    ap.add_argument("--remove", metavar="ID_A", help="Remove the entry for this id_a and exit")
    ap.add_argument("--list", action="store_true", help="List registered entries and exit")
    args = ap.parse_args()

    registry = load(args.registry)

    if args.list:
        if not registry:
            print("(empty registry)")
        for id_a_hex, entry in registry.items():
            print(f"{id_a_hex}  label={entry.get('label', '?')}  "
                  f"shs_a_ref={entry.get('shs_a_ref', '?')[:16]}...")
        return

    if args.remove:
        if args.remove in registry:
            del registry[args.remove]
            save(args.registry, registry)
            print(f"Removed {args.remove}")
        else:
            print(f"Not found: {args.remove}")
        return

    if not args.id_a or not args.shs_a_ref:
        ap.error("--id-a and --shs-a-ref are required (or use --list / --remove)")
    if len(args.id_a) != 32:
        ap.error("--id-a must be 32 hex chars (16 bytes)")
    if len(args.shs_a_ref) != 64:
        ap.error("--shs-a-ref must be 64 hex chars (32 bytes)")

    registry[args.id_a] = {
        "shs_a_ref": args.shs_a_ref,
        "label": args.label or args.id_a[:8],
    }
    save(args.registry, registry)
    print(f"Registered {args.id_a} -> {registry[args.id_a]}")
    print(f"Wrote: {args.registry}")


if __name__ == "__main__":
    main()
