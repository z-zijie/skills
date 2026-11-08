# AGENTS.md — skills

Each skill is a directory with a `SKILL.md`; it is an executable instruction for agents, not ordinary prose. The repository's `CODEOWNERS` treats `skills/` as owner-reviewed.

- Read `skills/writing-for-agents/SKILL.md` before creating or changing a skill.
- The frontmatter `name` and `description` are the trigger; the description must say when the skill fires, and the body must stay within the writing guide's limits.
- A skill that ships scripts must include a way to verify them (a test or a documented check command). State in the PR how you verified the skill's behavior.
- Do not change a skill's output contract without updating every consumer and the skill's tests.
- Keep examples and long reference material in `references/`, reached by a pointer from `SKILL.md`.
- Do not add secrets, tokens, machine-specific paths, or generated caches.
