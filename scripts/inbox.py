#!/usr/bin/env python3
"""inbox — the two drop zones, and what it takes to put a file in one (spec §4, §6).

`staging/` takes typed notes; `staging/scans/` takes photographs of handwritten ones. Both were filled
entirely out-of-band until now — by a shell, an editor, or the transcribe-notes skill — and this
module is what lets the graph UI drop a file into either without a terminal.

**An upload is not an operation.** This module writes a file and nothing else: no note exists, no
`Op` is logged, and nothing reaches the graph until `ingest-staging.sh` commits it (workflow 2) or
a person transcribes the image. That is why it is DB-free, and why it can be tested without one.

Two things here are less obvious than they look:

  * **the filename is the note's identity** (staging/README.md), so a drop already named for a real
    id keeps it, and only anything else gets a fresh one. That is what makes re-dropping an
    exported note — or rebuilding a corpus with its real history — work at all.
  * **`source_ref` may only be injected behind a fence.** See needs_source_ref; the parser is
    unforgiving in a way that loses text silently.
"""
import base64
import os
import re
import time

import ingest_staging
import new_id
import notelib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = ingest_staging.DEFAULT_STAGING       # one definition of where typed notes land
SCANS = os.path.join(REPO, "staging", "scans")

TYPED_EXTS = ingest_staging.EXTS               # (".md", ".txt", ".note")
SCAN_EXTS = (".png", ".jpg", ".jpeg", ".pdf")  # what staging/scans/README.md already promises

MAX_BYTES = 16 << 20        # 16 MiB decoded — a phone photo of a notecard, with room for a scan
MAX_NAME = 200
BOM = "﻿"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DATA_URI = re.compile(r"^data:[^,]*;base64,", re.I)


def safe_name(name):
    """The plain filename an upload may be written under, or ValueError.

    The name arrives from a browser and names a path we are about to open for writing, so it is
    checked rather than trusted. Both separators are refused, not just this platform's: the server
    runs under WSL and the browser is often on Windows, so a backslash here is a path there.
    A leading dot is refused too — `staged_files` skips dotfiles, so such a drop would land in
    `staging/` and then be invisible to every reader of it.
    """
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    name = name.strip()
    if not name:
        raise ValueError("a dropped file needs a name")
    if len(name) > MAX_NAME:
        raise ValueError(f"filename is too long: {len(name)} chars, and the cap is {MAX_NAME}")
    if "/" in name or "\\" in name or _CONTROL.search(name):
        raise ValueError(f"filename may not hold a path separator or a control character: {name!r}")
    if name.startswith("."):
        raise ValueError(f"filename may not start with a dot: {name!r}")
    if name != os.path.basename(name):
        raise ValueError(f"not a plain filename: {name!r}")
    return name


def kind_of(name):
    """'typed' or 'scan', from the extension alone, or ValueError.

    The extension is the whole routing rule. The page offers two drop zones, but either accepts
    anything and this is what decides — so a photo dropped on the typed zone still lands in
    `staging/scans/` rather than being refused, and the reply says where it went.
    """
    ext = os.path.splitext(name)[1].lower()
    if ext in TYPED_EXTS:
        return "typed"
    if ext in SCAN_EXTS:
        return "scan"
    raise ValueError(f"nothing here reads {ext or 'a file with no extension'} — typed notes are "
                     + ", ".join(TYPED_EXTS) + "; handwritten ones are " + ", ".join(SCAN_EXTS))


def decode_payload(data, max_bytes=MAX_BYTES):
    """The raw bytes behind the base64 a drop arrives as, or ValueError.

    `validate=True` is the point of this function. b64decode's default **discards** every
    character outside the alphabet, so a truncated or corrupted upload decodes to something
    shorter and parks silently — a note that quietly lost its second half is worse than one that
    never arrived. Whitespace is stripped first so a hand-rolled `base64 -w 76 | curl` still
    works, and a `data:` prefix is tolerated because that is the shape FileReader hands the page.
    """
    if not isinstance(data, str):
        raise ValueError("data must be a base64 string")
    data = _DATA_URI.sub("", "".join(data.split()))
    if not data:
        raise ValueError("the file is empty")
    if len(data) // 4 * 3 > max_bytes:       # refuse on the encoded length, before allocating
        raise ValueError(f"file is too large: the cap is {max_bytes} bytes")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as e:                  # binascii.Error subclasses ValueError
        raise ValueError(f"data is not valid base64: {e}")
    if not raw:
        raise ValueError("the file is empty")
    if len(raw) > max_bytes:
        raise ValueError(f"file is too large: {len(raw)} bytes, and the cap is {max_bytes}")
    return raw


# ---- the staged-file header (spec §4, staging/README.md) --------------------
def _has_header_key(lines):
    """Would parse_props read any of these lines as a `key:` line? Asked of ingest_staging's own
    KEY_RE and RECOGNIZED set, so this cannot drift from the parser it is predicting."""
    for raw in lines:
        m = ingest_staging.KEY_RE.match(raw)
        if m and m.group(1).lower().replace("-", "_") in ingest_staging.RECOGNIZED:
            return True
    return False


def _head(text):
    """The lines parse_props inspects for header keys — everything before the first lone `---`."""
    head = []
    for raw in text.splitlines():
        if raw.strip() == "---":
            break
        head.append(raw)
    return head


def unwrap_front_matter(text):
    """`---` / keys / `---` / body -> keys / `---` / body.

    YAML front matter is what an editor writes and what parse_props gets wrong: the *opening*
    fence is the fence as far as it is concerned, so the keys land inside the body and the title
    is lost. Dropping the opening line leaves the same note said the way staging/ says it.

    Only when the block actually holds a recognized key. `---` / prose / `---` / prose is a
    markdown horizontal rule at the top of a document, and unwrapping that one would move the
    fence down and cost the first paragraph.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:]) if _has_header_key(lines[1:i]) else text
    return text


def needs_source_ref(text):
    """True when parse_props would find no header key at all — the file is bare prose, and a
    `source_ref` may be added in front of it.

    The rule is this narrow because the parser is unforgiving in two directions at once:

      * it folds every unrecognized line into the **current** key, so prepending `source_ref:` to
        a headerless file makes the entire body the source_ref and leaves no `body` — which
        `Note.body` being MANDATORY then refuses at ingest.
      * on reaching a lone `---` it **overwrites** whatever it collected, so a prose file holding
        a markdown divider silently loses everything above that divider.

    The second is why injecting is not merely safe here but *safer than leaving the file alone*:
    the fence we add is the first one, so the whole original becomes the body intact.
    """
    return not _has_header_key(_head(text))


def prepare_typed(raw, source_ref=None):
    """The text to write for a typed drop: decoded, BOM-stripped, unwrapped, and given a
    `source_ref` when one is offered and the file has no header of its own.

    A file that brought its own header keeps it untouched — the author already said what they
    meant, including if they meant to leave source_ref out. And `source_ref` is None whenever the
    drop was already named for its id: a note whose provenance is its own filename has not
    recorded anything, and a header line saying so is noise in every reader of it.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"a typed note has to be UTF-8 text ({e})")
    if text.startswith(BOM):
        text = text[len(BOM):]      # KEY_RE is anchored, so a BOM turns a header into a body
    text = unwrap_front_matter(text)
    if not text.strip():
        raise ValueError("the file is empty — a note is its body, and that is the whole of it")
    if source_ref and needs_source_ref(text):
        text = f"source_ref: {source_ref}\n---\n{text}"
    return text if text.endswith("\n") else text + "\n"


# ---- writing into the two inboxes ------------------------------------------
def save_typed(name, raw, staging=None):
    """Write a typed drop into `staging/` under a valid note id. Returns (path, note_id, minted).

    A stem that is already a spec §4 id is kept verbatim; anything else is minted fresh and the
    name it arrived as survives as the source_ref (see prepare_typed).
    """
    staging = staging or STAGING
    name = safe_name(name)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in TYPED_EXTS:
        ext = ".md"
    try:
        note_id, minted = notelib.validate_id(stem), False
    except ValueError:
        note_id, minted = new_id.mint(1, staging)[0], True

    # The id is the identity, so the collision that matters is an id already staged under **any**
    # extension — `<id>.md` and `<id>.txt` name one note and the second would 409 at commit.
    # Only `staging/` is consulted: `processed/` and `failed/` hold ids that have had their turn,
    # and refusing on those would break exactly the re-drop the branch above exists to allow.
    # (Minting checks all three, which is a different question: what is free to take.)
    for other, _path in ingest_staging.staged_files(staging):
        if ingest_staging.id_from_filename(other) == note_id:
            raise ValueError(f"{other} is already staged for {note_id} — ingest it, or move it "
                             "aside, before dropping another")

    # The name is only worth recording when it said something the id does not: a drop already
    # named `<id>.md` would otherwise be given its own filename as its provenance.
    text = prepare_typed(raw, source_ref=name if minted else None)
    os.makedirs(staging, exist_ok=True)
    path = os.path.join(staging, note_id + ext)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path, note_id, minted


def save_scan(name, raw, scans=None):
    """Park an image in `staging/scans/` and return its path. Nothing else happens to it.

    Turning a photograph into notes needs a person and a model reading the page together, and the
    ratification step in the middle of that is the whole point of it (see the transcribe-notes
    skill). So this parks the file and says so, rather than pretending the browser did the reading.

    A name already taken gets a `-1` suffix instead of overwriting: a scan filename is a label,
    not an identity, and two photos both called `card.jpg` are two photos.
    """
    scans = scans or SCANS
    name = safe_name(name)
    os.makedirs(scans, exist_ok=True)
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(scans, name), 0
    while os.path.exists(path):
        n += 1
        path = os.path.join(scans, f"{stem}-{n}{ext}")
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


def scans_waiting(scans=None):
    """Images sitting in `staging/scans/`: [{name, bytes, modified}], by name.

    Mirrors ingest_staging.staged_files — dotfiles and README.md are not input, and `processed/`
    is a subdirectory, so what the transcribe-notes skill already consumed does not show up as
    still waiting. Nothing is filtered by extension: a file somebody put here should be visible
    here, and calling it unreadable is the transcriber's judgement to make, not this function's.
    """
    scans = scans or SCANS
    if not os.path.isdir(scans):
        return []
    out = []
    for name in sorted(os.listdir(scans)):
        path = os.path.join(scans, name)
        if not os.path.isfile(path) or name.startswith(".") or name.lower() == "readme.md":
            continue
        st = os.stat(path)
        out.append({"name": name, "bytes": st.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))})
    return out


def save(name, data, staging=None, scans=None, max_bytes=MAX_BYTES):
    """Route one dropped file to the inbox its extension names, write it, and say what happened:

        {kind, name, saved_as, dir, note_id, minted}

    The single entry point the HTTP layer calls, so that routing, validation and the two write
    paths have one definition between them rather than one each side of the wire.
    """
    name = safe_name(name)
    kind = kind_of(name)
    raw = decode_payload(data, max_bytes=max_bytes)
    if kind == "typed":
        path, note_id, minted = save_typed(name, raw, staging=staging)
    else:
        path, note_id, minted = save_scan(name, raw, scans=scans), None, False
    return {"kind": kind, "name": name, "saved_as": os.path.basename(path),
            "dir": os.path.dirname(path), "note_id": note_id, "minted": minted}
