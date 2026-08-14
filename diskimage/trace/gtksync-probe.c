/*
 * gtksync-probe.c - tests whether calling GTK/GDK from a GIO worker thread
 * deadlocks under CheerpX (plans/display-bug.md §2.9). pcmanfm's libfm
 * async jobs (thread pool) emit "finished" whose handler runs in the worker
 * and touches GTK (fm_icon_from_name -> icon theme). A bare GTK3 window works,
 * and plain threads work, so this tests the combination: GTK initialized,
 * then a worker thread calls into GTK while the main thread continues.
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add gcc musl-dev pkgconf gtk+3.0-dev
 *   gcc -O2 -o gtksync-probe gtksync-probe.c $(pkg-config --cflags --libs gtk+-3.0)
 */
#include <stdio.h>
#include <glib.h>
#include <gtk/gtk.h>

static GThreadPool *pool = NULL;

static void worker(gpointer data, gpointer user_data)
{
	fprintf(stderr, "  worker: calling GTK from worker thread ENTER\n");
	fflush(stderr);
	GtkIconTheme *theme = gtk_icon_theme_get_default();
	GtkIconInfo *info = gtk_icon_theme_lookup_icon(theme, "folder", 24, 0);
	fprintf(stderr, "  worker: gtk_icon_theme_lookup_icon RET=%p\n", (void *)info);
	fflush(stderr);
	if (info)
		g_object_unref(info);
	fprintf(stderr, "  worker: GTK call done\n");
	fflush(stderr);
}

int main(int argc, char **argv)
{
	gtk_init(&argc, &argv);
	fprintf(stderr, "GTKSYNC-START (gtk initialized)\n");
	fflush(stderr);

	/* create a main window like pcmanfm would */
	GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
	gtk_window_set_default_size(GTK_WINDOW(win), 400, 300);
	gtk_widget_show_all(win);
	fprintf(stderr, "window shown\n");
	fflush(stderr);

	pool = g_thread_pool_new(worker, NULL, 1, FALSE, NULL);
	fprintf(stderr, "pool created, pushing job ENTER\n");
	fflush(stderr);
	g_thread_pool_push(pool, GINT_TO_POINTER(1), NULL);
	fprintf(stderr, "push RET (not waiting)\n");
	fflush(stderr);

	/* main thread keeps doing GTK work + main loop */
	fprintf(stderr, "gtk_main ENTER\n");
	fflush(stderr);
	gtk_main();
	fprintf(stderr, "GTKSYNC-EXIT\n");
	return 0;
}
