# Publishing oracle3-extras to PyPI

`.github/workflows/pypi-publish.yml` builds and checks the package on every push. When a GitHub release is published, it also uploads the package to PyPI with Trusted Publishing (OIDC), so no PyPI token is stored anywhere. Before the first release, PyPI and GitHub each need one setting. Both are done by the account owner and take a few minutes.

The PyPI name `oracle3-extras` was free on 7 October 2026 (`https://pypi.org/simple/oracle3-extras/` returned 404).

## 1. Add a pending publisher on PyPI

Sign in to the PyPI account that owns `oracle3` (two-factor authentication is required), open <https://pypi.org/manage/account/publishing/>, and under **Add a new pending publisher** enter exactly:

| Field | Value |
|---|---|
| PyPI project name | `oracle3-extras` |
| Owner | `YichengYang-Ethan` |
| Repository name | `oracle3-extras` |
| Workflow filename | `pypi-publish.yml` |
| Environment name | `pypi` |

PyPI creates the project on the first successful upload.

## 2. Create the `pypi` environment on GitHub

Open <https://github.com/YichengYang-Ethan/oracle3-extras/settings/environments>, click **New environment** and name it `pypi` (case-sensitive). Adding yourself as a required reviewer is recommended: every upload then waits for one click. No secrets are needed.

## 3. Release

1. Set the version in `pyproject.toml` and give the `CHANGELOG.md` entry its date.
2. Commit, then tag and publish the release:

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   gh release create v0.2.0 --title "oracle3-extras 0.2.0" --generate-notes
   ```

3. The workflow checks that the tag matches the `pyproject.toml` version, builds, installs and imports the wheel and the sdist, then uploads. After the first upload, change the install line in `README.md` to `pip install oracle3-extras`.

## Troubleshooting

- **invalid-publisher**: the repository, workflow file or environment name on PyPI does not match. Fix it at <https://pypi.org/manage/project/oracle3-extras/settings/publishing/>.
- **File already exists**: PyPI never accepts the same version twice. Bump the version and release again.
- **Waiting for review**: if the environment requires a reviewer, approve the run in the Actions tab.
