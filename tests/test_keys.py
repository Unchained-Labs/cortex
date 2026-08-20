from cortex.keys import generate_key, hash_key, key_prefix, verify_key


def test_generate_verify_roundtrip():
    key = generate_key()
    assert key.startswith("ctx_")
    assert verify_key(key, hash_key(key))
    assert not verify_key(key + "x", hash_key(key))


def test_prefix_identifies_without_revealing():
    key = generate_key()
    assert key_prefix(key) == key[:10]
    assert len(key_prefix(key)) < len(key) / 3
