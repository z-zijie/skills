"""Run every test without a device.

    python3 RSI/tests/run_all.py

The suite exists so the scaffold can be checked on any machine, including one with no
accelerator and no profiler. What it proves:

* the contracts in ``schemas/`` accept real documents and reject malformed ones
* the ledger refuses unsigned results and never accepts a duplicate experiment id
* the write policy refuses what it is supposed to refuse
* the timing statistics behave: a real gap wins, noise does not, and a single sample does not
* the signature thresholds fire on canonical metrics and ignore unknown ones
* the microbenchmark suite runs end to end on the reference backend
* the campaign state machine exhausts both retry loops and stops on every stop condition
* the red-team kernels are all caught by the evaluator
* the YAML subset round-trips the shapes the framework's own files use
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(ROOT))

PASSED: list = []
FAILED: list = []


def case(name):
    """Register and run one test case."""

    def decorator(function):
        try:
            function()
            PASSED.append(name)
        except Exception as error:  # a failing test must not stop the rest
            FAILED.append({"case": name, "error": f"{type(error).__name__}: {error}",
                           "trace": traceback.format_exc()[-1200:]})
        return function

    return decorator


#: A fixed key for the signature tests. They must not depend on the machine having run `keygen`,
#: because a test that only passes after a deployment step is a test that fails in CI.
TEST_KEY = b"rsi-test-key-not-a-secret-0123456789"


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def schema(name):
    from rsi import jsonschema_lite

    return jsonschema_lite.load_schema(str(ROOT / "schemas" / name))


def _schema_field_names() -> set:
    """Every property name the frozen schemas define: the legal non-metric nouns in prose."""
    import json

    names = set()
    for path in (ROOT / "schemas").glob("*.json"):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stack = [schema]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        names.update(value)
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    for path in (ROOT / "skills").glob("*/contract/*.json"):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stack = [schema]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        names.update(value)
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    return names


# -- contracts ------------------------------------------------------------

@case("schema: signature accepts a real signature and rejects a bad class")
def _():
    from rsi import jsonschema_lite, signature

    sig = signature.build("op", "hw", {"device_mem_throughput_pct": 90, "compute_throughput_pct": 10},
                          {"bound_us": 100.0, "measured_us": 200.0, "attained_pct": 50.0})
    expect(jsonschema_lite.validate(sig, schema("signature.schema.json")) == [], "valid signature rejected")
    sig["class"] = "gpu_bound"
    expect(jsonschema_lite.validate(sig, schema("signature.schema.json")), "invalid class accepted")


@case("schema: hypothesis rejects a card with no implementation evidence")
def _():
    from rsi import jsonschema_lite

    card = {
        "id": "H-0001", "op": "op", "parent": "best", "signature_tags": ["mem_saturated"],
        "transformation": "fuse", "mechanism": "x" * 30,
        "predictions": {"counters": [{"metric": "device_mem_throughput_pct", "direction": "down"}],
                        "speedup": {"expect": 1.2, "low": 1.1, "high": 1.3}},
        "evidence_of_implementation": ["the diff reads the second input once"],
        "scope_of_change": "the inner loop only",
    }
    expect(jsonschema_lite.validate(card, schema("hypothesis.schema.json")) == [], "valid card rejected")
    del card["evidence_of_implementation"]
    errors = jsonschema_lite.validate(card, schema("hypothesis.schema.json"))
    expect(any("evidence_of_implementation" in error for error in errors),
           "a card with no implementation evidence was accepted")


@case("schema: a malformed contract fails loudly at load time")
def _():
    from rsi import jsonschema_lite

    bad = {"type": "object", "propertys": {}}
    try:
        jsonschema_lite._check_keywords(bad)
    except jsonschema_lite.SchemaError:
        return
    raise AssertionError("an unsupported keyword was silently accepted")


# -- ledger ---------------------------------------------------------------

@case("ledger: refuses an unsigned result and accepts a signed one")
def _():
    from rsi import ledger

    result = {
        "schema_version": "1", "exp_kind": "evaluate", "op": "vec_add", "candidate": "x.py",
        "hw_id": "hw", "env_fingerprint": "fp", "evaluator_version": "1", "evaluator_hash": "abc",
        "created": "2026-01-01T00:00:00Z",
        "correctness": {"passed": True, "cases_run": 1, "cases_failed": 0},
        "timing": {"candidate_us": 1.0, "baseline_us": 1.2, "samples": 30, "setup_us": 0.0},
    }
    try:
        ledger.validate_result(result)
    except ledger.SignatureError:
        pass
    else:
        raise AssertionError("an unsigned result was accepted")
    signed = ledger.sign(result, key=TEST_KEY)
    ledger.validate_result(signed, key=TEST_KEY)
    tampered = dict(signed)
    tampered["timing"] = dict(signed["timing"], candidate_us=0.1)
    try:
        ledger.validate_result(tampered, key=TEST_KEY)
    except ledger.SignatureError:
        return
    raise AssertionError("a modified result kept a valid signature")


@case("ledger: is append-only and refuses a duplicate experiment id")
def _():
    from rsi import ledger, paths

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "ledger").mkdir()
        entry = {
            "exp_id": "exp-0001", "op": "vec_add", "hw_id": "hw", "env_fingerprint": "fp",
            "scaffold_commit": "abc", "parent": "best",
            "hypothesis": {}, "correctness": {"passed": True}, "verdict": "confirmed",
            "evaluator_signature": "stub",
        }
        ledger.append(entry, root=root)
        try:
            ledger.append(entry, root=root)
        except ledger.LedgerError:
            pass
        else:
            raise AssertionError("a duplicate experiment id was accepted")
        expect(len(ledger.read_entries(root=root)) == 1, "the ledger changed size")


@case("ledger: rejects unregistered metric names in counters")
def _():
    from rsi import ledger

    entry = {
        "exp_id": "exp-0002", "op": "vec_add", "hw_id": "hw", "env_fingerprint": "fp",
        "scaffold_commit": "abc", "parent": "best", "hypothesis": {},
        "correctness": {"passed": True}, "verdict": "confirmed",
        "evaluator_signature": "stub",
        "counters_before": {"totally_unregistered_counter": 1.0},
    }
    try:
        ledger.validate_entry(entry)
    except ledger.LedgerError:
        return
    raise AssertionError("an unregistered metric name was accepted into the ledger")


# -- permissions ----------------------------------------------------------

@case("paths: the write policy refuses what it must refuse")
def _():
    from rsi import paths

    root = ROOT
    allowed = [
        ("inner_loop", "operators/vec_add/workspace/kernel.py"),
        ("outer_loop", "kb/proposals/P-0001.yaml"),
        ("evaluator", "kb/hardware/measured/accel-a.yaml"),
        ("human", "evaluator/cli.py"),
    ]
    refused = [
        ("inner_loop", "evaluator/cli.py"),
        ("inner_loop", "kb/rules/R-001.yaml"),
        ("inner_loop", "playbooks/compute-bound.md"),
        ("outer_loop", "operators/vec_add/spec.yaml"),
        ("ui", "operators/vec_add/workspace/kernel.py"),
        ("outer_loop", "benchmark/holdout.txt"),
    ]
    for role, path in allowed:
        expect(paths.writable(role, root / path, root), f"{role} should write {path}")
    for role, path in refused:
        expect(not paths.writable(role, root / path, root), f"{role} must not write {path}")
    expect(paths.hidden_from_agents(root / "benchmark" / "holdout.txt", root), "holdout is visible")


# -- timing ---------------------------------------------------------------

@case("timing: a real gap wins, noise does not, one sample never wins")
def _():
    from rsi import timing

    candidate = [90.0, 91.0, 89.5, 90.5] * 10
    baseline = [100.0, 101.0, 99.0, 100.5] * 10
    ratio = timing.speedup_ci(candidate, baseline)
    expect(ratio["low"] > 1.05, f"a 10% gap did not clear the lower bound: {ratio}")
    expect(timing.wins(ratio, 1.02), "a real gap did not win")

    same = [100.0, 101.0, 99.0, 100.5] * 10
    noisy = timing.speedup_ci(same, baseline)
    expect(not timing.wins(noisy, 1.02), "an A/A comparison won")

    single = timing.speedup_ci(candidate[:1], baseline[:1])
    expect(not timing.wins(single, 1.01), "a single sample won")


@case("timing: the A/A noise floor is measured, not assumed")
def _():
    from rsi import timing

    arm_a = [100.0 + (index % 7) * 0.5 for index in range(40)]
    arm_b = [100.0 + (index % 5) * 0.4 for index in range(40)]
    noise = timing.aa_noise(arm_a, arm_b)
    expect(noise["threshold"] >= 1.0, f"noise threshold below 1: {noise}")
    expect(noise["relative_spread"] < 0.1, f"noise spread is implausibly wide: {noise}")


@case("timing: prediction error is a log ratio and 0.2 is about 20 percent")
def _():
    from rsi import timing

    expect(abs(timing.log_ratio_error(1.2, 1.0) - 0.182) < 0.01, "log ratio is wrong")
    expect(timing.log_ratio_error(None, 1.0) is None, "a missing measurement produced an error")


# -- signature, metrics, knowledge ---------------------------------------

@case("signature: canonical tags fire and unknown metrics are ignored")
def _():
    from rsi import signature

    values = {"device_mem_throughput_pct": 92, "compute_throughput_pct": 12,
              "sectors_per_request": 3.1, "occupancy_pct": 40, "gpu_mystery_counter": 7}
    tags = signature.tags(values)
    for expected in ("mem_saturated", "uncoalesced", "occupancy_low"):
        expect(expected in tags, f"{expected} did not fire: {tags}")
    expect(signature.classify(values) == "memory_bound", "memory-bound kernel misclassified")


@case("metrics: legacy spellings map to canonical names and unknown names are refused")
def _():
    from rsi import metrics

    expect(metrics.canonical("dram_throughput_pct") == "device_mem_throughput_pct", "alias not mapped")
    expect(metrics.is_known("sm_throughput_pct"), "a known alias was refused")
    try:
        metrics.require_known("totally_made_up")
    except metrics.UnknownMetric:
        return
    raise AssertionError("an unknown metric was accepted")


@case("knowledge: retrieval applies the caps and reports the match level")
def _():
    from rsi import knowledge

    sig = {"op": "vec_add", "hw_id": "accel-a", "class": "memory_bound",
           "tags": ["mem_saturated", "uncoalesced"], "metrics": {}, "roofline": {}}
    result = knowledge.retrieve(sig)
    expect(len(result["rules"]) <= knowledge.RETRIEVAL_CAPS["rules"], "rule cap exceeded")
    expect(len(result["antipatterns"]) <= knowledge.RETRIEVAL_CAPS["antipatterns"], "antipattern cap exceeded")
    expect("match_level" in result, "the match level was not reported")


@case("knowledge: the rule life cycle is mechanical")
def _():
    from rsi import knowledge

    rule = {"id": "R-001", "evidence": {"confirmed": ["exp-1", "exp-2", "exp-3"], "refuted": []},
            "mean_prediction_error": 0.05}
    verdict = knowledge.lifecycle(rule, {"exp-1": "a", "exp-2": "b", "exp-3": "a"})
    expect(verdict["status"] == "established", f"should be established: {verdict}")
    verdict = knowledge.lifecycle(rule, {"exp-1": "a", "exp-2": "a", "exp-3": "a"})
    expect(verdict["status"] == "candidate", f"one operator cannot establish a rule: {verdict}")
    verdict = knowledge.lifecycle({"id": "R-2", "evidence": {"confirmed": [], "refuted": []}}, {})
    expect(verdict["status"] == "conjecture", "no evidence should stay a conjecture")
    verdict = knowledge.lifecycle({"id": "R-3", "evidence": {"confirmed": ["e1"], "refuted": ["e2", "e3", "e4"]}},
                                  {"e1": "a", "e2": "a", "e3": "a", "e4": "a"})
    expect(verdict["status"] == "deprecated", "a failing confirmation rate should deprecate")


# -- spec and cost model --------------------------------------------------

@case("spec: the cost model is arithmetic only and the bound is honest")
def _():
    from rsi import spec as spec_mod

    op_spec = spec_mod.Spec.load("vec_add", ROOT)
    model = op_spec.model({"n": 1000})
    expect(model["bytes"] == 3 * 1000 * 4, f"byte formula is wrong: {model}")
    expect(model["flops"] == 1000, "flop formula is wrong")
    try:
        spec_mod._compile_expr("__import__('os').system('true')", "bytes_expr")
    except spec_mod.SpecError:
        pass
    else:
        raise AssertionError("a cost formula can execute arbitrary code")


@case("spec: cases include typical, edge and fresh random shapes")
def _():
    from rsi import spec as spec_mod

    op_spec = spec_mod.Spec.load("vec_add", ROOT)
    cases = op_spec.cases(seed=1)
    expect(any(case["n"] == 1 for case in cases), "the edge shape n=1 is missing")
    expect(len({case["n"] for case in cases}) >= 5, "the random shapes look fixed")
    expect(op_spec.cases(seed=2) != op_spec.cases(seed=3), "random shapes do not vary by seed")


# -- evaluator end to end ------------------------------------------------

@case("evaluator: a correct candidate passes and is timed")
def _():
    from evaluator.oracle import correctness
    from evaluator import backends
    from rsi import spec as spec_mod

    op_spec = spec_mod.Spec.load("vec_add", ROOT)
    backend = backends.create("cpu")
    report = correctness.run_suite(op_spec, ROOT / "operators/vec_add/baseline/kernel.py", backend,
                                   seed=3, max_cases=3)
    expect(report["passed"], f"a correct candidate failed: {report['failures']}")
    expect(report["cases_run"] == 3, "not every case ran")


@case("evaluator: every deliberately cheating kernel is caught")
def _():
    from evaluator.oracle import correctness
    from evaluator import backends, cli
    from rsi import spec as spec_mod

    op_spec = spec_mod.Spec.load("vec_add", ROOT)
    cheats = cli._red_team_kernels(op_spec)
    expect(len(cheats) >= 5, f"expected several red-team kernels, found {len(cheats)}")
    report = correctness.red_team(op_spec, cheats, backends.create("cpu"), seed=5)
    expect(report["all_caught"], f"the evaluator was fooled by: {report['holes']}")


@case("evaluator: the noise floor and the win rule come from measurement")
def _():
    from evaluator.harness import timing as protocol
    from evaluator import backends

    backend = backends.create("cpu")
    report = protocol.aa_test(lambda: sum(range(400)), backend, samples=8, warmup=1)
    expect(report["threshold"] >= 1.0, f"noise threshold below 1: {report}")
    expect(report["samples"] == 8, "sample count is wrong")


@case("evaluator: the microbenchmark suite runs and never invents a value")
def _():
    from evaluator.microbench import runner
    from evaluator import backends

    report = runner.run_suite("quick", backends.create("cpu"),
                              {"hw_id": "test", "model": "host", "hw_class": "other"}, samples=3)
    expect("noise_floor" in report["results"], f"the noise floor did not run: {report['skipped']}")
    for name, result in report["results"].items():
        expect(result["value"] is not None, f"{name} reported a null value as measured")


@case("evaluate: the CLI runs correctness, timing, guards and signing end to end")
def _():
    """Exercise the real `evaluate` command, which is where the trust layer actually lives.

    Every earlier test checked one piece: correctness alone, statistics alone, the signature alone.
    The command that composes them was the one path nothing ran, so a break in the composition
    would have shipped.
    """
    import json
    import subprocess
    import tempfile

    from rsi import ledger

    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "result.json"
        # A key of the test's own: the suite must not depend on the machine having run `keygen`.
        key_file = Path(directory) / "evaluator.key"
        key_file.write_bytes(TEST_KEY)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "evaluator" / "cli.py"), "evaluate",
             "--op", "vec_add", "--candidate", "operators/vec_add/baseline/kernel.py",
             "--samples", "5", "--warmup", "1", "--out", str(out),
             "--key-file", str(key_file)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=1800, check=False)
        expect(completed.returncode == 0,
               f"evaluate failed: {completed.stderr[-600:]}")
        result = json.loads(out.read_text())

    # The shape and the signature.
    ledger.validate_result(result, key=TEST_KEY)
    expect(result["correctness"]["passed"], "the baseline failed correctness")
    expect(result["timing"]["samples"] == 5, "the sample count is wrong")

    # The bindings: a signature over numbers alone can be re-attributed to other code.
    expect(len(result.get("candidate_hash") or "") == 64,
           "the result does not pin the candidate source")
    expect(len(result.get("tolerance_hash") or "") > 0,
           "the result does not pin the tolerances it was judged under")
    expect(isinstance(result.get("seed"), int),
           "the seed was not recorded, so the run cannot be reproduced")

    # The timing ran away from the signing process, and setup was measured where it happens.
    expect(result["timing"].get("measured_in") == "separate-process",
           "timing did not run in a separate process")
    expect("setup_us" in result["timing"], "setup was not measured")


@case("evaluate: a kernel that caches its answer is refused, not timed fast")
def _():
    import subprocess

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        key_file = Path(directory) / "evaluator.key"
        key_file.write_bytes(TEST_KEY)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "evaluator" / "cli.py"), "evaluate",
             "--op", "vec_add", "--candidate", "operators/vec_add/redteam/timing_only_gain.py",
             "--samples", "5", "--warmup", "1", "--key-file", str(key_file)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=1800, check=False)
    expect(completed.returncode != 0,
           "a candidate that replays a cached answer was accepted for timing")
    expect("timing child failed" in completed.stderr or "cached" in completed.stderr,
           f"the refusal did not explain itself: {completed.stderr[-300:]}")


@case("pipeline: a signed result reaches the ledger only when it verifies")
def _():
    from rsi import ledger, spec as spec_mod
    from evaluator.oracle import correctness
    from evaluator.harness import timing as protocol
    from evaluator import backends

    op_spec = spec_mod.Spec.load("vec_add", ROOT)
    backend = backends.create("cpu")
    correctness_report = correctness.run_suite(
        op_spec, ROOT / "operators/vec_add/baseline/kernel.py", backend, seed=11, max_cases=2)
    expect(correctness_report["passed"], "the baseline failed correctness")

    arms = protocol.build_arms(op_spec, ROOT / "operators/vec_add/baseline/kernel.py",
                               op_spec.entry["baseline"], backend, case={"n": 256})
    samples = protocol.interleaved(arms["candidate"], arms["parent"], backend, samples=6, warmup=1)
    timing_block = protocol.timing_report(samples["candidate"], samples["parent"], noise_threshold=1.0)
    result = {
        "schema_version": "1", "exp_kind": "evaluate", "op": "vec_add",
        "candidate": "operators/vec_add/baseline/kernel.py", "hw_id": "test",
        "env_fingerprint": "fp", "evaluator_version": "1", "evaluator_hash": "abc",
        "created": "2026-01-01T00:00:00Z",
        "correctness": {"passed": True, "cases_run": 2, "cases_failed": 0},
        "timing": {key: timing_block[key] for key in
                   ("candidate_us", "baseline_us", "speedup", "samples", "setup_us")},
    }
    signed = ledger.sign(result, key=TEST_KEY)
    ledger.validate_result(signed, key=TEST_KEY)
    expect(signed["timing"]["samples"] == 6, "the sample count did not survive")


@case("roofline: refuses peaks measured on the host when the device claims an accelerator")
def _():
    from rsi import hardware

    untrustworthy = {
        "peak_bandwidth_gbps": 100.0, "peak_flops": {"fp32": 1000.0},
        "trustworthy_for_roofline": False, "measurement_note": "host measurement",
    }
    try:
        hardware.Roofline.from_measured(untrustworthy)
    except hardware.HardwareError as error:
        expect("wrong machine" in str(error) or "host" in str(error), str(error))
    else:
        raise AssertionError("a roofline was built from a host measurement")

    trustworthy = {"peak_bandwidth_gbps": 1600.0, "peak_flops": {"fp32": 100e12}}
    roofline = hardware.Roofline.from_measured(trustworthy)
    bound = roofline.bound_us(bytes_moved=1.6e9, flops=1e6, dtype="fp32")
    expect(abs(bound["bound_us"] - 1000.0) < 1.0, f"bound is wrong: {bound}")


@case("ops: a copy actually copies, on both array spellings")
def _():
    from evaluator import backends
    from evaluator.backends import ops as ops_mod

    backend = backends.create("cpu")
    plain = ops_mod.ReferenceOps(backend)
    source = backend.from_list([1.0, 2.0, 3.0])
    destination = backend.zeros(3)
    plain.copy(source, destination)
    expect(backend.tolist(destination) == [1.0, 2.0, 3.0], "the reference copy did not copy")

    class FakeArray(list):
        """A minimal array: slice assignment, no ``copy_``, like a NumPy-like runtime."""

        def __init__(self, values):
            super().__init__(values)

    fake = ops_mod.ArrayOps(backend, __import__("types").SimpleNamespace(__name__="fake"))
    target = FakeArray([0.0, 0.0, 0.0])
    fake.copy([4.0, 5.0, 6.0], target)
    expect(list(target) == [4.0, 5.0, 6.0], "the array copy silently did nothing")


# -- orchestration --------------------------------------------------------

@case("campaign: both retry loops exhaust and then block")
def _():
    from orchestrator import sm

    campaign = sm.new_campaign("vec_add", budget=100)
    sm.advance(campaign, {"signature": {"tags": [], "roofline": {"attained_pct": 10}}})
    sm.advance(campaign, {})
    sm.advance(campaign, {"cards": [{"id": "H-1"}]})
    for _ in range(sm.IMPLEMENT_RETRIES + 2):
        sm.advance(campaign, {"correctness": {"passed": False}})
        sm.advance(campaign, {"diff": "x"})
    step = sm.next_step(campaign)
    expect(step["state"] == "blocked", f"the correctness loop did not exhaust: {step}")

    second = sm.new_campaign("vec_add", budget=100)
    sm.advance(second, {"signature": {"tags": [], "roofline": {"attained_pct": 10}}})
    sm.advance(second, {})
    sm.advance(second, {"cards": [{"id": "H-1"}]})
    sm.advance(second, {"diff": "x"})
    for _ in range(sm.IMPLEMENT_RETRIES + 2):
        sm.advance(second, {"correctness": {"passed": True}})
        sm.advance(second, {})
        sm.advance(second, {"verdict": "not_implemented"})
        sm.advance(second, {"diff": "y"})
    step = sm.next_step(second)
    expect(step["state"] == "blocked", f"the not-implemented loop did not exhaust: {step}")


@case("campaign: every stop condition stops")
def _():
    from orchestrator import sm

    target = sm.new_campaign("op", budget=100)
    target.current_attainment_pct = 90.0
    sm.apply_plan(target, "continue")
    expect(target.state == "report", "reaching the target did not stop the campaign")

    budget = sm.new_campaign("op", budget=1)
    budget.experiments = 1
    sm.apply_plan(budget, "continue")
    expect(budget.state == "report", "spending the budget did not stop the campaign")

    stalled = sm.new_campaign("op", budget=100)
    stalled.stalled_rounds = sm.NO_GAIN_ROUNDS
    sm.apply_plan(stalled, "continue")
    expect(stalled.state == "report", "five stalled rounds did not stop the campaign")

    reprofile = sm.new_campaign("op", budget=100)
    reprofile.stalled_rounds = sm.REPROFILE_AFTER_STALLED_ROUNDS
    sm.apply_plan(reprofile, "continue")
    expect(reprofile.state == "diagnose", "three stalled rounds did not force a re-diagnosis")


@case("campaign: a stop with an unresolved closure is spent on the revisit, not on the report")
def _():
    from orchestrator import sm

    campaign = sm.new_campaign("op", budget=100)
    campaign.closures = [{"direction": "rope-tile", "evidence": "implementation",
                          "retirable": False, "points_tried": 1}]
    step = sm.apply_plan(campaign, "stop", {"reason": "no open route"})
    expect(campaign.state == "revisit", f"an unresolved closure did not force a revisit: {step}")
    expect(step["skill"] == "revisit-closures", "the revisit ran no skill")

    # The plan answers the question once: with every reopen used, the campaign may then stop and
    # the report carries the unresolved direction instead of pretending the playbook is exhausted.
    sm.advance(campaign, {"decision": "hold",
                          "closures": [{"direction": "rope-tile", "evidence": "implementation",
                                        "retirable": False, "points_tried": 1}],
                          "note": "the closure used its reopens: report it as unresolved"})
    expect(campaign.state == "plan", "the revisit did not hand back to the planner")
    sm.apply_plan(campaign, "stop", {"reason": "no open route"})
    expect(campaign.state == "report", "the second stop did not reach the report")
    expect("rope-tile" in sm.stop_conditions(campaign)["unresolved_closures"],
           "the report cannot name the direction it left unresolved")

    target = sm.new_campaign("op", budget=100)
    target.current_attainment_pct = 90.0
    target.closures = [{"direction": "x", "retirable": False}]
    sm.apply_plan(target, "stop_target")
    expect(target.state == "report", "reaching the target must not wait on a closure check")


@case("campaign: a reopen goes to hypothesize and the campaign continues")
def _():
    from orchestrator import sm

    campaign = sm.new_campaign("op", budget=100)
    sm.record_revisit(campaign, {"decision": "reopen",
                                 "reopens": [{"direction": "rope-tile",
                                              "retry": "fix_implementation"}],
                                 "closures": [{"direction": "rope-tile",
                                               "evidence": "implementation",
                                               "retirable": False}]})
    expect(campaign.state == "hypothesize", "a reopen did not become the next card")
    expect(campaign.reopen_direction == "rope-tile", "the reopen did not name the direction")
    expect(campaign.reopen_retry == "fix_implementation", "the reopen did not name the retry")
    expect(campaign.revisit_due, "the campaign does not record that a revisit was due")


@case("campaign: the state machine saves and reloads without losing a decision")
def _():
    from orchestrator import sm

    campaign = sm.new_campaign("vec_add", budget=7)
    sm.advance(campaign, {"signature": {"tags": ["mem_saturated"], "roofline": {"attained_pct": 42}}})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "campaign.json"
        campaign.save(path)
        reloaded = sm.Campaign.load(path)
    expect(reloaded.state == campaign.state, "state was lost")
    expect(reloaded.signature_tags == ["mem_saturated"], "tags were lost")
    expect(reloaded.history == campaign.history, "history was lost")


# -- replay ---------------------------------------------------------------

@case("replay: merges a real gain, rolls back noise and a holdout regression")
def _():
    from orchestrator import replay

    budget = {"experiments_per_operator": 10, "repeats": 3}
    good = [
        {"operator": "a", "split": "dev", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 1.6}},
        {"operator": "b", "split": "dev", "old": {"attainment_gain": 0.8}, "new": {"attainment_gain": 1.4}},
        {"operator": "h", "split": "holdout", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 1.1}},
    ]
    expect(replay.evaluate_change(good, budget)["decision"] == "merge", "a real gain did not merge")

    noise = [
        {"operator": "a", "split": "dev", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 1.01}},
        {"operator": "b", "split": "dev", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 0.98}},
        {"operator": "h", "split": "holdout", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 1.0}},
    ]
    expect(replay.evaluate_change(noise, budget)["decision"] == "rollback", "noise merged")

    # An empty holdout must not read as "did not regress": absence of a generalisation test is not
    # evidence of generalisation.
    try:
        replay.evaluate_change([good[0], good[1]], budget)
    except replay.ReplayError as error:
        expect("holdout" in str(error), str(error))
    else:
        raise AssertionError("a replay with no holdout results was accepted")

    # One dev operator is an anecdote, whatever its interval says.
    try:
        replay.evaluate_change([good[0], good[2]], budget)
    except replay.ReplayError as error:
        expect("dev operators" in str(error), str(error))
    else:
        raise AssertionError("a single-operator replay was accepted")

    regressed = [
        {"operator": "a", "split": "dev", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 1.6}},
        {"operator": "b", "split": "dev", "old": {"attainment_gain": 0.8}, "new": {"attainment_gain": 1.4}},
        {"operator": "h", "split": "holdout", "old": {"attainment_gain": 1.0}, "new": {"attainment_gain": 0.5}},
    ]
    expect(replay.evaluate_change(regressed, budget)["decision"] == "rollback",
           "a holdout regression merged")


@case("replay: refuses fewer than three repeats")
def _():
    from orchestrator import replay

    try:
        replay.evaluate_change([{"operator": "a", "split": "dev", "old": {}, "new": {}}], {}, repeats=1)
    except replay.ReplayError:
        return
    raise AssertionError("a single-repeat replay was accepted")


# -- yaml subset ----------------------------------------------------------

@case("yaml: parses the shapes this repository's own files use")
def _():
    from rsi import yamlite

    text = """
# comment
name: verify-mechanism
tier: strong
isolated: true
retries: 2
list:
  - a
  - b: 1
    c: [1, 2]
flow: {x: 1, y: "two"}
block: |
  one
  two
fold: >
  alpha
  beta
empty:
quoted: 'it''s fine'
"""
    data = yamlite.loads(text)
    expect(data["name"] == "verify-mechanism", "scalar parse failed")
    expect(data["list"][1] == {"b": 1, "c": [1, 2]}, f"nested sequence parse failed: {data['list']}")
    expect(data["flow"] == {"x": 1, "y": "two"}, "flow mapping parse failed")
    expect(data["block"] == "one\ntwo\n", "block scalar parse failed")
    expect(data["fold"] == "alpha beta\n", "folded scalar parse failed")
    expect(data["empty"] is None, "empty value should be null")
    expect(data["quoted"] == "it's fine", "single-quote escaping failed")


@case("yaml: the emitter's output parses back to the same document")
def _():
    from rsi import yamlite

    # The framework generates its own files — the documented index, the measured layer — so an
    # emitter that produces something the parser rejects is a defect that only shows up on a
    # machine a week later. Every shape the generated files use is covered here.
    sample = {
        "topic_index": [
            {"topic": "coalescing", "hw_ids": ["any"], "source": ["a", "b"], "accessed": ""},
            {"topic": "roofline", "hw_ids": ["any"], "source": [], "accessed": "2026-11-01"},
        ],
        "history": [
            {"at": "2026-11-01T00:00:00Z", "suite": "quick", "moved": [], "skipped": []},
            {"at": "2026-11-02T00:00:00Z", "moved": [{"parameter": "peak", "before": 1.0}]},
        ],
        "unsourced": [],
        "peak_bandwidth_gbps": 0.1449,
        "noise_floor": {"threshold": 1.0276, "note": "a colon: and a hash # inside a string"},
        "empty": {},
        "null_value": None,
    }
    rendered = yamlite.safe_dump_json_style(sample)
    expect(yamlite.loads(rendered) == sample, f"emitted YAML did not round-trip:\n{rendered}")


@case("yaml: every generated and authored document in the repository parses")
def _():
    from rsi import yamlite

    failures = []
    for path in sorted(ROOT.rglob("*.yaml")):
        if "__pycache__" in path.parts:
            continue
        try:
            yamlite.load(path)
        except yamlite.YamlError as error:
            failures.append(f"{path.relative_to(ROOT)}: {error}")
    expect(not failures, "; ".join(failures[:10]))


@case("yaml: refuses a tab-indented document instead of guessing")
def _():
    from rsi import yamlite

    try:
        yamlite.loads("a:\n\tb: 1\n")
    except yamlite.YamlError:
        return
    raise AssertionError("tab indentation was accepted")


# -- skills ---------------------------------------------------------------

@case("skills: every shipped skill has a sound layout and contracts")
def _():
    from rsi import skills

    problems = []
    for path in skills.discover(ROOT):
        problems.extend(skills.validate_skill(path.name, ROOT))
    expect(not problems, "; ".join(problems))
    found = len(skills.discover(ROOT))
    expect(found == 23, f"expected 23 skills, found {found}")


@case("skills: every skill's own regression cases pass")
def _():
    from rsi import skills

    problems = []
    for path in skills.discover(ROOT):
        report = skills.replay_tests(path.name, ROOT)
        if report["failed"]:
            problems.append(f"{path.name}: {report['failed']}")
    expect(not problems, "; ".join(problems))


@case("skills: every playbook names a real signature and a real rule")
def _():
    from rsi import knowledge

    rule_ids = {rule.get("id") for rule in knowledge.load_store("rules", ROOT)}
    anti_ids = {rule.get("id") for rule in knowledge.load_store("antipatterns", ROOT)}
    playbooks = sorted((ROOT / "playbooks").glob("*.md"))
    expect(len(playbooks) >= 8, f"expected eight playbooks, found {len(playbooks)}")
    import re

    for path in playbooks:
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        for cited in re.findall(r"\b(R-[0-9]+)\b", text):
            expect(cited in rule_ids or "no rule yet" in text,
                   f"{path.name} cites {cited}, which is not in kb/rules")
        for cited in re.findall(r"\b(A-[0-9]+)\b", text):
            expect(cited in anti_ids, f"{path.name} cites {cited}, which is not in kb/antipatterns")


@case("closures: the class is derived from the checks and the verdict word cannot choose it")
def _():
    from rsi import closures

    refuting = {"verdict": "refuted", "checks": [
        {"id": "A1", "group": "A", "conclusion": "pass"},
        {"id": "B1", "group": "B", "conclusion": "pass"},
        {"id": "C1", "group": "C", "conclusion": "fail"}]}
    expect(closures.classify(refuting)["evidence"] == "premise",
           "a landed, measured, unmoved counter is not a premise refutation")

    never_landed = dict(refuting, checks=[
        {"id": "A1", "group": "A", "conclusion": "pass"},
        {"id": "B1", "group": "B", "conclusion": "fail"},
        {"id": "C1", "group": "C", "conclusion": "fail"}])
    expect(closures.classify(never_landed)["evidence"] == "implementation",
           "a change that did not reach machine code was classified as evidence")

    confirmed_no_score = dict(refuting, checks=[
        {"id": "A1", "group": "A", "conclusion": "pass"},
        {"id": "B1", "group": "B", "conclusion": "pass"},
        {"id": "C1", "group": "C", "conclusion": "pass"}])
    expect(closures.classify(confirmed_no_score)["evidence"] == "measurement",
           "a confirmed mechanism with no score is a mislabelled confirmation")

    expect(closures.classify({"verdict": "refuted", "checks": []})["evidence"] == "unclassified",
           "a refutation with no checks was classified anyway")

    claimed = closures.from_verdict(refuting, {"direction": "d"}, {},
                                    {"evidence": "implementation"})
    expect(claimed["evidence"] == "premise" and claimed["declared_evidence"] == "implementation",
           "a caller-supplied evidence class overrode the checks")

    swept = {"direction": "d", "evidence": "premise", "landed": True, "points_tried": 3}
    expect(closures.is_retirable(swept), "a swept premise refutation was not retirable")
    expect(not closures.is_retirable(dict(swept, points_tried=1)),
           "a one-point refutation retired a direction")
    expect(not closures.is_retirable(dict(swept, reopen_when="when the kernel's share rises")),
           "a closure with a retry condition was retired")
    expect(not closures.is_retirable(None), "a missing closure was treated as retirable")


@case("knowledge: every rule card validates against the shared schema")
def _():
    from rsi import knowledge

    problems = []
    for store in ("rules", "antipatterns"):
        for rule in knowledge.load_store(store, ROOT):
            problems.extend(knowledge.validate_rule(rule, ROOT))
    expect(not problems, "; ".join(problems))


@case("knowledge: every pattern names a mechanism and the counters it should move")
def _():
    from rsi import knowledge, metrics

    problems = []
    patterns = knowledge.load_store("patterns", ROOT)
    expect(len(patterns) >= 12, f"expected at least 12 patterns, found {len(patterns)}")
    for pattern in patterns:
        if not pattern.get("mechanism") or len(str(pattern["mechanism"])) < 40:
            problems.append(f"{pattern.get('id')}: no mechanism, so the hypothesizer cannot write a card")
        counters = pattern.get("expected_counters") or []
        if not counters:
            problems.append(f"{pattern.get('id')}: names no counter that should move")
        unknown = metrics.check_names([str(name) for name in counters])
        if unknown:
            problems.append(f"{pattern.get('id')}: unregistered metrics {unknown}")
        if not pattern.get("implementation_traps"):
            problems.append(f"{pattern.get('id')}: no implementation traps, which is where superficial forms live")
    expect(not problems, "; ".join(problems))


@case("knowledge: the documented layer is indexed and every entry is sourced")
def _():
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(ROOT / "skills" / "ingest-docs" / "scripts" / "index_documented.py"),
         "--check"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    expect(completed.returncode == 0, completed.stderr.strip())


@case("knowledge: the audit finds no stale status, no unevidenced rule, no cited conjecture")
def _():
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "rsi-cli"), "kb-audit"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    expect(completed.returncode == 0, completed.stdout.strip()[-800:] + completed.stderr.strip()[-400:])


@case("docs: no skill or playbook names a vendor as the thing to do")
def _():
    import re

    # Skills and playbooks instruct an agent; a vendor name there becomes an instruction to use
    # that vendor's tool on a device that may not have it. Mapping tables live in docs/, and a
    # hardware fact's vendor-specific note lives in kb/hardware/documented/, so neither is checked.
    banned = re.compile(
        r"\b(ncu|nsight|nvcc|cuda|sm__|dram__|warp|warp-wide|npu-smi|msopprof|HBM|"
        r"tensor\s+core|shared\s+memory|blockIdx|threadIdx)\b",
        re.IGNORECASE)
    offenders = []
    for path in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "playbooks").glob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    expect(not offenders, "vendor vocabulary leaked into skills or playbooks:\n" + "\n".join(offenders[:12]))


@case("knowledge: every metric declared as a metric is a canonical one")
def _():
    import re

    from rsi import knowledge, metrics

    # A metric name that reaches a rule's effect, a pattern's expected counters or a hypothesis
    # prediction is compared against a profile by machine. A typo there does not read as wrong, it
    # reads as "the counter did not move", which is a false refutation. So those places are
    # checked; field names like setup_us are not metrics and are not inspected.
    problems = []
    for store in ("rules", "antipatterns", "patterns"):
        for entry in knowledge.load_store(store, ROOT):
            declared = []
            effect = entry.get("effect") or {}
            if effect.get("metric"):
                declared.append(effect["metric"])
            declared.extend(entry.get("expected_counters") or [])
            unknown = metrics.check_names([str(name) for name in declared])
            if unknown:
                problems.append(f"{entry.get('id')}: {unknown}")

    # In prose, a backticked name may be a metric or a schema field (``setup_us``, ``attained_pct``).
    # The distinction that matters: a name compared against a profile by machine is checked above;
    # a name only quoted in prose is checked here, against the set of field names the schemas define.
    fields = _schema_field_names()
    for path in sorted((ROOT / "skills").rglob("strategy/*.md")) + sorted((ROOT / "playbooks").glob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"`([a-z][a-z0-9_]*_(?:pct|ratio|bytes|count|us))`", line):
                name = match.group(1)
                if metrics.is_known(name) or name in fields:
                    continue
                problems.append(f"{path.relative_to(ROOT)}:{number}: {name}")
    expect(not problems, "unknown metric names: " + "; ".join(problems[:15]))


def main() -> int:
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print()
        for failure in FAILED:
            print(f"FAIL  {failure['case']}")
            print(f"      {failure['error']}")
        print()
        print("Run with --verbose for tracebacks.")
        if "--verbose" in sys.argv:
            for failure in FAILED:
                print(failure["trace"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
