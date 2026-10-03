# Run TheAppliedScientist on your paper

[Folder layout and runnable example](../README.md#add-your-paper)

The configuration file lives inside `components/ai-scientist/user-ideas/` in
your repository checkout. You can keep the inputs beside it:

```text
user-ideas/
├── my-paper.json
└── my-paper/
    ├── paper-source/     # complete LaTeX project, including figures and bibliography
    ├── code/             # experiment code
    └── reviews.md        # all existing review text
```

Paths are relative to `my-paper.json`. For this layout, `my-paper/reviews.md`
names the review file shown above. Absolute paths work too, so your inputs
can remain elsewhere on the machine.

The runner copies the inputs into each job's workspace for both Docker and
native runs. The originals are not changed.

For a LaTeX project, pass the whole source directory so that figures, bibliography,
styles, and files included by `\input` are copied too. For example:

```json
"Paper": {
  "local_path": "my-paper/paper-source",
  "code_path": "my-paper/code"
}
```

## Required JSON fields

| Field | Type | What to provide |
|---|---|---|
| `Name` | string | A short identifier using letters, numbers, dots, hyphens, or underscores |
| `Title` | string | The paper title |
| `Task` | string | What to reproduce, test, improve, and deliver |

Use `Task` to describe the important experiments and metrics, time or hardware
limits, and the required final result. Paths in `Paper` are staged automatically.
Code is optional but strongly recommended for reproducing and improving results.

For a self-contained TeX file or PDF, `local_path` can name that file instead of
a directory. Source files are preferable when the Scientist will revise the paper.

## Useful optional fields

| Field | Type | What to provide |
|---|---|---|
| `Paper` | object, string, or `false` | A local path, public URL, identifier, or short inline paper |
| `Human_Reviews_From_OpenReview` | object, array, string, or `false` | Existing public reviews, or `false` when none exist |
| `What_NOT_To_Do` | string or array | Scope and research-integrity rules |
| `HOW_TO_RUN_EXPERIMENTS` | string | Known setup and evaluation commands |
| `GPU_Needed` | boolean | Whether the work needs a GPU |
| `GPU_Note` | string | Expected memory, model size, or CPU fallback |
| `_meta` | object | Optional source details such as paper ID or code URL |

Extra fields are allowed and passed to the Scientist unchanged.

## Paper with public reviews

Paste your existing reviews into `my-paper/reviews.md` next to the JSON as
shown above. Plain text in a Markdown file is sufficient; no special format is
required. Then point the JSON to that file:

```json
"Human_Reviews_From_OpenReview": {
  "source_file": "my-paper/reviews.md"
}
```

You may instead place reviews directly in the JSON as separate objects, a list,
or plain text. See [with-public-reviews.json](../components/ai-scientist/examples/ideas/with-public-reviews.json).

## Paper without public reviews

Use:

```json
"Human_Reviews_From_OpenReview": false
```

Do not add placeholder reviews. Tell the Scientist to understand the paper,
reproduce its main result, use Search to find important gaps, make measured
improvements, and submit each revision to the AI Reviewer. See
[without-public-reviews.json](../components/ai-scientist/examples/ideas/without-public-reviews.json).

## Public paper and code URLs

Local files are simplest, but public URLs also work:

```json
"Paper": {
  "paper_url": "https://arxiv.org/abs/2401.01234",
  "source_url": "https://arxiv.org/e-print/2401.01234",
  "code_url": "https://github.com/example/project"
}
```

Repeat the required download and reading steps in `Task`. Source archives are
better than PDFs when the Scientist must revise the LaTeX.

## Inline content

Short abstracts, reviews, and constraints can be stored directly in JSON
strings. Newlines must be escaped as `\n`. Keep long LaTeX in a separate file,
and never place credentials in the JSON or its referenced inputs.

## Start the run

```bash
./tas run components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600 --gpus 1
```

Omit `--gpus 1` for Docker CPU runs. For native mode, use:

```bash
./tas run --runtime native components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600
```

Native mode uses host devices directly and does not accept `--gpus`.

The runner checks the JSON automatically before starting. Invalid JSON, missing
required fields, invalid names, and `Human_Reviews_From_OpenReview: true` are
rejected with a clear error.
