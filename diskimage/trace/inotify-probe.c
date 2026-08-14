/*
 * inotify-probe.c - pinpoints where pcmanfm's startup stalls under CheerpX.
 *
 * pcmanfm (via libfm's fm_gtk_init -> _fm_templates_init) stalls right after
 * scanning the ~/Templates dir, before its window maps (plans/display-bug.md
 * §2.9). _template_dir_init() then does two things that could hang invisibly:
 *   (1) fm_dir_list_job_run_async()  -> pthread_create + readdir
 *   (2) fm_monitor_directory()       -> g_file_monitor_directory -> inotify
 * The syscall logger does not interpose inotify or clone, so a hang in either
 * looks like a silent stall. This probe runs those calls DIRECTLY against the
 * guest libc and prints ENTER/RET lines (stderr -> /dev/console), so a call
 * that prints ENTER but never RET is the hang.
 *
 * Build (i386 musl, Alpine 3.17):
 *   gcc -O2 -o inotify-probe inotify-probe.c -pthread
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/inotify.h>
#include <pthread.h>

static void *thread_fn(void *arg)
{
	(void)arg;
	return NULL;
}

int main(void)
{
	int fd, wd, rc;
	pthread_t t;
	void *rv;
	DIR *d;
	struct dirent *de;
	int n;

	fprintf(stderr, "INOTIFY-PROBE-START\n");
	fflush(stderr);

	/* 1. inotify_init1 (what GIO's directory monitor would call) */
	fprintf(stderr, "inotify_init1 ENTER\n");
	fflush(stderr);
	fd = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);
	fprintf(stderr, "inotify_init1 RET=%d errno=%d (%s)\n", fd, errno, strerror(errno));
	fflush(stderr);
	if (fd >= 0)
	{
		fprintf(stderr, "inotify_add_watch ENTER\n");
		fflush(stderr);
		wd = inotify_add_watch(fd, "/tmp", IN_ALL_EVENTS);
		fprintf(stderr, "inotify_add_watch RET=%d errno=%d (%s)\n", wd, errno, strerror(errno));
		fflush(stderr);
		close(fd);
	}

	/* 2. pthread_create/join (what libfm's async dir-list job would do) */
	fprintf(stderr, "pthread_create ENTER\n");
	fflush(stderr);
	rc = pthread_create(&t, NULL, thread_fn, NULL);
	fprintf(stderr, "pthread_create RET=%d (%s)\n", rc, rc ? strerror(errno) : "ok");
	fflush(stderr);
	if (rc == 0)
	{
		fprintf(stderr, "pthread_join ENTER\n");
		fflush(stderr);
		rc = pthread_join(t, &rv);
		fprintf(stderr, "pthread_join RET=%d\n", rc);
		fflush(stderr);
	}

	/* 3. readdir (what the dir-list job does on the folder) */
	fprintf(stderr, "opendir(/home/user) ENTER\n");
	fflush(stderr);
	d = opendir("/home/user");
	fprintf(stderr, "opendir RET=%p errno=%d (%s)\n", (void *)d, errno, strerror(errno));
	fflush(stderr);
	if (d)
	{
		n = 0;
		while ((de = readdir(d)))
			n++;
		fprintf(stderr, "readdir count=%d\n", n);
		fflush(stderr);
		closedir(d);
	}

	fprintf(stderr, "INOTIFY-PROBE-DONE\n");
	return 0;
}
