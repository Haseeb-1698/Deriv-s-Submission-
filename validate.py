#!/usr/bin/env python3
"""
validate.py
===========
End-to-end validator for the Support Triage Pipeline. Confirms that:

  * all required artifact files exist and are valid JSON
  * normalization was deterministic (text_for_model + char_count)
  * every prediction has an allowed category/priority and correct route
  * overrides are valid and applied in final_queue
  * suggested replies respect reply_style.max_words
"""

import json
import os
import sys

# Ensure the ✅ marker prints cleanly on Windows consoles (cp1252 default).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


REQUIRED_FILES = [
    "tickets.json",
    "triage_config.json",
    "normalized_tickets.json",
    "triage_predictions.json",
    "review_overrides.json",
    "final_queue.json",
    "queue_summary.md",
]


class Validator:
    def __init__(self):
        self.errors = []

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  FAIL: {msg}")

    def ok(self, msg: str) -> None:
        print(f"  ok:   {msg}")

    # -- 1. files exist and parse ------------------------------------------------
    def check_files(self):
        print("[1/5] Checking required files exist and parse...")
        for path in REQUIRED_FILES:
            if not os.path.exists(path):
                self.fail(f"missing file: {path}")
                continue
            if path.endswith(".json"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        json.load(f)
                    self.ok(f"valid JSON: {path}")
                except json.JSONDecodeError as e:
                    self.fail(f"invalid JSON in {path}: {e}")
            else:
                self.ok(f"present: {path}")

    # -- 2. deterministic normalization -----------------------------------------
    def check_normalization(self, tickets, normalized):
        print("[2/5] Checking deterministic normalization...")
        if len(tickets) != len(normalized):
            self.fail(f"ticket count mismatch: "
                      f"{len(tickets)} input vs {len(normalized)} normalized")
            return

        norm_by_id = {n["ticket_id"]: n for n in normalized}
        for t in tickets:
            n = norm_by_id.get(t["ticket_id"])
            if n is None:
                self.fail(f"normalized record missing for {t['ticket_id']}")
                continue
            expected_text = f"Subject: {t['subject']}\nMessage: {t['message']}"
            if n.get("text_for_model") != expected_text:
                self.fail(f"text_for_model mismatch for {t['ticket_id']}")
            if n.get("char_count") != len(expected_text):
                self.fail(f"char_count wrong for {t['ticket_id']}")
        self.ok("normalization deterministic for all tickets")

    # -- 3. predictions respect config ------------------------------------------
    def check_predictions(self, predictions, config):
        print("[3/5] Checking predictions against config...")
        cats = set(config["allowed_categories"])
        pris = set(config["allowed_priorities"])
        routing = config["routing_rules"]
        max_words = config["reply_style"]["max_words"]

        for p in predictions:
            tid = p.get("ticket_id", "?")
            if p.get("category") not in cats:
                self.fail(f"{tid}: category not allowed: {p.get('category')}")
            if p.get("priority") not in pris:
                self.fail(f"{tid}: priority not allowed: {p.get('priority')}")
            if p.get("route_to") != routing.get(p.get("category")):
                self.fail(f"{tid}: route_to does not match routing_rules for category")
            try:
                c = float(p.get("confidence", -1))
                if not 0.0 <= c <= 1.0:
                    self.fail(f"{tid}: confidence out of range: {c}")
            except (TypeError, ValueError):
                self.fail(f"{tid}: confidence not a float")

            reply = p.get("suggested_reply", "")
            wc = len(reply.split())
            if wc > max_words:
                self.fail(f"{tid}: suggested_reply has {wc} words > {max_words}")
        self.ok("all predictions valid")

    # -- 4. overrides ------------------------------------------------------------
    def check_overrides(self, overrides, predictions, config):
        print("[4/5] Checking overrides...")
        cats = set(config["allowed_categories"])
        pris = set(config["allowed_priorities"])
        pred_by_id = {p["ticket_id"]: p for p in predictions}

        for o in overrides:
            tid = o.get("ticket_id")
            if tid not in pred_by_id:
                self.fail(f"override references unknown ticket: {tid}")
                continue
            if o.get("new_category") not in cats:
                self.fail(f"{tid}: override new_category invalid")
            if o.get("new_priority") not in pris:
                self.fail(f"{tid}: override new_priority invalid")
            if o.get("old_category") != pred_by_id[tid]["category"]:
                self.fail(f"{tid}: override old_category does not match prediction")
            if o.get("old_priority") != pred_by_id[tid]["priority"]:
                self.fail(f"{tid}: override old_priority does not match prediction")
        self.ok(f"{len(overrides)} override(s) validated")

    # -- 5. final queue ----------------------------------------------------------
    def check_final_queue(self, final_queue, predictions, overrides, config):
        print("[5/5] Checking final queue...")
        if len(final_queue) != len(predictions):
            self.fail(f"final_queue length {len(final_queue)} "
                      f"!= predictions length {len(predictions)}")
            return

        routing = config["routing_rules"]
        ov_by_id = {o["ticket_id"]: o for o in overrides}
        pred_by_id = {p["ticket_id"]: p for p in predictions}

        for e in final_queue:
            tid = e["ticket_id"]
            if e["final_route_to"] != routing.get(e["final_category"]):
                self.fail(f"{tid}: final_route_to does not match category routing")

            if tid in ov_by_id:
                o = ov_by_id[tid]
                if not e.get("was_overridden"):
                    self.fail(f"{tid}: overridden ticket missing was_overridden=true")
                if e["final_category"] != o["new_category"]:
                    self.fail(f"{tid}: final_category != override new_category")
                if e["final_priority"] != o["new_priority"]:
                    self.fail(f"{tid}: final_priority != override new_priority")
            else:
                p = pred_by_id[tid]
                if e.get("was_overridden"):
                    self.fail(f"{tid}: non-overridden ticket marked was_overridden=true")
                if e["final_category"] != p["category"]:
                    self.fail(f"{tid}: final_category drifted from prediction")
                if e["final_priority"] != p["priority"]:
                    self.fail(f"{tid}: final_priority drifted from prediction")
        self.ok("final queue consistent with predictions and overrides")

    # -- driver ------------------------------------------------------------------
    def run(self) -> bool:
        print("=" * 60)
        print("VALIDATING SUPPORT TRIAGE PIPELINE")
        print("=" * 60)

        self.check_files()
        if self.errors:
            self._summary()
            return False

        with open("tickets.json", encoding="utf-8") as f:
            tickets = json.load(f)
        with open("triage_config.json", encoding="utf-8") as f:
            config = json.load(f)
        with open("normalized_tickets.json", encoding="utf-8") as f:
            normalized = json.load(f)
        with open("triage_predictions.json", encoding="utf-8") as f:
            predictions = json.load(f)
        with open("review_overrides.json", encoding="utf-8") as f:
            overrides = json.load(f)
        with open("final_queue.json", encoding="utf-8") as f:
            final_queue = json.load(f)

        self.check_normalization(tickets, normalized)
        self.check_predictions(predictions, config)
        self.check_overrides(overrides, predictions, config)
        self.check_final_queue(final_queue, predictions, overrides, config)

        return self._summary()

    def _summary(self) -> bool:
        print()
        print("=" * 60)
        if self.errors:
            print(f"VALIDATION FAILED with {len(self.errors)} error(s):")
            for e in self.errors:
                print(f"  - {e}")
            print("=" * 60)
            return False
        print("✅ VALIDATION PASSED")
        print("=" * 60)
        return True


def main() -> int:
    return 0 if Validator().run() else 1


if __name__ == "__main__":
    sys.exit(main())
