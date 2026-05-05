# Issue Tracker: GitHub

Issues and PRDs for this repo live in GitHub Issues for `solomonsjoseph/RePORT-AI-Portal`.

Use the `gh` CLI for issue operations from inside the repository clone.

## Conventions

- Create an issue with `gh issue create --title "..." --body "..."`
- Read an issue with `gh issue view <number> --comments`
- List issues with `gh issue list --state open --json number,title,body,labels,comments`
- Comment with `gh issue comment <number> --body "..."`
- Apply or remove labels with `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close with `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`; `gh` handles this automatically inside the clone.

## When A Skill Says "Publish To The Issue Tracker"

Create a GitHub issue.

## When A Skill Says "Fetch The Relevant Ticket"

Run `gh issue view <number> --comments`.
