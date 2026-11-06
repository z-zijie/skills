"""Harness: the timing protocol and the microbenchmark driver.

The harness is part of frozen Layer 0. Agents call it through ``rsi evaluate`` and read
``result.json``; they never edit it, because a measurement protocol that an agent can adjust
is not a measurement.
"""
