/*
 * glib-probe.c - tests the GLib/GIO machinery pcmanfm uses right where it
 * stalls under CheerpX (plans/display-bug.md §2.9): the raw syscalls
 * (inotify_init1, pthread_create, readdir) all work, so the stall is one
 * level up in GLib. This probe exercises:
 *   1. g_thread_new + g_thread_join      (libfm async dir-list job)
 *   2. g_file_monitor_directory          (libfm template-dir monitor)
 *   3. g_file_enumerate_children         (libfm dir-list job file listing)
 * Each prints ENTER/RET to stderr (-> /dev/console); a step that prints
 * ENTER but never RET is the blocker.
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add glib-dev gcc musl-dev pkgconf
 *   gcc -O2 -o glib-probe glib-probe.c $(pkg-config --cflags --libs gio-2.0)
 */
#include <stdio.h>
#include <glib.h>
#include <gio/gio.h>

static gpointer thread_fn(gpointer data)
{
	fprintf(stderr, "  thread_fn running\n");
	fflush(stderr);
	return NULL;
}

static void pool_fn(gpointer data, gpointer user_data)
{
	fprintf(stderr, "  pool_fn running\n");
	fflush(stderr);
}

int main(void)
{
	GThread *th;
	GThreadPool *pool;
	GFile *gf;
	GFileMonitor *mon;
	GError *error = NULL;
	GFileEnumerator *en;
	GFileInfo *info;

	fprintf(stderr, "GLIB-PROBE-START\n");
	fflush(stderr);

	/* 1. GLib thread create/join (g_thread_new, not raw pthread) */
	fprintf(stderr, "g_thread_new ENTER\n");
	fflush(stderr);
	th = g_thread_new("probe", thread_fn, NULL);
	fprintf(stderr, "g_thread_new RET=%p\n", (void *)th);
	fflush(stderr);
	if (th)
	{
		fprintf(stderr, "g_thread_join ENTER\n");
		fflush(stderr);
		g_thread_join(th);
		fprintf(stderr, "g_thread_join RET\n");
		fflush(stderr);
	}

	/* 1b. GLib THREAD POOL (what fm_job_run_async actually uses) */
	fprintf(stderr, "g_thread_pool_new ENTER\n");
	fflush(stderr);
	pool = g_thread_pool_new(pool_fn, NULL, -1, FALSE, NULL);
	fprintf(stderr, "g_thread_pool_new RET=%p\n", (void *)pool);
	fflush(stderr);
	if (pool)
	{
		fprintf(stderr, "g_thread_pool_push ENTER\n");
		fflush(stderr);
		g_thread_pool_push(pool, GINT_TO_POINTER(1), NULL);
		fprintf(stderr, "g_thread_pool_push RET\n");
		fflush(stderr);
		fprintf(stderr, "g_thread_pool_free ENTER\n");
		fflush(stderr);
		g_thread_pool_free(pool, FALSE, TRUE);
		fprintf(stderr, "g_thread_pool_free RET\n");
		fflush(stderr);
	}

	/* 2. GIO directory monitor (what fm_monitor_directory calls) */
	gf = g_file_new_for_path("/home/user");
	fprintf(stderr, "g_file_monitor_directory ENTER\n");
	fflush(stderr);
	mon = g_file_monitor_directory(gf, G_FILE_MONITOR_NONE, NULL, &error);
	fprintf(stderr, "g_file_monitor_directory RET=%p err=%s\n",
		(void *)mon, error ? error->message : "(null)");
	fflush(stderr);
	if (mon)
		g_object_unref(mon);
	if (error)
	{
		g_error_free(error);
		error = NULL;
	}

	/* 3. GIO directory enumeration (what the dir-list job does) */
	fprintf(stderr, "g_file_enumerate_children ENTER\n");
	fflush(stderr);
	en = g_file_enumerate_children(gf, G_FILE_ATTRIBUTE_STANDARD_NAME, 0,
				       NULL, &error);
	fprintf(stderr, "g_file_enumerate_children RET=%p err=%s\n",
		(void *)en, error ? error->message : "(null)");
	fflush(stderr);
	if (en)
	{
		int n = 0;
		fprintf(stderr, "g_file_enumerator_next_file ENTER\n");
		fflush(stderr);
		while ((info = g_file_enumerator_next_file(en, NULL, &error)))
		{
			n++;
			g_object_unref(info);
		}
		fprintf(stderr, "g_file_enumerator_next_file count=%d err=%s\n", n,
			error ? error->message : "(null)");
		fflush(stderr);
		g_object_unref(en);
	}
	if (error)
		g_error_free(error);

	g_object_unref(gf);
	fprintf(stderr, "GLIB-PROBE-DONE\n");
	return 0;
}
