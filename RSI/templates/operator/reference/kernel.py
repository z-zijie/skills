"""Reference implementation: the definition of correct.

It runs inside the evaluator's process, before the candidate, and its output is copied out
first. Keep it obviously correct rather than fast: it is the oracle, not a competitor.
"""


def <operator-name>(inputs, out, case, dtype):
    source = inputs[0]
    for index in range(len(out)):
        out[index] = source[index]
