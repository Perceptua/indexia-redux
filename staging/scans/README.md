# staging/scans/ — image inbox for handwritten notes

Drop a **photo or scan of a handwritten notecard or page** here (PNG / JPG / PDF), then start a
Claude session and invoke the **`transcribe-notes`** skill (or just say "transcribe the scan in
`staging/scans/`").

Copy it in from a shell, or drag it onto the **handwritten** zone in the graph UI's `inbox` panel
(`bash scripts/ui.sh start`, then <http://localhost:8420/>) — the panel lists what is waiting
here. Either way the image is only **parked**: nothing reads it, nothing is transcribed, and no
note exists yet. That is not a gap in the UI. Transcription needs a person and a model reading the
page together with a ratification step in the middle, and a button that claimed to do it in a
click would be claiming the wrong thing. A name already taken is suffixed `-1`, `-2` rather than
overwritten — a scan filename is a label, not an identity, and two photos called `card.jpg` are
two photos.

Once you invoke the skill, Claude will:

1. **Transcribe the text verbatim** — no re-wording, summarizing, or correcting. Illegible bits are
   flagged `[illegible]`, not guessed.
2. **Propose note boundaries** if the page holds several distinct notes (you can also mark them
   yourself — a divider line, numbering, separate cards). Splitting only *segments* the transcription;
   it never merges or alters wording.
3. **Ask you to ratify** each proposed note. Nothing is written until you approve.
4. **Write each ratified note** to `staging/<id>.md` (`<id>` is a spec §4 datetimestamp), with
   `author: human` and `source_ref:` pointing back to the image.

From there the normal pipeline takes over — revise the files if you like, then commit + embed them:

```bash
bash scripts/ingest-staging.sh --dry-run   # check they parse
bash scripts/ingest-staging.sh             # commit + embed
```

Processed images are moved to `staging/scans/processed/`. This directory (except this README) is
gitignored — the images are private source material.
