# claude-skills

Skills for [Claude Code](https://claude.com/claude-code), packaged as a plugin marketplace.
Each plugin installs on its own — take only what you need.

## Contents

- [Installation](#installation)
- [Plugins](#plugins)
  - [session-cost-report](#session-cost-report)
  - [ste100-style](#ste100-style)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [License](#license)

## Installation

Add the marketplace once, then install the plugins you want:

```
/plugin marketplace add kepes/claude-skills
/plugin install session-cost-report@claude-skills
```

Update later with `/plugin marketplace update claude-skills`.

Every skill is a plain `SKILL.md` folder under `plugins/<plugin>/skills/`, so you can also
copy a skill folder straight into `~/.claude/skills/` (or another agent's skills directory)
without the plugin system.

## Plugins

### session-cost-report

Per-session token, cost and time summary for a Claude Code project. For each session it shows
the tokens used, what the session would have cost on Sonnet 5 / Opus 5 / Fable 5.1 (marking the
model it actually ran on), the real mixed cost including subagents, the wall-clock span and an
estimated active working time.

Inside Claude Code just ask: *"how much did this session cost?"*, *"token usage across all
projects for the last 2 days, sorted by cost"*, or run `/session-cost-report`.

**Requirements:** Python 3.10+, no third-party packages.

**Running the script directly:**

```bash
python3 plugins/session-cost-report/skills/session-cost-report/scripts/report.py \
  [-n N|all] [--project PATH] [--all-projects] \
  [--since SPEC] [--until SPEC] [--sort recent|tokens|cost] \
  [--idle-gap SECS] [--format table|markdown|json]
```

| Flag | Meaning |
|---|---|
| `-n, --number N` | Sessions to include (default `10`; `all` or `0` = no limit) |
| `--project PATH` | Project directory to analyze (default: current directory) |
| `--all-projects` | Analyze every project under `~/.claude/projects` |
| `--since SPEC` / `--until SPEC` | Time window (default: today midnight → now). SPEC: `2d`, `2h`, `90m`, `15:00`, `3pm`, `today`, `yesterday`, `YYYY-MM-DD`, ISO datetime |
| `--sort` | `recent` (default), `tokens` or `cost` |
| `--idle-gap SECS` | Gap above which time counts as idle for the `ACTIVE` estimate (default `300`) |
| `--format` | `table` (default, terminal only), `markdown`, `json`; shortcuts `--markdown`, `--json` |

Examples:

```bash
# Today's 10 most recent sessions of the current project
python3 .../report.py

# Every project, last 2 days, most expensive first, as Markdown
python3 .../report.py --all-projects --since 2d --sort cost -n all --markdown
```

**Notes:**

- Read-only: it only reads the local session logs in `~/.claude/projects/`, nothing is sent anywhere.
- The output contains session titles and project folder names — review it before sharing a report publicly.
- Costs are API-equivalent list prices (see the pricing table in `SKILL.md`), useful for comparing
  models, not for predicting a subscription invoice. Update `RATES` in `report.py` when prices change.

### ste100-style

An [output style](https://docs.claude.com/en/docs/claude-code/output-styles) that makes Claude
write all prose by the ASD-STE100 Simplified Technical English rules: short sentences, simple
vocabulary, active voice, one instruction per sentence, the result first. It answers in the
language of the user and has extra rules for Hungarian. Code, commands, paths and log output
stay unchanged.

After installing the plugin, pick it with `/output-style` (or set `outputStyle` in your settings).

## Repository layout

```
.claude-plugin/marketplace.json        # marketplace catalog: lists the plugins
plugins/<plugin>/
  .claude-plugin/plugin.json           # plugin manifest: name, version, description
  skills/<skill>/SKILL.md              # the skill itself (+ scripts/, references/)
  agents/                              # optional subagents the skills depend on
  output-styles/<style>.md             # optional output styles
```

## Local development

To work on a skill and use it at the same time, symlink the skill folder into your personal
skills directory instead of installing the plugin (installing both would register the skill twice):

```bash
ln -s "$PWD/plugins/session-cost-report/skills/session-cost-report" ~/.claude/skills/session-cost-report
```

Output styles work the same way, with a file symlink:

```bash
ln -s "$PWD/plugins/ste100-style/output-styles/ste100.md" ~/.claude/output-styles/ste100.md
```

Edits in the repository are then live in every Claude Code session.

Enable the secret-scanning pre-commit hook once per clone (needs `brew install gitleaks`):

```bash
git config core.hooksPath githooks
```

CI (`.github/workflows/ci.yml`) runs on every push and pull request to `main`: it validates all
JSON files and the marketplace → `plugin.json` mapping, checks that every `SKILL.md` has a `name`
and `description` in its frontmatter, byte-compiles the Python scripts and runs a `--help` smoke
test on Python 3.10, and scans the full git history with gitleaks.

## License

[MIT](LICENSE) © Peter Kepes
