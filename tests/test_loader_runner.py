"""Regression tests for experiment integrity records and diagnostic figures."""

import errno
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location(
        "review_loader_runner", Path(__file__).parents[1] / "scripts/06_loader_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("phase", ["before", "after"])
def test_source_state_records_all_missing_or_unstattable_paths_before_raising(tmp_path, monkeypatch, runner, phase):
    readable = tmp_path / "readable.parquet"
    readable.write_bytes(b"a source file")
    missing = tmp_path / "missing.parquet"
    blocked = tmp_path / "blocked.parquet"
    blocked.write_bytes(b"another source file")
    stat = Path.stat

    def deny_one_path(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_one_path)
    reader = SimpleNamespace(files=pd.DataFrame({"path": [str(readable), str(missing), str(blocked)]}))
    integrity_path = tmp_path / "integrity.json"
    with pytest.raises(OSError):
        runner.source_state(reader, integrity_path, phase=phase)
    record = json.loads(integrity_path.read_text())
    assert record["phase"] == phase
    assert record["unchanged"] is False
    assert record["files_requested"] == 3
    assert record["files_statted"] == 1
    assert record["missing_files"] == [str(missing)]
    errors = {item["path"]: item for item in record["stat_errors"]}
    assert set(errors) == {str(missing), str(blocked)}
    assert errors[str(missing)]["errno"] == errno.ENOENT
    assert errors[str(blocked)]["errno"] == errno.EACCES
    assert errors[str(blocked)]["error_type"] == "PermissionError"


def test_source_state_success_returns_exact_stats_without_failure_record(tmp_path, runner):
    source = tmp_path / "source.parquet"
    source.write_bytes(b"preserve me")
    before = source.stat()
    reader = SimpleNamespace(files=pd.DataFrame({"path": [str(source)]}))
    record = tmp_path / "integrity.json"
    result = runner.source_state(reader, record, phase="before")
    assert result.to_pylist() == [
        {"path": str(source), "size_bytes": len(b"preserve me"), "mtime_ns": before.st_mtime_ns}
    ]
    assert not record.exists()
    assert source.read_bytes() == b"preserve me"


@pytest.mark.parametrize("scored", [0, 1, 3, 8])
def test_plot_examples_uses_available_finite_scores(tmp_path, monkeypatch, runner, scored):
    import numpy as np

    count = scored + 2
    frame = pd.DataFrame(
        {
            "score": list(range(scored, 0, -1)) + [np.nan, np.inf],
            "rank": list(range(1, scored + 1)) + [None, None],
            "target_id": [39627953495081494 + i for i in range(count)],
            "row_uid": [f"7:{i}" for i in range(count)],
            "z": [1.6] * count,
            "snr_proxy": [2.0] * count,
            "masked_fraction": [0.0] * count,
            "zero_ivar_fraction": [0.0] * count,
        }
    )
    wave = np.linspace(1442, 3558, 20)
    flux = np.repeat(np.arange(1, count + 1, dtype=float)[:, None], len(wave), axis=1)
    figures = []
    close = runner.plt.close

    def remember_figure(fig):
        if hasattr(fig, "axes"):
            figures.append(fig)
        close(fig)

    monkeypatch.setattr(runner.plt, "close", remember_figure)
    try:
        runner.plot_examples("W2", frame, wave, flux, np.ones_like(flux), flux, tmp_path)
        fig = figures[-1]
        if scored:
            assert len(fig.axes) == min(scored, 6)
            for i, ax in enumerate(fig.axes):
                assert f"TARGETID {39627953495081494 + i}" in ax.get_title(loc="left")
                np.testing.assert_array_equal(ax.lines[0].get_ydata(), flux[i])
        else:
            assert len(fig.axes) == 1
            assert any("No finite scored spectra" in text.get_text() for text in fig.axes[0].texts)
        image = runner.plt.imread(tmp_path / "W2-top-spectra.png")
        assert image.ndim == 3 and np.ptp(image) > 0
        assert (tmp_path / "W2-top-spectra.pdf").read_bytes().startswith(b"%PDF-")
    finally:
        close("all")
