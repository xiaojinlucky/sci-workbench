# Third-Party Notices

This Skill package contains original pure-Python standard library scripts and Markdown documentation written for offline scientific LaTeX authoring and compilation verification.

At runtime, the scripts invoke standard system-installed command-line utilities when present in the evaluation container:
- TeX Live (`latexmk`, `pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`) or Tectonic
- Poppler utilities (`pdfinfo`, `pdftotext`, `pdffonts`, `pdftoppm`)

No third-party binaries, fonts, proprietary templates, or external Python libraries are bundled inside this package.
