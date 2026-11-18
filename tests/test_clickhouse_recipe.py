from __future__ import annotations

import io
import tarfile
from pathlib import Path


def test_clickhouse_registered_linux_darwin_only() -> None:
    from software.recipes.clickhouse.recipe import ClickHouseRecipe

    assert ClickHouseRecipe.key == "clickhouse"
    assert ClickHouseRecipe.category == "devops"
    assert ClickHouseRecipe.platforms == ["linux", "darwin"]
    assert "windows" not in ClickHouseRecipe.platforms


def test_clickhouse_release_tag_parse() -> None:
    from software.recipes.clickhouse.common import parse_release_version, parse_release_versions

    assert parse_release_version("v26.7.3.19-stable") == "26.7.3.19"
    assert parse_release_version("v26.3.17.110-lts") == "26.3.17.110"
    assert parse_release_version("bad") is None
    assert parse_release_versions([
        {"tag_name": "v26.3.17.110-lts"},
        {"tag_name": "v26.7.3.19-stable"},
        {"tag_name": "bad"},
    ]) == ["26.7.3.19", "26.3.17.110"]


def test_clickhouse_background_cache_uses_real_versions(monkeypatch) -> None:
    from core.version_cache import fetch_versions_online
    from software.recipes.clickhouse.recipe import ClickHouseRecipe

    monkeypatch.setattr(
        "core.http.get_json",
        lambda *args, **kwargs: [{"tag_name": "v26.7.3.19-stable"}],
    )

    assert fetch_versions_online(ClickHouseRecipe()) == ["26.7.3.19"]


def test_clickhouse_extracts_binary_and_aliases(tmp_path: Path, monkeypatch) -> None:
    from software.recipes.clickhouse.linux import LinuxDriver

    tarball = tmp_path / "clickhouse.tgz"
    payload = b"#!/bin/sh\necho 'ClickHouse local version 26.7.3.19'\n"
    with tarfile.open(tarball, "w:gz") as tf:
        info = tarfile.TarInfo("clickhouse-common-static-26.7.3.19/usr/bin/clickhouse")
        info.mode = 0o755
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    monkeypatch.setattr(
        "software.recipes.clickhouse.linux.clickhouse_version_dir",
        lambda version: tmp_path / f"clickhouse{version}",
    )
    bin_dir = Path(LinuxDriver().install_tarball("26.7.3.19", tarball))

    assert (bin_dir / "clickhouse").exists()
    assert (bin_dir / "clickhouse-server").exists()
    assert (bin_dir / "clickhouse-client").exists()
