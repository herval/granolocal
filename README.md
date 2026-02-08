# granolocal

Extract Granola.ai meeting transcripts + summaries from the local cache into Markdown files. Also supports downloading shared notes from public Granola URLs.

## Usage

```bash
# Export all local meetings
python3 granolocal.py

# Export to a custom directory
python3 granolocal.py --output /some/path

# Download a shared Granola note
python3 granolocal.py --url https://notes.granola.ai/d/<id>

# Download multiple shared notes
python3 granolocal.py --url https://notes.granola.ai/d/<id1> --url https://notes.granola.ai/d/<id2>

# Download shared note to a custom directory
python3 granolocal.py --url https://notes.granola.ai/d/<id> --output /some/path
```

Requires Python 3.9+ (no external dependencies).

## Output

Local exports are organized as `YYYY/YYYY-MM/YYYY-MM-DD - Meeting Title.md`, each containing metadata, AI summary, notes, and transcript.

Shared notes are saved under `shared/YYYY/YYYY-MM/YYYY-MM-DD - Title.md`, including creator, attendees, summary, and a link back to the source.
