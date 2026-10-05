#!/usr/bin/env python3
"""
Script Name  : 06_loader_experiment.py
Description  : Compare three candidate windows with a bounded PCA baseline.
Repository   : desi-qso-anomaly-detection
Author       : Codex
Created      : 2026-10-04
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Read manifests and at most 2,304 selected spectra; save all derived artifacts
inside the approved staging directory. Source data are read-only. The report
supports an operator decision and never selects the adopted window.

Usage: OPENBLAS_NUM_THREADS=2 python scripts/06_loader_experiment.py
Example: python scripts/06_loader_experiment.py --output-name run-01
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from matplotlib.ticker import MaxNLocator, PercentFormatter

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dqad_audit import SNAPSHOT_SCHEMA, compare_corpus_snapshots, snapshot_records
from dqad_audit.experiment import (
    CANDIDATES,
    SAMPLING_VERSION,
    SEED,
    WINDOW_SPAWN_KEYS,
    fit_baseline,
    normalize,
    nuisance_diagnostics,
    score_baseline,
    select_candidates,
)
from dqad_audit.manifest_loader import ManifestLoader
from dqad_audit.spectral_loader import OVERLAPS, common_grid, load_rest_spectrum

STAGING = REPO / "staging" / "2026-10-04-astra-loader-experiment"
MANIFESTS = Path("/mnt/nvme01/desidr1-manifest")
CORPUS = Path("/mnt/nvme01/desidr1-parquet-tiles/desi-qad")
logger = logging.getLogger("dqad_experiment")


def digest(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def source_state(reader: ManifestLoader, integrity_path: Path, *, phase: str) -> pa.Table:
    """Snapshot readable sources and persist all stat failures before raising."""
    records, errors = [], []
    first_error = None
    for name in reader.files.path:
        path = Path(name)
        try:
            records.extend(snapshot_records([path]))
        except OSError as error:
            if first_error is None:
                first_error = error
            errors.append(
                {
                    "path": str(path),
                    "error_type": type(error).__name__,
                    "errno": error.errno,
                    "message": str(error),
                }
            )
    if first_error is not None:
        write_json(
            integrity_path,
            {
                "phase": phase,
                "unchanged": False,
                "files_requested": len(reader.files),
                "files_statted": len(records),
                "missing_files": [item["path"] for item in errors if item["error_type"] == "FileNotFoundError"],
                "stat_errors": errors,
            },
        )
        raise first_error
    return pa.Table.from_pylist(records, schema=SNAPSHOT_SCHEMA)


def run_candidate(key: str, selection: pd.DataFrame, reader: ManifestLoader, out: Path) -> dict:
    lo, hi, population = CANDIDATES[key]
    if int(((reader.objects.z >= lo) & (reader.objects.z < hi)).sum()) != population:
        raise ValueError(f"{key}: manifest population differs from frozen spec")
    selection.to_csv(out / f"{key}-selection.csv", index=False)
    metadata, fluxes, ivars, overlaps = [], [], [], []
    split_by_uid = selection.set_index("row_uid").split.to_dict()
    started = time.perf_counter()
    for i, (record, raw) in enumerate(reader.load(selection)):
        spectrum = load_rest_spectrum(raw, lo, hi)
        # Recompute all raw-pixel nuisances; never treat null sampled flags as zero.
        f, v, mask = (np.asarray(raw[k]) for k in ["flux", "ivar", "mask_array"])
        unmasked = mask == 0
        snr = float(np.median(f[unmasked] * np.sqrt(v[unmasked]))) if unmasked.any() else np.nan
        masked_fraction = float(np.mean(~unmasked))
        if not np.isclose(masked_fraction, record["masked_fraction"], rtol=0, atol=1e-12):
            raise ValueError(f'{record["row_uid"]}: masked fraction differs from manifest')
        if not np.isclose(snr, record["snr_proxy"], rtol=1e-10, atol=1e-12, equal_nan=True):
            raise ValueError(f'{record["row_uid"]}: snr proxy differs from manifest')
        metadata.append(
            {k: record[k] for k in ["row_uid", "tile_id", "target_id", "z", "snr_proxy", "masked_fraction"]}
        )
        metadata[-1].update(
            split=split_by_uid[record["row_uid"]],
            zero_ivar_fraction=float(np.mean(v == 0)),
            nonfinite_flux_count=int((~np.isfinite(f)).sum()),
        )
        fluxes.append(spectrum.flux)
        ivars.append(spectrum.ivar)
        overlaps.append(spectrum.overlap)
        if (i + 1) % 128 == 0:
            logger.info("%s: loaded %d/%d spectra", key, i + 1, len(selection))
    frame = pd.DataFrame(metadata)
    flux, ivar, overlap = np.array(fluxes), np.array(ivars), np.array(overlaps)
    edges = common_grid(lo, hi)
    wave = (edges[1:] + edges[:-1]) / 2
    normalized = normalize(flux, ivar)
    frame["scale"] = normalized.scale
    frame["valid_fraction"] = normalized.valid_fraction
    frame["included"] = normalized.included
    frame["exclusion"] = np.where(
        normalized.included,
        "",
        np.where(normalized.valid_fraction < 0.8, "valid_fraction_below_0.8", "nonpositive_or_nonfinite_median"),
    )
    train = (frame.split == "train").to_numpy() & normalized.included
    test = (frame.split == "holdout").to_numpy() & normalized.included
    if set(frame.loc[train, "tile_id"]) & set(frame.loc[test, "tile_id"]):
        raise ValueError("tile leakage")
    if set(frame.loc[train, "target_id"]) & set(frame.loc[test, "target_id"]):
        raise ValueError("target leakage")
    model = fit_baseline(normalized.flux[train], normalized.ivar[train])
    result = score_baseline(model, normalized.flux[test], normalized.ivar[test], overlap[test])
    heldout = frame[test].copy().reset_index(drop=True)
    for name, values in result.items():
        if values.ndim == 1:
            heldout[name] = values
    heldout["rank"] = heldout.score.rank(ascending=False, method="first").astype("Int64")
    heldout.to_csv(out / f"{key}-scores.csv", index=False)
    frame.to_csv(out / f"{key}-sample.csv", index=False)
    np.savez_compressed(
        out / f"{key}-arrays.npz",
        flux=flux,
        ivar=ivar,
        overlap=overlap,
        edges=edges,
        train=train,
        holdout=test,
        row_uid=frame.row_uid.to_numpy(dtype=str),
    )
    np.savez_compressed(
        out / f"{key}-model.npz",
        active=model.active,
        center=model.center,
        components=model.components,
        fill=model.fill,
        singular_values=model.singular_values,
        n_train=model.n_train,
    )
    np.savez_compressed(out / f"{key}-predictions.npz", **result)
    diagnostics = nuisance_diagnostics(heldout)
    write_json(out / f"{key}-diagnostics.json", diagnostics)
    tiles = (
        heldout.assign(top10=heldout["rank"] <= 10)
        .groupby("tile_id")
        .agg(n=("score", "count"), median_score=("score", "median"), max_score=("score", "max"), top10=("top10", "sum"))
    )
    tiles.to_csv(out / f"{key}-tiles.csv")
    pd.DataFrame(diagnostics["score_deciles"]).to_csv(out / f"{key}-deciles.csv", index=False)
    plot_examples(key, heldout, wave, normalized.flux[test], normalized.ivar[test], result["reconstruction"], out)
    plot_nuisances(key, heldout, out)
    # Replay the saved arrays, independently of the in-memory arrays/model.
    with np.load(out / f"{key}-arrays.npz") as saved:
        replay = normalize(saved["flux"], saved["ivar"])
        np.testing.assert_array_equal(replay.included, normalized.included)
        replay_model = fit_baseline(replay.flux[saved["train"]], replay.ivar[saved["train"]])
        replay_result = score_baseline(
            replay_model,
            replay.flux[saved["holdout"]],
            replay.ivar[saved["holdout"]],
            saved["overlap"][saved["holdout"]],
        )
    np.testing.assert_allclose(result["score"], replay_result["score"], rtol=1e-10, atol=1e-12)
    np.testing.assert_array_equal(
        np.argsort(-result["score"], kind="stable"), np.argsort(-replay_result["score"], kind="stable")
    )
    summary = dict(
        window=[lo, hi],
        population=population,
        selected=len(frame),
        train_selected=int((frame.split == "train").sum()),
        holdout_selected=int((frame.split == "holdout").sum()),
        train_included=int(train.sum()),
        holdout_included=int(test.sum()),
        scored=int(heldout.score.notna().sum()),
        exclusions=frame[~frame.included].groupby(["split", "exclusion"]).size().to_dict(),
        grid_pixels=len(wave),
        modeled_pixels=int(model.active.sum()),
        rest_edges=[float(edges[0]), float(edges[-1])],
        median_score=float(heldout.score.median()),
        max_score=float(heldout.score.max()),
        median_valid_fraction=float(frame.valid_fraction.median()),
        tile_intersection=0,
        target_intersection=0,
        replay_scores_and_ranks_match=True,
        runtime_seconds=time.perf_counter() - started,
    )
    summary["exclusions"] = {"/".join(k): int(v) for k, v in summary["exclusions"].items()}
    write_json(out / f"{key}-summary.json", summary)
    logger.info("%s complete: %d train, %d holdout, %.1fs", key, train.sum(), test.sum(), summary["runtime_seconds"])
    return summary


def plot_examples(
    key: str,
    frame: pd.DataFrame,
    wave: np.ndarray,
    flux: np.ndarray,
    ivar: np.ndarray,
    reconstruction: np.ndarray,
    out: Path,
) -> None:
    chosen = frame[np.isfinite(frame.score)].nlargest(6, "score").index
    count = len(chosen)
    fig, panels = plt.subplots(max(1, count), 1, figsize=(13, max(4, 3.2 * count)), sharex=True, squeeze=False)
    axes = panels[:, 0]
    if count == 0:
        axes[0].text(0.5, 0.5, "No finite scored spectra available", ha="center", va="center")
        axes[0].set_axis_off()
    for ax, index in zip(axes[:count], chosen, strict=True):
        row = frame.loc[index]
        valid = ivar[index] > 0
        sigma = np.divide(1.0, np.sqrt(ivar[index]), out=np.zeros_like(wave), where=valid)
        plotted = np.where(valid, flux[index], np.nan)
        ax.plot(wave, plotted, lw=0.8, color="#253d5b", label="Spectrum")
        ax.fill_between(wave, plotted - sigma, plotted + sigma, alpha=0.22, color="#509bc0", label="Propagated 1σ")
        ax.plot(wave, reconstruction[index], lw=1.0, color="#c75034", label="PCA reconstruction")
        for low, high in OVERLAPS:
            ax.axvspan(
                low / (1 + row.z),
                high / (1 + row.z),
                color="#ddb52e",
                alpha=0.3,
                label="Arm overlap" if low == 5760 else None,
            )
        ax.plot(
            wave[~valid],
            np.full((~valid).sum(), 0.02),
            "|",
            color="#8b4da4",
            transform=ax.get_xaxis_transform(),
            label="Invalid pixel",
        )
        ax.set_xlim(wave[0], wave[-1])
        ax.set_ylabel("Normalized flux")
        ax.set_title(
            f'Rank {row["rank"]} · TARGETID {row.target_id} · row {row.row_uid} · z={row.z:.4f} · '
            f"score={row.score:.3g}\n"
            f"SNR proxy={row.snr_proxy:.2f} · masked={row.masked_fraction:.2%} · zero "
            f"ivar={row.zero_ivar_fraction:.2%}",
            fontsize=10,
            loc="left",
        )
        ax.grid(alpha=0.15)
    if count:
        axes[0].legend(loc="upper right", fontsize=8, ncol=3)
        axes[-1].set_xlabel("Rest-frame wavelength [Å]")
    fig.suptitle(
        f"{key}: {count} highest residual scores in held-out tiles\n"
        "Exploratory PCA rankings; no physical classification",
        fontsize=14,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out / f"{key}-top-spectra.png", dpi=145)
    fig.savefig(out / f"{key}-top-spectra.pdf")
    plt.close(fig)


def plot_nuisances(key: str, frame: pd.DataFrame, out: Path) -> None:
    columns = ["snr_proxy", "masked_fraction", "zero_ivar_fraction", "z", "valid_fraction", "overlap_residual_fraction"]
    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    for ax, column in zip(axes.flat, columns, strict=True):
        ax.scatter(frame[column], frame.score, s=13, alpha=0.6, color="#253d5b")
        ax.set_xlabel(column.replace("_", " "))
        ax.xaxis.set_major_locator(MaxNLocator(4))
        if "fraction" in column:
            ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=1))
        ax.set_ylabel("Residual score")
        ax.set_yscale("log")
        ax.grid(alpha=0.15)
    fig.suptitle(f"{key}: nuisance checks on held-out scores")
    fig.tight_layout()
    fig.savefig(out / f"{key}-nuisances.png", dpi=145)
    plt.close(fig)


def write_report(out: Path, summaries: dict, provenance: dict) -> None:
    lines = [
        "# Candidate Redshift Windows: First Experiment",
        "",
        "No window is adopted. This report supports Don’s choice for a subsequent Spender/VAE unit.",
        "",
        "| Window | Population | Rest bin edges Å | Train / scored holdout | Modeled pixels | Median score |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for key, s in summaries.items():
        lines.append(
            f'| {key} [{s["window"][0]}, {s["window"][1]}) | {s["population"]:,} | '
            f'{s["rest_edges"]} | {s["train_included"]} '
            f'/ {s["scored"]} | {s["modeled_pixels"]} | {s["median_score"]:.3f} |'
        )
    lines += [
        "",
        "## Method and reproducibility",
        "",
        "Seed 20261004; globally shared 25% tile holdout; uniform row sampling within the assigned "
        "training/holdout subsets, 576/192 selected per window. Exact selections are saved. The "
        "candidates overlap in redshift, so these are not independent experiments. Each candidate "
        "uses its own grid and PCA; absolute scores are not calibrated for cross-window comparison.",
        "",
        "Exact-wavelength overlaps are inverse-variance coadded from valid arm pixels. Four-Å rest "
        "bins average native pixel footprints with squared-weight variance propagation and require "
        "complete valid support. Observed flux-density amplitudes are retained on rest coordinates. "
        "Each row is divided by its positive median flux, and ivar is multiplied by that median "
        "squared. Rows with less than 80% valid pixels or nonpositive normalization are excluded and "
        "recorded, without replacement.",
        "",
        "Eight-component PCA uses training-only median imputation, centering and pixels with at "
        "least 80% training support. Held-out coefficients use weighted least squares. Ranking is "
        "mean normalized-ivar-weighted squared reconstruction residual. Ordinary PCA can itself be "
        "influenced by noisy training spectra. Model error, fitted coefficients and covariance "
        "prevent interpreting this score as a calibrated chi-square probability.",
        "",
        "Replays from each saved array file matched inclusion, numerical scores and rank order. "
        "Input/output provenance, software versions and source-code hashes are in "
        "[provenance.json](provenance.json). All 10,793 corpus file size/mtime pairs match "
        "before/after and the Stage 0 snapshot; all three manifest/provenance file hashes are "
        "unchanged. Size/mtime checks are not content hashes of the corpus.",
        "",
        f'Cross-candidate reuse: {provenance["cross_candidate_shared_targets"]}. Shared targets '
        f"always retain the same global tile split.",
        "",
    ]
    rationales = {
        "W1": "Smaller population with the widest common rest coverage.",
        "W2": "Most populated narrow audit bin with intermediate rest coverage.",
        "W3": "Largest listed broad-bin population with the narrowest common rest coverage.",
    }
    for key, s in summaries.items():
        d = json.loads((out / f"{key}-diagnostics.json").read_text())
        scores = pd.read_csv(out / f"{key}-scores.csv", dtype={"target_id": str, "row_uid": str, "tile_id": str})
        lines += [
            f'## {key}: [{s["window"][0]}, {s["window"][1]})',
            "",
            rationales[key],
            "",
            f'Selected {s["selected"]}; retained training {s["train_included"]}, holdout '
            f'{s["holdout_included"]}, scored {s["scored"]}. Exclusions: {s["exclusions"] or "none"}. '
            f'Median valid coverage: {s["median_valid_fraction"]:.2%}.',
            "",
            f"[Highest-ranked spectra]({key}-top-spectra.png) · [Exportable "
            f"PDF]({key}-top-spectra.pdf) · [Nuisance plots]({key}-nuisances.png) · "
            f"[Scores]({key}-scores.csv) · [Diagnostic values]({key}-diagnostics.json)",
            "",
            "| Nuisance | Spearman ρ | Paired rows |",
            "|---|---:|---:|",
        ]
        for name, stats in d["correlations"].items():
            rho = "unresolved" if stats["rho"] is None else f'{stats["rho"]:.3f}'
            lines.append(f'| {name} | {rho} | {stats["n"]} |')
        tile = d["tile"]
        overlap = d["overlap"]
        rho = overlap["score_correlation_without_overlap"]["rho"]
        rho_text = "constant or insufficient pairs" if rho is None else f"{rho:.4f}"
        lines += [
            "",
            f'**N-{key}-tile:** {tile["n_tiles"]} holdout tiles; {tile["repeat_tiles"]} repeated '
            f'tiles containing {tile["repeat_rows"]} rows. Tile check: {tile["status"]}; '
            f'eta²={tile["eta_squared"]}, permutation p={tile["p_permutation"]}. [Per-tile '
            f"evidence]({key}-tiles.csv).",
            "",
            f"**N-{key}-overlap:** median overlap residual fraction "
            f'{overlap["median_residual_fraction"]:.2%}, median overlap pixel fraction '
            f'{overlap["median_pixel_fraction"]:.2%}; score correlation after excluding overlap '
            f"pixels {rho_text}; retained "
            f'{overlap["top_retained_without_overlap"]}/{overlap["top_count"]} of the top ranks. This '
            f"diagnostic reuses the fitted reconstruction and is not a refit.",
            "",
            "| Rank | Target ID | Row UID | z | Score | SNR proxy |",
            "|---:|---|---|---:|---:|---:|",
        ]
        for _, r in scores.sort_values("rank").head(6).iterrows():
            lines.append(
                f'| {r["rank"]} | {r.target_id} | {r.row_uid} | {r.z:.4f} | {r.score:.3f} | {r.snr_proxy:.2f} |'
            )
        lines += [
            "",
            f"**Decision {key}:** Does Don approve this window for the next model unit, given its "
            f"coverage, population and measured nuisance behavior? Yes / No.",
            "",
            f"**Decision N-{key}:** Are these nuisance checks sufficient to take this window forward "
            f"to a stronger baseline, with their stated limits? Yes / No.",
            "",
        ]
    lines += [
        "## Limits that survive target deduplication",
        "",
        "- Holding out tile IDs prevents shared manifest tiles crossing the split. It does not "
        "guarantee independent sky regions, calibration, exposure, observing night, camera or "
        "spectrograph; these metadata are not available here. Tiles may share observing conditions "
        "or calibration. Sparse repeated tiles limit the tile-effect test.",
        "- The upstream QSO and ZWARN==0 filter defines a selected archive; results do not represent "
        "all DESI quasars. Redshift errors and redshift-dependent coverage can drive residuals.",
        "- Missing support, normalization and signal-to-noise can change rankings. Selection before "
        "exclusions is uniform within tile-assigned subsets, not a claim of representative anomaly "
        "prevalence. Only 192 held-out selections per candidate provide limited tail statistics.",
        "- Independent-arm noise and diagonal propagated variance omit calibration covariance and "
        "correlations from shared native pixels. Four-Å averaging also smooths narrow features. The "
        "PCA uses no resolution model.",
        "- Reported nuisance p-values are descriptive and uncorrected for multiple comparisons. "
        "There is no labeled anomaly set, external validation, discovery threshold, or physical "
        "object classification. The experiment cannot establish which window is scientifically best.",
        "- The broad candidate overlaps the narrow candidate; shared objects and the same underlying "
        "survey make comparisons dependent. Subsequent tuning on these diagnostics consumes the "
        "holdout as a development set; a new final evaluation split would be needed.",
        "",
        "## Operator handoff",
        "",
        "Don may approve one candidate, request another bounded comparison, or decline all three. "
        "The primary Spender/VAE implementation and adopted window remain outside this unit.",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines))
    (out / "README.md").write_text(
        "# Experiment artifact index\n\n[Comparison and decision report](report.md) · [Provenance](provenance.json)\n\n"
        "For each W1/W2/W3: selection.csv identifies chosen rows and split; sample.csv includes "
        "exclusions; arrays.npz stores rebinned inputs; model.npz stores PCA; predictions.npz stores "
        "reconstructions and coefficients; scores.csv ranks holdout objects; diagnostics.json, "
        "deciles.csv and tiles.csv contain nuisance evidence; top-spectra.png/.pdf and nuisances.png "
        "are figures.\n\n"
        "corpus-before.parquet and corpus-after.parquet record source size/mtime; integrity.json "
        "compares them with Stage 0. Files are derived, gitignored and local to ML01.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-name", default="run-01", help="new child directory inside approved staging")
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name or args.output_name in {".", ".."}:
        raise ValueError("output-name must be a single new directory name")
    out = STAGING / args.output_name
    if out.resolve().parent != STAGING.resolve():
        raise ValueError("output escaped staging")
    out.mkdir(exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.perf_counter()
    manifest_paths = [
        MANIFESTS / n
        for n in ["qso_file_manifest_v1.parquet", "qso_object_manifest_v1.parquet", "manifest_provenance.json"]
    ]
    hashes = {p.name: digest(p) for p in manifest_paths}
    original = json.loads(manifest_paths[-1].read_text())
    for record in original["outputs"].values():
        path = MANIFESTS / Path(record["path"]).name
        if digest(path, "md5") != record["md5"]:
            raise ValueError("manifest provenance checksum mismatch")
    reader = ManifestLoader(MANIFESTS, CORPUS)
    if (len(reader.files), len(reader.objects)) != (10793, 1030934):
        raise ValueError("manifest totals differ from Stage 0")
    before = source_state(reader, out / "integrity.json", phase="before")
    pq.write_table(before, out / "corpus-before.parquet")
    historical = compare_corpus_snapshots(pq.read_table(MANIFESTS / "work/corpus_snapshot.parquet"), before)
    if not historical["unchanged"]:
        write_json(out / "integrity.json", {"stage0_to_before": historical})
        raise ValueError("corpus differs from Stage 0 snapshot; stop boundary")
    selected = select_candidates(reader.objects)
    combined = pd.concat([f.assign(candidate=k) for k, f in selected.items()])
    if combined.groupby("target_id").split.nunique().max() != 1:
        raise ValueError("cross-candidate target split differs")
    shared = {
        f"{a}/{b}": len(set(selected[a].target_id) & set(selected[b].target_id))
        for a, b in [("W1", "W2"), ("W1", "W3"), ("W2", "W3")]
    }
    summaries = {key: run_candidate(key, frame, reader, out) for key, frame in selected.items()}
    after = source_state(reader, out / "integrity.json", phase="after")
    pq.write_table(after, out / "corpus-after.parquet")
    current = compare_corpus_snapshots(before, after)
    after_hashes = {p.name: digest(p) for p in manifest_paths}
    integrity = {
        "stage0_to_before": historical,
        "before_to_after": current,
        "manifest_hashes_unchanged": hashes == after_hashes,
    }
    write_json(out / "integrity.json", integrity)
    if not current["unchanged"] or hashes != after_hashes:
        raise ValueError("source integrity changed; stop boundary")
    sources = [REPO / "src/dqad_audit" / n for n in ["spectral_loader.py", "manifest_loader.py", "experiment.py"]] + [
        Path(__file__)
    ]
    provenance = dict(
        seed=SEED,
        sampling_version=SAMPLING_VERSION,
        window_spawn_keys={key: [WINDOW_SPAWN_KEYS[key]] for key in selected},
        components=8,
        grid_step_angstrom=4,
        selected_per_window=dict(train=576, holdout=192),
        host=platform.node(),
        python=platform.python_version(),
        packages={p: importlib.metadata.version(p) for p in ["numpy", "pandas", "pyarrow", "scipy", "matplotlib"]},
        blas_threads=os.environ.get("OPENBLAS_NUM_THREADS"),
        head_at_run=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source_sha256={str(p.relative_to(REPO)): digest(p) for p in sources},
        manifest_sha256=hashes,
        command=sys.argv,
        cross_candidate_shared_targets=shared,
        unique_selected_targets=int(combined.target_id.nunique()),
        summaries=summaries,
        integrity_passed=True,
        runtime_seconds=time.perf_counter() - started,
    )
    write_json(out / "provenance.json", provenance)
    write_report(out, summaries, provenance)
    logger.info("Complete: %s/report.md; %.1f seconds", out, provenance["runtime_seconds"])


if __name__ == "__main__":
    main()
