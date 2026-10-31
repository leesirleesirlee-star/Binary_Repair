# routea

Route A worker: Capstone decode, trampoline plan, gated LIEF build, layout audit.

```bash
# 1. instruction boundaries for the frozen function
python -m routea.worker decode \
  --elf fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf \
  --target CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink

# 2. plan only (never writes the candidate)
python -m routea.worker plan --elf ... --contract contract.json --out plan.json

# 3. build (only after the Gate 3 scope ruling)
python -m routea.worker build --elf ... --contract contract.json \
  --out patched.elf --manifest edit-manifest.json --scope-authorized

# 4. static layout audit
python -m routea.worker audit --original ... --patched ... --contract contract.json
```

`build` is gated by `--scope-authorized` because adding an R-- message segment,
an R-X trampoline segment and changing the file layout is a Gate 3 scope
decision. Until the ruling, no exact-stderr patch is applied to the candidate.
