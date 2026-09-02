# Run TheAppliedScientist on your paper

Create one JSON file for the paper. It can name local files and directories by
absolute path or by a path relative to the JSON file. The runner copies them to
`/app/inputs/` for both Docker and native runs. The originals are not changed.

For example:

```json
"Paper": {
  "local_path": "/path/to/paper/main.tex",
  "code_path": "/path/to/code"
}
```

## Required JSON fields

| Field | Type | What to provide |
|---|---|---|
| `Name` | string | A short identifier using letters, numbers, dots, hyphens, or underscores |
| `Title` | string | The paper title |
| `Task` | string | What to reproduce, test, improve, and deliver |

The `Task` should name the paper and code paths, important experiments and
metrics, time or hardware limits, and the required final result.

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

Point the JSON to the review file:

```json
"Human_Reviews_From_OpenReview": {
  "source_file": "/path/to/reviews.md"
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

The runner checks the JSON automatically before starting. Invalid JSON, missing
required fields, invalid names, and `Human_Reviews_From_OpenReview: true` are
rejected with a clear error.
