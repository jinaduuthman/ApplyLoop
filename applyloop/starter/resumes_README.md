# resumes/

Put your base CVs here (this folder's contents are gitignored, except this file).

- One CV per track in `config/applyloop.yml` (`tracks[].cv`), e.g. `data_engineer.tex`.
- LaTeX (`.tex`) is preferred: applyloop tailors it per job and recompiles it (needs `pdflatex`).
- Also add a compiled PDF as `fallback_pdf`, used when LaTeX isn't installed.
- Plain PDF CVs work too; they're sent as-is.

File names recruiters see come from `config/profile.yml → candidate.full_name`:
`output/cvs/JaneSmith_DE_Base.pdf`, tailored ones in `output/cvs/<job>-<company>/JaneSmith_DE.pdf`.
