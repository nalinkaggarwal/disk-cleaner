# SignPath setup checklist (maintainer only)

The repository is prepared. These steps need your accounts, so only you can do them. Until step 6 is done, releases are built and published **unsigned**.

## 1. Publish the repository
- Create a **public** GitHub repository and push this folder to it. Keep the MIT `LICENSE`.
- `CODE_SIGNING_POLICY.md` lists `@nalinkaggarwal` in all three roles. Change it if the GitHub account or the team differs, and list any other maintainers by role.
- Turn on two-factor authentication for your GitHub account.
- Create a first release so SignPath can see a working, downloadable project: push a tag (`git tag v0.1.0` then `git push origin v0.1.0`). It will be unsigned.

## 2. Apply
- Go to https://signpath.org/apply and fill in the form. Give the repository URL, say it is a Windows disk-cleanup tool, and link `CODE_SIGNING_POLICY.md`.
- The Foundation checks the license, that the project is maintained and described, and that it contains no unwanted software. Approval can take days to weeks.

## 3. Create the SignPath project (after approval)
- SignPath normally provisions the organization, certificate and policies for Foundation projects. In the SignPath web app, create or use the project with slug `tickclean` (or choose another and set the repository variable in step 5).
- Add the predefined trusted build system **GitHub.com** to your organization and link it to the project.
- Upload `.signpath/artifact-configurations/default.xml` as the project's artifact configuration.
- Create or use a signing policy with slug `release-signing` that uses the Foundation's certificate, **requires manual approval** and has **origin verification** switched on.
- If the Foundation asks for it, install the **SignPath GitHub App** on the repository.

## 4. Make an API token
- In SignPath, create an API token for a user who is allowed to submit to this signing policy.

## 5. Add the settings to GitHub (Settings > Secrets and variables > Actions)
| Kind | Name | Value |
|---|---|---|
| Secret | `SIGNPATH_API_TOKEN` | the token from step 4 |
| Variable | `SIGNPATH_ORGANIZATION_ID` | your SignPath organization ID |
| Variable | `SIGNPATH_PROJECT_SLUG` | only if not `tickclean` |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | only if not `release-signing` |

## 6. Release a signed version
- Bump `__version__` in `tickclean/__init__.py` (the only place; `pyproject.toml` reads it) and add an entry to `CHANGELOG.md` (for example `0.1.1`), commit, then push a tag `v0.1.1`. The workflow refuses to run if the tag and the version differ.
- The workflow builds the exe, submits it to SignPath, and waits up to 6 hours. If you approve later than that, re-run the failed job.
- Open SignPath and **approve** the signing request. The workflow then checks the signature is valid and publishes the release.

## Notes
- The exe's product name (`TickClean`) and version are embedded at build time and checked by SignPath, so a mismatch fails the request. This is deliberate.
- The README must keep the line "Free code signing provided by SignPath.io, certificate by SignPath Foundation."
