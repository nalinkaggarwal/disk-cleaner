# Security policy

Disk Cleaner deletes files and can start uninstallers, so security and safety reports are taken seriously.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Use GitHub's private reporting instead: open the **Security** tab of this repository and choose **Report a vulnerability**. If that option is not available, open a public issue that says only "security contact requested" (no details) and a maintainer will arrange a private channel.

Helpful details: the version, what you did, what happened, what you expected, and whether it needs administrator rights. Reports about the following are in scope:

- Anything that can delete data the user did not tick, or bypass the safety guard or the second confirmation for personal photos and videos.
- Anything that lets a non-administrator influence what the elevated helper deletes or runs.
- Command, path or PowerShell injection through settings, program names or registry values.

You can expect an acknowledgement within 7 days. We will agree a disclosure date with you and credit you in the release notes unless you prefer otherwise.

## Verifying a download

Release assets come with a `.sha256` file. Releases are built by GitHub Actions from the public source and, once the SignPath Foundation certificate is active, code-signed. See [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md).
