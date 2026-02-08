# granolocal

Extract Granola.ai meeting transcripts + summaries from the local cache into Markdown files.

## Usage

```bash
python3 granolocal.py              # exports to ./granola-backup/
python3 granolocal.py /some/path   # exports to custom directory
```

Requires Python 3.9+ (no external dependencies).

## Output

Files are organized as `YYYY/YYYY-MM/YYYY-MM-DD - Meeting Title.md`, each containing metadata, AI summary, notes, and transcript.
