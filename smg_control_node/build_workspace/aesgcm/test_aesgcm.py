import aesgcm

print("aesgcm module loaded")
print("Constants:", aesgcm.BLOCK_SIZE, aesgcm.KEY_SIZE, aesgcm.NONCE_SIZE, aesgcm.TAG_SIZE)

# NIST GCM Test Vector 3 (AES-256-GCM, 128-bit plaintext)
# From: https://csrc.nist.gov/CSRC/media/Projects/Cryptographic-Algorithm-Validation-Program/documents/mac/gcmtestvectors.zip
# Test Case 3:
key = bytes.fromhex('92e11dcdaa866f5ce790fd24501f9256335eec0995c29736d64a45cb9e8bc871')
nonce = bytes.fromhex('ca47a8a2d88c7b61')
plaintext = bytes.fromhex('2951a0c0d575a1c96f3f8a4d')
aad = bytes.fromhex('0e1c7bc5031ed25e')
expected_ct = bytes.fromhex('ad6944a15ab68d797f3fb438')
expected_tag = bytes.fromhex('488a9320dcc4e62d2bf50e8d530ba0f6')

# Test module-level encrypt
ct, tag = aesgcm.encrypt(key, nonce, plaintext, aad=aad)
assert ct == expected_ct, f"encrypt ct mismatch: {ct.hex()} != {expected_ct.hex()}"
assert tag == expected_tag, f"encrypt tag mismatch: {tag.hex()} != {expected_tag.hex()}"
print("Module-level encrypt: PASS")

# Test module-level decrypt
pt = aesgcm.decrypt(key, nonce, ct, tag, aad=aad)
assert pt == plaintext, f"decrypt pt mismatch: {pt.hex()} != {plaintext.hex()}"
print("Module-level decrypt: PASS")

# Test class API
gcm = aesgcm.AESGCM(key)
ct2, tag2 = gcm.encrypt(nonce, plaintext, aad=aad)
assert ct2 == expected_ct, f"class encrypt ct mismatch"
assert tag2 == expected_tag, f"class encrypt tag mismatch"
pt2 = gcm.decrypt(nonce, ct2, tag2, aad=aad)
assert pt2 == plaintext, f"class decrypt pt mismatch"
print("Class API: PASS")

# Test tag tampering (auth failure)
bad_tag = bytearray(tag)
bad_tag[0] ^= 0xFF
try:
    aesgcm.decrypt(key, nonce, ct, bytes(bad_tag), aad=aad)
    assert False, "Should have raised OSError"
except OSError:
    print("Tag tamper detection: PASS")

# Test wrong key
wrong_key = bytes(32)
try:
    aesgcm.decrypt(wrong_key, nonce, ct, tag, aad=aad)
    assert False, "Should have raised OSError"
except (OSError, ValueError):
    print("Wrong key detection: PASS")

# Test empty plaintext (only AAD)
ct3, tag3 = aesgcm.encrypt(key, nonce, b'', aad=aad)
assert len(ct3) == 0, f"empty plaintext ct should be empty, got {len(ct3)}"
assert len(tag3) == 16, f"tag should be 16 bytes, got {len(tag3)}"
pt3 = aesgcm.decrypt(key, nonce, ct3, tag3, aad=aad)
assert pt3 == b'', "empty plaintext decrypt should return empty"
print("Empty plaintext with AAD: PASS")

# Test without AAD
ct4, tag4 = aesgcm.encrypt(key, nonce, plaintext)
pt4 = aesgcm.decrypt(key, nonce, ct4, tag4)
assert pt4 == plaintext, "no-AAD decrypt mismatch"
print("No AAD: PASS")

# Test invalid key length
try:
    aesgcm.encrypt(bytes(16), nonce, plaintext)
    assert False, "Should have raised ValueError"
except ValueError:
    print("Invalid key length: PASS")

# Test custom tag_len
ct5, tag5 = aesgcm.encrypt(key, nonce, plaintext, aad=aad, tag_len=4)
assert len(tag5) == 4, f"custom tag_len should be 4, got {len(tag5)}"
pt5 = aesgcm.decrypt(key, nonce, ct5, tag5, aad=aad)
assert pt5 == plaintext, "custom tag_len decrypt mismatch"
print("Custom tag_len=4: PASS")

# Benchmark
import time
data = bytes(256)
iterations = 100

t0 = time.ticks_us()
for _ in range(iterations):
    ct_b, tag_b = gcm.encrypt(nonce, data)
dt_enc = time.ticks_diff(time.ticks_us(), t0)

t0 = time.ticks_us()
for _ in range(iterations):
    pt_b = gcm.decrypt(nonce, ct_b, tag_b)
dt_dec = time.ticks_diff(time.ticks_us(), t0)

print(f"Benchmark: {iterations}x encrypt 256B: {dt_enc} us ({dt_enc // iterations} us/op)")
print(f"Benchmark: {iterations}x decrypt 256B: {dt_dec} us ({dt_dec // iterations} us/op)")

print("\nAll tests PASSED!")
