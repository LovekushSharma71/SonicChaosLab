# DEV fixtures — synthetic lab output

Everything under `mock/` is **synthetic, plausible `sonic-vs` output** used only by
`MockAdapter` so the lesson loop runs with no containerlab, no Docker, and no API keys.

- These files are **NOT** authored curriculum. The curriculum (under `lessons/`) never
  contains command outputs — only commands.
- `mock/<slug>.txt` is the healthy/baseline output for a command.
- `mock/<chaos_id>/<slug>.txt` overrides that command while the given anchor chaos is active,
  so the before/after diff shows real changes.
- Regenerate with `python scripts/gen_mock_fixtures.py` (also run by `make setup`).

Slugs are produced by `chaoslab.lab.adapter.slugify_spec`.
