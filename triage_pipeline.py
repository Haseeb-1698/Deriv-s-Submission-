#!/usr/bin/env python3
"""
Support Triage Pipeline
=======================
A clean, stdlib-only pipeline that classifies customer support tickets,
allows a human reviewer to override predictions, and produces a final
routing queue.

Stages (enforced in order):
    INIT
      -> INPUTS_LOADED
      -> TICKETS_NORMALIZED
      -> TRIAGE_PREDICTED
      -> HUMAN_REVIEW_COMPLETE
      -> FINAL_QUEUE_GENERATED
      -> RESULTS_FINALISED
"""

import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from enum import Enum

# --- Optional real-LLM support (OpenAI-compatible, e.g. Kimi / Moonshot) -----
# The pipeline still runs with stdlib only; these imports are best-effort.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

try:
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except Exception:
    _OPENAI_AVAILABLE = False

# Kimi / Moonshot is an OpenAI-compatible endpoint.
LLM_API_KEY  = os.environ.get("KIMI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.moonshot.ai/v1")
LLM_MODEL    = os.environ.get("LLM_MODEL", "kimi-k2.6")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "moonshot")


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------
class Stage(Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    TICKETS_NORMALIZED = "TICKETS_NORMALIZED"
    TRIAGE_PREDICTED = "TRIAGE_PREDICTED"
    HUMAN_REVIEW_COMPLETE = "HUMAN_REVIEW_COMPLETE"
    FINAL_QUEUE_GENERATED = "FINAL_QUEUE_GENERATED"
    RESULTS_FINALISED = "RESULTS_FINALISED"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def banner(title: str) -> None:
    """Pretty stage banner with === borders, as required by the spec."""
    line = "=" * 60
    print()
    print(line)
    print(f"STAGE: {title}")
    print(line)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class TriagePipeline:
    """Orchestrates all triage stages."""

    # File names are intentional and stable so validate.py can find them.
    TICKETS_FILE        = "tickets.json"
    CONFIG_FILE         = "triage_config.json"
    NORMALIZED_FILE     = "normalized_tickets.json"
    PREDICTIONS_FILE    = "triage_predictions.json"
    OVERRIDES_FILE      = "review_overrides.json"
    FINAL_QUEUE_FILE    = "final_queue.json"
    SUMMARY_FILE        = "queue_summary.md"
    ESCALATIONS_FILE    = "escalations.json"
    LLM_LOG_FILE        = "llm_calls.jsonl"

    def __init__(self):
        self.stage = Stage.INIT
        self.tickets = []
        self.config = {}
        self.normalized = []
        self.predictions = []
        self.overrides = []
        self.final_queue = []
        self.escalations = []
        self._llm_mode = "simulated"  # set to "real" when Kimi/OpenAI is called

    # ----- Stage 1: load inputs ------------------------------------------------
    def load_inputs(self) -> None:
        banner(Stage.INPUTS_LOADED.value)
        self.tickets = read_json(self.TICKETS_FILE)
        self.config = read_json(self.CONFIG_FILE)
        print(f"Loaded {len(self.tickets)} tickets from {self.TICKETS_FILE}")
        print(f"Loaded config from {self.CONFIG_FILE}")
        print(f"  categories: {self.config['allowed_categories']}")
        print(f"  priorities: {self.config['allowed_priorities']}")
        self.stage = Stage.INPUTS_LOADED

    # ----- Stage 2: deterministic normalization --------------------------------
    def normalize_tickets(self) -> None:
        """Pure-Python, deterministic. No LLM involvement."""
        banner(Stage.TICKETS_NORMALIZED.value)

        self.normalized = []
        for t in self.tickets:
            text_for_model = f"Subject: {t['subject']}\nMessage: {t['message']}"
            self.normalized.append({
                "ticket_id":      t["ticket_id"],
                "customer_id":    t.get("customer_id"),
                "subject":        t["subject"],
                "message":        t["message"],
                "channel":        t.get("channel"),
                "created_at":     t.get("created_at"),
                "text_for_model": text_for_model,
                "char_count":     len(text_for_model),
            })

        write_json(self.NORMALIZED_FILE, self.normalized)
        print(f"Normalized {len(self.normalized)} tickets -> {self.NORMALIZED_FILE}")
        self.stage = Stage.TICKETS_NORMALIZED

    # ----- Stage 3: triage prediction ------------------------------------------
    def predict_triage(self) -> None:
        """
        Build ONE prompt containing the full triage_config + every normalized
        ticket, then invoke the (simulated) LLM. Real OpenAI-compatible call
        site is marked inside _simulate_llm_triage().
        """
        banner(Stage.TRIAGE_PREDICTED.value)

        prompt = self._build_triage_prompt()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        print(f"Built single triage prompt (sha256[:16]={prompt_hash}, "
              f"chars={len(prompt)})")

        raw_predictions = self._simulate_llm_triage(prompt)
        self.predictions = self._enforce_config(raw_predictions)
        write_json(self.PREDICTIONS_FILE, self.predictions)
        print(f"Wrote {len(self.predictions)} predictions -> {self.PREDICTIONS_FILE}")

        # Log the LLM call (jsonl, append-mode so multiple runs accumulate).
        self._log_llm_call(
            stage=Stage.TRIAGE_PREDICTED.value,
            mode=self._llm_mode,
            provider=(LLM_PROVIDER if self._llm_mode == "real" else "heuristic"),
            model=(LLM_MODEL if self._llm_mode == "real" else "rule_based_v1"),
            prompt_hash=prompt_hash,
            prompt_chars=len(prompt),
            n_inputs=len(self.normalized),
            n_outputs=len(self.predictions),
        )

        # Deterministic escalations (extra credit).
        self._compute_escalations()
        self.stage = Stage.TRIAGE_PREDICTED

    def _build_triage_prompt(self) -> str:
        cfg = self.config
        return (
            "You are a support-ticket triage assistant.\n"
            "Classify each ticket and draft a short reply.\n\n"
            "STRICT RULES:\n"
            f"- category MUST be one of: {cfg['allowed_categories']}\n"
            f"- priority MUST be one of: {cfg['allowed_priorities']}\n"
            f"- route_to MUST be looked up from routing_rules using the chosen category\n"
            f"- suggested_reply tone: {cfg['reply_style']['tone']}\n"
            f"- suggested_reply max words: {cfg['reply_style']['max_words']}\n"
            f"- confidence is a float in [0.0, 1.0]\n\n"
            f"CONFIG:\n{json.dumps(cfg, indent=2)}\n\n"
            f"TICKETS:\n{json.dumps(self.normalized, indent=2)}\n\n"
            "Return a JSON array. One object per ticket with keys: "
            "ticket_id, category, priority, confidence, reason, "
            "suggested_reply, route_to."
        )

    def _simulate_llm_triage(self, prompt: str):
        """
        Triage entry point. If an OpenAI-compatible API key is configured
        (KIMI_API_KEY / OPENAI_API_KEY) and the `openai` SDK is installed,
        we call the real model. Otherwise we fall back to a deterministic
        keyword-based heuristic so the pipeline still runs offline.
        """
        # --- Real LLM path (Kimi / Moonshot, OpenAI-compatible) ---------------
        if _OPENAI_AVAILABLE and LLM_API_KEY:
            try:
                result = self._call_real_llm(prompt)
                self._llm_mode = "real"
                return result
            except Exception as exc:  # noqa: BLE001
                print(f"  ! real LLM call failed ({exc.__class__.__name__}: {exc})")
                print("  ! falling back to deterministic heuristic")

        # --- Deterministic fallback ------------------------------------------
        self._llm_mode = "simulated"
        return self._heuristic_triage()

    def _call_real_llm(self, prompt: str):
        """Real OpenAI-compatible call (Kimi/Moonshot by default)."""
        print(f"  calling {LLM_PROVIDER} / {LLM_MODEL} at {LLM_BASE_URL} ...")
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        # Some Kimi models (e.g. kimi-k2.6) only accept temperature=1.
        try:
            temperature = float(os.environ.get("LLM_TEMPERATURE", "1"))
        except ValueError:
            temperature = 1.0
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=temperature,
            messages=[
                {"role": "system",
                 "content": "You output ONLY valid JSON. No prose, no markdown."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        # Strip ``` fences if the model added them anyway.
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[4:]
            raw = raw.strip().rstrip("`").strip()
        data = json.loads(raw)
        if isinstance(data, dict) and "predictions" in data:
            data = data["predictions"]
        if not isinstance(data, list):
            raise ValueError("LLM did not return a JSON array")
        print(f"  received {len(data)} predictions from {LLM_MODEL}")
        return data

    def _heuristic_triage(self):
        """Deterministic offline classifier used when the real LLM is unavailable."""
        routing = self.config["routing_rules"]
        max_words = self.config["reply_style"]["max_words"]
        results = []

        for t in self.normalized:
            blob = (t["subject"] + " " + t["message"]).lower()

            # Heuristic category detection.
            if any(k in blob for k in ("charge", "charged", "refund", "deposit",
                                      "billing", "payment", "invoice")):
                category, confidence = "billing_issue", 0.88
                reason = "Mentions duplicate charges or payment problem."
            elif any(k in blob for k in ("log in", "login", "password",
                                        "credentials", "access", "locked")):
                category, confidence = "account_access", 0.90
                reason = "Customer cannot authenticate / access account."
            elif any(k in blob for k in ("crash", "bug", "error", "broken",
                                        "doesn't work", "freeze")):
                category, confidence = "bug_report", 0.85
                reason = "Reports software malfunction."
            elif any(k in blob for k in ("how do i", "how can i", "where can i",
                                        "export", "download", "tutorial")):
                category, confidence = "product_how_to", 0.82
                reason = "How-to / usage question."
            else:
                category, confidence = "other", 0.40
                reason = "No strong category signal in ticket text."

            # Heuristic priority detection.
            if any(k in blob for k in ("urgent", "asap", "immediately",
                                       "market open", "can't access")):
                priority = "urgent"
            elif category in ("billing_issue", "account_access"):
                priority = "high"
            elif category == "bug_report":
                priority = "normal"
            elif category == "product_how_to":
                priority = "low"
            else:
                priority = "normal"

            reply = self._draft_reply(category, max_words)

            results.append({
                "ticket_id":       t["ticket_id"],
                "category":        category,
                "priority":        priority,
                "confidence":      confidence,
                "reason":          reason,
                "suggested_reply": reply,
                "route_to":        routing[category],
            })
        return results

    @staticmethod
    def _draft_reply(category: str, max_words: int) -> str:
        templates = {
            "billing_issue":
                "Thanks for flagging this. We can see the duplicate charge "
                "on your account and have escalated it to our payments team. "
                "You should see the refund within 3-5 business days.",
            "account_access":
                "Sorry you're locked out. We've triggered a secure reset on "
                "your account; please check your email for the new link and "
                "reply here if you still can't get in.",
            "product_how_to":
                "Happy to help. You can export your transaction history from "
                "Settings -> Statements -> Export CSV. Let us know if you "
                "hit any issues with the download.",
            "bug_report":
                "Thanks for the report. We've logged a bug for the portfolio "
                "tab crash and our mobile team is investigating. We'll follow "
                "up as soon as a fix is available.",
            "other":
                "Thanks for reaching out. A support specialist will review "
                "your message and get back to you shortly.",
        }
        text = templates.get(category, templates["other"])
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words])
        return text

    def _enforce_config(self, raw):
        """Defensive guard: reject anything outside the allowed config."""
        cats = self.config["allowed_categories"]
        pris = self.config["allowed_priorities"]
        routing = self.config["routing_rules"]

        cleaned = []
        for r in raw:
            cat = r["category"] if r.get("category") in cats else "other"
            pri = r["priority"] if r.get("priority") in pris else "normal"
            cleaned.append({
                "ticket_id":       r["ticket_id"],
                "category":        cat,
                "priority":        pri,
                "confidence":      float(r.get("confidence", 0.5)),
                "reason":          r.get("reason", ""),
                "suggested_reply": r.get("suggested_reply", ""),
                "route_to":        routing[cat],  # always rederived from config
            })
        return cleaned

    def _compute_escalations(self):
        """Deterministic escalations: category=='other' OR confidence < 0.60."""
        self.escalations = [
            {
                "ticket_id":  p["ticket_id"],
                "category":   p["category"],
                "confidence": p["confidence"],
                "reasons":    (["category_is_other"] if p["category"] == "other" else [])
                              + (["low_confidence"] if p["confidence"] < 0.60 else []),
            }
            for p in self.predictions
            if p["category"] == "other" or p["confidence"] < 0.60
        ]
        write_json(self.ESCALATIONS_FILE, self.escalations)
        print(f"Escalations: {len(self.escalations)} -> {self.ESCALATIONS_FILE}")

    def _log_llm_call(self, **fields):
        entry = {"timestamp": utcnow_iso(), **fields}
        with open(self.LLM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ----- Stage 4: human review -----------------------------------------------
    def human_review(self) -> None:
        banner(Stage.HUMAN_REVIEW_COMPLETE.value)
        self._print_predictions_table()

        # EXACT prompt text required by the spec:
        print("Enter overrides as: ticket_id,category,priority")
        print("Press Enter on an empty line when done.")

        cats = set(self.config["allowed_categories"])
        pris = set(self.config["allowed_priorities"])
        known_ids = {p["ticket_id"] for p in self.predictions}
        pred_by_id = {p["ticket_id"]: p for p in self.predictions}

        self.overrides = []
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if line == "":
                break

            parts = [x.strip() for x in line.split(",")]
            if len(parts) != 3:
                print("  ! expected 3 comma-separated fields, skipping")
                continue
            tid, new_cat, new_pri = parts
            if tid not in known_ids:
                print(f"  ! unknown ticket_id '{tid}', skipping")
                continue
            if new_cat not in cats:
                print(f"  ! invalid category '{new_cat}', skipping")
                continue
            if new_pri not in pris:
                print(f"  ! invalid priority '{new_pri}', skipping")
                continue

            original = pred_by_id[tid]
            self.overrides.append({
                "ticket_id":    tid,
                "old_category": original["category"],
                "new_category": new_cat,
                "old_priority": original["priority"],
                "new_priority": new_pri,
            })
            print(f"  + override recorded for {tid}")

        write_json(self.OVERRIDES_FILE, self.overrides)
        print(f"Recorded {len(self.overrides)} override(s) -> {self.OVERRIDES_FILE}")
        self.stage = Stage.HUMAN_REVIEW_COMPLETE

    def _print_predictions_table(self) -> None:
        print()
        print(f"{'TICKET':<10} {'CATEGORY':<18} {'PRIORITY':<10} "
              f"{'CONF':<6} {'ROUTE_TO':<24}")
        print("-" * 72)
        for p in self.predictions:
            print(f"{p['ticket_id']:<10} {p['category']:<18} "
                  f"{p['priority']:<10} {p['confidence']:<6.2f} "
                  f"{p['route_to']:<24}")
        print()

    # ----- Stage 5: final queue ------------------------------------------------
    def generate_final_queue(self) -> None:
        banner(Stage.FINAL_QUEUE_GENERATED.value)
        overrides_by_id = {o["ticket_id"]: o for o in self.overrides}
        routing = self.config["routing_rules"]

        self.final_queue = []
        for p in self.predictions:
            tid = p["ticket_id"]
            o = overrides_by_id.get(tid)
            if o:
                final_cat = o["new_category"]
                final_pri = o["new_priority"]
                overridden = True
            else:
                final_cat = p["category"]
                final_pri = p["priority"]
                overridden = False

            self.final_queue.append({
                "ticket_id":       tid,
                "final_category":  final_cat,
                "final_priority":  final_pri,
                "final_route_to":  routing[final_cat],  # rederive from config
                "suggested_reply": p["suggested_reply"],
                "was_overridden":  overridden,
            })

        write_json(self.FINAL_QUEUE_FILE, self.final_queue)
        print(f"Final queue: {len(self.final_queue)} entries "
              f"-> {self.FINAL_QUEUE_FILE}")

        self._write_summary()
        self.stage = Stage.FINAL_QUEUE_GENERATED

    def _write_summary(self) -> None:
        cat_counts = {}
        pri_counts = {}
        queue_counts = {}
        for e in self.final_queue:
            cat_counts[e["final_category"]] = cat_counts.get(e["final_category"], 0) + 1
            pri_counts[e["final_priority"]] = pri_counts.get(e["final_priority"], 0) + 1
            queue_counts[e["final_route_to"]] = queue_counts.get(e["final_route_to"], 0) + 1

        overridden = [e for e in self.final_queue if e["was_overridden"]]

        lines = []
        lines.append("# Support Triage - Queue Summary")
        lines.append("")
        lines.append(f"_Generated: {utcnow_iso()}_")
        lines.append("")
        lines.append(f"**Total Tickets:** {len(self.final_queue)}")
        lines.append("")
        lines.append("## Category breakdown")
        for c in self.config["allowed_categories"]:
            lines.append(f"- {c}: {cat_counts.get(c, 0)}")
        lines.append("")
        lines.append("## Priority breakdown")
        for p in self.config["allowed_priorities"]:
            lines.append(f"- {p}: {pri_counts.get(p, 0)}")
        lines.append("")
        lines.append("## Queue distribution")
        for q in sorted(queue_counts):
            lines.append(f"- {q}: {queue_counts[q]}")
        lines.append("")
        lines.append("## Overridden tickets")
        if overridden:
            for e in overridden:
                lines.append(f"- {e['ticket_id']} -> {e['final_category']} "
                             f"/ {e['final_priority']} ({e['final_route_to']})")
        else:
            lines.append("- (none)")
        lines.append("")

        with open(self.SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Wrote queue summary -> {self.SUMMARY_FILE}")

    # ----- Final stage ---------------------------------------------------------
    def finalise(self) -> None:
        banner(Stage.RESULTS_FINALISED.value)
        print("All artifacts written:")
        for name in (self.NORMALIZED_FILE, self.PREDICTIONS_FILE,
                     self.OVERRIDES_FILE, self.FINAL_QUEUE_FILE,
                     self.SUMMARY_FILE, self.ESCALATIONS_FILE,
                     self.LLM_LOG_FILE):
            if os.path.exists(name):
                print(f"  - {name}")
        self.stage = Stage.RESULTS_FINALISED

    # ----- Driver --------------------------------------------------------------
    def run(self) -> None:
        banner(Stage.INIT.value)
        print("Starting Support Triage Pipeline")
        self.load_inputs()
        self.normalize_tickets()
        self.predict_triage()
        self.human_review()
        self.generate_final_queue()
        self.finalise()


def main() -> int:
    try:
        TriagePipeline().run()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nPipeline failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
