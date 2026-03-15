# Contributing

This repository follows a "local-first + GitHub synchronization" workflow.

## Standard Flow

1. Create a branch from `main`.
2. Implement and test locally.
3. Commit in small, reviewable chunks.
4. Push the branch and open a Pull Request.
5. Merge through PR after review and passing CI.

## Branch and Commit Conventions

- Branch naming:
  - `feat/<topic>`
  - `fix/<topic>`
  - `chore/<topic>`
  - `codex/<topic>`
- Commits should be atomic and use clear intent, for example:
  - `feat: add fifa filter for soccer entries`
  - `fix: avoid pytest collection on temp directories`

## Local Validation

Use these commands before pushing:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`pytest.ini` scopes collection to `tests/` so `python -m pytest -q` is the default command.

## Pull Request Checklist

- [ ] Branch is up to date with `main`
- [ ] Local tests pass (`python -m pytest -q`)
- [ ] CI checks pass
- [ ] Description explains what changed and why
- [ ] Rollback plan is clear for risky changes
