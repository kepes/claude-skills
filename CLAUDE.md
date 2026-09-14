# claude-skills — project instructions

## Language

- **This project is English-first.** Skills, plugin manifests, the marketplace catalog, scripts
  (code, comments, CLI help, output), README and commit messages are written in English.
- **Hungarian plugins are the exception, and they are marked.** If a plugin is deliberately
  developed in Hungarian, its name must end in `-hu` (e.g. `weekly-focus-hu`). Use the same name
  in `plugin.json`, in the plugin folder under `plugins/`, and in the `marketplace.json` entry.
  Use a hyphen, not an underscore: plugin names must be kebab-case, otherwise
  `claude plugin validate` warns and the Claude.ai marketplace sync rejects the plugin.

## Importing non-English skills or plugins

When a skill or plugin written in another language (typically Hungarian) is copied into this
repository:

1. **Ask the user first** whether it should be translated to English. Do not translate on your own.
   - **No:** keep it as it is and apply the `-hu` naming rule above.
   - **Yes:** translate it, following the rules below.
2. **Some Hungarian words must stay untranslated**, because the skill depends on them to work.
   Translating them would break the skill without any error. Keep these as they are:
   - **Trigger phrases in the `description` frontmatter** (e.g. `"mennyibe került a session"`,
     `"elmúlt két nap"`). Hungarian users will still type these, so the skill has to keep matching
     them. Add English triggers next to them; do not replace them.
   - **Strings that code or instructions match on or parse:** Hungarian keywords, argument values,
     regex patterns, time-window expressions, and labels or column names that a script or the
     skill's instructions look for.
   - **Identifiers:** file and folder names, paths, JSON/YAML keys, command names and flags,
     and names of other skills, agents or tools the skill references.
   - **Quoted examples of user input** that show which Hungarian phrasing the skill must handle.
3. **When unsure whether a word is functional, leave it in Hungarian** and ask the user.
4. After translating, give the user a short list of the Hungarian terms you kept and why.
