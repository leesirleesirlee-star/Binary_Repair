/* Reference normalization for copy_name, produced from Ghidra output.
 *
 * This is what a *reviewed* normalization looks like for this fixture. Compare it
 * with your own before using it; the point of the exercise is to notice every edit
 * you had to make, not to copy this file.
 *
 * Changes from raw decompiler output, expected:
 *   - undefined4/undefined8 -> int / unsigned int
 *   - parameter names param_1.. -> dst/src/n (interface from the binary, reviewed)
 *   - local uVar1/local_10 -> i
 *   - add const on src to match the recovered signature
 * Why each edit is safe: types and names only; no control-flow change.
 */

int copy_name(char *dst, const char *src, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
    return (int)i;
}
