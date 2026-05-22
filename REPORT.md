# Support Triage Pipeline — Project Report

**Date:** May 22, 2026  
**Status:** Implemented & Validated ✅

---

## 1. Overview

This project is a **replayable, end-to-end customer support triage pipeline** built in Python. It reads raw support tickets and a triage configuration from disk, classifies each ticket using an LLM (or a deterministic fallback), exposes a human review checkpoint for corrections, and produces a fully-routed final queue with a markdown summary.

The pipeline is designed to be run from a clean checkout. All outputs are regenerated on every run from the input files — no precomputed artifacts are stored or relied upon.

---

## 2. Input Files

| File | Purpose |
|------|---------|
| `tickets.json` | Array of raw customer support tickets |
| `triage_config.json` | Allowed categories, priorities, routing rules, and reply style |

The pipeline reads both files from disk at startup. Neither file is hardcoded into the logic — the pipeline works with any equivalent replacement files.

---

## 3. Pipeline Stages

The pipeline enforces these stages in order. Each transition is logged to the terminal with a `=== STAGE: ... ===` banner.

```
INIT
 → INPUTS_LOADED          tickets.json + triage_config.json read from disk
 → TICKETS_NORMALIZED     deterministic normalization → normalized_tickets.json
 → TRIAGE_PREDICTED       LLM call (or heuristic fallback) → triage_predictions.json
 → HUMAN_REVIEW_COMPLETE  interactive terminal review → review_overrides.json
 → FINAL_QUEUE_GENERATED  apply overrides, reroute → final_queue.json + queue_summary.md
 → RESULTS_FINALISED      all artifacts confirmed written
```

---

## 4. Requirements Coverage

### MUST Requirements

**Stage 1 — Deterministic Normalization**

Each raw ticket is transformed into a normalized record in pure Python before any LLM call. The `text_for_model` field is built as:

```
Subject: {subject}\nMessage: {message}
```

The `char_count` field is the length of that string. This is fully deterministic — no LLM involvement. Output is saved to `normalized_tickets.json`.

**Stage 2 — Ticket Triage Prediction**

A single prompt is built containing the full `triage_config.json` (categories, priorities, routing rules, reply style) and all normalized tickets. This prompt is sent to the LLM in one call.

The pipeline includes two execution paths:

- **Real LLM path**: If `KIMI_API_KEY` (or `OPENAI_API_KEY`) is set and the `openai` package is installed, the pipeline calls the Kimi/Moonshot API using the OpenAI-compatible SDK (`base_url=https://api.moonshot.ai/v1`, model `kimi-k2.6` by default). These are configurable via environment variables.
- **Deterministic fallback**: If no API key is set, a keyword-based heuristic classifier runs instead. This allows the pipeline to execute fully offline without any external calls.

After receiving the model response, `_enforce_config()` validates every prediction. Any category or priority outside the allowed lists is replaced with a safe default (`other` / `normal`). The `route_to` value is **always re-derived from `routing_rules` in code** — the model's routing output is never trusted directly.

Predictions are saved to `triage_predictions.json`.

**Stage 3 — Human Review Checkpoint**

Before generating the final queue, the pipeline displays a formatted table of all predictions (ticket ID, category, priority, confidence, and destination queue) and presents the exact prompt required by the spec:

```
Enter overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

Each override is validated against `allowed_categories` and `allowed_priorities` before being accepted. Invalid inputs are rejected with an error message. Overrides are saved to `review_overrides.json` with `old_category`, `new_category`, `old_priority`, and `new_priority` fields.

**Stage 4 — Final Queue**

`final_queue.json` is only generated after the human review is complete. For each ticket, the pipeline applies any recorded override and re-derives `final_route_to` from the routing rules. The `was_overridden` boolean reflects whether a human correction was applied.

`queue_summary.md` is also generated, containing total ticket count, breakdown by category, breakdown by priority, queue distribution, and a list of overridden tickets.

### SHOULD Requirements

**Confidence and Escalation Rules**

Each prediction includes a `confidence` float (0.0–1.0). After predictions are computed, `_compute_escalations()` runs entirely in deterministic Python code:

- Flag if `category == "other"`
- Flag if `confidence < 0.60`

Results are saved to `escalations.json`.

**Validation Script**

`validate.py` can be run independently after the pipeline:

```bash
python validate.py
```

It performs five checks:

1. All required artifact files exist and contain valid JSON
2. Normalization is deterministic (`text_for_model` and `char_count` are recomputed and compared)
3. Every prediction uses allowed categories and priorities, and routes match config
4. Overrides reference valid tickets and use allowed values
5. Final queue correctly reflects all overrides, with consistent routing

### STRETCH Requirements

**Batch Resilience**

The `_enforce_config()` function handles malformed or missing predictions gracefully. If a ticket is absent from the model response, or if a field contains an invalid value, the pipeline substitutes safe defaults (`other` / `normal` / `manual_review_queue`) and continues — it does not crash.

When the real LLM call fails entirely (network error, API error, etc.), the pipeline catches the exception and falls back to the deterministic heuristic classifier automatically.

**LLM Call Logging**

Every LLM call is appended to `llm_calls.jsonl` (one JSON object per line). Each entry records:

- `timestamp` (ISO-8601)
- `stage`
- `mode` (`real` or `simulated`)
- `provider` and `model`
- `prompt_hash` (SHA-256 first 16 hex chars)
- `prompt_chars` (prompt length)
- `n_inputs` and `n_outputs`

---

## 5. Generated Artifacts

| Artifact | Stage Generated | Required |
|----------|----------------|----------|
| `normalized_tickets.json` | TICKETS_NORMALIZED | ✅ Required |
| `triage_predictions.json` | TRIAGE_PREDICTED | ✅ Required |
| `review_overrides.json` | HUMAN_REVIEW_COMPLETE | ✅ Required |
| `final_queue.json` | FINAL_QUEUE_GENERATED | ✅ Required |
| `queue_summary.md` | FINAL_QUEUE_GENERATED | ✅ Required |
| `escalations.json` | TRIAGE_PREDICTED | ✅ Should |
| `llm_calls.jsonl` | TRIAGE_PREDICTED (append) | ✅ Stretch |

---

## 6. How to Run

**Install dependencies:**

```bash
pip install -r requirements.txt
```

`requirements.txt` contains:
```
openai>=1.0.0
python-dotenv>=1.0.0
```

**Set your API key (optional — pipeline works without it):**

```bash
# On Linux/macOS
export KIMI_API_KEY=sk-your-key-here

# On Windows
set KIMI_API_KEY=sk-your-key-here
```

Or create a `.env` file in the project directory:

```
KIMI_API_KEY=sk-your-key-here
```

**Optional environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `KIMI_API_KEY` | _(none)_ | Kimi API key |
| `OPENAI_API_KEY` | _(none)_ | Fallback if no Kimi key |
| `LLM_BASE_URL` | `https://api.moonshot.ai/v1` | API endpoint |
| `LLM_MODEL` | `kimi-k2.6` | Model name |
| `LLM_PROVIDER` | `moonshot` | Used in log entries |
| `LLM_TEMPERATURE` | `1` | Sampling temperature |

**Run the pipeline:**

```bash
python triage_pipeline.py
```

**Run in non-interactive mode (no human overrides):**

```bash
echo "" | python triage_pipeline.py
```

**Run the validator:**

```bash
python validate.py
```

Expected output:
```
✅ VALIDATION PASSED
```

---

## 7. Key Design Decisions

**Config-driven constraints, not model-driven.** Category, priority, and routing values are always enforced in Python from `triage_config.json`. The model cannot produce an output that violates the configuration — `_enforce_config()` runs unconditionally on every response.

**Single prompt for all tickets.** Rather than making one LLM call per ticket, a single prompt containing all tickets is sent at once. This is more efficient and keeps the LLM call log clean with one entry per pipeline run.

**Offline capability.** The deterministic keyword-based heuristic in `_heuristic_triage()` ensures the pipeline produces valid, well-structured output even without any API key or network access. This makes it testable in any environment.

**Routing is always re-derived.** `route_to` and `final_route_to` are never copied from the model output. They are computed as `routing_rules[category]` in code. This means routing is guaranteed to be consistent with the config regardless of what the model returns.

---

## 8. File Structure

```
.
├── tickets.json              Input tickets
├── triage_config.json        Allowed labels, routing, reply style
├── triage_pipeline.py        Main pipeline (all stages)
├── validate.py               End-to-end validation script
├── requirements.txt          Python dependencies
├── REPORT.md                 This file
│
├── normalized_tickets.json   (generated)
├── triage_predictions.json   (generated)
├── review_overrides.json     (generated)
├── final_queue.json          (generated)
├── queue_summary.md          (generated)
├── escalations.json          (generated)
└── llm_calls.jsonl           (generated, append mode)
```
