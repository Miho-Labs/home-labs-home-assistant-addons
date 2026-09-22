import pytest

from hl_sync.manifest import (
    MAX_FILE_SIZE,
    MAX_RELEASE_SIZE,
    ManifestError,
    applied_key,
    display_path,
    scope_for_key,
    scope_for_path,
    split_applied_key,
    validate_manifest,
    validate_path,
)

from .conftest import MEDIA_FILES, SAMPLE_FILES, base_manifest, make_release, sha

MEDIA_BAD_PATHS = [
    "wallpanel/../x.jpg",
    "wallpanel/../../secrets.yaml",
    "wallpanel",
    "wallpanel/",
    "/wallpanel/x.jpg",
    "wallpanel//x.jpg",
    "wallpanel/./x.jpg",
    "wallpanel\\x.jpg",
    "Wallpanel/x.jpg",
    "x.jpg",
    "dashboards/dom.yaml",
    "www/wallpanel/x.jpg",
    "home_labs/automations.yaml",
    "",
]

BAD_PATHS = [
    "../x",
    "/etc/passwd",
    "dashboards/../secrets.yaml",
    "packages/evil.yaml",
    "home_labs/package.yaml",
    "home_labs/other.yaml",
    "dashboards\\x.yaml",
    "dashboards//x.yaml",
    "dashboards/./x.yaml",
    "dashboards/",
    "secrets.yaml",
    ".storage/lovelace",
    "",
]


@pytest.mark.parametrize("path", BAD_PATHS)
def test_validate_path_rejects(path):
    with pytest.raises(ManifestError):
        validate_path(path)


@pytest.mark.parametrize("path", list(SAMPLE_FILES))
def test_validate_path_accepts(path):
    assert validate_path(path) == path


@pytest.mark.parametrize(
    "path", ["../x", "/abs.yaml", "packages/evil.yaml", "home_labs/package.yaml"]
)
def test_manifest_rejects_bad_file_paths(path):
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"].append({"path": path, "scope": "dashboards", "sha256": sha(b"x"), "size": 1})
    with pytest.raises(ManifestError):
        validate_manifest(base_manifest(release))


def test_manifest_rejects_unknown_scope():
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"][0]["scope"] = "secrets"
    with pytest.raises(ManifestError, match="zakres"):
        validate_manifest(base_manifest(release))


def test_manifest_rejects_scope_path_mismatch():
    release = make_release({"themes/t.yaml": b"x"})
    release["files"][0]["scope"] = "dashboards"
    with pytest.raises(ManifestError, match="nie pasuje"):
        validate_manifest(base_manifest(release))


def test_manifest_rejects_foreign_keys_in_lovelace_fragment():
    release = make_release({"dashboards/ok.yaml": b"x"}, lovelace="shell_command:\n  x: rm -rf /\n")
    with pytest.raises(ManifestError, match="niedozwolone"):
        validate_manifest(base_manifest(release))


def test_manifest_rejects_tags_in_lovelace_fragment():
    release = make_release({"dashboards/ok.yaml": b"x"}, lovelace="lovelace: !include evil.yaml\n")
    with pytest.raises(ManifestError, match="tag"):
        validate_manifest(base_manifest(release))


def test_manifest_rejects_oversized_file():
    assert MAX_FILE_SIZE == 10 * 1024 * 1024
    assert MAX_RELEASE_SIZE == 150 * 1024 * 1024
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"][0]["size"] = MAX_FILE_SIZE + 1
    with pytest.raises(ManifestError, match="limit"):
        validate_manifest(base_manifest(release))
    # 16 photos at the per-file cap add up to 160 MB > 150 MB for the whole release
    release = make_release({}, media={f"wallpanel/{i}.jpg": bytes([i]) for i in range(16)})
    for entry in release["files"]:
        entry["size"] = MAX_FILE_SIZE
    with pytest.raises(ManifestError, match="wydanie ma"):
        validate_manifest(base_manifest(release))
    release["files"].pop()
    assert len(validate_manifest(base_manifest(release)).release.files) == 15


def test_manifest_rejects_duplicate_paths_and_bad_sha():
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"].append(dict(release["files"][0]))
    with pytest.raises(ManifestError, match="zduplikowana"):
        validate_manifest(base_manifest(release))
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"][0]["sha256"] = "zz"
    with pytest.raises(ManifestError, match="sha256"):
        validate_manifest(base_manifest(release))


def test_manifest_requires_schema_1_and_types():
    with pytest.raises(ManifestError):
        validate_manifest(base_manifest(schema=2))
    with pytest.raises(ManifestError):
        validate_manifest(base_manifest(paused="no"))
    with pytest.raises(ManifestError):
        validate_manifest([])


def test_manifest_accepts_valid_release():
    release = make_release(
        SAMPLE_FILES,
        managed={
            "automations": [1789848132433, "1790001000001"],
            "scenes": ["homelabs_kule_baltyk"],
        },
        templates={"entities_export": "x"},
        warnings=["uwaga"],
    )
    manifest = validate_manifest(
        base_manifest(release, requests={"entities_export": "2026-09-22T10:00:00+00:00"})
    )
    assert manifest.client == "testowy"
    assert manifest.paused is False
    assert manifest.entities_export_requested == "2026-09-22T10:00:00+00:00"
    rel = manifest.release
    assert rel is not None
    assert {f.path for f in rel.files} == set(SAMPLE_FILES)
    assert rel.managed_automations == ["1789848132433", "1790001000001"]
    assert rel.managed_scenes == ["homelabs_kule_baltyk"]
    assert rel.templates == {"entities_export": "x"}
    assert rel.warnings == ["uwaga"]
    assert rel.lovelace_fragment.startswith("lovelace:")


def test_manifest_null_release():
    manifest = validate_manifest(base_manifest(None))
    assert manifest.release is None


# --- root ``media`` (WallPanel photos) -----------------------------------------------------


def _media_entry(path: str = "wallpanel/las.jpg", **override) -> dict:
    entry = {"path": path, "root": "media", "scope": "media", "sha256": sha(b"jpg"), "size": 3}
    entry.update(override)
    return entry


@pytest.mark.parametrize("path", MEDIA_BAD_PATHS)
def test_validate_path_media_root_rejects(path):
    with pytest.raises(ManifestError):
        validate_path(path, "media")


@pytest.mark.parametrize("path", [*MEDIA_FILES, "wallpanel/lato/plaza.jpg"])
def test_validate_path_media_root_accepts(path):
    assert validate_path(path, "media") == path
    assert scope_for_path(path, "media") == "media"
    assert scope_for_path(path) is None
    with pytest.raises(ManifestError, match="poza dozwolonymi"):
        validate_path(path)


def test_config_paths_are_not_allowed_under_media_root():
    for path in SAMPLE_FILES:
        assert scope_for_path(path, "media") is None
        with pytest.raises(ManifestError):
            validate_path(path, "media")
    assert scope_for_path("wallpanel/x.jpg", "secrets") is None


def test_manifest_accepts_media_root_file():
    release = make_release({"dashboards/ok.yaml": b"x"}, media=MEDIA_FILES)
    rel = validate_manifest(base_manifest(release)).release
    assert rel is not None
    media = {f.path: f for f in rel.files if f.root == "media"}
    assert set(media) == set(MEDIA_FILES)
    entry = media["wallpanel/las.jpg"]
    assert entry.scope == "media"
    assert entry.key == "media:wallpanel/las.jpg"
    assert entry.display == "media:wallpanel/las.jpg"
    assert "wallpanel/las.jpg" not in rel.files_by_path
    assert rel.files_by_path["dashboards/ok.yaml"].root == "config"
    assert rel.files_by_path["dashboards/ok.yaml"].display == "dashboards/ok.yaml"


def test_manifest_rejects_media_root_traversal():
    for path in ("wallpanel/../x.jpg", "wallpanel/../../dashboards/dom.yaml"):
        release = make_release({})
        release["files"].append(_media_entry(path))
        with pytest.raises(ManifestError, match="znormalizowana"):
            validate_manifest(base_manifest(release))


def test_manifest_rejects_media_root_outside_wallpanel():
    for path in ("www/x.jpg", "dashboards/dom.yaml", "zdjecia/las.jpg"):
        release = make_release({})
        release["files"].append(_media_entry(path, scope="dashboards"))
        with pytest.raises(ManifestError, match="poza dozwolonymi"):
            validate_manifest(base_manifest(release))


def test_manifest_rejects_media_root_with_other_scope():
    for scope in ("dashboards", "themes", "automations", "scenes"):
        release = make_release({})
        release["files"].append(_media_entry(scope=scope))
        with pytest.raises(ManifestError, match="nie pasuje"):
            validate_manifest(base_manifest(release))


def test_manifest_rejects_config_root_with_media_scope():
    release = make_release({"dashboards/ok.yaml": b"x"})
    release["files"][0]["scope"] = "media"
    with pytest.raises(ManifestError, match="nie pasuje"):
        validate_manifest(base_manifest(release))
    release = make_release({})
    release["files"].append(_media_entry(root="config"))
    with pytest.raises(ManifestError, match="poza dozwolonymi"):
        validate_manifest(base_manifest(release))


def test_manifest_missing_root_means_config():
    release = make_release({"dashboards/ok.yaml": b"x"})
    del release["files"][0]["root"]
    rel = validate_manifest(base_manifest(release)).release
    assert rel is not None
    assert rel.files[0].root == "config"
    assert rel.files[0].key == "config:dashboards/ok.yaml"
    release["files"][0]["root"] = None
    assert validate_manifest(base_manifest(release)).release.files[0].root == "config"


def test_manifest_rejects_unknown_root():
    for bad in ("secrets", "", 1, "/media"):
        release = make_release({"dashboards/ok.yaml": b"x"})
        release["files"][0]["root"] = bad
        with pytest.raises(ManifestError, match="root"):
            validate_manifest(base_manifest(release))


def test_manifest_rejects_duplicate_media_path():
    release = make_release({"dashboards/ok.yaml": b"x"}, media={"wallpanel/las.jpg": b"a"})
    release["files"].append(_media_entry("wallpanel/las.jpg"))
    with pytest.raises(ManifestError, match="zduplikowana"):
        validate_manifest(base_manifest(release))


def test_applied_keys_round_trip_and_legacy_form():
    assert applied_key("media", "wallpanel/las.jpg") == "media:wallpanel/las.jpg"
    assert applied_key("config", "dashboards/dom.yaml") == "config:dashboards/dom.yaml"
    assert split_applied_key("media:wallpanel/las.jpg") == ("media", "wallpanel/las.jpg")
    assert split_applied_key("config:www/a:b.jpg") == ("config", "www/a:b.jpg")
    # keys written by add-on 0.1.x carry no root: they are config paths
    assert split_applied_key("dashboards/dom.yaml") == ("config", "dashboards/dom.yaml")
    assert split_applied_key("www/a:b.jpg") == ("config", "www/a:b.jpg")
    assert scope_for_key("dashboards/dom.yaml") == "dashboards"
    assert scope_for_key("media:wallpanel/las.jpg") == "media"
    assert scope_for_key("config:wallpanel/las.jpg") is None
    assert scope_for_key("media:dashboards/dom.yaml") is None
    assert display_path("config", "dashboards/dom.yaml") == "dashboards/dom.yaml"
    assert display_path("media", "wallpanel/las.jpg") == "media:wallpanel/las.jpg"
