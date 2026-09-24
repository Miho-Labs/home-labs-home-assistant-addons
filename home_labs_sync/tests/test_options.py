import json
from pathlib import Path

from hl_sync.options import Options, load_options, resolve_paths


def test_load_options_defaults_and_normalization(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "api_url": "https://example.test/",
                "api_token": " hls_x ",
                "interval_minutes": 1,
                "scopes": ["themes", "bogus", "themes"],
                "restart_policy": "sometimes",
                "restart_window": "25:00-01:00",
                "log_level": "trace",
                "unknown": 1,
            }
        )
    )
    options = load_options(path)
    assert options.api_url == "https://example.test"
    assert options.api_token == "hls_x"
    assert options.interval_minutes == 5
    assert options.scopes == ("themes",)
    assert options.restart_policy == "auto"
    assert options.restart_window == "02:00-05:00"
    assert options.log_level == "info"


def test_load_options_missing_file(tmp_path):
    options = load_options(tmp_path / "nope.json")
    assert options == Options()
    assert options.scopes == ("dashboards", "themes", "automations", "scenes", "media", "packages")


def test_resolve_paths_from_env(tmp_path):
    env = {
        "HL_SYNC_CONFIG_DIR": str(tmp_path / "cfg"),
        "HL_SYNC_DATA_DIR": str(tmp_path / "data"),
        "HL_SYNC_OPTIONS_JSON": str(tmp_path / "o.json"),
    }
    paths = resolve_paths(env)
    assert paths.config_dir == tmp_path / "cfg"
    assert paths.data_dir == tmp_path / "data"
    assert paths.options_file == tmp_path / "o.json"
    assert paths.state_file == tmp_path / "data" / "state.json"
    assert paths.media_dir == Path("/media")
    assert paths.roots == {"config": tmp_path / "cfg", "media": Path("/media")}

    env["HL_SYNC_MEDIA_DIR"] = str(tmp_path / "med")
    assert resolve_paths(env).media_dir == tmp_path / "med"
