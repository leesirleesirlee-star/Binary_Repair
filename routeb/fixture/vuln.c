/*
 * vuln.c — synthetic public fixture for the Route B feasibility experiment.
 *
 * Deliberately vulnerable: copy_name() copies n bytes plus a terminator without
 * checking the destination capacity. main() places a canary immediately after the
 * 16-byte destination so the out-of-bounds write is observed deterministically
 * (no reliance on a crash, ASan, or stack-protector behaviour).
 *
 * Contract used by the team:
 *   valid   : 0..15 bytes, ASCII, copy succeeded, guard intact, exit code 0
 *   invalid : >=16 bytes, must return -1 before writing, guard intact, exit code 0
 *
 * Build with fixture/build.sh (non-PIE, -O0, no stack protector) so that the
 * function is small and Ghidra's recovered C stays readable.
 */

#include <stdio.h>
#include <string.h>

#define DST_CAP 16u
#define CANARY 0xDEADBEEFu

struct record {
    char name[DST_CAP];
    unsigned int guard;
};

int copy_name(char *dst, const char *src, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
    return (int)i;
}

int main(int argc, char **argv)
{
    struct record rec;
    const char *input = (argc > 1) ? argv[1] : "hello";
    unsigned int n = (unsigned int)strlen(input);
    int ret;

    memset(&rec, 0, sizeof rec);
    rec.guard = CANARY;

    ret = copy_name(rec.name, input, n);

    printf("in_len=%u ret=%d name=%.16s guard=%s\n",
           n, ret, rec.name, (rec.guard == CANARY) ? "ok" : "CORRUPT");
    return (rec.guard == CANARY) ? 0 : 3;
}
