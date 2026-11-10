# Contributing

Thanks for helping! wheelgap is deliberately small: one job, one dependency (`packaging`). First contributions are very welcome, and nobody expects you to know the codebase.

wheelgap is maintained by [@patrickballard](https://github.com/patrickballard), who reviews and merges pull requests.

Using a coding agent (Codex, Claude Code, Copilot, Cursor…)? Point it at [AGENTS.md](AGENTS.md), which has the setup, a map of the code and the house rules in one place.

## Setup (3 commands)

You need [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/wheelgap/wheelgap && cd wheelgap
uv run --group dev pytest        # tests
uv run --group dev ruff check .  # lint
uv run wheelgap numpy -t all     # try it
```

No local setup? [Open the repo in GitHub Codespaces](https://codespaces.new/wheelgap/wheelgap): the dev container comes with uv and the dev tools installed.

## Picking something to work on

- Issues labelled [`good first issue`](https://github.com/wheelgap/wheelgap/labels/good%20first%20issue) are scoped to be doable in an evening, and each one says which files to touch and what to test. [`help wanted`](https://github.com/wheelgap/wheelgap/labels/help%20wanted) issues are bigger: a good next step once you've done one or two. The [`roadmap`](https://github.com/wheelgap/wheelgap/labels/roadmap) ones shape where wheelgap goes next.
- **Ask, and we'll assign the issue to you for 18 hours.** Comment "I'd like to take this" and we'll assign you. The 18 hours run from the moment you're assigned (our reply gives the end time). Open a pull request that links the issue (`Closes #N` or `Part of #N`) within those 18 hours (a draft is fine) to keep it. If there's no PR by then, the assignment lapses and we fix the issue ourselves, so please ask when you can start soon. If plans change, just tell us and we'll release it, no hard feelings. If your PR arrives after the 18 hours but before ours is open, we'll still review yours. An issue nobody has asked for goes to the first pull request opened for it.
- **Check before you start.** Look at the issue's assignee and linked PRs. While an issue is held for someone else, please pick another one. A PR from anyone else during a hold waits until the hold ends: if the assigned person's PR arrives in time, theirs is merged and the other is closed with a pointer to another issue; if not, we review the waiting PR instead of fixing the issue ourselves. A later PR for an issue that already has one is closed the same way.
- **Want to add something that isn't an issue yet?** Post it in [Discussions → Ideas](https://github.com/wheelgap/wheelgap/discussions/categories/ideas) first, so we can agree it fits before you spend time on it. Small fixes (typos, docs, obvious bugs) can go straight to a PR.

## Making a pull request

1. Fork the repo and create a branch.
2. Make your change. Before pushing, run:
   ```bash
   uv run --group dev ruff format .
   uv run --group dev ruff check .
   uv run --group dev mypy
   uv run --group dev pytest
   ```
3. Open a PR. Put `Closes #123` in the description if it finishes the issue, so the issue closes when it's merged, or `Part of #123` if it's one step of a bigger issue.

The PR template has a short checklist:

- **Tests** for any behaviour change. Tests never hit the network: use the `fake_pypi` fixture in `tests/conftest.py`, or a lockfile fixture under `tests/fixtures/`.
- **A line in `CHANGELOG.md`** under `## Unreleased`, written for users.
- **README updates** if users would notice the change (a new flag, target or input format).
- Keep the code in the same style as the surrounding code. `ruff` handles formatting.

## What to expect

- A review within hours, usually. Most PRs need a small change or two, which is normal: the maintainer often pushes the fix to your branch and says what changed, so leave "Allow edits by maintainers" on. If you'd rather make the changes yourself, say so in the PR.
- PRs are squash-merged, so don't worry about tidying your commit history. You stay the author of the commit.
- You're credited in the CHANGELOG and the release notes, and you appear in the thanks at the end of the README.
- When your PR is merged, we suggest another issue that fits, if you'd like one.

## AI-assisted contributions

Using AI tools (coding agents, bug-fix bots and the like) is fine, and [AGENTS.md](AGENTS.md) helps them follow the house rules. We ask three things:

- **Say so in the PR description**, with the tool's name. It doesn't count against you; it just helps the reviewer know what to check.
- **You're responsible for the diff.** Read it, run the checks yourself, and be ready to explain what it does, the same as for code you write by hand.
- **One issue per PR.** Small fixes can skip the issue (see above); anything bigger needs an open issue or an idea agreed in Discussions. Pull requests that fail the checks, that the author can't explain, or that change things nobody asked for may be closed.

## Reporting a wrong result

Found a package that wheelgap misjudges? Open a [bug report](https://github.com/wheelgap/wheelgap/issues/new?template=bug_report.yml) with the output of the same command plus `--explain`. It lists the wheel filenames and why each was rejected, which is everything needed to write a test, so you don't have to share your lockfile.

## Questions

Ask in [Discussions → Q&A](https://github.com/wheelgap/wheelgap/discussions/categories/q-a). No question is too basic.

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
