# Evidence manifest index

This directory contains the final evidence **manifests** for the accepted MVP.
Raw guest logs, binaries and large bundles remain in the original development
repository / release archive; this clean repository keeps the hash index small.

**Source snapshot:** `753c0973c4eeac614fcc457dd327fd93ecf6a119`
**Final tag in original repo:** `mvp-2026-10-31` (annotated object
`9d6dba82a92681990fb0cff18c17cdb021805aab`).

## Manifests

| File | Evidence package | Entries | Manifest SHA-256 |
|---|---|---|---|
| `P4-SHA256SUMS` | Gate 4 P4 | 26 | `e4b81b34df9b2707542fe2158f9f76ce5760a675e6494d82df85994f669e6c5e` |
| `P3A-SHA256SUMS` | Route A v6 | 26 | `023b94fac2ecc3d6e991901092c2c7c980002e875e2fe5e283524118e3b22115` |
| `P3B-SHA256SUMS` | Route B v5 | 22 | `a312949cef45bbacc1324a66feb9f68fbdebe8bb55c6c278daebb87367f7dfd8` |
| `P2-real-SHA256SUMS` | real p2 | 32 | `a3c3fdf429dcfcf2ed1615c4e8114d4e5c8d784ea933afdb95c9056c1c8cec74` |
| `Gate5A-SHA256SUMS` | Gate 5 Route A | 43 | `a193a749bceb17e854effa041bc315507e86a3e5288a0e7743c55cc2d578d845` |
| `Gate5B-SHA256SUMS` | Gate 5 Route B | 43 | `0eafc14bd1c35c8977b273c96ccf9a8b1361398d9ba349a32d91920f39837d19` |

## Key identities

```text
original ELF             aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393
Route A candidate        c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82
Route B candidate        208574ab5b7753fab6f28860830069770622fb7b1fcb8d181fa50453f8656717
Gate 4 roundtrip         decdcdb821bfe4c41a070b0a23a2f6497f46a92f439cb02a1e4139a82cde954b
Route A probe            f39a0302dd1aa42635da80fa4e9160d37a221f5ac383270add3eaedcffd146cb
Route A successor        90af91ef6b238428d202f891e5829ed81733b76dd9e7f184acd91f4cd769ff20
Route B probe            05d9a2cd417880c9be51e1a4cd4608158d94b50da233b223c2f4a589850edb82
Route B successor        0066973d5304e06024b56fbc1e9d969d5007eac290314935129517f931209eee
```

The manifests use the same SHA-256 algorithm as `sha256sum -c`. Line endings may
be LF/CRLF depending on the original evidence capture; compare hashes with a
line-ending-neutral re-hash if needed.

## Raw evidence location

Raw guest logs and full bundles are not duplicated in this clean repository.
They remain available in the original development repository's final tag and
release artifacts. This index is sufficient to identify and re-hash the accepted
evidence set.
