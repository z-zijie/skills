# Permissions

Directory boundaries are permission boundaries. The fewer places an agent can write, the more
the system can be trusted. This file states the boundary; `tooling/rsi/paths.py` enforces it,
and an attempt to write outside it is logged because the attempt itself is worth seeing.

## Who may write what

| Path | Inner-loop agents | Outer-loop agents | Notes |
| --- | --- | --- | --- |
| `evaluator/` | Call the CLI only | Call the CLI only | Human only; any edit means re-running every baseline |
| `operators/*/workspace/` | Read and write | Read-only | The only code area an agent may write |
| `operators/*/best/` | Read-only | Read-only | Promoted into only by the evaluator, when a result qualifies |
| `operators/*/spec.yaml` | Read-only | Read-only | A human sets tolerances and shapes |
| `ledger/` | Append through a tool | Read-only | History is never edited or deleted |
| `kb/hardware/measured/` | Read-only | Read-only | Written only by the calibration workflow |
| `kb/rules`, `kb/patterns`, `kb/antipatterns` | Read-only | Submit through `kb/proposals/` | Merged after replay or the evidence rules pass |
| `kb/conjectures/` | Append through a tool | Append through a tool | Clearly marked unverified |
| `skills/`, `playbooks/` | Read-only | Edit on a branch | Merged after replay passes |
| `benchmark/holdout.txt` | Not visible | Not visible | Only the replay judge and a human |
| `CHANGELOG-scaffold.md` | Not writable | Append through the replay judge | Every merge and every rollback |

## How it is enforced

Four mechanisms, because a rule that only exists in prose is a suggestion:

1. **A write policy in code, called at the write.** `paths.require_writable` raises
   `PermissionDenied` and records the attempt, and `ledger.append`, `ledger.archive_artifact` and
   the calibration writer actually call it. Roles are `inner_loop`, `outer_loop`, `distiller`,
   `replay_judge`, `evaluator` and `human`; the human may write everything. A boundary with no
   call site is a comment, which is why the enforcement lives at the three writes the system
   performs rather than in a document nobody executes.
2. **A read block on the held-out split.** `paths.hidden_from_agents` and `paths.require_hidden`
   refuse to hand `benchmark/holdout.txt` to anything except the replay judge.
3. **Process isolation for candidate code.** Correctness runs in the evaluator's process, so
   timing runs in a separate interpreter (`evaluator/sandbox.py`): the child gets a minimal
   environment with `RSI_SIGNING_KEY` stripped, holds no reference output, and returns only
   samples. The parent does the statistics and the signing, so candidate code never runs in the
   process that holds the key.
4. **A separate user for the evaluator.** Run Layer 0 under its own system user or in a
   container with `evaluator/` mounted read-only and its signing key readable only by that user.
   The signing key is what makes a forged result detectable: the ledger accepts only signed
   results, and the signature covers the whole body.

```sh
# A deployment that separates the evaluator from the agents.
useradd --system rsi-evaluator
chown -R rsi-evaluator: /srv/rsi/evaluator /srv/rsi/.rsi
chmod 700 /srv/rsi/.rsi && chmod 600 /srv/rsi/.rsi/evaluator.key
mount --bind -o ro /srv/rsi/evaluator /srv/rsi/evaluator
```

The evaluator's own user owns the signing key, so an agent process running as another user
cannot mint a result even if it can read the source.

## What is not enforced, and why

The process split in mechanism 3 is a real boundary at the process level: candidate code cannot
read the key, the reference output or the parent's timing. It is not a security sandbox, because
both processes run as the same user and a determined kernel can still reach the filesystem.

The write policy is a guardrail against an agent wandering, not a security boundary against the
human who owns the machine. An agent with the same filesystem permissions as the owner can
change anything the owner can. The honest statement of the guarantee is therefore:

- **Tamper-evident:** the ledger rejects unsigned results, results carry the evaluator's source
  hash and the environment fingerprint, and the scaffold commit is on every entry. A change made
  in secret leaves evidence.
- **Not tamper-proof:** a process with the owner's filesystem rights can rewrite history. Run the
  evaluator under a separate user when that matters.

Read-only mounts and separate users are what turn the first property into the second. They are a
deployment choice, and the deployment section above shows the three commands that do it.
