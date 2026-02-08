#!/usr/bin/env python3
"""
granolocal.py - Extract Granola.ai transcripts + summaries into Markdown files.

Reads the local Granola cache and exports each meeting as a Markdown file
organized by date: output_dir/YYYY/YYYY-MM/YYYY-MM-DD - Title.md
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

CACHE_PATH = os.path.expanduser(
    "~/Library/Application Support/Granola/cache-v3.json"
)
AUTH_PATH = os.path.expanduser(
    "~/Library/Application Support/Granola/supabase.json"
)
GRANOLA_API = "https://api.granola.ai"
WORKOS_AUTH_URL = "https://api.workos.com/user_management/authenticate"
DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "granola-backup")


def load_cache(path: str) -> dict:
    with open(path) as f:
        outer = json.load(f)
    cache = json.loads(outer["cache"])
    return cache["state"]


def _api_request(endpoint: str, body: dict, access_token: str) -> any:
    """Make an authenticated POST request to the Granola API."""
    url = f"{GRANOLA_API}{endpoint}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "granolocal/1.0",
    })
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def load_auth_tokens() -> dict:
    """Load WorkOS auth tokens from Granola's local auth file."""
    with open(AUTH_PATH) as f:
        data = json.load(f)
    return json.loads(data["workos_tokens"])


def refresh_access_token(tokens: dict) -> dict:
    """Refresh the WorkOS access token using the refresh token.

    WorkOS uses refresh token rotation — the old refresh token is
    invalidated and a new one is returned. We save the updated tokens
    back to disk so the Granola app and future runs stay in sync.
    """
    # Extract client_id from the JWT issuer claim
    import base64
    jwt_payload = tokens["access_token"].split(".")[1]
    jwt_payload += "=" * (4 - len(jwt_payload) % 4)
    claims = json.loads(base64.b64decode(jwt_payload))
    client_id = claims["iss"].rstrip("/").rsplit("/", 1)[-1]

    body = json.dumps({
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }).encode()

    req = urllib.request.Request(WORKOS_AUTH_URL, data=body, headers={
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        result = json.loads(raw.decode("utf-8"))

    new_tokens = {
        **tokens,
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_in": result.get("expires_in", 21599),
        "obtained_at": int(time.time() * 1000),
    }

    # Persist so the Granola app picks up the rotated refresh token
    with open(AUTH_PATH) as f:
        auth_file = json.load(f)
    auth_file["workos_tokens"] = json.dumps(new_tokens)
    with open(AUTH_PATH, "w") as f:
        json.dump(auth_file, f)

    return new_tokens


def ensure_valid_token(tokens: dict) -> dict:
    """Return tokens with a valid (non-expired) access token, refreshing if needed."""
    obtained = tokens["obtained_at"] / 1000
    expires_at = obtained + tokens["expires_in"]
    # Refresh if less than 5 minutes remaining
    if time.time() > expires_at - 300:
        print("Access token expired, refreshing...")
        tokens = refresh_access_token(tokens)
        print("Token refreshed successfully.")
    return tokens


def fetch_transcript_from_api(doc_id: str, access_token: str) -> list:
    """Fetch transcript entries for a document from the Granola API."""
    return _api_request("/v1/get-document-transcript", {"document_id": doc_id}, access_token)


def extract_text_from_prosemirror(node: dict) -> str:
    """Recursively extract markdown-ish text from Prosemirror JSON."""
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    content = node.get("content", [])
    text = node.get("text", "")

    if node_type == "text":
        marks = node.get("marks", [])
        for mark in marks:
            mt = mark.get("type", "")
            if mt == "bold":
                text = f"**{text}**"
            elif mt == "italic":
                text = f"*{text}*"
            elif mt == "code":
                text = f"`{text}`"
            elif mt == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text

    parts = []
    for child in content:
        parts.append(extract_text_from_prosemirror(child))

    joined = "".join(parts)

    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 1)
        prefix = "#" * level
        return f"\n{prefix} {joined}\n\n"
    elif node_type == "paragraph":
        return f"{joined}\n\n"
    elif node_type == "bulletList":
        return joined
    elif node_type == "orderedList":
        return joined
    elif node_type == "listItem":
        # Indent nested content
        lines = joined.strip().split("\n")
        result = f"- {lines[0]}\n"
        for line in lines[1:]:
            if line.strip():
                result += f"  {line}\n"
        return result
    elif node_type == "blockquote":
        lines = joined.strip().split("\n")
        return "\n".join(f"> {line}" for line in lines) + "\n\n"
    elif node_type == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        return f"\n```{lang}\n{joined}\n```\n\n"
    elif node_type == "hardBreak":
        return "\n"
    elif node_type == "horizontalRule":
        return "\n---\n\n"
    elif node_type == "doc":
        return joined

    return joined


def sanitize_filename(name: str) -> str:
    """Remove characters that are problematic in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Truncate to reasonable length
    if len(name) > 80:
        name = name[:80].rsplit(" ", 1)[0]
    return name or "Untitled"


def format_transcript(entries: list) -> str:
    """Format transcript entries into readable text."""
    if not entries:
        return ""

    lines = []
    for entry in entries:
        text = entry.get("text", "").strip()
        if not text:
            continue
        timestamp = entry.get("start_timestamp", "")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M:%S")
                lines.append(f"**[{time_str}]** {text}")
            except (ValueError, TypeError):
                lines.append(text)
        else:
            lines.append(text)
    return "\n\n".join(lines)


def get_attendees(doc: dict) -> list[str]:
    """Extract attendee names/emails from a document."""
    attendees = []

    # From people field
    people = doc.get("people") or {}
    if isinstance(people, dict):
        for att in people.get("attendees", []):
            name = att.get("name") or att.get("email", "")
            if name:
                attendees.append(name)

    # From calendar event if people field is sparse
    cal = doc.get("google_calendar_event") or {}
    if not attendees and cal.get("attendees"):
        for att in cal["attendees"]:
            name = att.get("displayName") or att.get("email", "")
            if name and not att.get("self"):
                attendees.append(name)

    return attendees


def get_meeting_time(doc: dict) -> tuple[str, str]:
    """Extract start/end times from calendar event."""
    cal = doc.get("google_calendar_event") or {}
    start = cal.get("start", {}).get("dateTime", "")
    end = cal.get("end", {}).get("dateTime", "")
    return start, end


def build_markdown(doc: dict, summary_text: str, transcript_text: str) -> str:
    """Assemble the final Markdown content for a meeting."""
    title = doc.get("title") or "Untitled"
    created = doc.get("created_at", "")
    doc_type = doc.get("type", "meeting")
    notes_md = (doc.get("notes_markdown") or "").strip()
    notes_plain = (doc.get("notes_plain") or "").strip()

    attendees = get_attendees(doc)
    start_time, end_time = get_meeting_time(doc)

    sections = []

    # Header
    sections.append(f"# {title}\n")

    # Metadata
    meta_lines = []
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            meta_lines.append(f"**Date:** {dt.strftime('%Y-%m-%d %H:%M')}")
        except (ValueError, TypeError):
            meta_lines.append(f"**Date:** {created}")

    if start_time and end_time:
        try:
            st = datetime.fromisoformat(start_time)
            et = datetime.fromisoformat(end_time)
            meta_lines.append(
                f"**Time:** {st.strftime('%H:%M')} - {et.strftime('%H:%M')}"
            )
        except (ValueError, TypeError):
            pass

    if doc_type:
        meta_lines.append(f"**Type:** {doc_type}")

    if attendees:
        meta_lines.append(f"**Attendees:** {', '.join(attendees)}")

    if meta_lines:
        sections.append("\n".join(meta_lines) + "\n")

    # Summary (from panels)
    if summary_text.strip():
        sections.append("---\n")
        sections.append("## Summary\n")
        sections.append(summary_text.strip() + "\n")

    # Notes
    notes = notes_md or notes_plain
    if notes:
        sections.append("---\n")
        sections.append("## Notes\n")
        sections.append(notes + "\n")

    # Transcript
    if transcript_text.strip():
        sections.append("---\n")
        sections.append("## Transcript\n")
        sections.append(transcript_text + "\n")

    return "\n".join(sections)


class _HTMLToMarkdown(HTMLParser):
    """Simple HTML to Markdown converter for Granola summary content."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._tag_stack.append(tag)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._parts.append("\n" + "#" * level + " ")
        elif tag == "li":
            # Count nesting depth for indentation
            depth = sum(1 for t in self._tag_stack if t in ("ul", "ol")) - 1
            self._parts.append("  " * depth + "- ")
        elif tag == "p":
            pass
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href", "")
            self._parts.append(f"[")
            self._tag_stack[-1] = f"a:{href}"
        elif tag == "strong" or tag == "b":
            self._parts.append("**")
        elif tag == "em" or tag == "i":
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "blockquote":
            self._parts.append("> ")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n\n")
        elif tag == "li":
            self._parts.append("\n")
        elif tag == "p":
            self._parts.append("\n\n")
        elif tag in ("ul", "ol"):
            self._parts.append("\n")
        elif tag == "a":
            # Pop the a:href entry
            if self._tag_stack and self._tag_stack[-1].startswith("a:"):
                href = self._tag_stack[-1].split(":", 1)[1]
                self._tag_stack.pop()
                self._parts.append(f"]({href})")
                return
            self._parts.append("]")
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "blockquote":
            self._parts.append("\n")
        if self._tag_stack and self._tag_stack[-1].split(":")[0] == tag:
            self._tag_stack.pop()
        elif self._tag_stack:
            # Pop the matching tag (may not be top due to a: entries)
            for i in range(len(self._tag_stack) - 1, -1, -1):
                if self._tag_stack[i].split(":")[0] == tag:
                    self._tag_stack.pop(i)
                    break

    def handle_data(self, data):
        self._parts.append(data)

    def get_markdown(self) -> str:
        text = "".join(self._parts)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(html: str) -> str:
    """Convert HTML content to Markdown."""
    parser = _HTMLToMarkdown()
    parser.feed(html)
    return parser.get_markdown()


def _decode_js_string(s: str) -> str:
    """Decode JavaScript escape sequences without mangling existing UTF-8."""
    simple = {"\\n": "\n", "\\t": "\t", "\\r": "\r", "\\\\": "\\", '\\"': '"', "\\/": "/"}

    def replace(m):
        esc = m.group(0)
        if esc.startswith("\\u"):
            return chr(int(esc[2:], 16))
        return simple.get(esc, esc)

    return re.sub(r"\\u[0-9a-fA-F]{4}|\\[ntr\\\"/]", replace, s)


def fetch_shared_note(url: str) -> dict:
    """Fetch a shared Granola note from its public URL.

    Returns a dict with: title, created_at, creator, attendees, summary_html,
    source_url, and doc_id.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "granolocal/1.0"})
    with urllib.request.urlopen(req) as resp:
        final_url = resp.url
        html = resp.read().decode("utf-8")

    # Extract doc ID from the final URL (after redirects, e.g. /t/... -> /d/...)
    match = re.search(r"/d/([0-9a-f-]+)", final_url)
    if not match:
        raise ValueError(f"Could not extract document ID from URL: {final_url}")
    doc_id = match.group(1)

    # Extract RSC payload containing documentPanel data
    pattern = r'self\.__next_f\.push\(\[\d+,"((?:[^"\\]|\\.)*)"\]'
    payloads = re.findall(pattern, html, re.DOTALL)

    doc_data = None
    summary_html = None

    for payload in payloads:
        decoded = _decode_js_string(payload)

        # Find the payload with documentPanel
        if "documentPanel" not in decoded:
            continue

        # Extract the JSON portion - starts after the RSC prefix (e.g. "5:")
        json_start = decoded.find("[")
        if json_start == -1:
            continue

        # The content/original_content fields are references like "$1a",
        # so we need to get the HTML content separately. First parse the
        # structure for metadata.
        try:
            rsc_data = json.loads(decoded[json_start:])
        except json.JSONDecodeError:
            continue

        # Walk the RSC tree to find documentPanel props
        doc_data = _find_in_rsc(rsc_data, "documentPanel")
        break

    if not doc_data:
        raise ValueError("Could not find document data in shared note page")

    # Extract the HTML summary content from separate RSC payloads
    for payload in payloads:
        decoded = _decode_js_string(payload)
        # The summary HTML is in payloads that start with <h and contain
        # the actual content (not RSC metadata)
        stripped = decoded.strip()
        if stripped.startswith("<") and ("<h" in stripped[:20] or "<ul" in stripped[:20] or "<p" in stripped[:20]):
            summary_html = stripped
            break

    # Build result
    doc_panel = doc_data
    document = doc_panel.get("document", {})
    panel = doc_panel.get("panel", {})
    metadata = doc_panel.get("documentMetadata", {})

    attendees = []
    for att in metadata.get("attendees", []):
        details = att.get("details", {})
        person = details.get("person", {})
        name_info = person.get("name", {})
        name = name_info.get("fullName") or att.get("email", "")
        if name:
            attendees.append(name)

    creator = metadata.get("creator", {})
    creator_details = creator.get("details", {})
    creator_person = creator_details.get("person", {})
    creator_name = creator_person.get("name", {}).get("fullName") or creator.get("name") or creator.get("email", "")

    return {
        "doc_id": doc_id,
        "title": document.get("title") or metadata.get("title") or "Untitled",
        "created_at": document.get("created_at") or metadata.get("created_at", ""),
        "creator": creator_name,
        "attendees": attendees,
        "summary_html": summary_html or "",
        "source_url": url,
    }


def _find_in_rsc(obj, key: str):
    """Recursively search an RSC data structure for a dict containing the given key."""
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for v in obj.values():
            result = _find_in_rsc(v, key)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_in_rsc(item, key)
            if result:
                return result
    return None


def build_shared_markdown(note: dict) -> str:
    """Build markdown content for a shared Granola note."""
    title = note["title"]
    created = note["created_at"]
    creator = note["creator"]
    attendees = note["attendees"]
    summary_html = note["summary_html"]
    source_url = note["source_url"]

    sections = []

    # Header
    sections.append(f"# {title}\n")

    # Metadata
    meta_lines = []
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            meta_lines.append(f"**Date:** {dt.strftime('%Y-%m-%d %H:%M')}")
        except (ValueError, TypeError):
            meta_lines.append(f"**Date:** {created}")
    if creator:
        meta_lines.append(f"**Creator:** {creator}")
    if attendees:
        meta_lines.append(f"**Attendees:** {', '.join(attendees)}")
    meta_lines.append(f"**Source:** {source_url}")

    if meta_lines:
        sections.append("\n".join(meta_lines) + "\n")

    # Summary
    if summary_html:
        summary_md = html_to_markdown(summary_html)
        if summary_md:
            sections.append("---\n")
            sections.append("## Summary\n")
            sections.append(summary_md + "\n")

    return "\n".join(sections)


def save_shared_note(url: str, output_dir: str, overwrite: bool = False):
    """Fetch a shared Granola note and save it locally."""
    print(f"Fetching shared note from {url} ...")
    note = fetch_shared_note(url)

    # Parse date for directory structure
    created = note["created_at"]
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        dt = datetime.now()

    # Save under shared/YYYY/YYYY-MM/
    output = Path(output_dir)
    shared_dir = output / "shared" / dt.strftime("%Y") / dt.strftime("%Y-%m")
    shared_dir.mkdir(parents=True, exist_ok=True)

    safe_title = sanitize_filename(note["title"])
    filename = f"{dt.strftime('%Y-%m-%d')} - {safe_title}.md"

    filepath = shared_dir / filename
    if filepath.exists() and not overwrite:
        print(f"Skipped (already exists): {filepath}")
        return

    md = build_shared_markdown(note)
    filepath.write_text(md, encoding="utf-8")
    print(f"Saved: {filepath}")


def export(output_dir: str, cache_path: str = CACHE_PATH, fetch_transcripts: bool = False, overwrite: bool = False):
    print(f"Loading cache from {cache_path} ...")
    state = load_cache(cache_path)

    documents = state.get("documents", {})
    transcripts = state.get("transcripts", {})
    panels = state.get("documentPanels", {})

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    # Set up API access if fetching transcripts
    tokens = None
    if fetch_transcripts:
        if not os.path.exists(AUTH_PATH):
            print(f"Error: Auth file not found at {AUTH_PATH}")
            print("Cannot fetch transcripts without authentication.")
            sys.exit(1)
        tokens = load_auth_tokens()
        tokens = ensure_valid_token(tokens)
        print("Authenticated. Will fetch missing transcripts from API.")

    exported = 0
    skipped = 0
    with_transcript = 0
    fetched_count = 0
    fetch_errors = 0

    doc_items = list(documents.items())
    total = len(doc_items)

    for idx, (doc_id, doc) in enumerate(doc_items):
        # Skip deleted documents
        if doc.get("deleted_at"):
            skipped += 1
            continue

        title = doc.get("title") or "Untitled"
        created = doc.get("created_at", "")

        # Parse date for directory structure
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now()

        # Check if file already exists before doing expensive work
        safe_title = sanitize_filename(title)
        month_dir = output / dt.strftime("%Y") / dt.strftime("%Y-%m")
        filepath = month_dir / f"{dt.strftime('%Y-%m-%d')} - {safe_title}.md"
        if filepath.exists() and not overwrite:
            skipped += 1
            continue

        # Build summary from panels
        summary_text = ""
        doc_panels = panels.get(doc_id, {})
        if isinstance(doc_panels, dict):
            # Get summary panels sorted by creation date (latest first)
            summary_panels = sorted(
                (
                    p
                    for p in doc_panels.values()
                    if isinstance(p, dict) and p.get("title") == "Summary"
                ),
                key=lambda p: p.get("created_at", ""),
                reverse=True,
            )
            if summary_panels:
                # Use the most recent summary
                content = summary_panels[0].get("content", {})
                summary_text = extract_text_from_prosemirror(content)

        # Build transcript — use cached first, fetch from API if missing
        transcript_entries = transcripts.get(doc_id, [])
        if not transcript_entries and fetch_transcripts and tokens:
            try:
                # Refresh token if needed (check every request)
                tokens = ensure_valid_token(tokens)
                transcript_entries = fetch_transcript_from_api(
                    doc_id, tokens["access_token"]
                )
                if transcript_entries:
                    fetched_count += 1
                # Rate limit: ~4 req/sec to stay under the 5/sec limit
                time.sleep(0.25)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    pass  # No transcript exists for this document
                else:
                    fetch_errors += 1
                    print(f"  API error ({e.code}) for '{title}', skipping transcript")
            except Exception as e:
                fetch_errors += 1
                print(f"  Error fetching transcript for '{title}': {e}")

        transcript_text = format_transcript(transcript_entries)

        # Skip docs with no meaningful content
        notes_md = (doc.get("notes_markdown") or "").strip()
        notes_plain = (doc.get("notes_plain") or "").strip()
        if not any([summary_text.strip(), notes_md, notes_plain, transcript_text]):
            skipped += 1
            continue

        # Build the markdown
        md = build_markdown(doc, summary_text, transcript_text)

        month_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_text(md, encoding="utf-8")
        exported += 1
        if transcript_text:
            with_transcript += 1

        # Progress indicator
        if fetch_transcripts and (idx + 1) % 50 == 0:
            print(f"  Progress: {idx + 1}/{total} documents processed...")

    print(f"\nDone! Exported {exported} documents ({with_transcript} with transcripts), skipped {skipped}.")
    if fetch_transcripts:
        print(f"Fetched {fetched_count} transcripts from API ({fetch_errors} errors).")
    print(f"Output: {output}")


def print_help():
    print("""granolocal - Export Granola.ai meetings to local Markdown files.

Usage:
  python3 granolocal.py                          Export all local meetings
  python3 granolocal.py --fetch-transcripts       Export and fetch missing transcripts from API
  python3 granolocal.py --output /some/path      Export to a custom directory
  python3 granolocal.py --url <url> [--url ...]  Download shared note(s)

Options:
  --fetch-transcripts  Fetch missing transcripts from Granola API
  --url <url>          Granola shared note URL (https://notes.granola.ai/d/...)
  --output <dir>       Output directory (default: ./granola-backup/)
  --overwrite          Overwrite existing files (default: skip)
  --help, -h           Show this help message

Local export saves to:  output_dir/YYYY/YYYY-MM/YYYY-MM-DD - Title.md
Shared notes save to:   output_dir/shared/YYYY/YYYY-MM/YYYY-MM-DD - Title.md""")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print_help()
        sys.exit(0)

    output_dir = DEFAULT_OUTPUT_DIR
    urls = []
    fetch_transcripts = False
    overwrite = False

    i = 0
    while i < len(args):
        if args[i] in ("--output", "-o") and i + 1 < len(args):
            output_dir = args[i + 1]
            i += 2
        elif args[i] == "--url" and i + 1 < len(args):
            urls.append(args[i + 1])
            i += 2
        elif args[i] == "--fetch-transcripts":
            fetch_transcripts = True
            i += 1
        elif args[i] == "--overwrite":
            overwrite = True
            i += 1
        else:
            print(f"Unknown argument: {args[i]}")
            print("Run with --help for usage.")
            sys.exit(1)

    if urls:
        # Fetch shared notes
        for url in urls:
            try:
                save_shared_note(url, output_dir, overwrite=overwrite)
            except Exception as e:
                print(f"Error fetching {url}: {e}")
    else:
        # Default: export local cache
        if not os.path.exists(CACHE_PATH):
            print(f"Error: Granola cache not found at {CACHE_PATH}")
            print("Make sure Granola is installed and has been used at least once.")
            sys.exit(1)
        export(output_dir, fetch_transcripts=fetch_transcripts, overwrite=overwrite)


if __name__ == "__main__":
    main()
