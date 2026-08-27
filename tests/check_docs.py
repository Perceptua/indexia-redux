#!/usr/bin/env python3
"""Documentation lint — the docs drift faster than the code, so check the mechanical parts.

  * every in-page `#anchor` in README.md resolves to a real heading;
  * every script on disk is mentioned in README.md, and every script it mentions exists;
  * every Op rule the code can log appears in README's rule grammar and in the spec.

Not run by default (`tests/run.sh --docs`), because a doc gap should not fail a code test run.
No database needed.
"""
import os
import re

import lib

check = lib.check
REPO = lib.REPO
readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
spec = open(os.path.join(REPO, "docs", "spec.md"), encoding="utf-8").read()


def slug(text):
    """GitHub's heading slug: drop a markdown link's target but keep ordinary parenthesised words
    ("Clusters (emergent themes)" -> clusters-emergent-themes), then strip punctuation."""
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"[`*\[\]]", "", text).strip().lower()
    return re.sub(r"[^a-z0-9 -]", "", text).replace(" ", "-")


# --- in-page anchors ---------------------------------------------------------------------------
heads = {slug(m) for m in re.findall(r"^#+ +(.*)$", readme, re.M)}
anchors = set(re.findall(r"\]\(#([a-z0-9-]+)\)", readme))
broken = sorted(a for a in anchors if a not in heads)
check(f"all {len(anchors)} in-page README links resolve", not broken,
      "broken: " + ", ".join("#" + a for a in broken) if broken else f"{len(heads)} headings")

# --- scripts documented both ways --------------------------------------------------------------
on_disk = {f for f in os.listdir(os.path.join(REPO, "scripts")) if f.endswith(".sh")}
referenced = (set(re.findall(r"scripts/([a-z0-9-]+\.sh)", readme))
              | set(re.findall(r"`([a-z0-9-]+\.sh)[` ]", readme)))
absent = sorted(referenced - on_disk)
undocumented = sorted(on_disk - referenced - {"lib.sh"})      # lib.sh is sourced, never invoked
check("every script the README references exists", not absent, ", ".join(absent))
check("every script on disk is documented in the README", not undocumented,
      ", ".join(undocumented) if undocumented else f"{len(on_disk)} script(s)")

# --- the Op grammar is complete ----------------------------------------------------------------
# Every rule name the code can pass to insert_op, scraped from the scripts.
logged = set()
scripts_dir = os.path.join(REPO, "scripts")
for f in os.listdir(scripts_dir):
    if not f.endswith(".py"):
        continue
    src = open(os.path.join(scripts_dir, f), encoding="utf-8").read()
    logged |= set(re.findall(r"""insert_op\([^,]+,\s*[^,]+,\s*["']([A-Z_][A-Z_0-9]*)["']""", src))
    logged |= set(re.findall(r"""insert_op\([^,]+,\s*[^,]+,\s*\n\s*["']([A-Z_][A-Z_0-9]*)["']""", src))
check("found the Op rules the code logs", len(logged) > 10, f"{len(logged)} rule(s)")
missing_readme = sorted(r for r in logged if r not in readme)
check("every Op rule the code logs is in the README grammar", not missing_readme,
      ", ".join(missing_readme))
missing_spec = sorted(r for r in logged if r not in spec)
check("every Op rule the code logs is in the spec (§12.3 table or its addendum)",
      not missing_spec, ", ".join(missing_spec))

# ...and the same check the other way. Documenting a rule nothing can emit is the drift that
# actually happened: PROPOSE_VARIANT/COMMIT_VARIANT/REJECT_VARIANT sat in both documents as
# "the rule grammar, as built" for four versions while no CLI, route or skill could reach them,
# and SEED_BINDS named a rule that never existed at all. The check above cannot see either, since
# it only asks whether the docs keep up with the code.
grammar = re.search(r"The rule grammar, as built:(.*?)\n\n", readme, re.S)
check("found the README's rule-grammar paragraph", bool(grammar),
      "anchor text changed — update this check" if not grammar else "")
if grammar:
    claimed = set(re.findall(r"`([A-Z][A-Z_0-9]{3,})`", grammar.group(1)))
    phantom = sorted(claimed - set(logged))
    check("every rule the README calls built is one the code can log", not phantom,
          ", ".join(phantom) if phantom else f"{len(claimed)} rule(s)")

# --- schema objects reach both documents -------------------------------------------------------
ddl = open(os.path.join(REPO, "ddl", "schema.sql"), encoding="utf-8").read()
types = set(re.findall(r"CREATE (?:VERTEX|EDGE|DOCUMENT) TYPE (\w+)", ddl))
# Five since v0.8.0: Note, Op, KnnCache, BEGETS, BINDS (spec §13). The floor is a canary for a
# truncated read, not a target — if it ever needs raising, something was reified that shouldn't be.
check("found the DDL types", len(types) >= 5, f"{len(types)} type(s)")
for doc, name in ((readme, "README"), (spec, "spec")):
    gaps = sorted(t for t in types if t not in doc)
    check(f"every DDL type appears in the {name}", not gaps, ", ".join(gaps))

lib.report_and_exit()
