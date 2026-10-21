# Rundown: rules for every session

This is a hackathon build. Follow the `hackathon-build` skill in
[jenzylove/my-skills](https://github.com/jenzylove/my-skills) (`hackathon-build/SKILL.md`). Attach
that repo and read the skill before starting work. The rules below are the ones already broken once.

## Commits
- Author every commit as `jenzylove <jennifereze12@gmail.com>`.
- **No AI attribution anywhere:** no `Co-Authored-By`, no `Claude-Session` trailer, no "Generated
  with" line in commits or PRs, and never author a commit as Claude. `.claude/settings.json` turns
  the trailers off; still check `git log` before pushing.

## Deployment
- **Deploy to the target early** and build against the live deployment: every claim is checked on
  the live URL, not only locally. Don't leave deploying to a late phase.
- No covering blockers: if a key, permission or network rule stops something, stop and report it
  with the evidence. The user decides the trade-off.
- Gates (stop for the user): idea lock, PRD and plan, a failed spike, and approval of the **live**
  product plus the release-readiness audit before any demo or launch material.

## Where things are
- `docs/PRD.md`, `docs/PLAN.md` (phase status), `docs/BUILD_LOG.md`, `docs/CREW.md`, `docs/DEPLOY.md`.
- Deploy: `scripts/deploy.sh` (needs the `AZURE_*` service principal variables and the Azure network
  domains listed in `docs/DEPLOY.md`).
