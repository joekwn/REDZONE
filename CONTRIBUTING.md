# Contributing to REDZONE

Thanks for helping! Bug reports, ideas, code, docs and science reviews are all welcome.

## Ground rules

1. **Science first.** Do not merge, convert or average MODIS and VIIRS values (FRP, brightness)
   without evidence. Flag problem rows instead of deleting them. Explain any rule change in
   `docs/SCIENCE.md`.
2. **Never commit secrets or data.** No FIRMS keys, MotherDuck tokens, passwords, `.env` files,
   `data/`, `.duckdb` or `.parquet` files. Run `git status` before every commit.
3. **Keep it simple.** Plain HTML/JS front end, no build step; Python standard library where possible.

## Set up

```bash
git clone https://github.com/joekwn/REDZONE.git
cd REDZONE
python -m pip install -r requirements-dev.txt
python -m pytest            # should pass before you change anything
```

To run the site locally, see "Quick start" in the README.

## Making a change

1. Create a branch: `git checkout -b short-description`
2. Make the change, and add or update a test in `tests/` when you change behaviour.
3. `python -m pytest`
4. Commit with a clear message, push, and open a pull request. Fill in the checklist.

Harmonization and cleaning rules live in one place, `scripts/redzone_rules.py`, and are used by
the research pipeline, live updates and look-ups alike. Change them there, never in a copy.

## Reporting bugs

Open an issue with steps to reproduce. Remove keys, tokens and emails from any output you paste.
Security problems: see [SECURITY.md](SECURITY.md) instead.

By taking part you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).
