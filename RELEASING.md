# Releasing cortex

The distribution is **`cortxai`** on PyPI; the import name and the CLI stay
`cortex`. Publishing is tag-driven and runs on GitHub Actions — nothing is
uploaded from a laptop, and no API token exists to leak.

## One-time setup (do this before the first release)

PyPI trusted publishing has to be configured on PyPI, not here. It is the
only manual step, and it replaces storing a token in the repo.

1. Sign in to <https://pypi.org> and open
   **Your projects → Publishing → Add a new pending publisher**
   (<https://pypi.org/manage/account/publishing/>).
2. Fill in exactly:

   | Field | Value |
   | :--- | :--- |
   | PyPI project name | `cortxai` |
   | Owner | `Unchained-Labs` |
   | Repository name | `cortex` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. Optional but recommended — repeat on <https://test.pypi.org> with
   environment name `testpypi`, so the dry run can publish somewhere real.
4. In the GitHub repo, **Settings → Environments → New environment → `pypi`**
   (and `testpypi` if you did step 3). Add yourself as a required reviewer if
   you want a manual gate between the tag and the upload.

That is all. The workflow requests an OIDC token at publish time; PyPI
matches it against the pending publisher above and creates the project on
first upload.

## Cutting a release

1. **Make sure the dashboard is committed fresh.** The wheel carries the
   built SPA, and CI fails the release if the committed copy differs from a
   clean build:

   ```sh
   cd web && npm ci && npm run build && cd ..
   git status --short src/cortex/server/webdist   # expect no changes
   ```

2. **Bump the version in two places** (they are asserted to match the tag):

   ```sh
   $EDITOR pyproject.toml          # project.version
   $EDITOR src/cortex/__init__.py  # __version__
   ```

3. **Write the changelog section.** The GitHub release notes are extracted
   from the `## [x.y.z]` heading in `CHANGELOG.md`, so the section has to
   exist under that exact form.

4. **Rehearse.** Actions → *Release* → *Run workflow* with `dry_run: true`
   builds, tests, checks metadata, asserts the dashboard is inside the wheel,
   installs it into a clean venv and boots the dashboard — publishing
   nothing. A PyPI version number can never be reused, so it is worth the
   five minutes.

5. **Tag and push.**

   ```sh
   git commit -am "release 0.2.0"
   git tag v0.2.0
   git push origin main v0.2.0
   ```

   The tag triggers build → publish → GitHub release. Watch it with
   `gh run watch`.

6. **Verify.**

   ```sh
   pipx install cortxai            # or: uv tool install cortxai
   cortex --version
   ```

## What the workflow checks before it publishes

- `ruff check` and the full pytest suite, against the code being shipped.
- The tag matches `project.version` — a mismatched tag stops the run.
- `twine check` on both artifacts.
- The wheel actually contains `webdist/index.html`, a hashed `webdist/app/*.js`
  bundle and the brand stylesheets. A wheel without the dashboard installs
  cleanly and serves nothing, which is invisible until someone opens the
  page — so it is asserted, not assumed.
- A clean-venv install that runs `cortex setup`, `cortex index`, boots
  `cortex serve`, and confirms the served page references the real bundle.

## If a release goes wrong

You cannot overwrite or re-upload a version on PyPI. Yank the bad one
(`pip install` stops resolving to it) and release a patch:

```sh
# on pypi.org: Manage → Releases → Yank
git tag v0.2.1 && git push origin v0.2.1
```

Deleting the git tag alone does nothing to PyPI.
