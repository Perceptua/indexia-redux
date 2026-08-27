---
name: review-transcripts
description: Review and ratify machine transcripts of audio notes waiting in staging/transcripts/ into one or more Indexia notes. Use when the user has a transcript of a voice memo or audio note (from Whisper, Otter.ai, a phone dictation app, etc.) dropped in staging/transcripts/ and wants it reviewed, corrected, and turned into notes — "review my transcripts", "ratify the voice memo", "turn this audio transcript into notes". Correcting mis-transcriptions and splitting note boundaries only — never embellishing or inventing — with a human ratification step before anything is written to staging/. For a handwritten image use transcribe-notes instead; for typing or dictating a note directly use add-note.
---

# Review and ratify audio-note transcripts into the graph

Turn a machine transcript of an audio note into one or more `staging/<id>.md` files that the normal
pipeline (`ingest-staging.sh`) later commits + embeds. Your job is **faithful recovery of what was
said, plus note boundaries — never authoring**. This skill does not touch the database.

## The one hard rule: correct only what's clearly wrong

A machine transcript is not a transcript of the note — it's a transcript of an imperfect listening.
**Fix what the machine plainly mis-heard (a homophone, a dropped word, missing punctuation that
changes nothing about meaning). Do not re-word, summarize, paraphrase, condense, expand, or "clean
up" beyond that.** You have no access to the original audio, so anything you can't confidently
reconstruct gets flagged, not guessed.

- Preserve the speaker's actual wording and phrasing — correcting a mishearing is not license to
  smooth out how they actually talk.
- Passages you can't confidently reconstruct → write `[unclear]`. Never invent words to fill a gap.
- Keep paragraph/thought structure where it carries meaning; drop only trivial artifacts the
  transcription tool added. A `[inaudible 00:32]`-style marker you can't resolve becomes
  `[unclear]` too, rather than being silently dropped.
- **No invented titles.** Only set a `title:` if the speaker clearly stated one.

## Steps

1. **Find the transcript.** Look in `staging/transcripts/` (`.txt` or whatever your transcription
   tool exports). Read it with the Read tool. If several are present, handle one at a time (confirm
   which if ambiguous).

2. **Review** the full text against the hard rule above: correct clear mis-transcriptions, mark
   everything else `[unclear]`.

3. **Propose note boundaries.** A Zettelkasten note holds one idea (spec §3.1), and a single
   recording often rambles across several:
   - Respect explicit boundaries the speaker gave (an audible topic change, "next note," a pause
     structure already reflected in the transcript's own paragraphing).
   - Otherwise **suggest** a split at clear idea boundaries. Splitting only *segments* the reviewed
     text — you never merge notes or change wording to make them "flow."
   - When in doubt, keep it as one note and let the human decide.

4. **Ratify (required — write nothing yet).** Present the proposed notes as a numbered list, each
   showing: its reviewed body (quoted), the `title` only if one was clearly stated, and the
   `source_ref` (the transcript file). Call out every correction you made and every `[unclear]` you
   left, so the human is ratifying your reading of it, not rubber-stamping it. Then ask the human to
   ratify — they may approve all, approve a subset, re-draw boundaries, or edit text/titles first.
   **Only notes the human explicitly approves get written.**

5. **Write ratified notes to staging.** Mint one id per approved note (preserving reading order):
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/new-id.sh <N>'
   ```
   For each approved note, write `staging/<id>.md` (use the ids in order) with this exact shape:
   ```
   title: <only if clearly stated; otherwise omit this whole line>
   author: human
   source_ref: staging/transcripts/<transcript-filename>
   ---
   <reviewed, ratified body, exactly as ratified>
   ```
   The `---` fence lets the body be multi-line and contain colons safely (see `staging/README.md`).

6. **Verify + hand off.** Confirm the files parse, then move the transcript aside:
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/ingest-staging.sh --dry-run'
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && mkdir -p staging/transcripts/processed && mv "staging/transcripts/<file>" staging/transcripts/processed/'
   ```
   Tell the user the notes are staged, where they are, and that they can revise the files and then
   run `bash scripts/ingest-staging.sh` to commit + embed them.

## Notes & gotchas

- **Filenames are the id.** Each staging file is named `<id>.md`, where `<id>` is a spec §4
  datetimestamp minted by `new-id.sh` (never hand-craft ids — the minter guarantees a valid, unique,
  ordered batch).
- **Ratification is a hard gate.** Never write staging files before the human approves. Corrections
  and boundary suggestions are proposals, not decisions.
- **Author is the human.** They said the words; you only recovered them from a flawed transcript.
  Set `author: human`. Provenance to the transcript lives in `source_ref`, not in a reworded body.
- **You cannot check against the audio.** Unlike `transcribe-notes` reading an image directly, you
  are one step removed from the source — the machine transcript is the only evidence you have. That
  is exactly why over-correcting is worse than leaving `[unclear]`: a confident guess that reads
  fine is indistinguishable from a correct recovery once it's in the graph.
- Review needs no database — only the later `ingest-staging` step does.
