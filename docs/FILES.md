# Local folders and document attachments

The harness **Files** view grants read-only access to specific local folders. Nothing
is granted by default. Enter an absolute folder path and choose **Allow folder**.
Browse the folder, open subfolders, find names within the current directory, and preview
bounded excerpts. **Remove access** revokes the grant without changing files on disk.
Drive roots, the home folder itself, network shares, symbolic links and junctions are
rejected. Use a specific project or document folder and its real local path.

In Chat, expand **Files for this chat**. Select both folders and the tools you want:
List folders, Search file names, and Read file contents. New chats start with no file
access. The backend enforces both selections on every call. The model's file calls and
results use the existing visible tool activity blocks. Selected folders do not get
copied into the prompt: only requested excerpts enter the conversation. Names and
page, sheet/row, slide or text-offset labels accompany the content.

The paperclip also accepts documents directly. Attachment extraction is local and
serial; Send waits until it finishes. Changing chats or leaving the chat cancels queued
attachments and prevents late results from attaching to another chat. Direct image
attachments retain the existing model vision behavior. You can also attach a previewed
file excerpt from Files. Browsing, extraction and folder management work while model
power is Off and never load the model; sending the resulting chat still requires On.

## Install optional readers

From this checkout, in the Python environment used to launch the server:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-files.txt
```

The optional pins are pypdf 6.21.1, python-docx 1.2.0, openpyxl 3.1.5,
python-pptx 1.0.2, striprtf 0.0.33 and defusedxml 0.7.1. These do not automate
Microsoft Office, download models, use the GPU, or add an OCR service. Plain text/code,
folder browsing and existing images still work without the optional document parsers.
Missing readers produce a setup message when a document is opened.

| Format | Content and paging |
| --- | --- |
| PDF | Extractable page text; at most 8 pages per read, 2,000 pages per document |
| DOCX | Body paragraphs and tables in document order; up to 100 blocks per read |
| XLSX | Select a sheet; up to 100 rows and 100 columns per read, first 100,000 rows accessible; formulas shown as text |
| CSV / TSV | Up to 100 rows and 100 columns per read; UTF-8 or UTF-16 with BOM |
| PPTX | Text frames and tables; up to 10 slides per read |
| RTF | Plain text, up to 24,000 characters per range |
| Text / code / structured data | UTF-8 or UTF-16 with BOM; local files page by byte offset, at most 24,000 source bytes per read |
| Images | Attach directly for model vision; folder text tools do not decode images |

Uploads and rich-document sources are limited to 16 MiB. Text uploads are limited to
512 KiB and yield at most 24,000 characters; add their folder to continue reading larger
text files. Rich parser output is capped at 24,000 characters per range. A clipped
individual page/block may omit remaining text in that block; split such documents into
smaller parts when full text is required. Use the returned `next_offset` to continue
between ranges. Directory pages contain at most 100 entries and inspect at most 10,000
entries; very large directories should be divided or narrowed. Search matches filename
substrings in one directory at a time, with no background indexing or recursive walk.
Directory changes between requests may affect page ordering.

Empty or scanned PDFs report that OCR is unavailable. Legacy DOC/XLS/PPT, encrypted
documents, embedded Office binaries/macros, executable/binary files, and arbitrary
archives are unsupported. Layout, charts, diagrams, embedded objects, headers, footers,
speaker notes and formatting are not promised as extracted text. No macros or formulas
are run, and no external document relationships or URLs are fetched.

## Boundaries and storage

The root grants are saved in ignored `<harness_data_dir>/files.json`. This file contains
local folder names/paths; keep it private. Tool access uses relative paths only. Paths
cannot contain drive letters, parent traversal, UNC/device paths or alternate streams.
Each operation opens through non-link directory descriptors on POSIX or pinned,
non-reparse directories on Windows, and verifies the final Windows file handle before
reading. Removing a grant waits until active file requests finish.

Rich parsing runs in a disposable Python process with a fixed command and byte input
over stdin, rooted in the trusted checkout, never in a selected folder. It establishes
a 512 MiB process memory limit and 10-second CPU limit before reading the payload;
the parent imposes a 15-second wall timeout. The worker fails closed if its OS resource
limits cannot be installed. Office ZIPs stay in memory and are checked before parsing:
at most 2,000 entries, 32 MiB total expanded data, 8 MiB per entry, bounded compression
ratio, and XML without DTDs/entities/external references. These limits can reject some
legitimate large/complex documents; split or export them to text for reading.

No uploaded file is saved on the server. Parsed attachment contents stay transient in
the current chat, like upstream text attachments, and must be reattached after reload
or switching saved chats. **File tool excerpts and their arguments/results are saved
with browser chat history**, like other tool activity, and may be included in Markdown
exports. Choose folders with that persistence in mind. Removing access prevents future
reads; it does not erase existing chats. Delete those chats separately when needed.
All file mutation/read/extraction endpoints use the harness's same-origin JSON, Host
and optional API-key checks. File endpoints and native tools exist only in harness mode.
Documents and filenames are untrusted data, not a grant of new permissions.

## API and verification

`GET /files` returns configured roots and available native tool names. `POST /files`
accepts `action: add` with `path`, `remove` with `root`, or `list/search/read` with `root`,
relative `path`, `offset`, `count`, and optional `query`/`sheet`. Offsets start at zero;
read offset units are listed above. `POST /files/extract` accepts `name` (basename only)
and base64 `data`, plus optional range and sheet fields. Neither endpoint accepts a URL
or an arbitrary server file path for extraction.

Chat requests can include:

```json
{"strata_files":{"roots":["r_0123456789ab"],"tools":["files__list","files__read"]}}
```

Use actual IDs returned by `/files`. Root/tool choices are independent of MCP connection
choices and do not require installing an MCP server.

```powershell
python -m unittest serve.test_files -v
node --check serve/web/files.js
node serve/test_files_ui.js
```

Tests create synthetic fixtures in temporary directories. They exercise actual local
parsers, path/ADS/device/traversal/link rejection, grant revocation, pagination, faithful
UTF-8/UTF-16 continuation, Office expansion/XML/macros limits, malformed/timeout failures,
Power Off extraction, HTTP permission checks, and a native file call through the real
streaming tool loop into the next model prompt. The Node check defers requests to prove
serial attachment submission, stale-result cancellation, Power Off Send behavior and
the shared image attachment lifecycle. CI runs these on Windows and Linux without a
GPU, Office installation, model download, engine build or private folder access.
