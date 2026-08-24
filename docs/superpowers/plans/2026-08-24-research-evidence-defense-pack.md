# Research Evidence and Defense Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an honest five-seed sensitivity package, deterministic figures, defense preflight/reset/recording tools, bilingual login guidance, two one-page PDFs, and supported GitHub Actions versions without changing the frozen runtime model.

**Architecture:** New offline experiment modules reuse the existing generator, temporal split, TGNN, XGBoost, metrics, canonical JSON, and staged publication code. Defense tools call the public HTTP contract and existing browser route; generated evidence stays under `output/`, while editable document sources and verified one-page PDFs are committed.

**Tech Stack:** Python 3.12, NumPy, SciPy, scikit-learn, Matplotlib, PyTorch, XGBoost, Playwright, ReportLab, FastAPI, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-research-evidence-defense-pack-design.md`

## Global Constraints

- Keep `artifacts/reference` and the runtime policy thresholds `0.40` and `0.75` unchanged.
- Label every new multi-seed result `2026_EXPLORATORY_SENSITIVITY` and make no significance claim from five seeds.
- Use binary reporting threshold `0.50`; threshold sweeps describe sensitivity and never optimize against test labels.
- Keep `start-demo.cmd` non-destructive and the default two-container profile on `127.0.0.1:8010`.
- Generated video, screenshots, and experiment runs stay under ignored `output/`; committed PDFs must have editable Markdown sources.
- Use TDD and commit after every independently reviewable task.

---

### Task 1: Pure sensitivity statistics and evidence types

**Files:**
- Create: `research/experiments/__init__.py`
- Create: `research/experiments/sensitivity.py`
- Create: `tests/research/test_sensitivity_statistics.py`
- Modify: `requirements-research.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `evaluate_binary_predictions(labels, probabilities, threshold=0.5)` from `research.training.metrics`.
- Produces: `SeedEvidence`, `MetricSummary`, `summarize_runs(runs)`, `threshold_rows(labels, probabilities)`, and `t_confidence_interval(values, confidence=0.95)`.

- [ ] **Step 1: Write failing tests for aggregation and threshold separation**

```python
def test_summary_uses_sample_sd_and_t_interval():
    runs = [seed_evidence(seed, roc_auc=value) for seed, value in enumerate((0.60, 0.62, 0.64, 0.66, 0.68), 1)]
    summary = summarize_runs(runs)["tgnn"]["roc_auc"]
    assert summary["mean"] == pytest.approx(0.64)
    assert summary["sample_sd"] == pytest.approx(0.0316227766)
    assert summary["ci95_low"] < summary["mean"] < summary["ci95_high"]

def test_threshold_rows_are_fixed_and_do_not_select_an_optimum():
    rows = threshold_rows(np.array([0, 1]), np.array([0.2, 0.8]))
    assert [row["threshold"] for row in rows] == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.90]
    assert all("selected" not in row for row in rows)
```

- [ ] **Step 2: Run the tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_statistics.py -q`

Expected: FAIL because `research.experiments.sensitivity` does not exist.

- [ ] **Step 3: Implement immutable evidence records and statistics**

```python
@dataclass(frozen=True)
class SeedEvidence:
    seed: int
    dataset_sha256: str
    labels: tuple[int, ...]
    probabilities: dict[str, tuple[float, ...]]
    metrics: dict[str, dict[str, Any]]
    artifact_sha256: dict[str, str]

def t_confidence_interval(values: Sequence[float], confidence: float = 0.95) -> tuple[float, float]:
    sample = np.asarray(values, dtype=np.float64)
    mean = float(sample.mean())
    margin = float(stats.t.ppf((1 + confidence) / 2, len(sample) - 1) * stats.sem(sample))
    return mean - margin, mean + margin
```

Use `ddof=1`, reject fewer than two finite observations, and aggregate only `roc_auc`, `pr_auc`, and `brier_score` for confidence intervals. Add `scipy==1.18.1`, `matplotlib==3.11.1`, `reportlab==5.0.1`, and `pypdf==6.16.2` to the pinned research/dev dependencies and matching bounded optional dependencies.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_statistics.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add research/experiments tests/research/test_sensitivity_statistics.py requirements-research.txt pyproject.toml
git commit -m "feat: add exploratory sensitivity statistics"
```

### Task 2: Five-seed orchestration, canonical publication, and CLI

**Files:**
- Create: `research/experiments/runner.py`
- Create: `research/artifacts/sensitivity_verification.py`
- Create: `tests/research/test_sensitivity_runner.py`
- Modify: `research/cli.py`
- Modify: `tests/research/test_cli.py`

**Interfaces:**
- Consumes: `GeneratorConfig`, `generate_dataset`, `build_samples`, `temporal_split`, `TGNNTrainingConfig`, `XGBoostConfig`, `train_tgnn`, `train_xgboost`, and Task 1 evidence types.
- Produces: `SensitivityConfig(seeds, max_epochs, patience, threshold)`, `run_sensitivity(config, output, destination) -> dict`, `verify_sensitivity_pack(path) -> dict`, and CLI commands `evaluate-multiseed` / `verify-multiseed`.

- [ ] **Step 1: Write failing reduced-fixture orchestration tests**

```python
def test_runner_propagates_seed_to_data_and_both_models(tmp_path, monkeypatch):
    observed = {"data": [], "tgnn": [], "xgboost": []}
    install_fake_trainers(monkeypatch, observed)
    manifest = run_sensitivity(SensitivityConfig(seeds=(11, 12), max_epochs=1, patience=1), tmp_path / "runs", tmp_path / "pack")
    assert observed == {"data": [11, 12], "tgnn": [11, 12], "xgboost": [11, 12]}
    assert manifest["provenance"] == "2026_EXPLORATORY_SENSITIVITY"

def test_failed_run_does_not_replace_previous_verified_pack(tmp_path, monkeypatch):
    previous = create_verified_pack(tmp_path / "pack")
    monkeypatch.setattr("research.experiments.runner.train_tgnn", fail_training)
    with pytest.raises(RuntimeError):
        run_sensitivity(config_for(13), tmp_path / "runs", tmp_path / "pack")
    assert verify_sensitivity_pack(tmp_path / "pack")["manifest_sha256"] == previous
```

- [ ] **Step 2: Run and verify failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_runner.py tests/research/test_cli.py -q`

Expected: FAIL because the runner, verifier, and CLI commands are absent.

- [ ] **Step 3: Implement deterministic orchestration and staged publication**

For each seed construct `GeneratorConfig(seed=seed)`, `TGNNTrainingConfig(seed=seed, ...)`, and `XGBoostConfig(random_state=seed)`. Persist `labels.npy`, both probability arrays, per-seed canonical JSON, artifact hashes, and split anchors. Assemble the pack in `TemporaryDirectory(dir=destination.parent)`, verify every declared hash, then call the existing same-filesystem backup/rollback publication pattern.

```python
@dataclass(frozen=True)
class SensitivityConfig:
    seeds: tuple[int, ...] = (20260815, 20260816, 20260817, 20260818, 20260819)
    max_epochs: int = 100
    patience: int = 10
    threshold: float = 0.50
```

Expose exact CLI options `--seeds`, `--max-epochs`, `--patience`, `--output`, and `--destination`. `verify-multiseed` must import without Torch or XGBoost installed.

- [ ] **Step 4: Run focused tests and one two-seed smoke build**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_runner.py tests/research/test_cli.py -q`

Run: `.\.venv\Scripts\python.exe -m research.cli evaluate-multiseed --seeds 20260815 20260816 --max-epochs 1 --patience 1 --output output/research/sensitivity-smoke --destination output/research/sensitivity-pack-smoke`

Expected: tests PASS and `verify-multiseed` returns `status=verified`.

- [ ] **Step 5: Commit**

```powershell
git add research/experiments/runner.py research/artifacts/sensitivity_verification.py research/cli.py tests/research/test_sensitivity_runner.py tests/research/test_cli.py
git commit -m "feat: orchestrate verifiable multi-seed runs"
```

### Task 3: Publication figures and exploratory appendix

**Files:**
- Create: `research/experiments/figures.py`
- Create: `research/experiments/report.py`
- Create: `tests/research/test_sensitivity_figures.py`
- Modify: `research/experiments/runner.py`
- Modify: `docs/thesis-traceability.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified labels/probabilities and summaries from Tasks 1–2.
- Produces: `render_figures(pack, output) -> tuple[Path, ...]`, `write_appendix(pack, output) -> Path`, five PNG panels, two CSV files, and `research-appendix.md`.

- [ ] **Step 1: Write failing figure/report contract tests**

```python
def test_figures_have_required_names_and_finite_inputs(tmp_path):
    files = render_figures(fake_verified_pack(), tmp_path)
    assert {path.name for path in files} == {"roc.png", "precision-recall.png", "calibration.png", "threshold-sensitivity.png", "confusion-matrices.png"}
    assert all(path.stat().st_size > 10_000 for path in files)

def test_appendix_states_exploratory_boundary(tmp_path):
    text = write_appendix(fake_verified_pack(), tmp_path).read_text("utf-8")
    assert "2026_EXPLORATORY_SENSITIVITY" in text
    assert "five seeds" in text
    assert "does not reproduce the original thesis" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_figures.py -q`

Expected: FAIL because figure/report functions do not exist.

- [ ] **Step 3: Implement accessible deterministic rendering**

Use Matplotlib `Agg`, 160 dpi, colorblind-safe blue/orange, fixed 7.2 × 4.6 inch canvases, English axis labels, diagonal/reference baselines, confidence ribbons labeled as seed variability, and explicit `Synthetic five-seed sensitivity` subtitles. Use `CalibrationDisplay`, `RocCurveDisplay`, and `PrecisionRecallDisplay` data functions without fitting another model.

CSV columns must be stable and include `seed`, `model`, all metrics, dataset hash, and artifact hash. The appendix prints mean ± sample SD and t interval while stating that `n=5` is exploratory.

- [ ] **Step 4: Run figure tests and generate the real five-seed pack**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_sensitivity_figures.py -q`

Run: `.\.venv\Scripts\python.exe -m research.cli evaluate-multiseed --output output/research/sensitivity-runs --destination output/research/sensitivity-pack`

Expected: all declared files verify; review plots for clipped labels, misleading scales, and unreadable legends.

- [ ] **Step 5: Commit code and documentation, not generated run media**

```powershell
git add research/experiments/figures.py research/experiments/report.py research/experiments/runner.py tests/research/test_sensitivity_figures.py docs/thesis-traceability.md README.md
git commit -m "feat: render exploratory research evidence"
```

### Task 4: Defense preflight and clean reset launcher

**Files:**
- Create: `scripts/defense_preflight.py`
- Create: `reset-defense-demo.cmd`
- Create: `tests/test_defense_preflight.py`
- Modify: `tests/test_release_contract.py`
- Modify: `launcher-messages.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: `/api/health`, `/api/v1/auth/login`, `/api/v1/auth/session`, `/api/v1/auth/logout`, local UI/document paths.
- Produces: `run_preflight(base_url, client_factory) -> PreflightResult` and a destructive-but-confirmed reset launcher scoped to this Compose project.

- [ ] **Step 1: Write failing preflight and launcher tests**

```python
def test_preflight_checks_and_logs_out_all_five_roles(fake_http):
    result = run_preflight("http://127.0.0.1:8010", fake_http.client)
    assert result.ok is True
    assert result.roles == ("supplier", "core_enterprise", "financier", "risk_manager", "auditor")
    assert fake_http.logout_count == 5

def test_reset_launcher_is_separate_and_scoped():
    launcher = _read("reset-defense-demo.cmd")
    assert "docker compose down -v" in launcher
    assert 'call "%~dp0start-demo.cmd"' in launcher
    assert "Remove-Item" not in launcher and "rm -rf" not in launcher
    assert "docker compose down -v" not in _read("start-demo.cmd")
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_defense_preflight.py tests/test_release_contract.py -q`

Expected: FAIL because the preflight and reset launcher are absent.

- [ ] **Step 3: Implement read-only preflight and explicit reset**

Use `urllib.request` plus one independent cookie jar per account; verify returned role and always call logout in `finally`. Parse HTML to assert `<html lang="ru">`, the Chinese control, login form, and five-role guide. Verify the two source Markdown files and PDFs exist.

`reset-defense-demo.cmd` must display Russian and Chinese warnings, require the exact confirmation text `RESET DEMO`, execute only `docker compose down -v`, call `start-demo.cmd`, then run preflight. It must never enumerate or delete filesystem paths.

- [ ] **Step 4: Run focused tests and live preflight**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_defense_preflight.py tests/test_release_contract.py -q`

Run: `.\.venv\Scripts\python.exe scripts\defense_preflight.py --base-url http://127.0.0.1:8010`

Expected: PASS and a five-role success summary with zero ledger mutations.

- [ ] **Step 5: Commit**

```powershell
git add scripts/defense_preflight.py reset-defense-demo.cmd tests/test_defense_preflight.py tests/test_release_contract.py launcher-messages.json README.md
git commit -m "feat: add defense preflight and clean reset"
```

### Task 5: Login guide and optional browser video

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/workflow.css`
- Modify: `app/static/workflow.js`
- Refactor: `scripts/browser_acceptance.py`
- Modify: `tests/test_ui_contract.py`

**Interfaces:**
- Consumes: existing real login/logout and Playwright five-role flow.
- Produces: numbered `.demo-role-guide` buttons with `data-demo-username`, `run_acceptance(record_video=False)`, CLI `--record-video`, screenshot and optional WebM path.

- [ ] **Step 1: Write failing UI and recording-option tests**

```python
def test_login_has_bilingual_numbered_role_guide():
    html = _html()
    assert html.count('data-demo-username="') == 5
    assert html.count("demoRoleGuide:") == 2
    assert 'aria-label="Demo role order"' in html

def test_browser_script_requires_explicit_video_flag():
    source = Path("scripts/browser_acceptance.py").read_text("utf-8")
    assert "--record-video" in source
    assert "record_video_dir" in source
    assert "record_video=False" in source
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ui_contract.py -q`

Expected: FAIL on absent guide and recording option.

- [ ] **Step 3: Implement accessible role fill and refactor acceptance entry point**

Role buttons set username and `Demo123!`, focus the login button, and announce the selected role; they never submit the form. Preserve server authentication and all existing selectors. Use `browser.new_context(record_video_dir=...)` only when the explicit flag is set; close context before reading `page.video.path()`.

- [ ] **Step 4: Run UI tests and both real browser modes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ui_contract.py -q`

Run: `.\.venv\Scripts\python.exe scripts\browser_acceptance.py`

Run: `.\.venv\Scripts\python.exe scripts\browser_acceptance.py --record-video`

Expected: normal acceptance passes without video; explicit mode creates `output/defense-video/*.webm` and the existing final screenshot.

- [ ] **Step 5: Commit**

```powershell
git add app/static/index.html app/static/workflow.css app/static/workflow.js scripts/browser_acceptance.py tests/test_ui_contract.py
git commit -m "feat: guide and record the defense journey"
```

### Task 6: One-page documents, CI runtime, and release verification

**Files:**
- Create: `docs/defense-one-page.md`
- Create: `docs/research-brief-en.md`
- Create: `docs/defense-one-page.pdf`
- Create: `docs/research-brief-en.pdf`
- Create: `scripts/render_defense_documents.py`
- Create: `tests/test_defense_documents.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/demo-script.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: approved design, traceability matrix, fixed demo URLs/accounts, and Task 3 evidence boundary.
- Produces: `render_documents(root) -> tuple[Path, Path]`, two single-page PDFs, supported Action versions, and complete defense instructions.

- [ ] **Step 1: Write failing document and workflow tests**

```python
def test_defense_documents_render_to_exactly_one_page(tmp_path):
    pdfs = render_documents(ROOT)
    assert all(len(PdfReader(path).pages) == 1 for path in pdfs)

def test_ci_uses_node24_action_generations():
    workflow = _read(".github/workflows/ci.yml")
    assert "actions/checkout@v5" in workflow
    assert "actions/setup-python@v6" in workflow
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_defense_documents.py tests/test_release_contract.py -q`

Expected: FAIL because sources, renderer, and updated Actions are absent.

- [ ] **Step 3: Implement deterministic PDF rendering and concise sources**

Use ReportLab with embedded DejaVu Sans/DejaVu Sans Condensed fonts discovered from the bundled workspace runtime. The bilingual sheet contains architecture, five-role order, research evidence, exact non-claims, preflight/reset commands, and fallback paths. The English brief contains question, method, verified implementation, metrics labeled synthetic, limitations, and next research direction. Add SHA-256 and generated date in PDF metadata, not in visible content.

- [ ] **Step 4: Render and visually inspect both PDFs**

Run: `.\.venv\Scripts\python.exe scripts\render_defense_documents.py`

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_defense_documents.py tests/test_release_contract.py -q`

Render each PDF to PNG using the PDF skill and inspect for one page, readable bilingual glyphs, no clipping, and no overlap.

- [ ] **Step 5: Run full increment verification**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Run: `.\.venv\Scripts\python.exe -m research.cli verify --reference artifacts/reference`

Run: `docker compose up --build -d`

Run: `.\.venv\Scripts\python.exe scripts\defense_preflight.py`

Run: `.\.venv\Scripts\python.exe scripts\browser_acceptance.py`

Expected: all tests pass, reference hash remains unchanged, PostgreSQL/research/ledger health is green, and the browser route passes.

- [ ] **Step 6: Commit and push the increment**

```powershell
git add docs scripts/render_defense_documents.py tests/test_defense_documents.py .github/workflows/ci.yml README.md
git commit -m "docs: ship the defense and research brief pack"
git push origin main
```
