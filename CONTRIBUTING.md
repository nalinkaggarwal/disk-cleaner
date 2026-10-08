# Contributing

Thanks for helping. The project has no dependencies, so setup is just Python 3.10+ on Windows.

```
python -m unittest discover -s tests -t .
python -m tickclean --scan
```

- **Adding a rule for an unwanted program:** edit `tickclean/rules.json` (see the README) and explain in `reason` why a person might remove it.
- **Safety first:** a change that lets the tool delete more must come with tests that show the guard in `actions.py` still refuses system folders, the profile folder and personal folders.
- **Reviews:** changes from people other than the maintainers are reviewed before they are merged. Only reviewed code reaches a signed release.
- **Signed releases:** maintainers publish them by pushing a version tag. See `CODE_SIGNING_POLICY.md`.
