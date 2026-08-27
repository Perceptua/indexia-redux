#!/usr/bin/env python3
"""inbox — dropping a file into staging/ or staging/scans/ without a terminal. No database needed.

The half of this worth testing hard is the header rewrite, because `ingest_staging.parse_props` is
unforgiving in two directions and the failure mode is silence rather than an error:

  * it folds unrecognized lines into the **current** key, so prepending `source_ref:` to a
    headerless file swallows the whole body into it and leaves no `body` at all;
  * on reaching a lone `---` it **overwrites** what it collected, so a prose file holding a
    markdown divider loses everything above that divider — and still ingests, as a note that is
    quietly missing its first half.

So every rewrite below is asserted by running the real parser over the real output. Predicting
what parse_props will do is exactly the thing that goes wrong; asking it is not.
"""
import base64
import os
import tempfile

import lib

inbox = __import__("inbox")
ingest_staging = __import__("ingest_staging")
notelib = lib.notelib
check = lib.check

ID = "20260722T101500000Z"          # a real spec §4 id, so the "keeps its own id" path is exercised
b64 = lambda raw: base64.b64encode(raw).decode("ascii")


def raises(fn, *a, **kw):
    """(did it raise ValueError, the message) — the shape every refusal below is checked in."""
    try:
        fn(*a, **kw)
    except ValueError as e:
        return True, str(e)
    return False, ""


def parsed(text):
    """What ingest-staging would make of a prepared file."""
    return ingest_staging.parse_props(text)


# ---- safe_name: the browser names a path we are about to open ---------------
print("\n-- safe_name --")
for bad, why in [("../secrets.md", "traversal"), ("a/b.md", "posix separator"),
                 ("a\\b.md", "windows separator — the server is WSL, the browser is not"),
                 (".hidden.md", "a dotfile is invisible to staged_files"),
                 ("bad\x00name.md", "control character"), ("   ", "no name at all"),
                 ("x" * 300 + ".md", "absurd length"), (None, "not even a string")]:
    ok, msg = raises(inbox.safe_name, bad)
    check(f"refuses {why}", ok, msg or "accepted it")

check("an ordinary name passes through stripped", inbox.safe_name("  a note.md  ") == "a note.md")

# ---- kind_of: the extension is the whole routing rule -----------------------
print("\n-- kind_of --")
check("typed extensions route to staging/",
      all(inbox.kind_of("f" + e) == "typed" for e in inbox.TYPED_EXTS))
check("image extensions route to staging/scans/",
      all(inbox.kind_of("f" + e) == "scan" for e in inbox.SCAN_EXTS))
check("the extension is read case-insensitively",
      inbox.kind_of("NOTE.MD") == "typed" and inbox.kind_of("CARD.JPG") == "scan")
ok, msg = raises(inbox.kind_of, "archive.zip")
check("an extension nothing here reads is refused, naming what is read", ok and ".md" in msg, msg)

# ---- decode_payload ---------------------------------------------------------
print("\n-- decode_payload --")
check("plain base64 round-trips", inbox.decode_payload(b64(b"hello world")) == b"hello world")
check("line-wrapped base64 still decodes — `base64 -w 76 | curl` is a legitimate caller",
      inbox.decode_payload("aGVsbG8g\nd29ybGQ=") == b"hello world")
check("a data: URI prefix is tolerated — that is the shape FileReader hands the page",
      inbox.decode_payload("data:text/markdown;base64," + b64(b"hi")) == b"hi")

# validate=True is the whole point: b64decode's default DISCARDS characters outside the alphabet,
# so a corrupted upload would decode short and park silently. Short is worse than absent.
ok, msg = raises(inbox.decode_payload, "aGVsbG8*gd29ybGQ=")
check("a corrupted payload is refused rather than silently decoded short", ok, msg)
for bad, why in [(b"bytes", "not a string"), ("", "empty"), (b64(b""), "empty after decoding")]:
    ok, _ = raises(inbox.decode_payload, bad)
    check(f"refuses a payload that is {why}", ok)
ok, _ = raises(inbox.decode_payload, b64(b"x" * 100), max_bytes=10)
check("refuses a payload over the cap", ok)

# ---- the header rewrite, asserted through the real parser -------------------
print("\n-- prepare_typed, read back by parse_props --")

p = parsed(inbox.prepare_typed(b"just a thought\nand a second line\n", "thought.md"))
check("bare prose gains a source_ref and keeps its body whole",
      p.get("source_ref") == "thought.md" and p.get("body") == "just a thought\nand a second line",
      str(p))

# The regression this module exists to prevent. Written verbatim, parse_props returns
# {'body': 'second paragraph'} — the first paragraph is gone and the note ingests anyway.
divider = b"first paragraph\n\n---\n\nsecond paragraph\n"
check("prose holding a markdown divider loses its first half when written verbatim "
      "(this is what we are fixing, not what we do)",
      parsed(divider.decode()) == {"body": "second paragraph"}, str(parsed(divider.decode())))
p = parsed(inbox.prepare_typed(divider, "notes.md"))
check("...and survives intact once the injected fence comes first",
      p.get("body") == "first paragraph\n\n---\n\nsecond paragraph", str(p))

own = b"title: Folgezettel is display-only\nauthor: human\n---\nA derived projection.\n"
p = parsed(inbox.prepare_typed(own, "dropped.md"))
check("a file that brought its own header is left alone — no source_ref is invented for it",
      "source_ref" not in p and p.get("title") == "Folgezettel is display-only"
      and p.get("body") == "A derived projection.", str(p))

front = b"---\ntitle: from an editor\nsource_ref: docs/spec.md\n---\nthe body\n"
check("YAML front matter loses its title when written verbatim",
      "title" not in parsed(front.decode()), str(parsed(front.decode())))
p = parsed(inbox.prepare_typed(front, "editor.md"))
check("...and keeps it once unwrapped",
      p.get("title") == "from an editor" and p.get("source_ref") == "docs/spec.md"
      and p.get("body") == "the body", str(p))

rule = b"---\nsome prose\n---\nmore prose\n"
p = parsed(inbox.prepare_typed(rule, "ruled.md"))
check("a leading horizontal rule is NOT front matter — unwrapping it would cost a paragraph",
      p.get("body") == "---\nsome prose\n---\nmore prose", str(p))

bom = ("﻿" + "title: x\n---\nthe body\n").encode("utf-8")
check("a BOM turns a header into a body when written verbatim",
      "title" not in parsed(bom.decode("utf-8")), str(parsed(bom.decode("utf-8"))))
p = parsed(inbox.prepare_typed(bom, "bom.md"))
check("...and the header survives once it is stripped",
      p.get("title") == "x" and p.get("body") == "the body", str(p))

ok, msg = raises(inbox.prepare_typed, b"\xff\xfe\x00\x01", "binary.md")
check("a typed drop that is not UTF-8 is refused at the door, not parked to fail later", ok, msg)
ok, _ = raises(inbox.prepare_typed, b"   \n\n  \n", "blank.md")
check("a file with nothing in it is refused — a note is its body (§6)", ok)

check("every prepared file ends in a newline",
      inbox.prepare_typed(b"no trailing newline", "x.md").endswith("\n"))

# ---- save_typed -------------------------------------------------------------
print("\n-- save_typed --")
with tempfile.TemporaryDirectory() as tmp:
    path, note_id, minted = inbox.save_typed(f"{ID}.md", b"a body\n", staging=tmp)
    check("a stem that is already a valid id keeps it — this is what makes re-dropping an "
          "exported note, and back-dating a rebuild, work",
          note_id == ID and minted is False and os.path.basename(path) == f"{ID}.md",
          os.path.basename(path))

    path, other_id, minted = inbox.save_typed("thought.txt", b"a body\n", staging=tmp)
    check("anything else is minted, and the extension is preserved",
          minted is True and other_id != ID and os.path.basename(path) == f"{other_id}.txt",
          os.path.basename(path))
    check("the minted id is a real one", notelib.validate_id(other_id) == other_id)

    path, _id, _m = inbox.save_typed("thought.rst", b"a body\n", staging=tmp)
    check("an extension staging/ does not read becomes .md", path.endswith(".md"))

    # `<id>.md` and `<id>.txt` are one note, and the second would 409 at commit.
    check("a drop already named for its id is given no source_ref — its own filename is not "
          "provenance, and a header line saying so is noise",
          parsed(open(os.path.join(tmp, f"{ID}.md"), encoding="utf-8").read()) == {"body": "a body"},
          str(parsed(open(os.path.join(tmp, f"{ID}.md"), encoding="utf-8").read())))
    check("a minted one keeps the name it arrived as",
          parsed(open(os.path.join(tmp, f"{other_id}.txt"), encoding="utf-8").read())
          .get("source_ref") == "thought.txt")

    ok, msg = raises(inbox.save_typed, f"{ID}.txt", b"a body\n", staging=tmp)
    check("an id already staged under another extension is refused, never overwritten", ok, msg)
    check("...and the file that was there is untouched",
          open(os.path.join(tmp, f"{ID}.md"), encoding="utf-8").read() == "a body\n")

    # The real proof: what was written parses as a staged file.
    names = {n for n, _p in ingest_staging.staged_files(tmp)}
    parsed_ok = []
    for n, pth in ingest_staging.staged_files(tmp):
        nid, props = ingest_staging.parse_staged(n, pth)
        parsed_ok.append(bool(props.get("body")))
    check("every file save_typed wrote parses as a staged note with a body",
          len(names) == 3 and all(parsed_ok), f"{sorted(names)}")

# A minted id must dodge processed/ and failed/ too — those ids are spent, not free.
with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "processed"))
    open(os.path.join(tmp, "processed", f"{ID}.md"), "w").close()
    _p, note_id, _m = inbox.save_typed(f"{ID}.md", b"a body\n", staging=tmp)
    check("an id spent in processed/ can still be re-dropped — refusing looks only at staging/",
          note_id == ID)

# ---- save_scan + scans_waiting ---------------------------------------------
print("\n-- save_scan / scans_waiting --")
with tempfile.TemporaryDirectory() as tmp:
    a = inbox.save_scan("card.jpg", b"\xff\xd8\xffJPEG", scans=tmp)
    b = inbox.save_scan("card.jpg", b"a second photo", scans=tmp)
    c = inbox.save_scan("card.jpg", b"a third photo", scans=tmp)
    check("a scan name already taken is suffixed, never overwritten — a filename is a label, "
          "not an identity",
          [os.path.basename(x) for x in (a, b, c)] == ["card.jpg", "card-1.jpg", "card-2.jpg"],
          str([os.path.basename(x) for x in (a, b, c)]))
    check("the bytes land exactly", open(a, "rb").read() == b"\xff\xd8\xffJPEG")

    open(os.path.join(tmp, "README.md"), "w").close()
    open(os.path.join(tmp, ".DS_Store"), "w").close()
    os.makedirs(os.path.join(tmp, "processed"))
    open(os.path.join(tmp, "processed", "old.jpg"), "w").close()

    waiting = inbox.scans_waiting(scans=tmp)
    check("scans_waiting skips README.md, dotfiles and processed/ — what the transcriber already "
          "consumed is not still waiting",
          [w["name"] for w in waiting] == ["card-1.jpg", "card-2.jpg", "card.jpg"],
          str([w["name"] for w in waiting]))
    check("each entry carries a size and a timestamp",
          all(w["bytes"] > 0 and w["modified"].endswith("Z") for w in waiting), str(waiting[0]))

check("scans_waiting on a directory that does not exist is empty, not an error",
      inbox.scans_waiting(scans="/nonexistent/scans") == [])

# ---- save(): routing by extension, not by which zone caught the file --------
print("\n-- save --")
with tempfile.TemporaryDirectory() as tmp:
    staging, scans = os.path.join(tmp, "staging"), os.path.join(tmp, "scans")
    r = inbox.save("thought.md", b64(b"a body\n"), staging=staging, scans=scans)
    check("a typed drop reports where it landed and under what id",
          r["kind"] == "typed" and r["minted"] is True
          and r["saved_as"] == f"{r['note_id']}.md" and r["dir"] == staging, str(r))

    r = inbox.save("card.jpg", b64(b"JPEG"), staging=staging, scans=scans)
    check("an image lands in staging/scans/ whichever zone caught it, and names no note",
          r["kind"] == "scan" and r["note_id"] is None and r["dir"] == scans, str(r))

    ok, _ = raises(inbox.save, "archive.zip", b64(b"x"), staging=staging, scans=scans)
    check("an unreadable extension is refused before anything is written", ok)
    check("...and nothing was written for it",
          sorted(os.listdir(scans)) == ["card.jpg"] and len(os.listdir(staging)) == 1)

lib.report_and_exit()
