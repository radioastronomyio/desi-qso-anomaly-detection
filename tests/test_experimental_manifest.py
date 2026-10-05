"""Manifest identity and read-boundary tests using actual temporary Parquet files."""

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dqad_audit.manifest_loader import ManifestLoader
from test_spectral_loader import audit_row


def make_archive(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    path = root / "tile.parquet"
    row = {k: v.tolist() for k, v in audit_row().items()}
    row.update(target_id=42, z=1.6)
    pq.write_table(pa.Table.from_pylist([row]), path)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    files = pd.DataFrame([dict(tile_id="7", path=str(path), size_bytes=path.stat().st_size, n_rows_actual=1)])
    objects = pd.DataFrame([dict(tile_id="7", row_uid="7:0", target_id=42, z=1.6, array_length=7927)])
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
