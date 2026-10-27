from pathlib import Path

from PIL import Image

from iterator_pet.assets_extractor import (
    DEFAULT_INSTALL,
    SPRITE_NAMES,
    ensure_sprites,
    extract,
)


def test_missing_install_falls_back_to_generated(tmp_path):
    report = extract(install=tmp_path / "nope", cache_dir=tmp_path / "cache")
    assert report.source == "generated"
    assert set(report.sprites) == set(SPRITE_NAMES)
    for path in report.sprites.values():
        assert path.is_file()
        assert Image.open(path).mode == "RGBA"


def test_cache_is_reused_and_manifest_roundtrips(tmp_path):
    cache = tmp_path / "cache"
    first = extract(install=tmp_path / "nope", cache_dir=cache)
    assert first.source == "generated"
    assert (cache / "manifest.json").is_file()

    second = ensure_sprites(cache)
    assert second.source == "generated"
    assert set(second.sprites) == set(SPRITE_NAMES)


def test_generated_shapes_have_expected_sizes(tmp_path):
    report = extract(install=tmp_path / "nope", cache_dir=tmp_path / "cache")
    sizes = {name: Image.open(path).size for name, path in report.sprites.items()}
    assert sizes["Circle20"] == (20, 20)
    assert sizes["pixel"] == (1, 1)
    assert sizes["CentipedeSegment"] == (18, 15)
    assert sizes["LizardScaleA1"] == (4, 14)


def test_real_install_produces_game_sprites(tmp_path):
    if not (DEFAULT_INSTALL / "RainWorld_Data" / "resources.assets").is_file():
        return  # no local game: the generated fallback path is covered above
    report = extract(install=DEFAULT_INSTALL, cache_dir=tmp_path / "cache", force=True)
    assert report.source == "game"
    assert Image.open(report.sprites["Circle20"]).size == (20, 20)
    assert report.palette_path is not None and report.palette_path.is_file()
    assert report.colors["black"] == (0, 0, 0)
