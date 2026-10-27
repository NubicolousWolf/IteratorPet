from iterator_pet.assets_probe import probe_install

def test_probe_missing_is_safe(tmp_path):
    p=probe_install(tmp_path); assert not p.exists; assert p.size_bytes==0
