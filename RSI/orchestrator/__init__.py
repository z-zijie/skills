"""Orchestrator: state machine, scheduling, model routing, replay judging.

This is code, not a skill. Skills never call each other; this module decides which skill runs
next, how many retries are allowed and when a campaign stops. Keeping the order here is what
makes every step costable, replayable and replaceable.
"""
