# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `session-cost-report` 1.0.1: price Claude Opus 5.5 at its own tier ($4 / $20 per 1M
  tokens, cache reads at 5% of input). Before, the script priced Opus 5.5 as Opus 5
  ($5 / $25), about 25% too high. The Opus comparison column and the fallback for an
  unknown model id now use Opus 5.5.
- `session-cost-report` 1.0.1: correct subagent output tokens from Claude Code's token
  counter. The log often keeps only the stream-start `output_tokens` (3–30) of a
  response, so long subagent thinking turns were undercounted by 10–1000×. The real
  output is now derived from the `total_tokens_reminder` budget; it is never below the
  logged value, and main-session output stays as logged.

### Added
- Add `ste100-style` plugin: an output style that writes prose by the ASD-STE100
  Simplified Technical English rules (short sentences, simple vocabulary, active voice),
  in the user's language, with extra rules for Hungarian.
