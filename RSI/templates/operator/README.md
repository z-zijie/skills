# Operator template

Copy this directory to `operators/<op>/`, then:

1. Rename `<operator-name>` in `spec.yaml` and in both kernel files.
2. Set the tolerance. `bit_exact` for integer output; `relative_absolute` with a real rtol and
   atol for floating point. These are the numbers a candidate is judged against, so they come
   from the operator's own requirements, not from what a kernel happens to produce.
3. Write the byte and flop formulas. They decide the roofline bound, so count the traffic the
   operation genuinely requires: inputs read once, output written once, unless the algorithm
   says otherwise.
4. Add `reference/kernel.py` (the oracle) and `baseline/kernel.py` (the starting point). The
   entry function takes `(inputs, out, case, dtype)`; declare how many input buffers with
   `entry.inputs`.
5. Add `redteam/` deliberately cheating kernels and a `manifest.yaml`, and confirm the evaluator
   catches every one. Onboarding is not done until it does.
6. Add deliberately cheating kernels for *this* operator under `redteam/`, marking each with the
   detector that owns it (`via: correctness` by default, `via: timing` for a kernel that answers
   correctly once and then skips work). Onboarding is not done until `redteam` catches them all.

7. Run W1 in `docs/workflows.md`.

`best/` starts empty. The evaluator fills it when a candidate is confirmed and significant.
