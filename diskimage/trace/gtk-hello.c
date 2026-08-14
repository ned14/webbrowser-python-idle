/*
 * gtk-hello.c - minimal GTK2 app to test whether ANY GTK app can run under
 * CheerpX (plans/display-bug.md §2.9). pcmanfm stalls inside libfm's
 * async-job machinery; if a bare GTK window + gtk_main() works, the problem
 * is libfm-specific; if it also fails, GTK2 under CheerpX is the blocker.
 *
 * Prints progress markers to stderr (-> /dev/console). The canvas is the
 * real check: if the window maps, the page fills with light pixels.
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add gtk2-dev gcc musl-dev pkgconf
 *   gcc -O2 -o gtk-hello gtk-hello.c $(pkg-config --cflags --libs gtk+-2.0)
 */
#include <stdio.h>
#include <gtk/gtk.h>

static gboolean on_delete(GtkWidget *w, GdkEvent *ev, gpointer data)
{
	gtk_main_quit();
	return TRUE;
}

int main(int argc, char **argv)
{
	GtkWidget *win;
	GtkWidget *label;

	fprintf(stderr, "GTK-HELLO-START\n");
	fflush(stderr);

	fprintf(stderr, "gtk_init ENTER\n");
	fflush(stderr);
	gtk_init(&argc, &argv);
	fprintf(stderr, "gtk_init RET\n");
	fflush(stderr);

	fprintf(stderr, "gtk_window_new ENTER\n");
	fflush(stderr);
	win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
	gtk_window_set_title(GTK_WINDOW(win), "GTK Hello");
	gtk_window_set_default_size(GTK_WINDOW(win), 600, 400);
	fprintf(stderr, "gtk_window_new RET\n");
	fflush(stderr);

	label = gtk_label_new("Hello from GTK2 under CheerpX");
	gtk_container_add(GTK_CONTAINER(win), label);

	fprintf(stderr, "gtk_widget_show_all ENTER\n");
	fflush(stderr);
	gtk_widget_show_all(win);
	fprintf(stderr, "gtk_widget_show_all RET\n");
	fflush(stderr);

	g_signal_connect(win, "delete-event", G_CALLBACK(on_delete), NULL);

	fprintf(stderr, "gtk_main ENTER\n");
	fflush(stderr);
	gtk_main();
	fprintf(stderr, "GTK-HELLO-EXIT\n");
	return 0;
}
