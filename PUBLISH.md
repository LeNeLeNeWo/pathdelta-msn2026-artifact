# Publishing handoff

The local repository is ready to publish as a new public repository named
`pathdelta-msn2026-artifact`.

The final local commit is anonymous and clean:

```text
1ce762e4552f496719a0380f959b166223c67e84
```

From a GitHub-authenticated shell, the standard publication command is:

```bash
cd artifact/msn2026_anonymous
gh repo create pathdelta-msn2026-artifact --public \
  --description "Anonymous artifact for PathDelta MSN 2026" \
  --source . --remote origin --push
```

If the GitHub CLI is not installed, create an empty public repository with the
same name in the account that owns the artifact, then run:

```bash
git remote add origin https://github.com/<account>/pathdelta-msn2026-artifact.git
git push -u origin main
```

Run `python3 scripts/verify_artifact.py` immediately before pushing. Do not add
credentials, a `.env` file, private labels, reviewer correspondence, or a
personal author profile link. After publication, replace `<account>` in the
paper's artifact-submission field only when double-blind review has ended.
