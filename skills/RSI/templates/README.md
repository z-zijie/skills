# Templates

Copy-and-adapt starting points. The values in them are placeholders, not recommendations.

| Template | Copy to | When |
| --- | --- | --- |
| `operator/` | `operators/<op>/` | onboarding a new operator (W1) |
| `hypothesis/card.yaml` | the step's output directory | before an experiment (W2 step 3) |
| `proposal/proposal.yaml` | `kb/proposals/P-<n>.yaml` | proposing any knowledge-base or scaffold change |
| `campaign/outcome.json` | the step's output directory | reporting a step's outcome to the state machine |

Templates carry the structure and the reasoning behind each field. When a template and a schema
disagree, the schema wins: the schema is the frozen interface, and the template is a convenience.
