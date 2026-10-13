# Publish to GitHub

The intended repository name is `intc-short-put-backtest`, with public visibility.
This document is a publication recipe, not evidence that a remote repository
has already been created. Local code, data, reports and tests are complete.

Using the official [GitHub CLI](https://cli.github.com/), authenticate in your
own account, then run from the local project directory:

```bash
gh auth login
gh repo create intc-short-put-backtest --public --source . --remote origin --push --description "Model-based INTC short-put research with daily accounting, physical assignment and reproducible risk reports"
gh repo view --web
```

These commands assume a local Git repository with a commit, as supplied in the
workspace project. If using the ZIP instead, initialize Git and commit first:

```bash
git init -b main
git add .
git commit -m "Build reproducible INTC short-put backtest"
```

Git may require your chosen author name and email. Configure your own identity;
never paste a GitHub token or password into the source files. If a remote
repository of the same name already exists, inspect it first rather than
overwriting it. After publication, verify the file tree and README figures,
check the Actions run, and use the actual repository URL in your resume.

References: [repository creation](https://cli.github.com/manual/gh_repo_create),
[authentication](https://cli.github.com/manual/gh_auth_login).
