# staging/transcripts/ — inbox for machine-transcribed audio notes

Drop a **machine transcript of an audio note recorded outside Indexia** here — plain text, however
your transcription tool exports it. This is for voice memos and the like: you spoke the note into
some other app, that app (or a separate transcription service) turned it into text, and the text
lands here before it is a note.

That text is only **parked**, not reviewed. Machine transcription mishears words, drops
punctuation, and runs unrelated thoughts together with no boundary between them — the same way a
page of handwriting holds several ideas with no divider. Nothing here is a note yet.

Start a Claude session and invoke the **`review-transcripts`** skill (or just say "review the
transcript in `staging/transcripts/`"). Claude will:

1. **Correct only what's clearly mis-transcribed** — a homophone, a dropped word, garbled
   punctuation — never reword, expand, or polish beyond recovering what was actually said.
   Anything it can't reconstruct with confidence is flagged `[unclear]`, not guessed: there's no
   audio to check against, only the flawed transcript.
2. **Propose note boundaries** if the recording rambles across more than one idea (you can also
   mark them yourself). Splitting only *segments* the reviewed text; it never merges ideas or
   changes wording to make them flow.
3. **Ask you to ratify** each proposed note — including every correction and every `[unclear]` —
   before anything is written. Nothing reaches `staging/` until you approve.
4. **Write each ratified note** to `staging/<id>.md` (`<id>` is a spec §4 datetimestamp), with
   `author: human` and `source_ref:` pointing back to the transcript file.

From there the normal pipeline takes over — revise the files if you like, then commit + embed them:

```bash
bash scripts/ingest-staging.sh --dry-run   # check they parse
bash scripts/ingest-staging.sh             # commit + embed
```

Move the original transcript aside once its notes are ratified (a `processed/` subdirectory here
mirrors the convention in `staging/scans/`) so this directory keeps showing only what's still
waiting on review. This directory is gitignored except this README — transcripts of your own voice
notes are private source material, same as the scans.
