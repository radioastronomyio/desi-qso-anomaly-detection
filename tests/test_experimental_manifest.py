"""Manifest identity and read-boundary tests using actual temporary Parquet files."""

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dqad_audit.manifest_loader import ManifestLoader
from test_spectral_loader import audit_row


def make_archive(tmp_path, row_count=1, row_group_size=None):
    root = tmp_path / "corpus"
    root.mkdir()
    path = root / "tile.parquet"
    rows = []
    for offset in range(row_count):
        row = {k: v.tolist() for k, v in audit_row().items()}
        row.update(target_id=42 + offset, z=1.6 + offset * 0.001, flux=[1.0 + offset] * 7927)
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=row_group_size)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    files = pd.DataFrame([dict(tile_id="7", path=str(path), size_bytes=path.stat().st_size, n_rows_actual=row_count)])
    objects = pd.DataFrame(
        [
            dict(tile_id="7", row_uid=f"7:{offset}", target_id=row["target_id"], z=row["z"], array_length=7927)
            for offset, row in enumerate(rows)
        ]
    )
    files.to_parquet(manifests / "qso_file_manifest_v1.parquet", index=False)
    objects.to_parquet(manifests / "qso_object_manifest_v1.parquet", index=False)
    return root, manifests, objects, path


def test_manifest_resolves_exact_row_and_preserves_source(tmp_path):
    root, manifests, objects, path = make_archive(tmp_path)
    before = path.stat()
    reader = ManifestLoader(manifests, root)
    loaded = list(reader.load(objects))
    assert len(loaded) == 1
    assert loaded[0][0]["row_uid"] == "7:0"
    assert loaded[0][1]["target_id"] == 42
    assert len(loaded[0][1]["flux"]) == 7927
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)


@pytest.mark.parametrize("field,value", [("target_id", 43), ("z", 1.7), ("row_uid", "7:1")])
def test_wrong_manifest_identity_fails(tmp_path, field, value):
    root, manifests, objects, _ = make_archive(tmp_path)
    objects.loc[0, field] = value
    objects.to_parquet(manifests / "qso_object_manifest_v1.parquet", index=False)
    with pytest.raises(ValueError):
        list(ManifestLoader(manifests, root).load(objects))


def test_selection_cannot_forge_manifest_membership(tmp_path):
    root, manifests, objects, _ = make_archive(tmp_path)
    reader = ManifestLoader(manifests, root)
    objects.loc[0, "target_id"] = 99
    with pytest.raises(ValueError):
        list(reader.load(objects))


def test_outside_path_and_duplicate_selections_rejected(tmp_path):
    root, manifests, objects, _ = make_archive(tmp_path)
    reader = ManifestLoader(manifests, root)
    with pytest.raises(ValueError):
        list(reader.load(pd.concat([objects, objects])))
    files = pd.read_parquet(manifests / "qso_file_manifest_v1.parquet")
    files.loc[0, "path"] = str(tmp_path / "outside.parquet")
    files.to_parquet(manifests / "qso_file_manifest_v1.parquet", index=False)
    with pytest.raises(ValueError):
        ManifestLoader(manifests, root)


def test_rejects_unexpected_live_contract(tmp_path):
    root, manifests, objects, path = make_archive(tmp_path)
    row = pq.read_table(path).to_pylist()[0]
    row["wavelength"] = np.sort(row["wavelength"]).tolist()
    pq.write_table(pa.Table.from_pylist([row]), path)
    files = pd.read_parquet(manifests / "qso_file_manifest_v1.parquet")
    files.loc[0, "size_bytes"] = path.stat().st_size
    files.to_parquet(manifests / "qso_file_manifest_v1.parquet", index=False)
    with pytest.raises(ValueError, match="contract"):
        list(ManifestLoader(manifests, root).load(objects))


def test_selected_offsets_span_row_groups_and_exact_group_ends(tmp_path):
    root, manifests, objects, path = make_archive(tmp_path, row_count=7, row_group_size=2)
    parquet = pq.ParquetFile(path)
    assert [parquet.metadata.row_group(i).num_rows for i in range(parquet.num_row_groups)] == [2, 2, 2, 1]
    before = path.stat()
    # 1/2 and 3/4 straddle boundaries; 2, 4, 6 equal cumulative group ends.
    selected = objects.iloc[[6, 2, 1, 4, 3]]
    loaded = list(ManifestLoader(manifests, root).load(selected))
    assert len(loaded) == 5
    expected = {"7:1": (43, 2.0), "7:2": (44, 3.0), "7:3": (45, 4.0), "7:4": (46, 5.0), "7:6": (48, 7.0)}
    assert {record["row_uid"] for record, _ in loaded} == set(expected)
    for record, row in loaded:
        target_id, flux = expected[record["row_uid"]]
        assert record["target_id"] == row["target_id"] == target_id
        assert record["z"] == row["z"]
        np.testing.assert_array_equal(row["flux"], np.full(7927, flux))
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)
