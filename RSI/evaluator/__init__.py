"""Layer 0: the evaluator. Frozen. Agents may call the CLI; they never read or edit this source.

The three entry points are ``evaluate``, ``profile`` and ``calibrate`` (``cli.py``). Nothing in
this package imports a skill: Layer 0 depends on the shared tooling and the device, and on
nothing that the outer loop can change.
"""
