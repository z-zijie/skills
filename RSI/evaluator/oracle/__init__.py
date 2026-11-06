"""Correctness: reference implementation, sentinel-and-canary checks, random shapes.

The reference runs inside the evaluator's own process and its output is copied out before the
candidate runs, so a candidate cannot read what it is supposed to produce.
"""
