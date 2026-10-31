/* Reference *repaired* C for the same function: one guard, no other change.
 *
 * This is the expected shape of a Route B candidate for the fixture. The model may
 * produce something equivalent; the deterministic checks decide, not this file.
 */

int copy_name(char *dst, const char *src, unsigned int n)
{
    unsigned int i;
    if (n >= 16u) {
        return -1;
    }
    for (i = 0; i < n; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
    return (int)i;
}
