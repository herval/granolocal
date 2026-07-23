# granolocal

Extract Granola.ai meeting transcripts + summaries into Markdown files. Also supports downloading shared notes from public Granola URLs.

Granola encrypts its local files (`cache-v6.json.enc`, `granola.db`, …) with a key held in the macOS Keychain that only Granola's signed app can read, so this tool authenticates as its own client and reads everything from the Granola API instead. Log in once; the token is cached in `~/.granolocal/auth.json` and refreshed automatically.

## Usage

```bash
# Log in to Granola (one-time). Opens a browser flow; paste the redirect URL back.
python3 granolocal.py login

# Export all meetings (documents + summaries + transcripts)
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

Requires Python 3.10+ (standard library only, no external dependencies).

## Login

`login` reproduces Granola's WorkOS sign-in: it prints an auth URL, you open it and log in with Google, then paste the final `granola.ai` redirect URL (which contains a `code=…`) back into the terminal. The tool exchanges that code for its own WorkOS tokens and stores them in `~/.granolocal/auth.json` (mode 600). The refresh token rotates automatically, so you normally only log in once.

## Output

Exports are organized as `YYYY/YYYY-MM/YYYY-MM-DD - Meeting Title.md`, each containing metadata, AI summary, notes, and transcript.

Shared notes are saved under `shared/YYYY/YYYY-MM/YYYY-MM-DD - Title.md`, including creator, attendees, summary, and a link back to the source.
