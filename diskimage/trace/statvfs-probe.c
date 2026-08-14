/*
 * statvfs-probe.c - test statvfs/fstatvfs under CheerpX (plans/display-bug.md
 * §2.9). pcmanfm stalls right after reading the mount table (/etc/mtab,
 * /proc/self/mountinfo), which GIO's g_file_query_filesystem_info does before
 * calling statvfs — a syscall the trace logger does not interpose, so a hang
 * there is silent. If statvfs hangs, the desktop apps' FS-info queries are
 * the blocker.
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -o statvfs-probe statvfs-probe.c
 */
#include <stdio.h>
#include <sys/statvfs.h>
#include <errno.h>
#include <string.h>

int main(void)
{
	struct statvfs buf;
	int r;

	fprintf(stderr, "STATVFS-PROBE-START\n");
	fflush(stderr);

	fprintf(stderr, "statvfs(/home/user) ENTER\n");
	fflush(stderr);
	r = statvfs("/home/user", &buf);
	fprintf(stderr, "statvfs(/home/user) RET=%d errno=%d (%s)\n", r, errno, strerror(errno));
	fflush(stderr);

	fprintf(stderr, "statvfs(/) ENTER\n");
	fflush(stderr);
	r = statvfs("/", &buf);
	fprintf(stderr, "statvfs(/) RET=%d errno=%d (%s)\n", r, errno, strerror(errno));
	fflush(stderr);

	fprintf(stderr, "STATVFS-PROBE-DONE\n");
	return 0;
}
