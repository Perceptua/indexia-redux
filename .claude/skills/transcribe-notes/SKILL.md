---
name: transcribe-notes
description: Transcribe a photo/scan of a handwritten notecard or page into one or more Indexia notes. Use when the user has an image in staging/scans/ (or points to a picture of handwritten notes) and wants it turned into notes — "transcribe this scan", "OCR my notecard", "turn this photo of my notes into notes". Transcription only — no rewording or summarizing — with a human ratification step before anything is written to staging/.
---

# Transcribe handwritten notes into the graph

Turn an image of a handwritten notecard or page into one or more `staging/<id>.md` files that the
normal pipeline (`ingest-staging.sh`) later commits + embeds. Your job is **faithful transcription
and preparation — never authoring**. This skill does not touch the database.

## The one hard rule: verbatim

**Transcribe exactly what is written. Do not re-word, summarize, paraphrase, condense, expand,
translate, or "clean up."** The words in every note must be the human's, character for character.

- Preserve the author's wording, spelling, capitalization, and punctuation as written — do **not**
  silently autocorrect. (If something is clearly a slip, you may point it out for the human to fix
  during ratification; you do not fix it yourself.)
- Illegible text → write `[illegible]` (or `[?best-guess]` for a genuine guess). Never invent words
  to fill a gap.
- Keep line/paragraph structure where it carries meaning; drop only trivial layout artifacts.
- **No invented titles.** Only set a `title:` if a heading is literally written on the card.

## Steps

1. **Find the image.** Look in `staging/scans/` (PNG/JPG/PDF). Read it with the Read tool. If several images
   are present, handle one at a time (confirm which if ambiguous).

2. **Transcribe** the full text verbatim, per the rule above.

3. **Propose note boundaries.** A Zettelkasten note holds one idea (spec §3.1), so a page often
   becomes several notes:
   - **Respect explicit boundaries** the author gave — divider lines, numbering, bullet blocks,
     separate cards.
   - Otherwise **suggest** a split at clear idea boundaries. Splitting only *segments* the verbatim
     text — you never merge notes or change wording to make them "flow."
   - When in doubt, keep it as one note and let the human decide.

4. **Ratify (required — write nothing yet).** Present the proposed notes as a numbered list, each
   showing: its verbatim body (quoted), the `title` only if one was written, and the `source_ref`
   (the image). Then ask the human to ratify — they may approve all, approve a subset, re-draw
   boundaries, or edit text/titles first. **Only notes the human explicitly approves get written.**

5. **Write ratified notes to staging.** Mint one id per approved note (preserving reading order):
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/new-id.sh <N>'
   ```
   For each approved note, write `staging/<id>.md` (use the ids in order) with this exact shape:
   ```
   title: <only if a heading was written; otherwise omit this whole line>
   author: human
   source_ref: staging/scans/<image-filename>
   ---
   <verbatim transcribed body, exactly as ratified>
   ```
   The `---` fence lets the body be multi-line and contain colons safely (see `staging/README.md`).

6. **Verify + hand off.** Confirm the files parse, then move the image aside:
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/ingest-staging.sh --dry-run'
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && mkdir -p staging/scans/processed && mv "staging/scans/<image>" staging/scans/processed/'
   ```
   Tell the user the notes are staged, where they are, and that they can revise the files and then run
   `bash scripts/ingest-staging.sh` to commit + embed them.

## Notes & gotchas

- **Filenames are the id.** Each staging file is named `<id>.md`, where `<id>` is a spec §4
  datetimestamp minted by `new-id.sh` (never hand-craft ids — the minter guarantees a valid, unique,
  ordered batch).
- **Ratification is a hard gate.** Never write staging files before the human approves. Boundary
  suggestions are proposals, not decisions.
- **Author is the human.** They wrote the words; you only transcribed. Set `author: human`. Provenance
  to the image lives in `source_ref`, not in a reworded body.
- Transcription needs no database — only the later `ingest-staging` step does.
