#!/usr/bin/env python3
"""
Adds/updates one device entry in puf_node_registry.json (server-side registry
consumed by rot_challenge_service.RotSession._handshake).

pk_a is the device's P-256 public key (uncompressed point, 65 bytes / 130 hex
chars), captured once from the firmware's one-time provisioning-export serial
printout (rot_identity_derive_keypair() in the firmware derives the matching
private key sk_a fresh from the PUF response every boot; sk_a never leaves
the device, so pk_a is NOT generatable on the host -- this tool only
*records* a value already captured from a real, enrolled ESP32).

shs_b is this node's own server verifier: this tool GENERATES a fresh random
value for it on every new registration (never reused across nodes -- each
node gets its own, so recovering one node's shs_b from its NVS cannot be
used to impersonate the server to any other node). Print the follow-up
provision_server_ref.py command this tool suggests to pin that value into
the node's NVS image.

Usage:
    python register_puf_node.py --id-a <32-hex> --pk-a <130-hex> --label SMG_NODE_01
    python register_puf_node.py --list
    python register_puf_node.py --remove <32-hex>
"""
import argparse
import json
import os
import secrets

DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "puf_node_registry.json")
DEFAULT_SERVER_IDENTITY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "puf_server_identity.json")

ROT_PUBKEY_HEX_LEN = 65 * 2
ROT_DIGEST_LEN = 32


def load(path):
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save(path, registry):
    with open(path, "w") as f:
        json.dump(registry, f, indent=2, sort_keys=True)
        f.write("\n")


def read_id_b(path):
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f).get("id_b")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--server-identity", default=DEFAULT_SERVER_IDENTITY,
                     help="Used only to print the follow-up provisioning command (reads id_b)")
    ap.add_argument("--id-a", help="Device ID (32 hex chars / 16 bytes)")
    ap.add_argument("--pk-a", help="Device P-256 public key, uncompressed point "
                                    f"({ROT_PUBKEY_HEX_LEN} hex chars / 65 bytes)")
    ap.add_argument("--label", default=None, help="Human-readable node label")
    ap.add_argument("--shs-b", default=None,
                     help="Use this exact value instead of generating a fresh one (bring-up "
                          "only: e.g. re-registering a node whose NVS already has a V_B pinned "
                          "from a prior provision_server_ref.py run). Must be 64 hex chars.")
    ap.add_argument("--remove", metavar="ID_A", help="Remove the entry for this id_a and exit")
    ap.add_argument("--list", action="store_true", help="List registered entries and exit")
    args = ap.parse_args()

    registry = load(args.registry)

    if args.list:
        if not registry:
            print("(empty registry)")
        for id_a_hex, entry in registry.items():
            print(f"{id_a_hex}  label={entry.get('label', '?')}  "
                  f"pk_a={entry.get('pk_a', '?')[:16]}...  "
                  f"shs_b={entry.get('shs_b', '?')[:16]}...")
        return

    if args.remove:
        if args.remove in registry:
            del registry[args.remove]
            save(args.registry, registry)
            print(f"Removed {args.remove}")
        else:
            print(f"Not found: {args.remove}")
        return

    if not args.id_a or not args.pk_a:
        ap.error("--id-a and --pk-a are required (or use --list / --remove)")
    if len(args.id_a) != 32:
        ap.error("--id-a must be 32 hex chars (16 bytes)")
    if len(args.pk_a) != ROT_PUBKEY_HEX_LEN or args.pk_a[:2] != "04":
        ap.error(f"--pk-a must be {ROT_PUBKEY_HEX_LEN} hex chars (65-byte uncompressed "
                  "SECP256R1 point, starting with 04)")

    if args.shs_b:
        if len(args.shs_b) != ROT_DIGEST_LEN * 2:
            ap.error(f"--shs-b must be {ROT_DIGEST_LEN * 2} hex chars (32 bytes)")
        shs_b_hex = args.shs_b
    else:
        shs_b_hex = secrets.token_hex(ROT_DIGEST_LEN)  # fresh per-node server verifier

    registry[args.id_a] = {
        "pk_a": args.pk_a,
        "shs_b": shs_b_hex,
        "label": args.label or args.id_a[:8],
    }
    save(args.registry, registry)
    print(f"Registered {args.id_a} -> label={registry[args.id_a]['label']}, "
          f"pk_a={args.pk_a[:16]}..., shs_b={shs_b_hex[:16]}...")
    print(f"Wrote: {args.registry}")

    id_b_hex = read_id_b(args.server_identity)
    print()
    print("Next: pin this node's own V_B (shs_b) into its NVS image. Run on the device-side")
    print("host (smg_puf_rot_node/tools/):")
    if id_b_hex:
        print(f"  python provision_server_ref.py --id-b {id_b_hex} --shs-b {shs_b_hex} "
              f"--out-bin {args.label or args.id_a[:8]}_server_ref_nvs.bin")
    else:
        print(f"  python provision_server_ref.py   # establishes id_b first, if not done yet")
        print(f"  python provision_server_ref.py --id-b <id_b from above> --shs-b {shs_b_hex} "
              f"--out-bin {args.label or args.id_a[:8]}_server_ref_nvs.bin")


if __name__ == "__main__":
    main()
