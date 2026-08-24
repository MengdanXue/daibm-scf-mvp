# Embedded defense fonts

These repository assets make the two defense PDFs independent of host fonts.

- `DejaVuSans.ttf` is the DejaVu Sans 2.37 regular face, SHA-256
  `3fdf69cabf06049ea70a00b5919340e2ce1e6d02b0cc3c4b44fb6801bd1e0d22`.
  Its license is preserved in `LICENSE-DejaVu.txt`.
- `NotoSansSC-DefenseSubset.ttf` is a 99,844-byte subset of Google Fonts'
  `NotoSansSC[wght].ttf` at commit
  `ec626514f79f831f1ab848a82114a0ce7e2d6372`. The upstream file SHA-256 is
  `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`;
  the committed subset SHA-256 is
  `d1e3076356cc9d258189d7aa5ec7a40ca9d31deb528457ca391feb30fa4445b9`.
  Its SIL Open Font License 1.1 is preserved in `OFL-NotoSansSC.txt`.

The CJK subset contains the glyphs used by `docs/defense-one-page.md`. It was
created with FontTools 4.63.0 by pinning `wght=400` with
`fonttools varLib.instancer --update-name-table --no-recalc-timestamp`, then
running `pyftsubset --text-file=docs/defense-one-page.md --layout-features='*'
--glyph-names --symbol-cmap --legacy-cmap --notdef-glyph --notdef-outline
--recommended-glyphs --name-IDs='*' --name-legacy --name-languages='*'
--no-recalc-timestamp --canonical-order`.
