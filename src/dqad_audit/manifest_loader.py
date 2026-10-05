"""Read-only manifest-driven access to the measured Stage 0 spectral archive."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from dqad_audit.spectral_loader import validate_audit_contract


class ManifestLoader:
    """Resolve canonical row_uids without scanning or modifying corpus files.

    Both manifests are read once. Selected files are opened sequentially, and
    only the row groups containing selected offsets are decoded. Null sampled
    audit flags never stand in for validation of an actual selected spectrum.
    """

    def __init__(self, manifest_dir: Path, corpus_root: Path):
        self.root = Path(corpus_root).resolve()
        self.files = pd.read_parquet(Path(manifest_dir) / "qso_file_manifest_v1.parquet")
        self.objects = pd.read_parquet(Path(manifest_dir) / "qso_object_manifest_v1.parquet")
        if self.files.tile_id.duplicated().any() or self.objects.row_uid.duplicated().any():
            raise ValueError("manifest keys are not unique")
        if self.objects.target_id.duplicated().any():
            raise ValueError("manifest target duplication violates Stage 0")
        self._files = self.files.set_index("tile_id")
        self._objects = self.objects.set_index("row_uid", drop=False)
        if not self.objects.tile_id.isin(self.files.tile_id).all():
            raise ValueError("object manifest refers to unknown tile")
        for path in self.files.path:
            if not Path(path).resolve().is_relative_to(self.root):
                raise ValueError("manifest path is outside corpus root")

    def load(self, selection: pd.DataFrame) -> Iterator[tuple[dict, dict]]:
        """Yield canonical manifest metadata and raw rows after identity checks."""
        if selection.row_uid.duplicated().any() or not selection.row_uid.isin(self._objects.index).all():
            raise ValueError("selection must contain unique known row_uids")
        canonical = self._objects.loc[selection.row_uid]
        for key in ("tile_id", "target_id", "z"):
            if not np.array_equal(selection[key].to_numpy(), canonical[key].to_numpy()):
                raise ValueError(f"selection disagrees with manifest {key}")
        for tile, records in canonical.groupby("tile_id", sort=True):
            file_record = self._files.loc[tile]
            path = Path(file_record.path)
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("manifest path escaped corpus root")
            before = path.stat()
            if before.st_size != file_record.size_bytes:
                raise ValueError(f"corpus file size differs from manifest: {path}")
            parquet = pq.ParquetFile(path)
            if parquet.metadata.num_rows != file_record.n_rows_actual:
                raise ValueError(f"corpus row count differs from manifest: {path}")
            offsets = []
            for uid in records.row_uid:
                prefix, offset = uid.rsplit(":", 1)
                if prefix != str(tile) or not offset.isdigit():
                    raise ValueError(f"invalid row_uid: {uid}")
                offsets.append(int(offset))
            offsets = np.asarray(offsets)
            if (offsets >= parquet.metadata.num_rows).any():
                raise ValueError("row_uid offset is outside file")
            group_ends = np.cumsum([parquet.metadata.row_group(i).num_rows for i in range(parquet.num_row_groups)])
            group_ids = np.searchsorted(group_ends, offsets, side="right")
            meta = records.to_dict("records")
            for group in np.unique(group_ids):
                table = parquet.read_row_group(
                    int(group), columns=["target_id", "z", "wavelength", "flux", "ivar", "mask_array"]
                )
                base = 0 if group == 0 else group_ends[group - 1]
                for index in np.flatnonzero(group_ids == group):
                    row = table.slice(int(offsets[index] - base), 1).to_pylist()[0]
                    record = meta[index]
                    if row["target_id"] != record["target_id"] or row["z"] != record["z"]:
                        raise ValueError(f'corpus identity differs from manifest: {record["row_uid"]}')
                    if record["array_length"] != len(row["wavelength"]):
                        raise ValueError("corpus length differs from manifest")
                    validate_audit_contract(row)
                    yield record, row
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"corpus file changed during read: {path}")
