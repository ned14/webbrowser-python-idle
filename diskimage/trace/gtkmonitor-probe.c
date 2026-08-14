/*
 * gtkmonitor-probe.c - isolate why g_file_monitor_directory hangs in the
 * file managers but not in the plain-GLib probe (plans/display-bug.md §2.9).
 * Two variables are tested separately:
 *   (a) GTK/GDK initialized (pcmanfm/spacefm run with GTK active)
 *   (b) a GLib GMutex held across the call (libfm's fm_monitor_directory
 *       holds its global hash lock while calling g_file_monitor_directory)
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add gcc musl-dev pkgconf gtk+3.0-dev
 *   gcc -O2 -o gtkmonitor-probe gtkmonitor-probe.c $(pkg-config --cflags --libs gtk+-3.0)
 */
#include <stdio.h>
#include <glib.h>
#include <gio/gio.h>
#include <gtk/gtk.h>

static GMutex lock;

static void test_monitor(const char *label, gboolean with_lock)
{
	GFile *gf;
	GFileMonitor *mon;
	GError *error = NULL;

	fprintf(stderr, "[%s] begin (lock=%d)\n", label, with_lock);
	fflush(stderr);
	gf = g_file_new_for_path("/home/user");
	if (with_lock)
		g_mutex_lock(&lock);
	fprintf(stderr, "[%s] g_file_monitor_directory ENTER\n", label);
	fflush(stderr);
	mon = g_file_monitor_directory(gf, G_FILE_MONITOR_NONE, NULL, &error);
	fprintf(stderr, "[%s] g_file_monitor_directory RET=%p err=%s\n", label,
		(void *)mon, error ? error->message : "(null)");
	fflush(stderr);
	if (with_lock)
		g_mutex_unlock(&lock);
	if (mon)
	{
		g_file_monitor_set_rate_limit(mon, 5000);
		g_object_unref(mon);
	}
	if (error)
		g_error_free(error);
	g_object_unref(gf);
	fprintf(stderr, "[%s] done\n", label);
	fflush(stderr);
}

int main(int argc, char **argv)
{
	gtk_init(&argc, &argv);
	fprintf(stderr, "GTKMONITOR-START (gtk initialized)\n");
	fflush(stderr);

	test_monitor("nogtklock", FALSE);
	test_monitor("gtklock", TRUE);

	fprintf(stderr, "GTKMONITOR-DONE\n");
	return 0;
}
