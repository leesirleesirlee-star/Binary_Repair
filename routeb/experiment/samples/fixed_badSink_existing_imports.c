/* Fixed C using only imports already present in the fixture.
 *
 * No strlen(): a bounded NUL scan stops at 50.
 * No exit(): exit_group(1) goes directly through syscall 231.
 * fprintf/stderr/printLine/strcpy already exist in the binary.
 */

#include <stdio.h>

extern char *strcpy(char *dest, const char *src);
extern void printLine(char *line);

static void die(void) __attribute__((noreturn));
static void die(void)
{
    __asm__ volatile(
        "mov $231, %%eax\n\t"
        "mov $1, %%edi\n\t"
        "syscall"
        ::: "rax", "rdi", "memory");
    __builtin_unreachable();
}

void CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink(char *data)
{
    char dest[50];
    int i;
    int n = 0;
    volatile char *zero = dest;

    while (n < 50 && data[n] != '\0') {
        n++;
    }
    if (n >= 50) {
        fprintf(stderr, "ERROR: input exceeds destination capacity\n");
        die();
    }

    for (i = 0; i < 50; i++) {
        zero[i] = 0;
    }
    strcpy(dest, data);
    printLine(data);
}
