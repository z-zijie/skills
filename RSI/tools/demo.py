#!/usr/bin/env python3
"""Walk the whole loop on the shipped example operator, on whatever machine you have.

This is the script to run when you want to know whether the scaffold works before trusting it
with an operator you care about. It exercises every mechanical part end to end:

1. measure the device with the quick calibration suite
2. prove the evaluator catches every deliberately cheating kernel
3. evaluate a candidate and confirm the result is signed
4. refuse an edited result
5. build a signature and its roofline attainment
6. retrieve the knowledge that matches that signature
7. append the experiment to the ledger, and refuse a duplicate
8. drive a campaign round through the state machine
9. run the test suite

It prints one line per step with a pass or fail, and exits non-zero when anything fails.

    python3 tools/demo.py [--no-tests] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(ROOT))

from rsi import hardware, knowledge, ledger, signature as signature_mod, spec as spec_mod  # noqa: E402

STEPS: list = []


def step(name: str):
    """Record one step's outcome; keep going, so one failure still shows the rest."""

    def run(function):
        try:
            detail = function()
            STEPS.append({"step": name, "ok": True, "detail": detail})
            print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
        except Exception as error:
            STEPS.append({"step": name, "ok": False,
                          "detail": f"{type(error).__name__}: {error}"})
            print(f"  FAIL  {name}: {type(error).__name__}: {error}")
        return function

    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-tests", action="store_true", help="skip the full test suite")
    parser.add_argument("--json", action="store_true", help="print the outcome as JSON too")
    args = parser.parse_args(argv)

    from evaluator import backends, cli as evaluator_cli
    from evaluator.microbench import runner
    from orchestrator import sm

    op = "vec_add"
    op_spec = spec_mod.Spec.load(op, ROOT)
    backend = backends.select()
    device = hardware.discover_device()
    print(f"demo: operator {op}, backend {backend.name}\n")

    print("1. measure the device")

    @step("calibration suite runs and produces a noise floor")
    def _():
        report = runner.run_suite("quick", backend, device, samples=4)
        assert "noise_floor" in report["results"], report["skipped"]
        return f"noise floor {report['results']['noise_floor']['value']:.4f}x"

    print("\n2. the evaluator must catch cheating")

    @step("every red-team kernel is caught")
    def _():
        from evaluator.oracle import correctness

        cheats = evaluator_cli._red_team_kernels(op_spec)
        assert cheats, "no red-team kernels found"
        report = correctness.red_team(op_spec, cheats, backend, seed=7)
        assert report["all_caught"], report["holes"]
        return f"{len(cheats)} cheats, all caught"

    print("\n3. evaluate a candidate")

    @step("a correct candidate passes correctness")
    def _():
        from evaluator.oracle import correctness

        report = correctness.run_suite(
            op_spec, ROOT / "operators" / op / "workspace" / "kernel.py", backend,
            seed=11, max_cases=2)
        assert report["passed"], report["failures"]
        return f"{report['cases_run']} cases"

    print("\n4. an edited result must be refused")

    @step("a modified result fails its signature")
    def _():
        result = {
            "schema_version": "1", "exp_kind": "evaluate", "op": op, "candidate": "x.py",
            "hw_id": str(device.get("hw_id")), "env_fingerprint": "fp",
            "evaluator_version": "1", "evaluator_hash": "abc",
            "created": "2026-01-01T00:00:00Z",
            "correctness": {"passed": True, "cases_run": 1, "cases_failed": 0},
            "timing": {"candidate_us": 1.0, "baseline_us": 1.2, "samples": 30, "setup_us": 0.0},
        }
        signed = ledger.sign(result)
        ledger.validate_result(signed)
        tampered = dict(signed)
        tampered["timing"] = dict(signed["timing"], candidate_us=0.01)
        try:
            ledger.validate_result(tampered)
        except ledger.SignatureError:
            return "refused, as it must be"
        raise AssertionError("an edited result kept a valid signature")

    print("\n5. signature and roofline")

    @step("a signature is built from canonical metrics")
    def _():
        values = {"device_mem_throughput_pct": 71.0, "compute_throughput_pct": 18.0,
                  "sectors_per_request": 2.4, "occupancy_pct": 55.0}
        sig = signature_mod.build(op, str(device.get("hw_id")), values,
                                  {"bound_us": 10.0, "measured_us": 20.0, "attained_pct": 50.0})
        assert sig["class"] == "memory_bound", sig["class"]
        return f"class {sig['class']}, tags {sig['tags']}"

    print("\n6. retrieval")

    @step("retrieval returns capped, matched knowledge")
    def _():
        sig = {"op": op, "hw_id": str(device.get("hw_id")), "class": "memory_bound",
               "tags": ["mem_saturated", "uncoalesced"], "metrics": {}, "roofline": {}}
        found = knowledge.retrieve(sig, ROOT)
        return (f"{len(found['rules'])} rules, {len(found['patterns'])} patterns, "
                f"{len(found['antipatterns'])} anti-patterns, "
                f"match {found['match_level']}")

    print("\n7. the ledger is append-only")

    @step("an experiment appends once and a duplicate is refused")
    def _():
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ledger").mkdir()
            entry = {
                "exp_id": "exp-0001", "op": op, "hw_id": str(device.get("hw_id")),
                "env_fingerprint": "fp", "scaffold_commit": ledger.scaffold_commit(),
                "parent": "best", "hypothesis": {"id": "H-0001"},
                "correctness": {"passed": True}, "verdict": "confirmed",
            }
            ledger.append(entry, root=root)
            try:
                ledger.append(entry, root=root)
            except ledger.LedgerError:
                return f"{len(ledger.read_entries(root=root))} entry, duplicate refused"
            raise AssertionError("a duplicate was accepted")

    print("\n8. the campaign state machine")

    @step("a round runs through every state and records one experiment")
    def _():
        campaign = sm.new_campaign(op, budget=5)
        outcomes = [
            {"signature": {"tags": ["mem_saturated"], "roofline": {"attained_pct": 40.0}}},
            {},
            {"cards": [{"id": "H-0001"}]},
            {"diff": "diff --git a/kernel.py b/kernel.py"},
            {"correctness": {"passed": True}},
            {},
            {"verdict": "confirmed"},
            {"speedup": 1.2, "significant": True, "qualifies": True, "prediction_error": 0.05},
            {"attained_pct": 62.0},
            {},
            {"decision": "continue"},
        ]
        for outcome in outcomes:
            sm.advance(campaign, outcome)
        assert campaign.experiments == 1, campaign.experiments
        assert campaign.best_attainment_pct == 62.0, campaign.best_attainment_pct
        return f"attainment {campaign.best_attainment_pct:.0f}%, round {campaign.round}"

    if not args.no_tests:
        print("\n9. the test suite")

        @step("tests/run_all.py passes")
        def _():
            completed = subprocess.run([sys.executable, str(ROOT / "tests" / "run_all.py")],
                                       capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
            first = completed.stdout.strip().splitlines()[:1]
            assert completed.returncode == 0, " | ".join(first) + completed.stderr[-400:]
            return first[0] if first else "passed"

    failures = [entry for entry in STEPS if not entry["ok"]]
    print()
    if args.json:
        print(json.dumps({"steps": STEPS, "ok": not failures}, indent=2, ensure_ascii=False))
    print(f"{len(STEPS) - len(failures)}/{len(STEPS)} steps passed")
    if failures:
        print("failed: " + ", ".join(entry["step"] for entry in failures))
        return 1
    print("the scaffold is wired end to end on this machine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
