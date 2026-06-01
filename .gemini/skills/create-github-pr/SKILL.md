---
name: create-github-pr
description: Use this when the user asks to create a GitHub Pull Request from the current branch to another branch. Inspects the diff, summarizes changes, generates a professional PR description, and creates the PR using GitHub CLI.
---

# GitHub Pull Request Creator

You are an expert open-source maintainer.

When this skill is active, you MUST follow this workflow.

## 1. Discover Branch Context

Determine:

```bash
git branch --show-current
```

This is the PR source branch.

If the target branch is not explicitly provided by the user:

- Stop.
- Ask for the target branch.
- Never guess.

## 2. Inspect Repository State

Before creating any PR, inspect the actual changes:

```bash
git status
git log --oneline <target-branch>..HEAD
git diff --stat <target-branch>...HEAD
```

If the branch contains uncommitted changes:

- Warn the user.
- Do not create the PR until changes are committed.

## 3. Review the Changes

Review:

- Commit history
- Changed files
- Diff summary

Identify:

- Purpose of the change
- User-facing impact
- Breaking changes
- Documentation changes
- Test changes

Never invent details.

Base the PR only on the actual diff.

## 4. Generate PR Metadata

Create:

### Title

Requirements:

- Concise
- Technical
- Imperative style
- Reflect actual change

Examples:

- Improve OpenAPI endpoint documentation
- Add Google Generative API compatibility endpoint
- Refactor Playwright session lifecycle handling

### Body

Use this structure:

```markdown
## Summary

Brief description of the change.

## Key Changes

- Change 1
- Change 2
- Change 3

## Testing

- Tests run: `<command or "Not verified">`
- Result: `<passed / failed / unknown>`

## Notes

Additional context if relevant.
```

Only include sections that are supported by evidence.

## 5. Testing Integrity Rules

- If files under `tests/` were modified, explicitly verify whether tests were executed.
- Never claim tests passed unless there is evidence:
  - pytest output
  - CI result
  - commit message evidence
  - explicit user confirmation
- If no evidence exists, write:
  - `Tests run: Not verified`
  - `Result: Unknown`
- If files under `tests/` were modified and no test execution evidence exists, add this warning under `## Notes`:
  - `⚠ Test files were modified, but no test execution evidence was found.`

## 6. Show Planned PR

Before creating the PR, display:

- Source branch
- Target branch
- Title
- Body
- Exact command

Example:

```bash
gh pr create \
  --base feature/playwright \
  --head improve/endpoints \
  --title "Improve OpenAPI endpoint documentation" \
  --body-file /tmp/pr-body.md
```

## 7. Ensure Remote Branch Exists

Verify:

```bash
git ls-remote --heads origin <current-branch>
```

If missing:

```bash
git push -u origin <current-branch>
```

Explain why before pushing.

## 8. Create the Pull Request

Write the PR body to a temporary file:

```bash
cat > /tmp/pr-body.md
```

Create the PR:

```bash
gh pr create \
  --base <target-branch> \
  --head <current-branch> \
  --title "<title>" \
  --body-file /tmp/pr-body.md
```

Prefer `--body-file` over inline `--body`.

## 9. Return Result

After creation, return:

- PR URL
- PR Number
- Source branch
- Target branch
- Final title

## Rules

- Never create a PR without inspecting the diff.
- Never guess the target branch.
- Never invent test results.
- Never squash, merge, close, or approve a PR unless explicitly asked.
- Prefer evidence-based PR descriptions.
- Prefer concise titles.
- Prefer `--body-file` over inline `--body`.
