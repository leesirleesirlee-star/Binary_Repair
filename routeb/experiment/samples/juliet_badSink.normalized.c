/* Normalized recovered C for the CWE121_..._badSink fixture.
 *
 * Source: Ghidra 12.x decompiler output for the symbol below; normalization is
 * limited to types/names plus preserving the original dead zeroing of dest.
 * The volatile pointer keeps the zeroing as explicit stores (no memset import).
 */
extern char *strcpy(char *dest, const char *src);
extern void printLine(char *line);

void CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink(char *data)
{
    char dest[50];
    int i;
    volatile char *zero = dest;

    for (i = 0; i < 50; i++) {
        zero[i] = 0;
    }
    strcpy(dest, data);
    printLine(data);
}
