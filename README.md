# rstack

rstack is a Claude Code skill for running several agents on one software project. The main conductor owns decisions and merges. Each lane runs its own builds and reviews. Route parts to models by difficulty and give reviewers a separate brief.

## Quick start

From this checkout, install the skill and agents. Copy the example if your project has no config yet.

```sh
./install.sh
cp conductor.config.example.json /path/to/project/conductor.config.json
```

Fill in the project settings. Start a session in the project directory and try this prompt.

> Use rstack to update the setup guide. Check the steps against the repo and run the relevant tests.

The installer uses `~/.claude/skills/rstack` and `~/.claude/agents/`. Set `CLAUDE_SKILLS_DIR` to change the skills directory. Rerun the installer to check an existing install. Use `--force` to replace a different copy or agent file. The installer reports missing tools and lists any agent files it skips. It validates the first config it finds in the current directory, this checkout or `~/.claude/`.

## Use

Ask Claude Code to act as the conductor for a project. Give it a goal, such as adding team billing, or a short task, such as updating the setup guide. For large work, an independent panel reviews the plan while builders and reviewers work on drafts. Routine tasks and bug fixes start with a short spec.

With cross-window messaging, each lane can run in its own window. In one window the conductor runs the lane agents directly. The conductor uses the repo's merge path and verifies the deploy. See [human-only steps](references/trust.md#human-steps-and-queue-decisions).

See the skill from plan to deploy in [the worked team billing example](references/example-run.md).

## Configuration

Save `conductor.config.json` in the project root or `~/.claude/conductor.config.json` for user defaults. Use the model IDs your setup offers. See [adapters](references/adapters.md).

Run `python3 scripts/validate-config.py /path/to/project/conductor.config.json` from this checkout. Use the keys in the example. The `_comment` and `intake` sections are optional. Use positive integers for caps. If an older config has a prose note under `caps.windows`, remove that entry or give it a positive integer.

| Key | What it controls |
|---|---|
| `roles` | Default tool and model per role. Pick two or three eligible model families from `plan_panel`. |
| `difficulty_grades` | Ordered model choices for Frontier, Hard, Normal and Easy work. |
| `review_policy` | Reviews by risk and scope, and the limit on critical fix re-reads. |
| `metrics` | Part log path, routing review cadence and when to move work to a cheaper model. |
| `trust_tiers` | Reference for access by agent tier. |
| `hierarchy` | Main conductor, lane windows and maximum depth. |
| `caps` | Limits on agents, local test runs, commits and PRs per landing, and trains in CI. |
| `merge_queue` | GitHub repository and base branch, local push checks, workflow and deploy checks, freeze windows and polling. See [merge queue](references/merge-queue.md). |
| `intake.state_file` | Path to the intake checkpoint, pending reviews and acknowledgements. Defaults to `.rstack/intake-state.json`. |
| `intake.verdicts_dir` | Folder of verdicts named `<head-sha>.md`. Defaults to `.rstack/verdicts`. |
| `planning` | Which work needs a plan and which goes straight to a short spec. |
| `fallbacks` | Next eligible models when a route is unavailable. |
| `autonomy` | Reversible steps agents can take and actions that need a pause. |

The conductor follows these settings when it assigns work and gates merges. Set tool permissions separately. See [trust and access](references/trust.md).

Run `scripts/intake.sh /path/to/project` from this checkout. It lists all open PRs the first time, then new, updated and pending PRs.
Heads without a verdict are saved and shown again as `still pending`. A verdict, a closed or merged PR, or a new head clears the old entry.
Run `intake.sh --ack <pr>:<head> [reason]` from the worktree to record an outcome and clear that head. See [intake](references/merge-queue.md#intake).

## Repository files

- [SKILL.md](SKILL.md) has the core rules and links to every file in `references/`.
- [conductor.config.example.json](conductor.config.example.json) has the default settings.
- [agents/planner.md](agents/planner.md) writes large plans.
- [agents/plan-reviewer.md](agents/plan-reviewer.md) reviews large plans while draft work proceeds.
- [agents/builder.md](agents/builder.md) builds one part and opens a draft PR.
- [agents/reviewer.md](agents/reviewer.md) checks the spec or the outcome and writes a verdict.
- [agents/auditor.md](agents/auditor.md) checks project records and reports findings.
- [agents/codex.md](agents/codex.md) runs a build or review through the Codex CLI.
- [agents/opencode.md](agents/opencode.md) sends easy work through the guarded launcher.
- [agents/openrouter.md](agents/openrouter.md) sends read-only work with named files over the API.
- [scripts/oc-build.sh](scripts/oc-build.sh) prepares a worktree run and applies the guard.
- [scripts/opencode-guard.json](scripts/opencode-guard.json) lists paths and commands the guard denies.
- [scripts/openrouter-ask.py](scripts/openrouter-ask.py) sends one brief with named files.
- [scripts/openrouter-batch.py](scripts/openrouter-batch.py) runs a prompt over many items with a spend threshold and resume support.
- [scripts/route-stats.py](scripts/route-stats.py) summarises per-part metrics by task type and model.
- [scripts/run-slot.sh](scripts/run-slot.sh) limits heavy local runs across windows.
- [scripts/push-gate.sh](scripts/push-gate.sh) checks a candidate from a frozen copy before pushes and ready toggles.
- [scripts/merge-when-green.sh](scripts/merge-when-green.sh) watches one ruled landing and merges when its gates clear.
- [scripts/intake.sh](scripts/intake.sh) groups new, updated and pending open PRs by author.
- [scripts/validate-config.py](scripts/validate-config.py) checks the config and prints one line per problem.

Python can create local bytecode caches in `scripts/__pycache__/`.

## Script tests

Run [tests/run.sh](tests/run.sh) to check the queue scripts and config validator with fake commands.

## Tests for the skill

`evals/` holds three real-task test cases (landing a day of PRs, an infrastructure migration while the human is away, and coordinating sub-conductor windows), each with a prompt, the expected outcome and one grader per checkable behaviour. Run them with and without the skill and compare; with Claude Code: `claude plugin eval . --allow-tools Write`. The first run scored 19 of 20 with the skill against 13 of 20 without; its findings are folded into the guidance.

## Licence

See the MIT [LICENSE](LICENSE).
