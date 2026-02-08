#!/usr/bin/env python3
"""
granolocal.py - Extract Granola.ai transcripts + summaries into Markdown files.

Reads the local Granola cache and exports each meeting as a Markdown file
organized by date: output_dir/YYYY/YYYY-MM/YYYY-MM-DD - Title.md
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

CACHE_PATH = os.path.expanduser(
    "~/Library/Application Support/Granola/cache-v3.json"
)
DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "granola-backup")


def load_cache(path: str) -> dict:
    with open(path) as f:
        outer = json.load(f)
    cache = json.loads(outer["cache"])
    return cache["state"]


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


def export(output_dir: str, cache_path: str = CACHE_PATH):
    print(f"Loading cache from {cache_path} ...")
    state = load_cache(cache_path)

    documents = state.get("documents", {})
    transcripts = state.get("transcripts", {})
    panels = state.get("documentPanels", {})

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    exported = 0
    skipped = 0

    for doc_id, doc in documents.items():
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

        # Build transcript
        transcript_entries = transcripts.get(doc_id, [])
        transcript_text = format_transcript(transcript_entries)

        # Skip docs with no meaningful content
        notes_md = (doc.get("notes_markdown") or "").strip()
        notes_plain = (doc.get("notes_plain") or "").strip()
        if not any([summary_text.strip(), notes_md, notes_plain, transcript_text]):
            skipped += 1
            continue

        # Build the markdown
        md = build_markdown(doc, summary_text, transcript_text)

        # Create directory: YYYY/YYYY-MM/
        year_dir = output / dt.strftime("%Y")
        month_dir = year_dir / dt.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        # Filename: YYYY-MM-DD - Title.md
        safe_title = sanitize_filename(title)
        filename = f"{dt.strftime('%Y-%m-%d')} - {safe_title}.md"

        # Handle duplicates (multiple meetings same day, same title)
        filepath = month_dir / filename
        counter = 2
        while filepath.exists():
            filename = f"{dt.strftime('%Y-%m-%d')} - {safe_title} ({counter}).md"
            filepath = month_dir / filename
            counter += 1

        filepath.write_text(md, encoding="utf-8")
        exported += 1

    print(f"Done! Exported {exported} documents, skipped {skipped}.")
    print(f"Output: {output}")


def main():
    if len(sys.argv) > 1:
        output_dir = sys.argv[1]
    else:
        output_dir = DEFAULT_OUTPUT_DIR

    if not os.path.exists(CACHE_PATH):
        print(f"Error: Granola cache not found at {CACHE_PATH}")
        print("Make sure Granola is installed and has been used at least once.")
        sys.exit(1)

    export(output_dir)


if __name__ == "__main__":
    main()
